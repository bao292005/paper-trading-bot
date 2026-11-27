"""LiveHub: giữ ĐÚNG 1 kết nối stream Alpaca (gói miễn phí chỉ cho 1) cho mọi trình duyệt đang xem.

Luồng: stream gửi TRADE (từng giao dịch) + nến PHÚT (bars + updatedBars) -> hub dựng "phút đang chạy"
theo mã (bar chính thức thắng trade) -> gộp lên khung tf của từng client -> đẩy xuống websocket
(trade được gộp, tối đa ~4 lần/giây; bar đẩy ngay).

Realtime v2 (kết nối bền):
  - 1 stream sống lâu; hết người xem thì chờ `grace` giây (mặc định 5 phút) mới đóng -> tải lại trang
    hay đổi mã không mở kết nối mới;
  - stream lỗi: luôn đóng hẳn stream cũ (chờ socket đóng) rồi mới tạo stream mới;
  - lỗi "connection limit exceeded" (Alpaca còn giữ kết nối cũ): chờ lâu 30s -> 5 phút.

`adapter_factory()` trả đối tượng có: async subscribe(handler, symbols), async unsubscribe(symbols),
async run() (MỘT phiên kết nối, lỗi thì raise), async stop() (chờ socket đóng hẳn), thuộc tính connected.
`handler` nhận cả trade (có .price/.size) lẫn bar (có .open...). Bản thật ở api/live.py, test dùng bản giả.
"""

import asyncio
import logging
import time as _time
from collections import Counter
from dataclasses import dataclass
from datetime import time
from typing import Dict

import pandas as pd

from .api.timeutil import NY, snap_to_bar

logger = logging.getLogger("paper_bot")


@dataclass(eq=False)
class Client:
    ws: object  # có send_json()
    symbol: str
    tf: str
    asset: str = "stock"  # "stock" | "crypto"


def ny_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=NY).tz_localize(None)


def utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def market_is_open(now: pd.Timestamp) -> bool:
    """Phiên chính 09:30-16:00 thứ 2-6 (chưa tính ngày lễ)."""
    return now.day_of_week < 5 and time(9, 30) <= now.time() < time(16, 0)


def _utc(ts) -> pd.Timestamp:
    """Timestamp từ stream (có tz hoặc naive = UTC) -> Timestamp UTC có tz."""
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _merge(rows):
    """Gộp các phút (đã theo thứ tự thời gian) thành 1 nến; rỗng -> None."""
    if not rows:
        return None
    return {"open": rows[0]["open"], "high": max(r["high"] for r in rows), "low": min(r["low"] for r in rows),
            "close": rows[-1]["close"], "volume": sum(r["volume"] for r in rows)}


LIMIT_TEXT = "connection limit exceeded"  # lỗi Alpaca khi slot kết nối duy nhất còn bị giữ


