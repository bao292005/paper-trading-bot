"""Hàm THUẦN cho giao diện (KHÔNG import streamlit) nên test được bằng pytest.

Phần 1 (file này): dữ liệu + P1 (MarketState).
  Luồng: load_market_data -> get_forecasts -> build_state -> (P2, P3 ở phần sau).
Chống rò rỉ: mọi hàm cắt nến <= asof. Xem MarketState của ngày bất kỳ thì được; còn FIT thứ gì
(calibrator) chỉ dùng dữ liệu tới 2024-12-31 (xem get_calibrator).
"""

import json
import logging
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..config import BENCHMARK, CRYPTO_UNIVERSE, ENGINES, FEATURE_FLAGS, SHORT_FLAG_NAMES, UNIVERSE_50
from ..contracts import Forecast, MarketState, NewsItem, OrderIntent, Signal
from ..data.cache import load_bars
from ..data.forecast import get_forecast
from ..data.market_state import build_market_state
from ..decision.calibration import FEATURE_WINDOW
from ..decision.pipeline import DecisionPipeline
from ..decision.regime_switch import RegimeSwitchStrategy
from ..risk import RiskConfig, correlation_ok, legacy_position_size, make_order_intent
from ..strategies import MovingAverageCrossoverStrategy, RSIStrategy

HOLDOUT_START = pd.Timestamp("2025-01-01")  # từ ngày này là dữ liệu holdout (chưa được dùng để fit)
MIN_BUY_SCORE = 0.5  # ngưỡng score, CHỈ áp cho BUY (giống LiveBot / full_policy)
DEFAULT_UI_SYMBOLS = ["AAPL", "NVDA", "JPM"]
MA_WINDOWS = (10, 30, 200)
HORIZON = 5  # số phiên dự báo


FLAG_LABELS = {
    "use_regime": "Regime switch (đổi chiến lược theo bối cảnh thị trường)",
    "use_calibration": "Calibration (hiệu chỉnh xác suất)",
    "use_forecast": "Forecast (veto + giảm cỡ lệnh khi bất định)",
    "use_atr_sizing": "Cỡ lệnh theo ATR",
    "use_bracket": "Bracket (stop-loss / take-profit)",
    "use_corr_limit": "Giới hạn tương quan",
}


def load_forecast_table() -> pd.DataFrame:
    """Bảng forecast tính sẵn (data/forecasts/chronos_h5.parquet); chưa có -> bảng rỗng."""
    from ..data.forecast import load_forecasts

    return load_forecasts()


def is_holdout(asof) -> bool:
    """True nếu asof nằm trong giai đoạn holdout (chỉ để cảnh báo trên UI)."""
    return pd.Timestamp(asof) >= HOLDOUT_START


def flags_from_selection(enabled: Iterable[str]) -> Dict[str, bool]:
    """Danh sách cờ được tick -> dict đủ 6 cờ (cùng tên với config.FEATURE_FLAGS)."""
    enabled = set(enabled)
    unknown = enabled - set(FEATURE_FLAGS)
    if unknown:
        raise ValueError(f"cờ lạ: {sorted(unknown)} (hợp lệ: {', '.join(FEATURE_FLAGS)})")
    return {f: f in enabled for f in FEATURE_FLAGS}


# ---------------------------- Dữ liệu ----------------------------

def load_market_data(symbols: Iterable[str], bars_dir=None) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame, List[str]]:
    """Đọc cache: (nến từng mã, nến SPY, danh sách mã thiếu cache). Mã rỗng bị bỏ khỏi dict."""
    bars, missing = {}, []
    for s in symbols:
        df = load_bars(s, bars_dir=bars_dir)
        if df.empty:
            missing.append(s)
        else:
            bars[s] = df
    return bars, load_bars(BENCHMARK, bars_dir=bars_dir), missing


def available_dates(spy: pd.DataFrame) -> pd.DatetimeIndex:
    """Các ngày có phiên trong cache (lấy theo SPY)."""
    return spy.index


def recent_dates(index: pd.DatetimeIndex, n: int, end=None) -> pd.DatetimeIndex:
    """n phiên cuối cùng <= end (mặc định: phiên cuối cache)."""
    if end is not None:
        index = index[index <= pd.Timestamp(end)]
    return index[-n:]


