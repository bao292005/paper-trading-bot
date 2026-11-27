"""LiveBot: engine "full" chạy paper thật bằng pipeline mới (P1 → P2 → P3 → DB).

Mỗi vòng (run_cycle):
  giờ thị trường → (vòng đầu) đối soát → tài khoản/vị thế/lệnh chờ
  → nến ĐÃ ĐÓNG ≤ last_closed_day (symbols + SPY + mã đang nắm ngoài danh sách, chỉ để tính tương quan)
  → build_market_state (regime từ SPY, forecast nếu bật) → DecisionPipeline.decide
  → make_order_intent → AlpacaExecutor.submit (hoặc ghi orders status "dry_run")
  → sync fill → log_equity → kill switch (lưu data/live_state.json).

An toàn:
  - dry_run=True: KHÔNG BAO GIỜ gọi executor.submit / submit_order / cancel_order*.
  - Lỗi 1 mã: ghi log + event "symbol_error" rồi đi tiếp mã khác.
  - Lỗi mạng đầu vòng: ghi ERROR + event "cycle_error", bỏ vòng này (loop không sập).
Các flag trong config.flags giống hệt decision/full_policy (để kết quả live so được với backtest).
"""

import contextlib
import dataclasses
import json
import logging
import math
import os
import pickle
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from .config import BENCHMARK, CRYPTO_BENCHMARK, CRYPTO_UNIVERSE, UNIVERSE_50, TradingConfig, asset_profile
from .contracts import Fill, OrderIntent
from .data.cache import PROJECT_ROOT, load_bars
from .data.forecast import load_forecasts
from .data.market_state import build_market_state
from .decision.calibration import FEATURE_WINDOW
from .decision.pipeline import DecisionPipeline
from .decision.regime_switch import RegimeSwitchStrategy
from .execution import CRYPTO_MAX_NOTIONAL, AlpacaExecutor, normalize_crypto_symbol
from .risk import RiskConfig, legacy_position_size, make_order_intent
from .storage import Store
from .strategies import MovingAverageCrossoverStrategy, RSIStrategy

logger = logging.getLogger("paper_bot")

HISTORY_CALENDAR_DAYS = 1000  # ~690 phiên: đủ cho regime (MA200 + median 252) và FEATURE_WINDOW
RUN_ID_PAPER, RUN_ID_DRY = "live-paper", "live-dry"
RUN_ID_CRYPTO_PAPER, RUN_ID_CRYPTO_DRY = "live-crypto-paper", "live-crypto-dry"


def run_id_for(asset: str, dry_run: bool) -> str:
    """run_id của LiveBot theo asset và chế độ."""
    if asset == "crypto":
        return RUN_ID_CRYPTO_DRY if dry_run else RUN_ID_CRYPTO_PAPER
    return RUN_ID_DRY if dry_run else RUN_ID_PAPER


def now_utc() -> pd.Timestamp:
    """Giờ hiện tại UTC, không timezone (nến ngày crypto chốt 00:00 UTC)."""
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def last_closed_day_crypto(now: pd.Timestamp) -> pd.Timestamp:
    """Crypto giao dịch 24/7, không có giờ mở cửa: nến ĐÃ ĐÓNG = nến ngày UTC của hôm qua."""
    return now.normalize() - pd.Timedelta(days=1)


def now_ny() -> pd.Timestamp:
    """Giờ hiện tại ở New York, không timezone."""
    return pd.Timestamp.now(tz="America/New_York").tz_localize(None)


def today_ny() -> pd.Timestamp:
    """Ngày hiện tại theo giờ New York, dạng ngày không timezone (giống index của cache nến)."""
    return now_ny().normalize()


def last_closed_day(is_open: bool, now: pd.Timestamp) -> pd.Timestamp:
    """Ngày cuối cùng có nến ĐÃ ĐÓNG (nhất quán với backtest: quyết định trên giá đóng cửa).
    Hôm nay chỉ được tính khi thị trường đã đóng và đã qua 16:00 New York; ngược lại lấy tới hôm qua."""
    today = now.normalize()
    if not is_open and now.hour >= 16:
        return today
    return today - pd.Timedelta(days=1)


# ---------- Calibrator: fit trên cache, lưu file, fit lại khi cũ ----------
CALIB_PATH = PROJECT_ROOT / "data" / "models" / "calibrator.pkl"
CALIB_MAX_AGE_DAYS = 7
CALIB_TIMEOUT_S = 300
CALIB_TRAIN_YEARS = 3  # giống cửa sổ train của walk-forward


def calib_train_end(today: pd.Timestamp, calib_end: str = "2024-12-31") -> pd.Timestamp:
    """Mốc cuối dữ liệu fit = min(hôm qua, calib_end). calib_end="yesterday" -> hôm qua.
    Mặc định 2024-12-31: không chạm HOLDOUT (≥ 2025-01-01) cho tới khi chạy holdout xong."""
    yesterday = today - pd.Timedelta(days=1)
    if calib_end == "yesterday":
        return yesterday
    return min(yesterday, pd.Timestamp(calib_end))


def fit_live_calibrator(symbols, end: pd.Timestamp, use_fc_feat: bool):
    """Fit giống full_policy (cùng hàm _fit_on_train): nến cache [end - 3 năm, end]."""
    from .decision.full_policy import _fit_on_train

    start = end - pd.DateOffset(years=CALIB_TRAIN_YEARS)
    train = {s: load_bars(s, start=start, end=end) for s in symbols if s != BENCHMARK}
    spy = load_bars(BENCHMARK, end=end)
    fc_table = load_forecasts() if use_fc_feat else None
    return _fit_on_train(train, spy, fc_table, use_fc_feat, "isotonic")