class LiveHub:
    """Hub của MỘT loại tài sản. Hub stock giữ thêm `crypto_hub` (cùng lớp, asset="crypto") và chuyển client
    crypto sang đó -> tối đa 1 stream cổ phiếu + 1 stream crypto."""

    def __init__(self, adapter_factory, seed_fn=None, clock=None, sleep=asyncio.sleep,
                 heartbeat=30.0, backoff_start=1.0, backoff_max=60.0, asset="stock",
                 crypto_adapter_factory=None, crypto_seed_fn=None,
                 grace=0.0, limit_start=30.0, limit_max=300.0, throttle=0.25):
        # grace: số giây giữ kết nối sau khi hết người xem. Lớp mặc định 0 (đóng ngay, như bản cũ);
        # server web (app.py) dùng GRACE_SECONDS = 5 phút.
        self.asset = asset
        self.adapter_factory, self.seed_fn, self.sleep = adapter_factory, seed_fn, sleep
        self.clock = clock or (utc_now if asset == "crypto" else ny_now)
        self.heartbeat, self.backoff_start, self.backoff_max = heartbeat, backoff_start, backoff_max
        self.grace, self.limit_start, self.limit_max, self.throttle = grace, limit_start, limit_max, throttle
        self.clients = set()
        self.refs: Counter = Counter()  # mã -> số client đang xem
        self.minutes: Dict[str, Dict[pd.Timestamp, dict]] = {}  # mã -> {phút NY (crypto: UTC): ohlcv}
        self.last_min: Dict[str, pd.Timestamp] = {}  # mã -> phút mới nhất (khỏi max() cả dict mỗi lần)
        self.last_trade: Dict[str, float] = {}  # mã -> unix giây của trade cuối (để client đo độ trễ)
        self._cache = {}  # (mã, tf) -> (phút cuối, time nến, gộp các phút TRƯỚC phút cuối)
        self._dirty = set()  # mã có trade mới chưa đẩy
        self.adapter = None
        self.stream_state, self.last_error, self.retry_at = "idle", "", 0.0
        self._tasks = []
        self._grace_task = None
        self.crypto_hub = None
        if asset == "stock" and crypto_adapter_factory is not None:
            self.crypto_hub = LiveHub(crypto_adapter_factory, crypto_seed_fn, sleep=sleep, heartbeat=heartbeat,
                                      backoff_start=backoff_start, backoff_max=backoff_max, asset="crypto",
                                      grace=grace, limit_start=limit_start, limit_max=limit_max, throttle=throttle)

    def _hub_for(self, client: Client):
        return self.crypto_hub if client.asset == "crypto" and self.crypto_hub is not None else self

    # ---------- client ----------
    async def add(self, client: Client) -> None:
        hub = self._hub_for(client)
        if hub is not self:
            return await hub.add(client)
        if client.asset == "crypto" and self.asset != "crypto":
            return await self._send(client, {"type": "status", "market": "closed", "stream": "idle",
                                             "message": "Chưa bật stream crypto"})
        self._cancel_grace()  # có người xem lại trong lúc chờ: giữ nguyên kết nối cũ
        first = self.refs[client.symbol] == 0
        self.refs[client.symbol] += 1
        self.clients.add(client)
        await self._send(client, self.status())
        if first:
            await self._seed(client.symbol)
            if self.adapter is not None:
                try:
                    await self.adapter.subscribe(self._on_event, [client.symbol])
                except Exception as e:  # stream đang hỏng: supervise sẽ subscribe lại khi nối lại
                    logger.warning(f"subscribe {client.symbol} lỗi: {e}")
        await self._push_candle(client)
        self._ensure_tasks()

    def _ensure_tasks(self) -> None:
        """Chạy các task nền nếu chưa chạy (supervise tự thoát khi không còn mã nào được xem)."""
        if not self._tasks:
            self._tasks = [asyncio.create_task(self._supervise()), asyncio.create_task(self._beat()),
                           asyncio.create_task(self._flush_loop())]
        elif self._tasks[0].done():
            self._tasks[0] = asyncio.create_task(self._supervise())

    async def remove(self, client: Client) -> None:
        hub = self._hub_for(client)
        if hub is not self:
            return await hub.remove(client)
        if client not in self.clients:
            return
        self.clients.discard(client)
        sym = client.symbol
        self.refs[sym] -= 1
        if self.refs[sym] <= 0:
            del self.refs[sym]
            for d in (self.minutes, self.last_min, self.last_trade):
                d.pop(sym, None)
            self._invalidate(sym)
            if self.adapter is not None:
                try:
                    await self.adapter.unsubscribe([sym])
                except Exception as e:
                    logger.warning(f"unsubscribe {sym} lỗi: {e}")
        if not self.clients:
            if self.grace <= 0:
                await self._shutdown_own()
            else:  # hết người xem: CHƯA đóng ngay, chờ grace (tải lại trang sẽ dùng lại kết nối)
                self._grace_task = asyncio.create_task(self._grace_close())

    async def _grace_close(self) -> None:
        await asyncio.sleep(self.grace)
        self._grace_task = None  # đặt None trước để _shutdown_own không tự huỷ chính task này
        logger.info(f"Hết người xem {self.grace:.0f}s: đóng stream {self.asset}")
        await self._shutdown_own()

    def _cancel_grace(self) -> None:
        if self._grace_task is not None:
            self._grace_task.cancel()
            self._grace_task = None

    async def shutdown(self) -> None:
        """Đóng mọi kết nối Alpaca (stock + crypto) và dừng các task nền."""
        if self.crypto_hub is not None:
            await self.crypto_hub.shutdown()
        await self._shutdown_own()

    async def _shutdown_own(self) -> None:
        """Đóng kết nối của riêng hub này: dừng task trước (để không tự nối lại), rồi chờ socket đóng hẳn."""
        self._cancel_grace()
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._dispose()
        self.stream_state = "idle"

    async def _dispose(self) -> None:
        """Đóng hẳn stream hiện tại (adapter.stop() chờ socket đóng) để nhả slot kết nối duy nhất."""
        ad, self.adapter = self.adapter, None
        if ad is not None:
            try:
                await ad.stop()
            except Exception as e:
                logger.warning(f"stop stream lỗi: {e}")

    # ---------- dữ liệu ----------
    async def _seed(self, symbol: str) -> None:
        """Nạp nến phút hôm nay từ REST để nến đang chạy ở khung lớn có đủ phần đã qua."""
        if self.seed_fn is None:
            return
        try:
            df = await asyncio.to_thread(self.seed_fn, symbol)
        except Exception as e:
            logger.warning(f"seed {symbol} lỗi: {e}")
            return
        store = self.minutes.setdefault(symbol, {})
        for ts, r in df.iterrows():
            store.setdefault(ts, {c: float(r[c]) for c in ("open", "high", "low", "close", "volume")})
        if store:
            self.last_min[symbol] = max(store)
            self._invalidate(symbol)

    def _local(self, ts) -> pd.Timestamp:
        """Timestamp của stream (UTC) -> giờ địa phương của asset, không tz (stock: NY, crypto: UTC)."""
        return _utc(ts).tz_convert("UTC" if self.asset == "crypto" else NY).tz_localize(None)

    def _live(self, symbol: str, ts: pd.Timestamp) -> bool:
        """Có người xem mã này và đang trong giờ (stock: phiên chính; crypto: 24/7)."""
        return symbol in self.refs and (self.asset == "crypto" or market_is_open(ts))

    def _put(self, symbol: str, minute: pd.Timestamp, row: dict) -> bool:
        """Ghi 1 phút vào store, cập nhật phút cuối + cache. False nếu là phút của ngày cũ (bỏ)."""
        store = self.minutes.setdefault(symbol, {})
        last = self.last_min.get(symbol)
        if last is not None and minute.normalize() != last.normalize():
            if minute < last:
                return False
            store.clear()  # sang ngày mới: chỉ giữ hôm nay
            self._invalidate(symbol)
        store[minute] = row
        if last is None or minute > last:
            self.last_min[symbol] = minute
        elif minute < last:
            self._invalidate(symbol)  # sửa phút cũ trong bucket: phải gộp lại
        return True

    def _invalidate(self, symbol: str) -> None:
        for k in [k for k in self._cache if k[0] == symbol]:
            del self._cache[k]

    async def _on_event(self, item) -> None:
        """Handler duy nhất đưa cho stream: trade có .price, bar có .open."""
        if hasattr(item, "price"):
            await self._on_trade(item)
        else:
            await self._on_bar(item)

    async def _on_bar(self, bar) -> None:
        """Nến phút chính thức / được sửa (bars, updatedBars): THẮNG trade và dữ liệu seed, đẩy ngay."""
        ts = self._local(bar.timestamp)
        if not self._live(bar.symbol, ts):
            return
        row = {"open": float(bar.open), "high": float(bar.high), "low": float(bar.low),
               "close": float(bar.close), "volume": float(bar.volume), "final": True}
        if self._put(bar.symbol, ts, row):
            self._dirty.discard(bar.symbol)
            await self._push_symbol(bar.symbol)

    async def _on_trade(self, trade) -> None:
        """1 giao dịch: cập nhật phút đang chạy (high/low/close, volume += size). Chỉ đánh dấu, flush đẩy sau."""
        ts = self._local(trade.timestamp)
        sym = trade.symbol
        if not self._live(sym, ts):
            return
        self.last_trade[sym] = _utc(trade.timestamp).timestamp()
        self._dirty.add(sym)
        minute = ts.floor("min")
        price, size = float(trade.price), float(trade.size)
        cur = self.minutes.get(sym, {}).get(minute)
        if cur is None:
            self._put(sym, minute, {"open": price, "high": price, "low": price, "close": price, "volume": size})
        elif not cur.get("final"):  # phút đã có bar chính thức thì bỏ qua trade (bar thắng)
            cur["high"], cur["low"] = max(cur["high"], price), min(cur["low"], price)
            cur["close"], cur["volume"] = price, cur["volume"] + size
            if minute != self.last_min.get(sym):
                self._invalidate(sym)

    def candle(self, symbol: str, tf: str):
        """Nến khung tf đang chạy = (gộp các phút trước phút cuối, có cache) + phút cuối.
        Trade chỉ đổi phút cuối nên mỗi tick là O(1); chỉ tính lại phần trước khi sang phút mới / có sửa."""
        store, last = self.minutes.get(symbol), self.last_min.get(symbol)
        if not store or last is None or last not in store:
            return None
        hit = self._cache.get((symbol, tf))
        if hit is None or hit[0] != last:
            key = snap_to_bar(last, tf, self.asset)
            prev = [store[t] for t in sorted(store) if t < last and snap_to_bar(t, tf, self.asset) == key]
            hit = (last, key, _merge(prev))
            self._cache[(symbol, tf)] = hit
        _, key, head = hit
        cur = store[last]
        if head is None:
            out = {k: cur[k] for k in ("open", "high", "low", "close", "volume")}
        else:
            out = {"open": head["open"], "high": max(head["high"], cur["high"]), "low": min(head["low"], cur["low"]),
                   "close": cur["close"], "volume": head["volume"] + cur["volume"]}
        return {"time": key, **out}

    async def _push_symbol(self, symbol: str) -> None:
        for c in [c for c in self.clients if c.symbol == symbol]:
            await self._push_candle(c)

    async def flush(self) -> None:
        """Đẩy nến của các mã có trade mới (gọi mỗi `throttle` giây -> tối đa ~4 tin/giây mỗi client)."""
        dirty, self._dirty = self._dirty, set()
        for sym in dirty:
            await self._push_symbol(sym)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.throttle)
            await self.flush()

    async def _push_candle(self, client: Client) -> None:
        c = self.candle(client.symbol, client.tf)
        if c:
            extra = {"asset": "crypto"} if self.asset == "crypto" else {}  # tin cổ phiếu giữ nguyên như cũ
            if client.symbol in self.last_trade:
                extra["ts"] = self.last_trade[client.symbol]  # unix giây của trade cuối (đo độ trễ)
            await self._send(client, {"type": "bar", "symbol": client.symbol, "tf": client.tf, **extra, **c})

    async def _send(self, client: Client, msg: dict) -> None:
        try:
            await client.ws.send_json(msg)
        except Exception:  # trình duyệt đã đóng: endpoint sẽ gọi remove() (giữ client để refs đếm đúng)
            pass

    # ---------- trạng thái ----------
    def status(self) -> dict:
        market = "open" if self.asset == "crypto" or market_is_open(self.clock()) else "closed"  # crypto 24/7
        text = {"closed": "Thị trường đóng cửa (market closed)"}.get(market, "")
        stream = self.stream_state
        if stream == "connected" and self.adapter is not None and not getattr(self.adapter, "connected", True):
            stream = "connecting"  # đã tạo stream nhưng socket chưa bắt tay xong
        retry_in = max(0, round(self.retry_at - _time.time())) if stream in ("reconnecting", "limit") else 0
        if stream == "limit":
            text = f"Alpaca đang giữ kết nối cũ, thử lại sau {retry_in}s"
        elif stream in ("reconnecting", "error"):
            text = f"Stream lỗi, đang nối lại: {self.last_error} (thử lại sau {retry_in}s)"
        reason = self.last_error if stream in ("reconnecting", "limit") else ""
        return {"type": "status", "market": market, "stream": stream, "message": text,
                "retry_in": retry_in, "reason": reason}

    async def broadcast_status(self) -> None:
        msg = self.status()
        for c in list(self.clients):
            await self._send(c, msg)

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat)
            await self.broadcast_status()

    # ---------- kết nối Alpaca ----------
    async def _supervise(self) -> None:
        """Giữ 1 stream sống: run() kết thúc/lỗi -> đóng hẳn stream cũ -> chờ backoff -> tạo stream mới.
        Không còn mã nào được xem (sau lỗi) thì thoát; add() sẽ chạy lại."""
        delay, limit_delay = self.backoff_start, self.limit_start
        loop = asyncio.get_running_loop()
        while self.refs:
            started = loop.time()
            self.stream_state = "connecting"
            try:
                self.adapter = self.adapter_factory()
                await self.adapter.subscribe(self._on_event, sorted(self.refs))
                self.stream_state = "connected"
                await self.broadcast_status()
                await self.adapter.run()
                err = "stream Alpaca tự đóng"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                err = str(e) or type(e).__name__
            logger.warning(f"Stream Alpaca {self.asset} lỗi: {err}")
            await self._dispose()  # luôn dọn stream lỗi (chờ socket đóng) trước khi tạo cái mới
            if loop.time() - started > 60:
                delay, limit_delay = self.backoff_start, self.limit_start  # đã chạy ổn định: reset backoff
            if LIMIT_TEXT in err.lower():  # Alpaca còn giữ kết nối cũ: chờ lâu (30s -> 5 phút)
                wait, limit_delay = limit_delay, min(limit_delay * 2, self.limit_max)
                self.stream_state = "limit"
            else:
                wait, delay = delay, min(delay * 2, self.backoff_max)
                self.stream_state = "reconnecting"
            self.last_error, self.retry_at = err, _time.time() + wait
            await self.broadcast_status()
            await self.sleep(wait)
        self.stream_state = "idle"