def cut_upto(bars_by_symbol: Dict[str, pd.DataFrame], asof) -> Dict[str, pd.DataFrame]:
    asof = pd.Timestamp(asof)
    return {s: b[b.index <= asof] for s, b in bars_by_symbol.items()}


def price_with_ma(bars: pd.DataFrame, asof, days: int = 252) -> pd.DataFrame:
    """OHLC `days` phiên cuối <= asof kèm cột ma10/ma30/ma200 (MA tính trên TOÀN BỘ lịch sử <= asof,
    nên đầu đường MA200 không bị khuyết do cắt)."""
    cut = bars[bars.index <= pd.Timestamp(asof)].copy()
    for w in MA_WINDOWS:
        cut[f"ma{w}"] = cut["close"].rolling(w).mean()
    return cut.iloc[-days:]


def ref_width_at(table: pd.DataFrame, asof) -> Optional[float]:
    """Độ rộng forecast tham chiếu = median(q90 - q10) của bảng dự báo tính sẵn, chỉ ngày <= asof."""
    if table is None or table.empty:
        return None
    part = table[table.index.get_level_values("date") <= pd.Timestamp(asof)]
    return float((part["q90"] - part["q10"]).median()) if len(part) else None


# ---------------------------- Forecast + tin ----------------------------

def get_forecasts(bars: Dict[str, pd.DataFrame], asof, flags: Dict[str, bool],
                  table: Optional[pd.DataFrame] = None, forecaster=None) -> Dict[str, Forecast]:
    """Forecast 5 phiên cho từng mã. Ưu tiên bảng tính sẵn (đúng ngày asof, nhanh);
    mã thiếu thì hỏi `forecaster` (vd ChronosForecaster) nếu được truyền. Cờ use_forecast tắt -> {}."""
    if not flags.get("use_forecast", True):
        return {}
    out = {}
    if table is not None and not table.empty:
        for s in bars:
            fc = get_forecast(table, s, asof)
            if fc is not None:
                out[s] = fc
    missing = {s: b for s, b in bars.items() if s not in out}
    if missing and forecaster is not None:
        out.update(forecaster.predict_many(missing, asof, horizon=HORIZON))  # tự cắt <= asof
    return out


def fetch_news(symbols: List[str], asof, source=None) -> Tuple[List[NewsItem], Optional[str]]:
    """Tin 7 ngày trước asof từ Alpaca News. Trả (tin, lỗi); lỗi/thiếu key thì tin rỗng + thông báo.
    `source` (có get_news(symbols, start, end)) dùng để test; None -> tạo NewsSource thật."""
    try:
        end = pd.Timestamp(asof) + timedelta(hours=23)
        if source is None:
            from alpaca.data.historical import NewsClient

            from ..config import TradingConfig
            from ..data.sources import NewsSource

            cfg = TradingConfig.from_env()
            source = NewsSource(NewsClient(cfg.api_key, cfg.api_secret))
        return source.get_news(symbols, end - timedelta(days=7), end.to_pydatetime()), None
    except BaseException as e:  # thiếu key làm TradingConfig.from_env raise SystemExit
        return [], f"Không lấy được tin: {e}"


def build_state(symbol: str, asof, bars: Dict[str, pd.DataFrame], spy: pd.DataFrame,
                forecasts: Dict[str, Forecast], news: Optional[List[NewsItem]] = None) -> MarketState:
    """MarketState của 1 mã tại asof, giống LiveBot.decide_symbol: nến cắt <= asof, lấy FEATURE_WINDOW cuối."""
    cut = bars[symbol][bars[symbol].index <= pd.Timestamp(asof)].iloc[-FEATURE_WINDOW:]
    mine = [n for n in (news or []) if symbol in n.symbols]
    return build_market_state(symbol, asof, cut, spy, forecast=forecasts.get(symbol), news=mine)


def state_summary(state: MarketState) -> dict:
    """Vài số đáng chú ý để hiện trên UI (nến cuối, regime, forecast)."""
    last = state.bars.iloc[-1]
    out = {"asof": pd.Timestamp(state.asof).date().isoformat(), "regime": state.regime,
           "open": float(last.open), "high": float(last.high), "low": float(last.low),
           "close": float(last.close), "volume": float(last.volume), "n_bars": len(state.bars)}
    if state.forecast:
        q = state.forecast.quantiles
        out.update(forecast_model=state.forecast.model, q10=q[0.1], q50=q[0.5], q90=q[0.9],
                   width=state.forecast.width)
    return out