def load_or_fit_calibrator(symbols, today: pd.Timestamp, use_fc_feat: bool, path=CALIB_PATH,
                           fit_fn=fit_live_calibrator, timeout_s: float = CALIB_TIMEOUT_S,
                           calib_end: str = "2024-12-31"):
    """Trả (calibrator hoặc None, ghi chú). File còn mới (≤ 7 ngày, cùng use_forecast, cùng calib_end)
    thì dùng lại; không thì fit lại."""
    path = Path(path)
    if path.exists():
        try:
            with open(path, "rb") as f:
                saved = pickle.load(f)
            age = (today - pd.Timestamp(saved["fit_date"])).days
            same = saved["use_forecast"] == use_fc_feat and saved.get("calib_end", "2024-12-31") == calib_end
            if age <= CALIB_MAX_AGE_DAYS and same:
                return saved["calibrator"], f"dùng calibrator fit ngày {saved['fit_date']}"
        except Exception as e:
            logger.warning(f"Không đọc được {path}: {e} -> fit lại")
    end = calib_train_end(today, calib_end)
    result = {}

    def work():
        try:
            result["cal"] = fit_fn(symbols, end, use_fc_feat)
        except Exception as e:
            result["err"] = e

    # Thread daemon + join(timeout): fit quá lâu thì bỏ, không chặn bot
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        return None, f"fit calibrator quá {timeout_s:.0f}s -> chạy không calibrator"
    if "err" in result:
        return None, f"fit calibrator lỗi: {result['err']} -> chạy không calibrator"
    if result.get("cal") is None:
        return None, "không đủ tín hiệu để fit calibrator -> chạy không calibrator"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump({"fit_date": today.date().isoformat(), "train_end": end.date().isoformat(),
                     "calib_end": calib_end,
                     "use_forecast": use_fc_feat, "symbols": sorted(symbols),
                     "calibrator": result["cal"]}, f)
    return result["cal"], f"đã fit calibrator (train tới {end.date()}), lưu {path}"


# ---------- Crypto: calibrator riêng + nguồn nến ----------
CALIB_PATH_CRYPTO = PROJECT_ROOT / "data" / "models" / "calibrator_crypto.pkl"


def fit_live_calibrator_crypto(symbols, end: pd.Timestamp, use_fc_feat: bool):
    """Fit trên nến crypto [warmup_start, end] (train MỞ RỘNG như prereg), end ≤ calib_end (2024-12-31).
    Không dùng feature forecast (bảng forecast tính sẵn chỉ có cổ phiếu)."""
    from .decision.full_policy import _fit_on_train

    start = pd.Timestamp(asset_profile("crypto")["warmup_start"])
    train = {s: load_bars(s, start=start, end=end, asset="crypto") for s in symbols}
    bench = load_bars(CRYPTO_BENCHMARK, start=start, end=end, asset="crypto")
    return _fit_on_train(train, bench, None, False, "isotonic")


class CryptoDataManager:
    """Nến ngày crypto cho LiveBot: cache (data/bars_crypto) trước, thiếu/cũ thì gọi API (không cần key)."""

    def __init__(self, client=None, bars_dir=None):
        self._client = client
        self.bars_dir = bars_dir

    @property
    def client(self):
        if self._client is None:
            from alpaca.data.historical import CryptoHistoricalDataClient

            self._client = CryptoHistoricalDataClient()
        return self._client

    def get_bars(self, symbol: str, start, end) -> pd.DataFrame:
        """Nến ngày UTC trong [start, end], chỉ dữ liệu ≤ end. Cache còn thiếu nến `end` thì tải từ API
        (KHÔNG ghi đè cache: cache chỉ do scripts/fetch_crypto_history.py tạo)."""
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        cached = load_bars(symbol, bars_dir=self.bars_dir, asset="crypto")
        if not cached.empty and cached.index[-1] >= end:
            return cached[(cached.index >= start) & (cached.index <= end)]
        from .data.sources import fetch_crypto_daily_bars

        try:
            df = fetch_crypto_daily_bars(self.client, [symbol], start.to_pydatetime(),
                                         (end + pd.Timedelta(days=1)).to_pydatetime())[symbol]
        except Exception as e:
            logger.warning(f"Lỗi tải nến {symbol}: {e} -> dùng cache")
            df = cached
        return df[(df.index >= start) & (df.index <= end)]

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Giá giao dịch gần nhất (để kiểm tra SL/TP). Lỗi -> None."""
        try:
            from alpaca.data.requests import CryptoLatestTradeRequest

            trades = self.client.get_crypto_latest_trade(CryptoLatestTradeRequest(symbol_or_symbols=symbol))
            return float(trades[symbol].price)
        except Exception as e:
            logger.warning(f"Không lấy được giá mới nhất {symbol}: {e}")
            return None


# ---------- Trạng thái kill switch lưu ra file (khởi động lại không mất) ----------
STATE_PATH = PROJECT_ROOT / "data" / "live_state.json"


def load_state(path, run_id: str) -> dict:
    """{halted, day, day_start_equity, peak_equity} của run_id; chưa có -> {}."""
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get(run_id, {})


@contextlib.contextmanager
def _state_lock(path: Path):
    """Khoá đơn giản (flock trên file .lock cạnh live_state.json): web và bot không ghi đè lẫn nhau."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a") as f:
        try:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)  # nhả khi đóng file
        except ImportError:  # Windows: không có flock -> bỏ khoá
            pass
        yield


