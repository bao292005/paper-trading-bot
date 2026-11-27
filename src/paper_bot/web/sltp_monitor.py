"""SLTPMonitor: web server tự canh SL/TP crypto (Alpaca không có OCO/bracket cho crypto).

Nguồn SL/TP duy nhất: data/live_state.json, mục "sltp" của run_id live-crypto-paper (dùng chung với LiveBot,
ghi qua hàm có khoá của live.py). Mỗi `interval` giây (mặc định 3):
  giá mới nhất (mid quote, hoặc trade nếu mới hơn) ≤ SL hoặc ≥ TP -> giữ chỗ "triggering" (khoá file)
  -> ghi event -> market SELL đúng qty đang nắm TRÊN BROKER -> theo dõi tới khi khớp -> event sltp_hit, xoá entry.
Vị thế đã về 0 (người dùng tự đóng) -> xoá entry, không gửi gì. Chỉ BÁN (giảm rủi ro) nên kill switch không chặn.
Mọi lỗi mạng: ghi WARNING, lần sau thử lại, KHÔNG làm sập server.
"""

import logging
import threading
import time
from typing import Callable, Optional

from .. import live
from ..config import CRYPTO_UNIVERSE

logger = logging.getLogger("paper_bot")
RUN_ID = "web-sltp"  # run_id của event trong DB và mục cài đặt trong live_state.json
DONE = {"filled"}
DEAD = {"canceled", "cancelled", "expired", "rejected"}


def _text(x) -> str:
    return str(getattr(x, "value", x) or "").lower()


def alpaca_crypto_price(data_client, symbol: str):
    """(giá, nguồn) từ REST Alpaca: mid = (bid+ask)/2; latest trade nếu mới hơn quote."""
    from alpaca.data.requests import CryptoLatestQuoteRequest, CryptoLatestTradeRequest

    q = data_client.get_crypto_latest_quote(CryptoLatestQuoteRequest(symbol_or_symbols=symbol))[symbol]
    bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
    price, src, ts = ((bid + ask) / 2, "mid", q.timestamp) if bid > 0 and ask > 0 else (None, "", None)
    try:
        t = data_client.get_crypto_latest_trade(CryptoLatestTradeRequest(symbol_or_symbols=symbol))[symbol]
        if price is None or (t.timestamp and ts and t.timestamp > ts):
            price, src = float(t.price), "trade"
    except Exception as e:  # thiếu trade vẫn dùng được mid
        if price is None:
            raise e
    return price, src


def default_price_fn():
    """Giá thật (không cần key: dữ liệu crypto của Alpaca là công khai). Client tạo lười ở lần gọi đầu."""
    holder = {}

    def fn(symbol):
        if "c" not in holder:
            from alpaca.data.historical import CryptoHistoricalDataClient
            holder["c"] = CryptoHistoricalDataClient()
        return alpaca_crypto_price(holder["c"], symbol)
    return fn