def fan_chart_frame(state: MarketState, horizon: int = HORIZON) -> Optional[pd.DataFrame]:
    """Bảng giá dự báo q10/q50/q90 cho `horizon` phiên tới (dùng cho fan chart).
    Forecast chỉ có lợi suất log TÍCH LUỸ ở ngày cuối, nên nội suy tuyến tính theo ngày
    (chỉ để minh hoạ: đường cong thật nằm giữa). Không có forecast -> None."""
    if state.forecast is None:
        return None
    last_close = float(state.bars["close"].iloc[-1])
    last_day = state.bars.index[-1]
    days = pd.bdate_range(last_day, periods=horizon + 1)  # ngày 0 = asof
    rows = {"date": days}
    for q, name in ((0.1, "q10"), (0.5, "q50"), (0.9, "q90")):
        ret = state.forecast.quantiles[q]
        rows[name] = [last_close * float(np.exp(ret * i / horizon)) for i in range(horizon + 1)]
    return pd.DataFrame(rows).set_index("date")


# ---------------------------- P2: quyết định ----------------------------

def get_calibrator(symbols: Iterable[str], flags: Dict[str, bool], today=None, path=None, fit_fn=None):
    """(calibrator hoặc None, ghi chú). Dùng lại hàm của LiveBot: file còn mới thì nạp, không thì fit.
    LUÔN fit trên dữ liệu tới 2024-12-31 (calib_end mặc định; hàm này cố ý KHÔNG cho đổi) -> không chạm holdout.
    Lưu ý: nếu phải fit lại thì ghi file data/models/calibrator.pkl, giống bot thật."""
    if not flags.get("use_calibration", True):
        return None, "tắt thành phần calibration"
    from ..data.forecast import load_forecasts
    from ..live import load_or_fit_calibrator

    use_fc_feat = bool(flags.get("use_forecast", True)) and not load_forecasts().empty
    kw = {}
    if path is not None:
        kw["path"] = path
    if fit_fn is not None:
        kw["fit_fn"] = fit_fn
    today = pd.Timestamp(today) if today is not None else pd.Timestamp.today().normalize()
    return load_or_fit_calibrator(sorted(set(symbols) | set(UNIVERSE_50)), today, use_fc_feat, **kw)


def build_pipeline(flags: Dict[str, bool], calibrator=None) -> DecisionPipeline:
    """Giống LiveBot: use_regime -> RegimeSwitch, không thì MA + RSI. Ngưỡng pipeline = 0
    (ngưỡng thật chỉ áp cho BUY, xem decide). Không có LLM trong UI."""
    strategies = [RegimeSwitchStrategy()] if flags.get("use_regime", True) else \
        [MovingAverageCrossoverStrategy(), RSIStrategy()]
    cal = calibrator if flags.get("use_calibration", True) else None
    return DecisionPipeline(strategies, calibrator=cal, min_score=0.0)


@dataclass
class Decision:
    signals: list  # mọi tín hiệu sau veto + calibrate (kể cả bị veto)
    note: str  # ghi chú gộp của pipeline
    final: Optional[Signal]  # Signal cuối sau ngưỡng (None = không có)
    verdict: str  # câu giải thích cho người mới


def signal_rows(signals: list) -> pd.DataFrame:
    """Bảng mọi tín hiệu: strategy, action, confidence, prob, veto, veto_reason, reason."""
    cols = ["strategy", "action", "confidence", "prob", "veto", "veto_reason", "reason"]
    return pd.DataFrame([{c: getattr(s, c, None) for c in cols} for s in signals], columns=cols)


def decide(pipe: DecisionPipeline, state: MarketState, min_buy: float = MIN_BUY_SCORE) -> Decision:
    """Chạy pipeline cho 1 mã rồi áp ngưỡng giống LiveBot: min_buy CHỈ chặn BUY, SELL luôn qua."""
    final = pipe.decide(state)
    sigs, note = list(pipe.last_signals), pipe.last_note
    if not sigs:
        return Decision(sigs, note, None, "Không có Signal: không chiến lược nào lên tiếng (ngày thường).")
    if final is None:
        return Decision(sigs, note, None, f"Không có Signal: {note}")
    if final.action == "BUY" and final.score < min_buy:
        return Decision(sigs, note, None,
                        f"Không có Signal: BUY có score {final.score:.2f} < ngưỡng {min_buy} "
                        f"(ngưỡng chỉ áp cho BUY, SELL thì không).")
    return Decision(sigs, note, final,
                    f"Signal {final.action} {state.symbol} @ {final.price:.2f}, score {final.score:.2f}, "
                    f"chiến lược {final.strategy}.")