def _write_atomic(path: Path, data: dict) -> None:
    """Ghi file tạm rồi os.replace: đang ghi dở mà sập thì file cũ vẫn nguyên."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def save_state(path, run_id: str, state: dict) -> None:
    path = Path(path)
    with _state_lock(path):
        data = json.loads(path.read_text()) if path.exists() else {}
        data[run_id] = state
        _write_atomic(path, data)


def set_crypto_sltp(symbol: str, stop_loss: float, take_profit: float, order_id: Optional[str] = None,
                    path=STATE_PATH, run_id: str = RUN_ID_CRYPTO_PAPER, created_by: str = "web") -> str:
    """Web đặt/sửa SL/TP crypto (cùng khung bot đọc). Có order_id -> 'pending' (lệnh mua chờ khớp, bot tự
    chuyển sang sltp khi có vị thế); không -> 'sltp' (vị thế đã có, giữ stop_order_id cũ). Trả tên mục đã ghi.
    Mỗi entry: stop_loss, take_profit, side ("long"), qty_mode ("all" = bán toàn bộ vị thế), created_by, created_at."""
    sl, tp = float(stop_loss), float(take_profit)
    if not (math.isfinite(sl) and math.isfinite(tp) and 0 < sl < tp):
        raise ValueError("cần 0 < SL < TP")
    path = Path(path)
    now = now_utc().isoformat()
    levels = {"stop_loss": sl, "take_profit": tp, "asof": now, "source": "web", "side": "long", "qty_mode": "all"}
    with _state_lock(path):
        data = json.loads(path.read_text()) if path.exists() else {}
        st = data.setdefault(run_id, {})
        if order_id:
            section = "pending"
            st.setdefault(section, {})[symbol] = {**levels, "order_id": str(order_id), "created_by": created_by,
                                                  "created_at": now}
        else:
            section = "sltp"
            old = st.get(section, {}).get(symbol, {})
            st.setdefault(section, {})[symbol] = {"created_by": created_by, "created_at": now, **old, **levels}
        _write_atomic(path, data)
    return section


# ---------- Chống bán 2 lần: ai sắp bán (web server / LiveBot) thì "giữ chỗ" entry bằng "triggering" ----------
TRIGGER_STALE_SECONDS = 120  # giữ chỗ chưa có order_id quá lâu (tiến trình chết giữa chừng) -> coi như bỏ


def _edit_sltp(path, run_id: str, fn):
    """Đọc-sửa-ghi mục 'sltp' của run_id trong khoá. fn(sltp_dict) -> kết quả trả về."""
    path = Path(path)
    with _state_lock(path):
        data = json.loads(path.read_text()) if path.exists() else {}
        sltp = data.setdefault(run_id, {}).setdefault("sltp", {})
        out = fn(sltp)
        _write_atomic(path, data)
    return out


def crypto_sltp_entries(path=STATE_PATH, run_id: str = RUN_ID_CRYPTO_PAPER) -> dict:
    """Mọi entry SL/TP đang canh (vị thế đã có): {mã: {...}} (đọc, không khoá)."""
    return dict(load_state(path, run_id).get("sltp", {}))


def trigger_active(entry: Optional[dict], now: Optional[float] = None, stale: float = TRIGGER_STALE_SECONDS) -> bool:
    """Entry đang có người bán? Có order_id (lệnh đã gửi) -> luôn đúng; chưa có -> đúng nếu chưa quá `stale` giây."""
    trig = (entry or {}).get("triggering")
    if not trig:
        return False
    if trig.get("order_id"):
        return True
    return (now if now is not None else time.time()) - float(trig.get("at", 0)) < stale


def claim_crypto_sltp(symbol: str, by: str, path=STATE_PATH, run_id: str = RUN_ID_CRYPTO_PAPER,
                      price: Optional[float] = None) -> bool:
    """Giữ chỗ trước khi bán. False = người khác đang bán (bỏ qua). Không có entry trên file -> True, không ghi
    (bot đang giữ SL/TP trong bộ nhớ; web server chỉ canh entry có trên file nên không thể trùng)."""
    def fn(sltp):
        cur = sltp.get(symbol)
        if cur is None:
            return True
        if trigger_active(cur):
            return False
        cur["triggering"] = {"by": by, "at": time.time(), "price": price}
        return True
    return _edit_sltp(path, run_id, fn)


def mark_crypto_sltp_order(symbol: str, order_id: str, path=STATE_PATH, run_id: str = RUN_ID_CRYPTO_PAPER) -> None:
    """Đã gửi lệnh bán: ghi order_id vào giữ chỗ (khởi động lại server vẫn theo dõi tiếp được)."""
    def fn(sltp):
        if symbol in sltp:
            sltp[symbol].setdefault("triggering", {"by": "?", "at": time.time()})["order_id"] = str(order_id)
    _edit_sltp(path, run_id, fn)


def release_crypto_sltp(symbol: str, path=STATE_PATH, run_id: str = RUN_ID_CRYPTO_PAPER) -> None:
    """Bỏ giữ chỗ (gửi lệnh lỗi / lệnh bị huỷ): lần kiểm sau được thử lại."""
    _edit_sltp(path, run_id, lambda sltp: sltp.get(symbol, {}).pop("triggering", None))


def remove_crypto_sltp(symbol: str, path=STATE_PATH, run_id: str = RUN_ID_CRYPTO_PAPER) -> bool:
    """Xoá entry SL/TP (đã khớp / vị thế về 0 / người dùng gỡ). Trả True nếu có entry để xoá."""
    return _edit_sltp(path, run_id, lambda sltp: sltp.pop(symbol, None) is not None)


def get_crypto_sltp(symbol: str, path=STATE_PATH, run_id: str = RUN_ID_CRYPTO_PAPER) -> dict:
    """{'pending': {...} | None, 'sltp': {...} | None} của 1 mã crypto (đọc, không khoá)."""
    st = load_state(path, run_id)
    return {k: st.get(k, {}).get(symbol) for k in ("pending", "sltp")}


def reset_kill_switch(run_id: str, state_path=STATE_PATH, store: Optional[Store] = None) -> str:
    """Gỡ trạng thái ngắt của run_id trong live_state.json (bằng tay, sau khi đã xem lại). Trả thông báo."""
    state = load_state(state_path, run_id)
    was = bool(state.get("halted", False))
    state["halted"] = False
    save_state(state_path, run_id, state)
    msg = (f"Đã gỡ kill switch cho run_id={run_id}" if was
           else f"Kill switch của run_id={run_id} vốn không ngắt (không đổi gì)")
    (store or Store(run_id=run_id)).log_event("kill_switch", f"{msg} (--reset-kill-switch)",
                                              level="WARNING" if was else "INFO")
    logger.warning(msg) if was else logger.info(msg)
    return msg


def live_report(store: Optional[Store] = None, run_id: str = RUN_ID_PAPER) -> dict:
    """Báo cáo hiệu quả của LiveBot từ DB (rỗng nếu chưa có vòng nào ghi equity)."""
    from .backtest.report import report_from_store

    return report_from_store(store or Store(run_id=run_id), run_id)


def build_strategies(config: TradingConfig) -> list:
    """Chiến lược theo flag (giống full_policy) + LLM nếu bật --llm và có key."""
    if config.flags["use_regime"]:
        strategies = [RegimeSwitchStrategy()]
    else:
        strategies = [MovingAverageCrossoverStrategy(), RSIStrategy()]
    if config.use_llm:
        from .decision.llm_agent import LLMAgentStrategy, make_client_from_env

        client = make_client_from_env()
        if client is None:
            logger.warning("--llm nhưng thiếu LLM_API_KEY -> chạy tiếp KHÔNG có LLM")
        else:
            strategies.append(LLMAgentStrategy(client=client))
    return strategies


class LiveBot:
    """Các client được truyền vào để test offline bằng client giả; None -> tạo client Alpaca thật."""

    def __init__(self, config: TradingConfig, trading_client=None, data_manager=None,
                 store: Optional[Store] = None, forecaster=None, risk_cfg: Optional[RiskConfig] = None,
                 calibrator="auto", state_path=STATE_PATH, exchange_stop: bool = False):
        """calibrator="auto": đọc/fit từ data/models/calibrator.pkl (nếu use_calibration);
        truyền object/None để dùng thẳng (test).
        exchange_stop (chỉ crypto, mặc định TẮT): đặt thêm 1 lệnh stop_limit SL thật lên sàn, xem TICH-HOP.md."""
        self.config = config
        self.asset = config.asset
        self.crypto = config.asset == "crypto"
        self.benchmark = CRYPTO_BENCHMARK if self.crypto else BENCHMARK
        if self.crypto:  # chỉ cho phép CRYPTO_UNIVERSE (đúng dạng "XXX/USD")
            bad = [s for s in config.symbols if s not in CRYPTO_UNIVERSE]
            if bad:
                raise ValueError(f"mã crypto ngoài whitelist {CRYPTO_UNIVERSE}: {bad}")
        elif exchange_stop:
            raise ValueError("exchange_stop chỉ dùng cho crypto (cổ phiếu đã có bracket)")
        self.exchange_stop = exchange_stop
        self.risk_cfg = risk_cfg or (RiskConfig.for_asset("crypto") if self.crypto else RiskConfig())
        if trading_client is None or data_manager is None:
            from alpaca.trading.client import TradingClient

            # paper=True gắn cứng: KHÔNG BAO GIỜ giao dịch tiền thật
            trading_client = trading_client or TradingClient(config.api_key, config.api_secret, paper=True)
            if data_manager is None and self.crypto:
                data_manager = CryptoDataManager()
            elif data_manager is None:
                from alpaca.data.historical import StockHistoricalDataClient

                from .data.sources import AlpacaDataManager

                data_manager = AlpacaDataManager(StockHistoricalDataClient(config.api_key, config.api_secret))
        self.client = trading_client
        self.data = data_manager
        self.store = store or Store(run_id=run_id_for(self.asset, config.dry_run))
        self.executor = AlpacaExecutor(trading_client, self.store, self.risk_cfg, asset=self.asset)
        self._asset_info_cache: dict = {}  # crypto: min_order_size, min_trade_increment... theo mã

        self.forecaster = forecaster  # None + use_forecast -> tạo Chronos ở vòng đầu
        self._forecaster_failed = False
        self._fc_cache: dict = {}  # (ngày nến cuối, mã) -> Forecast: loop không tính lại trong ngày
        # Bảng forecast tính sẵn chỉ có cổ phiếu -> crypto không có ref_width (không giảm cỡ theo độ bất định)
        self.ref_width = self._ref_width() if config.flags["use_forecast"] and not self.crypto else None

        if calibrator == "auto":
            calibrator = self._load_calibrator() if config.flags["use_calibration"] else None
        self.calibrator = calibrator
        # Ngưỡng của pipeline = 0; ngưỡng thật (min_confidence) chỉ chặn BUY, như full_policy
        self.pipeline = DecisionPipeline(build_strategies(config), calibrator=self.calibrator, min_score=0.0)

        # Kill switch: khôi phục trạng thái từ file
        self.state_path = state_path
        self.state = load_state(state_path, self.store.run_id)
        self.executor.halted = bool(self.state.get("halted", False))
        if self.executor.halted:
            logger.warning("Kill switch ĐANG NGẮT (khôi phục từ live_state.json): chặn lệnh MUA")
        self._reconciled = False

        mode = "DRY-RUN (không gửi lệnh)" if config.dry_run else "PAPER"
        off = ", ".join(config.disabled()) or "không"
        logger.info(f"LiveBot khởi tạo ({mode}), run_id={self.store.run_id}, "
                    f"chiến lược={[s.name for s in self.pipeline.strategies]}, tắt: {off}")
        self.store.log_event("start", f"LiveBot {mode}, symbols={config.symbols}, tắt: {off}",
                             data={"flags": config.flags, "dry_run": config.dry_run})

    def _load_calibrator(self):
        if self.crypto:  # file riêng, fit trên nến crypto ≤ calib_end (prereg: ≤ 2024-12-31)
            cal, note = load_or_fit_calibrator(
                sorted(CRYPTO_UNIVERSE), now_utc().normalize(), False, path=CALIB_PATH_CRYPTO,
                fit_fn=fit_live_calibrator_crypto, calib_end=self.config.calib_end)
            logger.warning(note) if cal is None else logger.info(note)
            self.store.log_event("calibrator", note, level="WARNING" if cal is None else "INFO")
            return cal
        # Có feature forecast khi bật forecast VÀ có bảng forecast tính sẵn (giống full_policy)
        use_fc_feat = self.ref_width is not None
        symbols = sorted(set(self.config.symbols) | set(UNIVERSE_50))
        cal, note = load_or_fit_calibrator(symbols, today_ny(), use_fc_feat, calib_end=self.config.calib_end)
        if cal is None:
            logger.warning(note)
            self.store.log_event("calibrator", note, level="WARNING")
        else:
            logger.info(note)
            self.store.log_event("calibrator", note)
        return cal

    def _ref_width(self) -> Optional[float]:
        """Độ rộng forecast tham chiếu = median (q90 - q10) của bảng dự báo tính sẵn (dữ liệu quá khứ)."""
        table = load_forecasts()
        if table.empty:
            return None
        dates = table.index.get_level_values("date")
        part = table[dates < today_ny()]
        return float((part["q90"] - part["q10"]).median()) if len(part) else None

    def _get_forecaster(self):
        """Tạo ChronosForecaster lần đầu cần dùng; lỗi thì cảnh báo và chạy không forecast."""
        if self.forecaster is None and not self._forecaster_failed:
            try:
                from .data.forecast import ChronosForecaster

                self.forecaster = ChronosForecaster()
            except Exception as e:
                self._forecaster_failed = True
                logger.warning(f"Không tải được Chronos ({e}) -> chạy không forecast")
                self.store.log_event("forecast_error", str(e), level="WARNING")
        return self.forecaster

    # ---------- đọc broker + dữ liệu ----------
    def snapshot(self) -> dict:
        """Tài khoản, vị thế, mã đang có lệnh chờ. Lỗi mạng thì để nổi lên (run_cycle bắt)."""
        account = self.client.get_account()
        norm = normalize_crypto_symbol if self.crypto else (lambda s: s)  # broker có thể trả "BTCUSD"
        positions = {norm(p.symbol): float(p.qty) for p in self.client.get_all_positions()}
        open_orders = self.client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        if self.crypto:  # cùng tài khoản paper có thể đang giữ cổ phiếu: bot crypto chỉ quản mã trong whitelist
            positions = {s: q for s, q in positions.items() if s in CRYPTO_UNIVERSE}
            open_orders = [o for o in open_orders if norm(o.symbol) in CRYPTO_UNIVERSE]
        stop_ids = self._stop_order_ids()  # lệnh stop_limit bảo vệ của chính bot không tính là "lệnh chờ"
        return {"equity": float(account.equity), "cash": float(account.cash),
                "last_equity": float(getattr(account, "last_equity", 0) or 0),  # equity cuối phiên trước
                "long_value": float(getattr(account, "long_market_value", 0) or 0),
                "positions": positions,
                "open_symbols": {norm(o.symbol) for o in open_orders if str(o.id) not in stop_ids}}

    def load_bars(self, symbols: List[str], cutoff: pd.Timestamp) -> Dict[str, pd.DataFrame]:
        """Nến ĐÃ ĐÓNG ≤ cutoff (cache trước, thiếu thì API). Mã lỗi/rỗng bị bỏ, có ghi event."""
        start = cutoff - pd.Timedelta(days=HISTORY_CALENDAR_DAYS)
        out = {}
        for sym in symbols:
            try:
                bars = self.data.get_bars(sym, start, cutoff)
            except Exception as e:
                bars = None
                self._symbol_error(sym, f"lỗi tải nến: {e}")
            if bars is not None and not bars.empty:
                out[sym] = bars[bars.index <= cutoff]  # bỏ nến chưa đóng / tương lai
            elif bars is not None:
                self._symbol_error(sym, "không có nến")
        return out

    def forecasts(self, bars: Dict[str, pd.DataFrame], asof) -> dict:
        """Forecast cho các mã; cache theo (ngày nến cuối, mã) nên loop không tính lại trong ngày."""
        if not self.config.flags["use_forecast"] or self._get_forecaster() is None:
            return {}
        missing = {s: b for s, b in bars.items() if (b.index[-1], s) not in self._fc_cache}
        if missing:
            try:
                for s, fc in self.forecaster.predict_many(missing, asof).items():
                    self._fc_cache[(missing[s].index[-1], s)] = fc
            except Exception as e:
                logger.warning(f"Lỗi forecast ({e}) -> vòng này các mã mới không có forecast")
                self.store.log_event("forecast_error", str(e), level="WARNING")
        return {s: self._fc_cache.get((b.index[-1], s)) for s, b in bars.items()}

    def _symbol_error(self, sym: str, msg: str) -> None:
        logger.error(f"{sym}: {msg}")
        self.store.log_event("symbol_error", f"{sym}: {msg}", level="ERROR")

    # ---------- quyết định ----------
    def decide_symbol(self, sym: str, bars: pd.DataFrame, spy: pd.DataFrame, forecast,
                      snap: dict) -> Optional[OrderIntent]:
        """MarketState → Signal → OrderIntent cho 1 mã. Ghi mọi tín hiệu (kể cả bị veto) vào DB."""
        asof = bars.index[-1]
        state = build_market_state(sym, asof, bars.iloc[-FEATURE_WINDOW:], spy, forecast=forecast)
        sig = self.pipeline.decide(state)
        for s in self.pipeline.last_signals:
            self.store.log_signal(s, regime=state.regime)
        if sig is None:
            return None
        if sig.action == "BUY" and sig.score < self.config.min_confidence:
            logger.info(f"{sym}: BUY score {sig.score:.2f} < {self.config.min_confidence} -> bỏ")
            return None
        returns = None
        held = [s for s, q in snap["positions"].items() if q > 0 and s != sym]
        if self.config.flags["use_corr_limit"] and sig.action == "BUY" and held:
            returns = pd.DataFrame({s: b["close"].pct_change() for s, b in snap["bars"].items()
                                    if s in held + [sym]}).iloc[-self.risk_cfg.corr_window:]
        intent = make_order_intent(sig, state, snap["equity"], snap["cash"], snap["positions"],
                                   snap["open_symbols"], self.risk_cfg, returns=returns, ref_width=self.ref_width)
        if intent is None or intent.side == "sell":
            return intent
        price = float(bars["close"].iloc[-1])
        if not self.config.flags["use_atr_sizing"]:
            intent.qty = legacy_position_size(snap["cash"], price,
                                              qty_decimals=self.risk_cfg.qty_decimals if self.crypto else None)
        if not self.config.flags["use_bracket"]:
            intent.stop_loss = intent.take_profit = None
        if self.crypto:  # làm tròn theo sàn; quá nhỏ thì bỏ
            intent.qty = self.fit_crypto_qty(sym, intent.qty, price)
            if intent.qty <= 0:
                logger.info(f"{sym}: qty sau làm tròn theo sàn = 0 (< min_order_size) -> bỏ")
                return None
        snap["cash"] -= intent.qty * price  # tính luôn cho mã sau
        snap["positions"][sym] = intent.qty
        return intent

    def send(self, intent: OrderIntent) -> None:
        if self.crypto:
            return self._send_crypto(intent)
        if self.config.dry_run:
            if intent.side == "buy" and self.executor.halted:  # giống executor.submit khi bị ngắt
                self.store.log_order(intent, status="blocked")
                self.store.log_event("order_blocked", f"Kill switch đang ngắt: chặn MUA {intent.symbol}",
                                     level="WARNING")
                return
            self.store.log_order(intent, status="dry_run")  # KHÔNG gọi broker
            logger.info(f"[DRY-RUN] {intent.side} {intent.qty} {intent.symbol} (không gửi)")
        else:
            self.executor.submit(intent)

    # ---------- crypto: lệnh, làm tròn, SL/TP tự quản ----------
    def _save_state(self) -> None:
        save_state(self.state_path, self.store.run_id, self.state)

    def reload_sltp(self) -> None:
        """Crypto: đầu vòng đọc lại pending/sltp từ file (web có thể vừa đặt/sửa SL/TP bằng box Long Position).
        Bot luôn ghi file ngay sau khi tự đổi 2 mục này nên không có web sửa thì đọc lại = y hệt bộ nhớ."""
        try:
            disk = load_state(self.state_path, self.store.run_id)
        except Exception as e:  # file hỏng/đang ghi: giữ bản trong bộ nhớ
            logger.warning(f"Không đọc lại được SL/TP từ {self.state_path}: {e}")
            return
        for k in ("pending", "sltp"):
            if k in disk:
                self.state[k] = disk[k]

    def _stop_order_ids(self) -> set:
        return {str(v["stop_order_id"]) for v in self.state.get("sltp", {}).values() if v.get("stop_order_id")}

    def asset_info(self, sym: str) -> dict:
        """min_order_size, min_trade_increment, price_increment của mã (Assets API); lỗi -> mặc định an toàn."""
        if sym not in self._asset_info_cache:
            info = {"min_order_size": 0.0, "min_trade_increment": 10.0 ** -self.risk_cfg.qty_decimals,
                    "price_increment": 0.01}
            try:
                a = self.client.get_asset(sym)
                for k in info:
                    v = getattr(a, k, None)
                    if v:
                        info[k] = float(v)
            except Exception as e:
                logger.warning(f"{sym}: không lấy được thông tin asset ({e}) -> dùng mặc định")
            self._asset_info_cache[sym] = info
        return self._asset_info_cache[sym]

    def fit_crypto_qty(self, sym: str, qty: float, price: float) -> float:
        """Làm tròn XUỐNG theo min_trade_increment, chặn trần 200k$/lệnh; < min_order_size -> 0."""
        info = self.asset_info(sym)
        qty = min(qty, CRYPTO_MAX_NOTIONAL / price) if price > 0 else qty
        inc = info["min_trade_increment"]
        qty = round(math.floor(round(qty / inc, 6)) * inc, 9)  # round(.., 6) chống sai số số thực
        return qty if qty > 0 and qty >= info["min_order_size"] else 0.0

    def _send_crypto(self, intent: OrderIntent) -> None:
        """Gửi lệnh crypto. SL/TP KHÔNG gắn vào lệnh: cất vào state (pending) tới khi lệnh mua khớp."""
        levels = None
        if intent.side == "buy" and intent.stop_loss and intent.take_profit:
            levels = {"stop_loss": intent.stop_loss, "take_profit": intent.take_profit, "asof": str(intent.asof)}
        if self.config.dry_run:  # log lệnh gốc (kèm SL/TP dự kiến) vào DB, KHÔNG gọi broker
            if intent.side == "buy" and self.executor.halted:
                self.store.log_order(intent, status="blocked")
                self.store.log_event("order_blocked", f"Kill switch đang ngắt: chặn MUA {intent.symbol}",
                                     level="WARNING")
                return
            self.store.log_order(intent, status="dry_run")
            logger.info(f"[DRY-RUN] {intent.side} {intent.qty} {intent.symbol} (không gửi)")
            return
        if intent.side == "sell":
            self._cancel_exchange_stop(intent.symbol)  # stop_limit trên sàn đang giữ qty -> huỷ trước khi bán
        if levels:
            intent = dataclasses.replace(
                intent, stop_loss=None, take_profit=None,
                reason=f"{intent.reason} | SL {levels['stop_loss']} TP {levels['take_profit']} (bot tự quản)")
        order_id = self.executor.submit(intent)
        if order_id is None:
            return
        if levels:
            self.state.setdefault("pending", {})[intent.symbol] = {**levels, "order_id": order_id}
        if intent.side == "sell":
            self.state.get("sltp", {}).pop(intent.symbol, None)
        self._save_state()

    def _cancel_exchange_stop(self, sym: str) -> None:
        oid = self.state.get("sltp", {}).get(sym, {}).get("stop_order_id")
        if not oid:
            return
        try:
            self.client.cancel_order_by_id(str(oid))
        except Exception as e:  # vd stop đã khớp/đã huỷ
            self.store.log_event("cancel_error", f"{sym} stop {oid}: {e}", level="WARNING")
        self.state["sltp"][sym].pop("stop_order_id", None)

    def promote_pending(self, snap: dict) -> None:
        """Lệnh mua đã khớp (có vị thế trên broker) -> SL/TP chuyển từ pending sang sltp (dùng để tự quản)."""
        pending = self.state.get("pending", {})
        for sym, p in list(pending.items()):
            qty = snap["positions"].get(sym, 0)
            if qty > 0:
                self.state.setdefault("sltp", {})[sym] = {"stop_loss": p["stop_loss"],
                                                          "take_profit": p["take_profit"], "asof": p.get("asof")}
                del pending[sym]
                self.store.log_event("sltp_armed", f"{sym}: SL {p['stop_loss']} TP {p['take_profit']} (qty {qty})")
                if self.exchange_stop:
                    self._place_exchange_stop(sym, qty, p["stop_loss"])
            elif sym not in snap["open_symbols"] and self._order_dead(p.get("order_id")):
                del pending[sym]  # lệnh mua bị huỷ/từ chối, không có vị thế: bỏ SL/TP đã cất
                self.store.log_event("sltp_dropped", f"{sym}: lệnh mua {p.get('order_id')} không khớp", level="WARNING")
        self._save_state()

    def _order_dead(self, order_id) -> bool:
        """Lệnh đã huỷ/hết hạn/bị từ chối mà không khớp phần nào. Không chắc (lỗi mạng) -> False (giữ pending)."""
        try:
            o = self.client.get_order_by_id(str(order_id))
        except Exception:
            return False
        status = str(getattr(o.status, "value", o.status)).lower()
        return status in ("canceled", "expired", "rejected") and not float(getattr(o, "filled_qty", 0) or 0)

    def _place_exchange_stop(self, sym: str, qty: float, stop: float) -> None:
        """Tuỳ chọn: stop_limit bán trên sàn (stop = SL, limit thấp hơn 0.5% để dễ khớp)."""
        if self.config.dry_run:
            return
        inc = self.asset_info(sym)["price_increment"]
        stop_p = round(round(stop / inc) * inc, 9)
        limit_p = round(math.floor(stop * 0.995 / inc) * inc, 9)
        oid = self.executor.submit_protective_stop(sym, qty, stop_p, limit_p)
        if oid:
            self.state["sltp"][sym]["stop_order_id"] = oid

    def check_sl_tp(self, snap: dict) -> set:
        """Mỗi vòng: giá mới nhất ≤ SL hoặc ≥ TP -> bán market TOÀN BỘ qty THỰC trên broker.
        Trả tập mã đã gửi lệnh bán (vòng này không ra quyết định thêm cho các mã đó)."""
        sold = set()
        for sym, lv in list(self.state.get("sltp", {}).items()):
            try:
                qty = snap["positions"].get(sym, 0)
                if qty <= 0:  # vị thế đã đóng (stop trên sàn khớp, bán tay...) -> dọn
                    del self.state["sltp"][sym]
                    self.store.log_event("sltp_cleared", f"{sym}: không còn vị thế, bỏ SL/TP")
                    continue
                if sym in snap["open_symbols"]:
                    continue  # đang có lệnh chờ khác của mã này
                if trigger_active(lv):
                    continue  # web server (SLTPMonitor) đang bán mã này
                price = self.data.get_latest_price(sym)
                if price is None:
                    logger.warning(f"{sym}: không có giá mới nhất, bỏ qua kiểm tra SL/TP vòng này")
                    continue
                hit = ("sl_hit" if lv.get("stop_loss") and price <= lv["stop_loss"]
                       else "tp_hit" if lv.get("take_profit") and price >= lv["take_profit"] else None)
                if hit is None:
                    continue
                if not self.config.dry_run and not claim_crypto_sltp(sym, "livebot", self.state_path,
                                                                     self.store.run_id, price):
                    logger.info(f"{sym}: web server đang bán theo SL/TP, bot bỏ qua")
                    continue
                msg = f"{sym}: giá {price} {'≤ SL' if hit == 'sl_hit' else '≥ TP'} -> bán {qty} (toàn bộ vị thế thực)"
                self.store.log_event(hit, msg, level="WARNING", data={"price": price, **lv, "qty": qty})
                logger.warning(msg)
                self.send(OrderIntent(sym, "sell", qty, reason=f"{hit} @ {price}"))
                sold.add(sym)
            except Exception as e:
                self._symbol_error(sym, f"lỗi kiểm tra SL/TP: {e}")
        self._save_state()
        return sold

    # ---------- vòng ----------
    def run_cycle(self) -> None:
        logger.info("=== Bắt đầu vòng (engine full) ===")
        sold: set = set()
        try:
            if self.crypto:  # 24/7: không kiểm tra giờ thị trường
                cutoff = last_closed_day_crypto(now_utc())
            else:
                is_open = self.client.get_clock().is_open
                if not is_open:
                    logger.warning("Thị trường đang ĐÓNG: lệnh sẽ xếp hàng tới phiên sau")
            if not self._reconciled:
                self.reconcile_at_start()
            snap = self.snapshot()
            if self.crypto:  # SL/TP tự quản chạy TRƯỚC khi tải nến: lỗi dữ liệu không được chặn việc cắt lỗ
                self.reload_sltp()
                if not self.config.dry_run:
                    self.promote_pending(snap)
                sold = self.check_sl_tp(snap)
            else:
                cutoff = last_closed_day(is_open, now_ny())
            logger.info(f"Chỉ dùng nến ĐÃ ĐÓNG ≤ {cutoff.date()} (bỏ nến phiên chưa kết thúc, như backtest)")
            bm = self.benchmark
            all_bars = self.load_bars(list(self.config.symbols) + [bm] * (bm not in self.config.symbols), cutoff)
            # Mã đang nắm ngoài --symbols: chỉ tải nến để kiểm tra tương quan, KHÔNG ra quyết định
            extra = [s for s, q in snap["positions"].items() if q > 0 and s not in all_bars
                     and s not in self.config.symbols]
            extra_bars = self.load_bars(extra, cutoff) if extra and self.config.flags["use_corr_limit"] else {}
        except Exception as e:  # lỗi mạng đầu vòng: bỏ vòng, không sập
            logger.error(f"Lỗi đầu vòng, bỏ qua vòng này: {e}")
            self.store.log_event("cycle_error", str(e), level="ERROR")
            return
        # Crypto: BTC vừa là benchmark vừa là mã giao dịch -> giữ lại trong all_bars
        spy = all_bars.get(bm) if self.crypto and bm in self.config.symbols else all_bars.pop(bm, None)
        if spy is None:  # thiếu benchmark -> regime "unknown"
            spy = pd.DataFrame(columns=["close"], index=pd.DatetimeIndex([], name="date"))
        snap["bars"] = {**extra_bars, **all_bars}
        asof = max(b.index[-1] for b in all_bars.values()) if all_bars else cutoff
        fcs = self.forecasts(all_bars, asof)

        sells, buys = [], []
        acted = self.state.setdefault("acted", {}) if self.crypto else {}
        for sym, bars in all_bars.items():
            if bars.index[-1] != asof:  # như full_policy: mã không có phiên mới nhất thì bỏ
                logger.warning(f"{sym}: nến cuối {bars.index[-1].date()} ≠ {asof.date()}, bỏ qua")
                continue
            if sym in sold:
                continue  # vừa bán theo SL/TP
            if self.crypto and acted.get(sym) == str(asof.date()):
                logger.info(f"{sym}: đã ra lệnh cho nến {asof.date()}, vòng sau mới xét lại (tránh vào lại ngay sau SL/TP)")
                continue
            try:
                intent = self.decide_symbol(sym, bars, spy, fcs.get(sym), snap)
            except Exception as e:
                self._symbol_error(sym, f"lỗi quyết định: {e}")
                continue
            if intent is not None:
                (sells if intent.side == "sell" else buys).append(intent)
        for intent in sells + buys:  # bán trước (giảm rủi ro), mua sau
            try:
                self.send(intent)
                if self.crypto:
                    acted[intent.symbol] = str(asof.date())
            except Exception as e:
                self._symbol_error(intent.symbol, f"lỗi gửi lệnh: {e}")
        if self.crypto:
            if buys and not self.config.dry_run:
                try:  # lệnh mua market thường khớp ngay -> gắn SL/TP luôn, không đợi vòng sau
                    self.promote_pending(self.snapshot())
                except Exception as e:
                    logger.warning(f"Chưa gắn được SL/TP ngay ({e}); vòng sau thử lại")
            self._save_state()
        self.sync_fills()
        account = self.log_equity()
        if account is not None:
            self.check_kill_switch(account)
        logger.info(f"=== Kết thúc vòng: {len(sells)} bán, {len(buys)} mua ===")

    run_trading_cycle = run_cycle  # cùng tên với TradingBot (legacy) để CLI gọi chung

    # ---------- sau vòng: đối soát, fill, equity, kill switch ----------
    def reconcile_at_start(self) -> None:
        """Đối soát broker vs DB một lần khi khởi động (chỉ ĐỌC broker).
        Dry-run bỏ qua: run live-dry không có lệnh thật nên mọi vị thế đều 'lệch' vô nghĩa."""
        if self.config.dry_run:
            logger.info("Dry-run: bỏ qua đối soát (không có lệnh thật để so)")
            self._reconciled = True
            return
        diffs = self.executor.reconcile()
        self._reconciled = True  # lỗi mạng -> vòng sau thử lại
        if diffs:
            logger.warning(f"Đối soát: {len(diffs)} chênh lệch broker/DB: {diffs}")

    def sync_fills(self) -> int:
        """Kéo lệnh đã khớp từ broker → bảng fills (không trùng order_id). Chỉ lệnh do bot này gửi
        (id có trong bảng orders) và lệnh con stop/TP của chúng. Dry-run không có lệnh thật -> bỏ."""
        if self.config.dry_run:
            return 0
        q = self.store.conn.execute
        run = self.store.run_id
        ours = {r[0] for r in q("SELECT broker_order_id FROM orders WHERE run_id=? AND broker_order_id!=''", (run,))}
        if not ours:
            return 0
        done = {r[0] for r in q("SELECT order_id FROM fills WHERE run_id=?", (run,))}
        try:
            closed = self.client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.CLOSED,
                                                                    nested=True, limit=500))
        except Exception as e:
            logger.error(f"Không lấy được lệnh đã đóng: {e}")
            return 0
        n = 0
        for parent in closed:
            if str(parent.id) not in ours:
                continue
            for o in [parent] + list(getattr(parent, "legs", None) or []):
                qty = float(getattr(o, "filled_qty", 0) or 0)
                if str(o.id) in done or qty <= 0 or o.filled_avg_price is None:
                    continue
                side = str(getattr(o.side, "value", o.side)).lower()
                self.store.log_fill(Fill(str(o.id), o.symbol, side, qty, float(o.filled_avg_price),
                                         pd.Timestamp(o.filled_at).to_pydatetime()))
                done.add(str(o.id))
                n += 1
        if n:
            logger.info(f"Đã ghi {n} fill mới")
        return n

    def check_kill_switch(self, account) -> bool:
        """day_start = equity cuối phiên trước (account.last_equity); peak = max(file, DB, hiện tại, day_start).
        Thật: executor.check_kill_switch (huỷ lệnh MUA chờ). Dry-run: chỉ đánh dấu ngắt, KHÔNG huỷ gì."""
        equity = float(account.equity)
        day_start = float(getattr(account, "last_equity", 0) or 0) or equity
        row = self.store.conn.execute("SELECT MAX(equity) FROM equity WHERE run_id=?",
                                      (self.store.run_id,)).fetchone()
        # last_equity cũng là equity thật đã đạt -> tính vào đỉnh
        peak = max(float(self.state.get("peak_equity", 0)), float(row[0] or 0), equity, day_start)
        if self.config.dry_run:
            tripped, why = self.executor.kill_switch.check(equity, day_start, peak)
            if tripped and not self.executor.halted:
                self.executor.halted = True
                logger.critical(f"KILL SWITCH (dry-run, không huỷ lệnh): {why}")
                self.store.log_event("kill_switch", f"NGẮT (dry-run, không huỷ lệnh): {why}",
                                     level="CRITICAL", data={"equity": equity, "day_start": day_start,
                                                             "peak": peak})
        else:
            self.executor.check_kill_switch(equity, day_start, peak)
        day = now_utc().date().isoformat() if self.crypto else today_ny().date().isoformat()
        # gộp vào state cũ để giữ pending/sltp/acted của crypto
        self.state = {**self.state, "halted": self.executor.halted, "day": day,
                      "day_start_equity": day_start, "peak_equity": peak}
        save_state(self.state_path, self.store.run_id, self.state)
        return self.executor.halted

    def log_equity(self):
        """Ghi equity; trả account (để kiểm tra kill switch) hoặc None nếu lỗi."""
        try:
            a = self.client.get_account()
            equity = float(a.equity)
            long_value = float(getattr(a, "long_market_value", 0) or 0)
            self.store.log_equity(pd.Timestamp.now().floor("s").to_pydatetime(), equity, float(a.cash),
                                  long_value / equity if equity else 0.0)
            return a
        except Exception as e:
            logger.error(f"Không ghi được equity: {e}")
            return None

    def run_continuous(self, interval_minutes: int = 30) -> None:
        """Mỗi `interval_minutes` phút một vòng khi thị trường mở (crypto: luôn chạy, kể cả cuối tuần).
        Ctrl+C để dừng."""
        try:
            while True:
                try:
                    if self.crypto or self.client.get_clock().is_open:
                        self.run_cycle()
                    else:
                        logger.info("Thị trường đang đóng cửa, chờ...")
                except Exception as e:  # vd mất mạng khi hỏi giờ: chờ vòng sau
                    logger.error(f"Lỗi vòng loop: {e}")
                    self.store.log_event("cycle_error", str(e), level="ERROR")
                time.sleep(interval_minutes * 60)
        except KeyboardInterrupt:
            logger.info("Đã dừng bot.")
