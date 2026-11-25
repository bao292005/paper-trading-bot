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

import json
import logging
import pickle
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from .config import BENCHMARK, UNIVERSE_50, TradingConfig
from .contracts import Fill, OrderIntent
from .data.cache import PROJECT_ROOT, load_bars
from .data.forecast import load_forecasts
from .data.market_state import build_market_state
from .decision.calibration import FEATURE_WINDOW
from .decision.pipeline import DecisionPipeline
from .decision.regime_switch import RegimeSwitchStrategy
from .execution import AlpacaExecutor
from .risk import RiskConfig, legacy_position_size, make_order_intent
from .storage import Store
from .strategies import MovingAverageCrossoverStrategy, RSIStrategy

logger = logging.getLogger("paper_bot")

HISTORY_CALENDAR_DAYS = 1000  # ~690 phiên: đủ cho regime (MA200 + median 252) và FEATURE_WINDOW
RUN_ID_PAPER, RUN_ID_DRY = "live-paper", "live-dry"


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


# ---------- Trạng thái kill switch lưu ra file (khởi động lại không mất) ----------
STATE_PATH = PROJECT_ROOT / "data" / "live_state.json"


def load_state(path, run_id: str) -> dict:
    """{halted, day, day_start_equity, peak_equity} của run_id; chưa có -> {}."""
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get(run_id, {})


def save_state(path, run_id: str, state: dict) -> None:
    path = Path(path)
    data = json.loads(path.read_text()) if path.exists() else {}
    data[run_id] = state
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


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
                 calibrator="auto", state_path=STATE_PATH):
        """calibrator="auto": đọc/fit từ data/models/calibrator.pkl (nếu use_calibration);
        truyền object/None để dùng thẳng (test)."""
        self.config = config
        self.risk_cfg = risk_cfg or RiskConfig()
        if trading_client is None or data_manager is None:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.trading.client import TradingClient

            from .data.sources import AlpacaDataManager

            # paper=True gắn cứng: KHÔNG BAO GIỜ giao dịch tiền thật
            trading_client = trading_client or TradingClient(config.api_key, config.api_secret, paper=True)
            data_manager = data_manager or AlpacaDataManager(
                StockHistoricalDataClient(config.api_key, config.api_secret))
        self.client = trading_client
        self.data = data_manager
        self.store = store or Store(run_id=RUN_ID_DRY if config.dry_run else RUN_ID_PAPER)
        self.executor = AlpacaExecutor(trading_client, self.store, self.risk_cfg)

        self.forecaster = forecaster  # None + use_forecast -> tạo Chronos ở vòng đầu
        self._forecaster_failed = False
        self._fc_cache: dict = {}  # (ngày nến cuối, mã) -> Forecast: loop không tính lại trong ngày
        self.ref_width = self._ref_width() if config.flags["use_forecast"] else None

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
        positions = {p.symbol: float(p.qty) for p in self.client.get_all_positions()}
        open_orders = self.client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        return {"equity": float(account.equity), "cash": float(account.cash),
                "last_equity": float(getattr(account, "last_equity", 0) or 0),  # equity cuối phiên trước
                "long_value": float(getattr(account, "long_market_value", 0) or 0),
                "positions": positions, "open_symbols": {o.symbol for o in open_orders}}

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
            intent.qty = legacy_position_size(snap["cash"], price)
        if not self.config.flags["use_bracket"]:
            intent.stop_loss = intent.take_profit = None
        snap["cash"] -= intent.qty * price  # tính luôn cho mã sau
        snap["positions"][sym] = intent.qty
        return intent

    def send(self, intent: OrderIntent) -> None:
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

    # ---------- vòng ----------
    def run_cycle(self) -> None:
        logger.info("=== Bắt đầu vòng (engine full) ===")
        try:
            is_open = self.client.get_clock().is_open
            if not is_open:
                logger.warning("Thị trường đang ĐÓNG: lệnh sẽ xếp hàng tới phiên sau")
            if not self._reconciled:
                self.reconcile_at_start()
            snap = self.snapshot()
            cutoff = last_closed_day(is_open, now_ny())
            logger.info(f"Chỉ dùng nến ĐÃ ĐÓNG ≤ {cutoff.date()} (bỏ nến phiên chưa kết thúc, như backtest)")
            all_bars = self.load_bars(list(self.config.symbols) + [BENCHMARK], cutoff)
            # Mã đang nắm ngoài --symbols: chỉ tải nến để kiểm tra tương quan, KHÔNG ra quyết định
            extra = [s for s, q in snap["positions"].items() if q > 0 and s not in all_bars
                     and s not in self.config.symbols]
            extra_bars = self.load_bars(extra, cutoff) if extra and self.config.flags["use_corr_limit"] else {}
        except Exception as e:  # lỗi mạng đầu vòng: bỏ vòng, không sập
            logger.error(f"Lỗi đầu vòng, bỏ qua vòng này: {e}")
            self.store.log_event("cycle_error", str(e), level="ERROR")
            return
        spy = all_bars.pop(BENCHMARK, None)
        if spy is None:  # thiếu SPY -> regime "unknown"
            spy = pd.DataFrame(columns=["close"], index=pd.DatetimeIndex([], name="date"))
        snap["bars"] = {**extra_bars, **all_bars}
        asof = max(b.index[-1] for b in all_bars.values()) if all_bars else cutoff
        fcs = self.forecasts(all_bars, asof)

        sells, buys = [], []
        for sym, bars in all_bars.items():
            if bars.index[-1] != asof:  # như full_policy: mã không có phiên mới nhất thì bỏ
                logger.warning(f"{sym}: nến cuối {bars.index[-1].date()} ≠ {asof.date()}, bỏ qua")
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
            except Exception as e:
                self._symbol_error(intent.symbol, f"lỗi gửi lệnh: {e}")
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
        self.state = {"halted": self.executor.halted, "day": today_ny().date().isoformat(),
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
        """Mỗi `interval_minutes` phút một vòng khi thị trường mở. Ctrl+C để dừng."""
        try:
            while True:
                try:
                    if self.client.get_clock().is_open:
                        self.run_cycle()
                    else:
                        logger.info("Thị trường đang đóng cửa, chờ...")
                except Exception as e:  # vd mất mạng khi hỏi giờ: chờ vòng sau
                    logger.error(f"Lỗi vòng loop: {e}")
                    self.store.log_event("cycle_error", str(e), level="ERROR")
                time.sleep(interval_minutes * 60)
        except KeyboardInterrupt:
            logger.info("Đã dừng bot.")