# ---------------------------- P3: lệnh dự kiến (MÔ PHỎNG, không gửi) ----------------------------

@dataclass
class OrderPlan:
    intent: Optional[OrderIntent]
    rejected: str = ""  # lý do không ra lệnh
    corr_ok: Optional[bool] = None  # None = không cần kiểm tra
    corr_msg: str = ""
    price: float = 0.0
    notional: float = 0.0  # qty * giá
    pct_equity: float = 0.0  # notional / vốn
    risk_amount: Optional[float] = None  # tiền mất nếu chạm stop-loss


class _Capture(logging.Handler):
    """Hứng log của risk._skip để lấy lý do từ chối (make_order_intent chỉ log chứ không trả về)."""

    def __init__(self):
        super().__init__()
        self.messages: List[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _make_intent_with_reason(*args, **kwargs) -> Tuple[Optional[OrderIntent], str]:
    logger = logging.getLogger("paper_bot")
    cap, old_level = _Capture(), logger.level
    logger.addHandler(cap)
    if logger.getEffectiveLevel() > logging.INFO:
        logger.setLevel(logging.INFO)
    try:
        intent = make_order_intent(*args, **kwargs)
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)
    why = ""
    for m in cap.messages:
        if "bỏ qua lệnh - " in m:
            why = m.split("bỏ qua lệnh - ", 1)[1]
    return intent, why


def plan_order(signal: Optional[Signal], state: MarketState, flags: Dict[str, bool], equity: float,
               cash: float, positions: Dict[str, float], all_bars: Dict[str, pd.DataFrame],
               ref_width: Optional[float] = None, cfg: Optional[RiskConfig] = None) -> OrderPlan:
    """Signal -> lệnh dự kiến, đúng các bước của LiveBot.decide_symbol (kể cả cờ legacy sizing/bracket/corr).
    positions = vị thế GIẢ ĐỊNH {mã: số cổ}; all_bars = nến các mã (kể cả mã đang nắm) để tính tương quan.
    Chỉ tính toán, KHÔNG gửi lệnh."""
    if signal is None:
        return OrderPlan(None, "Không có Signal nên không có lệnh.")
    cfg = cfg or RiskConfig()
    sym, price = state.symbol, float(state.bars["close"].iloc[-1])
    returns, corr_ok, corr_msg = None, None, ""
    held = [s for s, q in positions.items() if q > 0 and s != sym]
    if flags.get("use_corr_limit", True) and signal.action == "BUY" and held:
        asof = pd.Timestamp(state.asof)
        cols = {s: b[b.index <= asof]["close"].pct_change() for s, b in all_bars.items() if s in held + [sym]}
        returns = pd.DataFrame(cols).iloc[-cfg.corr_window:]
        corr_ok, corr_msg = correlation_ok(sym, held, returns, cfg)
    intent, why = _make_intent_with_reason(signal, state, equity, cash, positions, set(), cfg,
                                           returns=returns, ref_width=ref_width if flags.get("use_forecast", True) else None)
    if intent is None:
        return OrderPlan(None, why or "Không rõ lý do (xem log).", corr_ok, corr_msg, price)
    if intent.side == "buy":
        if not flags.get("use_atr_sizing", True):
            intent.qty = legacy_position_size(cash, price)
        if not flags.get("use_bracket", True):
            intent.stop_loss = intent.take_profit = None
    notional = intent.qty * price
    risk = intent.qty * (price - intent.stop_loss) if intent.side == "buy" and intent.stop_loss else None
    return OrderPlan(intent, "", corr_ok, corr_msg, price, notional, notional / equity if equity else 0.0, risk)