class SLTPMonitor:
    def __init__(self, client_fn: Callable, price_fn: Optional[Callable] = None, state_path=None, db_path=None,
                 interval: float = 3.0, run_id: str = live.RUN_ID_CRYPTO_PAPER):
        """client_fn() -> TradingClient paper (test đưa client giả). price_fn(mã) -> (giá, nguồn) hoặc giá."""
        self.client_fn, self.price_fn = client_fn, price_fn or default_price_fn()
        self.state_path = state_path or live.STATE_PATH
        self.db_path, self.interval, self.run_id = db_path, float(interval), run_id
        self.inflight: dict = {}  # mã -> lệnh bán đã gửi, đang chờ khớp
        self.prices: dict = {}  # mã -> {price, source, at}
        self.last_check: Optional[float] = None
        self.last_error, self.last_error_at = "", None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._client = None

    # ---------- bật/tắt (lưu trong live_state.json, mục "web-sltp") ----------
    @property
    def enabled(self) -> bool:
        try:
            return bool(live.load_state(self.state_path, RUN_ID).get("enabled", True))  # mặc định BẬT
        except Exception:
            return True

    def set_enabled(self, on: bool) -> None:
        st = live.load_state(self.state_path, RUN_ID)
        live.save_state(self.state_path, RUN_ID, {**st, "enabled": bool(on)})

    # ---------- chạy nền ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return  # chỉ 1 luồng canh mỗi server
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sltp-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.enabled:
                    self.tick()
            except Exception as e:  # chốt chặn cuối: luồng không bao giờ chết vì 1 lỗi
                self._error(f"lỗi vòng canh SL/TP: {e}")
            self._stop.wait(self.interval)

    # ---------- tiện ích ----------
    def _error(self, msg: str) -> None:
        self.last_error, self.last_error_at = msg, time.time()
        logger.warning(f"[SLTPMonitor] {msg}")

    def _log(self, kind: str, msg: str, data: dict, level: str = "WARNING") -> None:
        """Ghi event DB (run_id web-sltp). Lỗi -> ném tiếp (trước khi gửi lệnh: không ghi được thì không gửi)."""
        from ..storage import Store

        st = Store(path=self.db_path, run_id=RUN_ID) if self.db_path else Store(run_id=RUN_ID)
        try:
            st.log_event(kind, msg, level=level, data=data)
        finally:
            st.conn.close()

    def client(self):
        if self._client is None:
            self._client = self.client_fn()
        return self._client

    def position_qty(self, symbol: str) -> Optional[float]:
        """Số đang nắm thực tế trên broker; 0 nếu không có vị thế; None nếu lỗi mạng (lần sau thử lại)."""
        try:
            p = self.client().get_open_position(symbol.replace("/", ""))
            return float(p.qty)
        except Exception as e:
            code = getattr(e, "status_code", None)
            if code == 404 or "position does not exist" in str(e).lower():
                return 0.0
            self._error(f"{symbol}: không lấy được vị thế ({e})")
            return None

    def price(self, symbol: str) -> Optional[float]:
        try:
            got = self.price_fn(symbol)
        except Exception as e:
            self._error(f"{symbol}: không lấy được giá ({e})")
            return None
        price, src = got if isinstance(got, tuple) else (got, "")
        if price is None:
            return None
        self.prices[symbol] = {"price": float(price), "source": src, "at": time.time()}
        return float(price)

    # ---------- 1 lần kiểm ----------
    def tick(self) -> None:
        """Kiểm mọi entry 1 lần (test gọi thẳng). Lỗi 1 mã không chặn mã khác."""
        try:
            entries = live.crypto_sltp_entries(self.state_path, self.run_id)
        except Exception as e:
            self._error(f"không đọc được {self.state_path}: {e}")
            return
        for sym, lv in entries.items():
            if sym not in CRYPTO_UNIVERSE or lv.get("side", "long") != "long":
                continue  # crypto thật chỉ long
            try:
                self._check(sym, lv)
            except Exception as e:
                self._error(f"{sym}: {e}")
        self.last_check = time.time()

    def _check(self, sym: str, lv: dict) -> None:
        trig = lv.get("triggering") or {}
        if sym not in self.inflight and trig.get("by") == RUN_ID and trig.get("order_id"):
            self.inflight[sym] = {**trig, "trigger_price": trig.get("price")}  # server khởi động lại: theo dõi tiếp
        if sym in self.inflight:
            return self._follow(sym)
        if live.trigger_active(lv):
            return  # LiveBot đang bán
        qty = self.position_qty(sym)
        if qty is None:
            return
        if qty <= 0:  # người dùng đã tự đóng
            live.remove_crypto_sltp(sym, self.state_path, self.run_id)
            self._log("sltp_cleared", f"{sym}: không còn vị thế, bỏ SL/TP", {"symbol": sym}, level="INFO")
            return
        price = self.price(sym)
        if price is None:
            return
        sl, tp = lv.get("stop_loss"), lv.get("take_profit")
        kind = "sl_hit" if sl and price <= sl else "tp_hit" if tp and price >= tp else None
        if kind:
            self._trigger(sym, kind, price, qty, sl, tp)

    def _trigger(self, sym, kind, price, qty, sl, tp) -> None:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        if not live.claim_crypto_sltp(sym, RUN_ID, self.state_path, self.run_id, price):
            return  # bên khác vừa giữ chỗ
        info = {"symbol": sym, "kind": kind, "trigger_price": price, "qty": qty, "sl": sl, "tp": tp}
        try:
            self._log("sltp_trigger", f"{sym}: giá {price} {'≤ SL' if kind == 'sl_hit' else '≥ TP'} -> bán {qty}", info)
            req = MarketOrderRequest(symbol=sym, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.GTC)
            res = self.client().submit_order(order_data=req)
        except Exception as e:
            live.release_crypto_sltp(sym, self.state_path, self.run_id)
            self._error(f"{sym}: gửi lệnh bán lỗi, sẽ thử lại ({e})")
            return
        oid = str(getattr(res, "id", ""))
        self.inflight[sym] = {**info, "order_id": oid, "at": time.time()}
        live.mark_crypto_sltp_order(sym, oid, self.state_path, self.run_id)
        self._follow(sym)

    def _follow(self, sym: str) -> None:
        """Lệnh bán đã gửi: khớp -> event sltp_hit + xoá entry; bị huỷ/từ chối -> bỏ giữ chỗ (lần sau thử lại)."""
        f = self.inflight[sym]
        try:
            o = self.client().get_order_by_id(f["order_id"])
        except Exception as e:
            self._error(f"{sym}: chưa hỏi được lệnh {f['order_id']} ({e})")
            return
        status = _text(getattr(o, "status", ""))
        if status in DONE:
            fill = getattr(o, "filled_avg_price", None)
            data = {**f, "fill_price": float(fill) if fill is not None else None,
                    "filled_qty": float(getattr(o, "filled_qty", 0) or 0)}
            self._log("sltp_hit", f"{sym}: {f.get('kind')} giá kích hoạt {f.get('trigger_price')} khớp {data['fill_price']}",
                      data)
            live.remove_crypto_sltp(sym, self.state_path, self.run_id)
            del self.inflight[sym]
        elif status in DEAD:
            self._log("sltp_failed", f"{sym}: lệnh bán {f['order_id']} {status}", {**f, "status": status})
            live.release_crypto_sltp(sym, self.state_path, self.run_id)
            del self.inflight[sym]

    # ---------- trạng thái cho UI ----------
    def status(self) -> dict:
        try:
            entries = live.crypto_sltp_entries(self.state_path, self.run_id)
            halted = bool(live.load_state(self.state_path, self.run_id).get("halted", False))
        except Exception as e:
            entries, halted = {}, False
            self._error(f"không đọc được live_state: {e}")
        rows = [{"symbol": s, "side": v.get("side", "long"), "sl": v.get("stop_loss"), "tp": v.get("take_profit"),
                 "qty_mode": v.get("qty_mode", "all"), "created_by": v.get("created_by", "bot"),
                 "created_at": v.get("created_at"), "triggering": v.get("triggering"),
                 "price": self.prices.get(s)} for s, v in entries.items()]
        return {"enabled": self.enabled, "running": self.running, "interval": self.interval,
                "last_check": self.last_check, "last_error": self.last_error, "last_error_at": self.last_error_at,
                "halted": halted, "entries": rows, "inflight": dict(self.inflight)}