def scan_sessions(symbols: List[str], dates: Iterable, bars: Dict[str, pd.DataFrame], spy: pd.DataFrame,
                  flags: Dict[str, bool], pipe: DecisionPipeline, table: Optional[pd.DataFrame] = None,
                  forecaster=None, min_buy: float = MIN_BUY_SCORE) -> pd.DataFrame:
    """Như demo_p2: duyệt từng ngày x mã, giữ các dòng mà có chiến lược lên tiếng.
    Cột: date, symbol, regime, close, actions (các tín hiệu), final (BUY/SELL/-), score, verdict."""
    rows = []
    for asof in dates:
        asof = pd.Timestamp(asof)
        day_bars = {s: b for s, b in bars.items() if s in symbols and asof in b.index}
        fcs = get_forecasts(cut_upto(day_bars, asof), asof, flags, table, forecaster)
        for s in day_bars:
            state = build_state(s, asof, bars, spy, fcs)
            d = decide(pipe, state, min_buy)
            if not d.signals:
                continue  # ngày thường
            rows.append({"date": asof.date().isoformat(), "symbol": s, "regime": state.regime,
                         "close": float(state.bars["close"].iloc[-1]),
                         "actions": ", ".join(f"{x.strategy}:{x.action}{' (veto)' if x.veto else ''}"
                                              for x in d.signals),
                         "final": d.final.action if d.final else "-",
                         "score": d.final.score if d.final else None, "verdict": d.verdict})
    cols = ["date", "symbol", "regime", "close", "actions", "final", "score", "verdict"]
    return pd.DataFrame(rows, columns=cols)


# ---------------------------- Ghép lệnh CLI (whitelist) ----------------------------
# Không bao giờ ghép chuỗi tuỳ ý: mỗi phần của lệnh hoặc đến từ hằng số, hoặc đã qua kiểm tra chặt.

CONFIRM_WORD = "XAC NHAN"
BASE_CMD = ["uv", "run", "paper-bot"]
SYMBOL_RE = re.compile(r"[A-Z]{1,6}")  # dùng fullmatch (không dùng $ vì $ cho phép "\n" ở cuối)
MAX_SYMBOLS = 50


def validate_symbols(symbols) -> List[str]:
    """Mã phải là chữ HOA A-Z (1-6 ký tự), 1..50 mã, bỏ trùng. Sai -> ValueError."""
    if isinstance(symbols, str) or not symbols:
        raise ValueError("cần ít nhất 1 mã (danh sách)")
    out = []
    for s in symbols:
        if not isinstance(s, str) or not SYMBOL_RE.fullmatch(s):
            raise ValueError(f"mã không hợp lệ: {s!r} (chỉ chữ HOA A-Z, tối đa 6 ký tự)")
        if s not in out:
            out.append(s)
    if len(out) > MAX_SYMBOLS:
        raise ValueError(f"tối đa {MAX_SYMBOLS} mã")
    return out


def validate_disable(disable) -> List[str]:
    """Chỉ nhận tên ngắn có trong config.SHORT_FLAG_NAMES (regime, calibration, ...)."""
    out = []
    for d in disable or []:
        if d not in SHORT_FLAG_NAMES:
            raise ValueError(f"thành phần không hợp lệ: {d!r} (hợp lệ: {', '.join(SHORT_FLAG_NAMES)})")
        if d not in out:
            out.append(d)
    return out


def validate_interval(n) -> int:
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 1440:
        raise ValueError("interval phải là số nguyên 1..1440 (phút)")
    return n


def confirmed(ack, text) -> bool:
    """Chế độ THẬT chỉ mở khi tick đồng ý (đúng True) VÀ gõ đúng chữ XAC NHAN (không strip, không đổi hoa/thường)."""
    return ack is True and text == CONFIRM_WORD


def validate_crypto_symbols(symbols) -> List[str]:
    """Mã crypto phải nằm đúng trong whitelist (dạng "BTC/USD"), bỏ trùng. Sai -> ValueError."""
    if isinstance(symbols, str) or not symbols:
        raise ValueError("cần ít nhất 1 mã crypto (danh sách)")
    out = []
    for s in symbols:
        if s not in CRYPTO_UNIVERSE:
            raise ValueError(f"mã crypto không hợp lệ: {s!r} (chỉ {', '.join(CRYPTO_UNIVERSE)})")
        if s not in out:
            out.append(s)
    return out


def build_once_command(symbols, engine: str = "full", disable=(), llm: bool = False,
                       dry_run: bool = True, ack=False, confirm_text: str = "", asset: str = "stock") -> List[str]:
    """Danh sách tham số (KHÔNG shell) cho `paper-bot once`. dry_run=False cần đủ 2 xác nhận.
    asset="crypto" thêm `--asset crypto`: chỉ nhận mã whitelist crypto và engine full."""
    if asset not in ("stock", "crypto"):
        raise ValueError(f"asset không hợp lệ: {asset!r}")
    if engine not in ENGINES:
        raise ValueError(f"engine không hợp lệ: {engine!r}")
    if asset == "crypto" and engine != "full":
        raise ValueError("crypto chỉ chạy với engine full")
    syms = validate_crypto_symbols(symbols) if asset == "crypto" else validate_symbols(symbols)
    off = validate_disable(disable)
    if engine == "legacy" and off:
        raise ValueError("engine legacy không hỗ trợ --disable")
    if not dry_run and not confirmed(ack, confirm_text):
        raise PermissionError(f"Chạy THẬT cần tick đồng ý và gõ đúng '{CONFIRM_WORD}'")
    cmd = BASE_CMD + ["once", "--symbols", *syms, "--engine", engine]
    if asset == "crypto":
        cmd += ["--asset", "crypto"]
    if off:
        cmd += ["--disable", *off]
    if llm:
        cmd.append("--llm")
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def build_reset_kill_switch_command(dry_run: bool = False, ack=False, confirm_text: str = "") -> List[str]:
    """`paper-bot report --reset-kill-switch` (thêm --dry-run để gỡ cho run live-dry). Luôn cần xác nhận."""
    if not confirmed(ack, confirm_text):
        raise PermissionError(f"Gỡ kill switch cần tick đồng ý và gõ đúng '{CONFIRM_WORD}'")
    return BASE_CMD + ["report", "--reset-kill-switch"] + (["--dry-run"] if dry_run else [])


def is_real_order_command(cmd: List[str]) -> bool:
    """True nếu lệnh là `once` mà KHÔNG có --dry-run (tức sẽ gửi lệnh paper). Dùng làm chốt chặn ở nơi chạy."""
    return "once" in cmd and "--dry-run" not in cmd


def loop_command_text(symbols, engine: str = "full", disable=(), llm: bool = False,
                      dry_run: bool = True, interval: int = 30, asset: str = "stock") -> str:
    """Chỉ để HIỂN THỊ: câu lệnh loop người dùng tự chép chạy ở terminal (UI không chạy loop)."""
    cmd = build_once_command(symbols, engine, disable, llm, dry_run=True, asset=asset)  # tái dùng kiểm tra
    cmd[cmd.index("once")] = "loop"
    cmd = [c for c in cmd if c != "--dry-run"]
    cmd += ["--interval", str(validate_interval(interval))] + (["--dry-run"] if dry_run else [])
    return shlex.join(cmd)


# ---------------------------- Chạy lệnh (chốt chặn an toàn) ----------------------------

RUN_TIMEOUT = 600  # giây
ALLOWED_SUBCOMMANDS = ("once", "report")  # UI không bao giờ chạy "loop"


def run_command(cmd: List[str], allow_real: bool = False, timeout: int = RUN_TIMEOUT) -> dict:
    """Chạy lệnh paper-bot bằng subprocess (list args, KHÔNG shell). Đây là CHỐT CHẶN cuối cùng:
    - lệnh phải bắt đầu bằng `uv run paper-bot` và là once/report (loop bị chặn);
    - lệnh `once` không có --dry-run (gửi lệnh paper thật) chỉ chạy khi allow_real=True.
    Trả {returncode, stdout, stderr, timed_out}."""
    if (not isinstance(cmd, list) or not all(isinstance(c, str) for c in cmd)
            or cmd[:3] != BASE_CMD or len(cmd) < 4 or cmd[3] not in ALLOWED_SUBCOMMANDS):
        raise PermissionError(f"lệnh không nằm trong danh sách cho phép: {cmd!r}")
    if is_real_order_command(cmd) and allow_real is not True:
        raise PermissionError("lệnh THẬT (không --dry-run) chưa được xác nhận, không chạy")
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=_PROJECT_ROOT, shell=False)
    except subprocess.TimeoutExpired as e:
        def _txt(x):
            return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")

        return {"returncode": None, "stdout": _txt(e.stdout), "stderr": _txt(e.stderr), "timed_out": True}
    return {"returncode": done.returncode, "stdout": done.stdout, "stderr": done.stderr, "timed_out": False}


def log_ui_event(run_id: str, kind: str, message: str, db_path=None) -> None:
    """Ghi một event vào DB (dùng khi UI gửi lệnh thật / gỡ kill switch), để lần sau xem lại ai đã bấm."""
    from ..storage import Store

    Store(path=db_path or _PROJECT_ROOT / "data" / "trading.db", run_id=run_id).log_event(
        kind, message, level="WARNING")


def real_run_id(dry_run: bool) -> str:
    return "live-dry" if dry_run else "live-paper"


RUN_LABELS = {
    "bt-abl-FULL": "FULL (bản đầy đủ, ablation)",
    "bt-abl-no-forecast": "FULL bỏ forecast",
    "bt-abl-no-regime": "FULL bỏ regime switching",
    "bt-abl-no-calibration": "FULL bỏ hiệu chỉnh xác suất",
    "bt-abl-no-atr-sizing": "FULL bỏ sizing ATR",
    "bt-abl-no-bracket": "FULL bỏ bracket/stop",
    "bt-abl-no-corr-limit": "FULL bỏ giới hạn tương quan",
    "bt-abl-B0": "B0 (mua giữ SPY, ablation)",
    "bt-abl-B1": "B1 (chia đều 50 mã, ablation)",
    "bt-abl-B2": "B2 (bot gốc, ablation)",
    "bt-B0": "B0 (mua giữ SPY)",
    "bt-B1": "B1 (chia đều 50 mã)",
    "bt-B2": "B2 (bot gốc)",
    "bt-B2-20bps": "B2 phí 20 bps",
    "bt-B2-5bps": "B2 phí 5 bps",
}


def run_label(run_id: str) -> str:
    """Nhãn tiếng Việt cho run_id (không biết thì giữ nguyên id). Giá trị chọn vẫn là run_id."""
    return f"{RUN_LABELS[run_id]}  [{run_id}]" if run_id in RUN_LABELS else run_id


# ---------------------------- Đánh giá + giám sát: đọc DB / file (CHỈ ĐỌC) ----------------------------
# Tái dùng hàm load của dashboard.py (P4); dashboard chỉ import streamlit bên trong main() nên import được.

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # thư mục gốc project (chứa data/, pyproject.toml)
LIVE_RUNS = ("live-paper", "live-dry")  # = live.RUN_ID_PAPER, live.RUN_ID_DRY
LIVE_STATE_FILE = "live_state.json"


def open_db(path=None):
    """Mở DB chỉ đọc (None nếu chưa có file)."""
    from .. import dashboard

    return dashboard.connect_readonly(path or dashboard.DB_PATH)


def db_runs(conn) -> List[str]:
    from .. import dashboard

    return dashboard.list_runs(conn)


def backtest_runs(runs: Iterable[str]) -> List[str]:
    """Các run backtest (bt-...); ablation cũng bắt đầu bằng bt-."""
    return [r for r in runs if r.startswith("bt-")]


def db_table(conn, table: str, run_id: str, limit: int = 5000) -> pd.DataFrame:
    from .. import dashboard

    return dashboard.load_table(conn, table, run_id, limit)


def equity_curves(conn, run_ids: List[str]) -> Dict[str, pd.DataFrame]:
    """Equity + drawdown từng run (run không có equity bị bỏ)."""
    from .. import dashboard

    return {r: c for r, c in dashboard.load_equity_many(conn, run_ids).items() if not c.empty}


def run_summary(conn, run_ids: List[str]) -> pd.DataFrame:
    from .. import dashboard

    return dashboard.summary_table(conn, run_ids)


def filter_events(df: pd.DataFrame, levels=(), kinds=()) -> pd.DataFrame:
    """Lọc bảng events theo level / kind (rỗng = không lọc)."""
    if df.empty:
        return df
    if levels:
        df = df[df["level"].isin(levels)]
    if kinds:
        df = df[df["kind"].isin(kinds)]
    return df


def read_live_state(run_id: str, path=None) -> dict:
    """Trạng thái kill switch của run_id đọc từ data/live_state.json. Chưa có file/run -> {}.
    File hỏng thì ném ValueError có thông báo rõ."""
    from ..live import STATE_PATH, load_state

    try:
        return load_state(path or STATE_PATH, run_id)
    except json.JSONDecodeError as e:
        raise ValueError(f"live_state.json hỏng: {e}") from e


def kill_switch_text(state: dict) -> Tuple[bool, str]:
    """(đang ngắt?, câu mô tả) từ state của read_live_state."""
    if not state:
        return False, "Chưa có trạng thái (bot chưa chạy vòng nào cho run này)."
    halted = bool(state.get("halted", False))
    head = "ĐANG NGẮT - bot chặn lệnh MUA" if halted else "Bình thường (không ngắt)"
    peak, start = state.get("peak_equity"), state.get("day_start_equity")
    extra = []
    if state.get("day"):
        extra.append(f"ngày {state['day']}")
    if start is not None:
        extra.append(f"equity đầu ngày {start:,.0f}$")
    if peak is not None:
        extra.append(f"đỉnh {peak:,.0f}$")
    return halted, head + (" | " + ", ".join(extra) if extra else "")


def read_result_doc(name: str, results_dir=None) -> str:
    """Nội dung docs/ket-qua/<name> (chỉ tên file thường, không cho '../'). Thiếu file -> chuỗi rỗng."""
    from .. import dashboard

    if name != Path(name).name or not name.endswith(".md"):
        raise ValueError(f"tên file không hợp lệ: {name!r}")
    p = Path(results_dir or dashboard.RESULTS_DIR) / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _num(cell: str):
    """'0.12' -> 0.12; chữ (kể cả '-16.3%') giữ nguyên để không mất đơn vị."""
    try:
        return float(cell.strip())
    except ValueError:
        return cell


def markdown_tables(text: str) -> List[Tuple[str, pd.DataFrame]]:
    """Tách các bảng markdown (| a | b |) thành [(tiêu đề '##' gần nhất, DataFrame)].
    Cột nào toàn số thì đổi sang số, còn lại giữ chữ."""
    out, heading, block = [], "", []

    def flush():
        if len(block) >= 3:  # tiêu đề + dòng phân cách + >= 1 dòng dữ liệu
            cells = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in block]
            cols, rows = cells[0], [r for r in cells[2:] if len(r) == len(cells[0])]
            if rows:
                out.append((heading, pd.DataFrame(rows, columns=cols).apply(
                    lambda col: col.map(_num) if all(isinstance(_num(x), float) for x in col) else col)))
        block.clear()

    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            block.append(line)
            continue
        flush()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
    flush()
    return out


def ablation_table(results_dir=None) -> pd.DataFrame:
    """Bảng metric của ablation-dev.md (bảng đầu tiên); thiếu file -> bảng rỗng."""
    tables = markdown_tables(read_result_doc("ablation-dev.md", results_dir))
    return tables[0][1].set_index("cấu hình") if tables else pd.DataFrame()


def fetch_account() -> Tuple[Optional[dict], Optional[str]]:
    """(ảnh chụp tài khoản paper, lỗi). CHỈ ĐỌC. Thiếu key / mất mạng -> (None, thông báo)."""
    try:
        from alpaca.trading.client import TradingClient

        from ..config import TradingConfig

        cfg = TradingConfig.from_env()  # thiếu key thì raise SystemExit
        return account_snapshot(TradingClient(cfg.api_key, cfg.api_secret, paper=True)), None
    except BaseException as e:
        return None, f"Không lấy được tài khoản Alpaca: {e}"


def account_snapshot(client) -> dict:
    """Ảnh chụp tài khoản Alpaca CHỈ ĐỌC (client là TradingClient hoặc đối tượng giả có get_account,
    get_all_positions, get_orders). Trả equity, cash, positions (DataFrame), orders (DataFrame)."""
    acc = client.get_account()
    pos = pd.DataFrame([{"symbol": p.symbol, "qty": float(p.qty), "giá vốn": float(p.avg_entry_price),
                         "giá trị": float(p.market_value), "lãi/lỗ": float(p.unrealized_pl)}
                        for p in client.get_all_positions()],
                       columns=["symbol", "qty", "giá vốn", "giá trị", "lãi/lỗ"])
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    orders = pd.DataFrame([{"symbol": o.symbol, "side": str(getattr(o.side, "value", o.side)),
                            "qty": o.qty, "type": str(getattr(o.order_type, "value", o.order_type)),
                            "status": str(getattr(o.status, "value", o.status)), "id": str(o.id)}
                           for o in client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))],
                          columns=["symbol", "side", "qty", "type", "status", "id"])
    return {"equity": float(acc.equity), "cash": float(acc.cash), "positions": pos, "orders": orders}
