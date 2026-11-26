"""Policy đầy đủ cho backtest/walk-forward của P4 (dòng "full" và các dòng ablation, prereg mục 8).

    factory = make_full_policy_factory(flags)          # flags mặc định True hết
    policy = factory(train_bars)                       # fit calibrator CHỈ trên train_bars
    policy(asof, view, portfolio) -> list[OrderIntent]

Mỗi flag tắt đúng một thành phần:
  use_regime       False -> MA + RSI như bài gốc (không regime switch)
  use_calibration  False -> không fit calibrator, ngưỡng áp lên confidence thô
  use_forecast     False -> state.forecast = None (tắt veto forecast, scale bất định, feature forecast)
  use_atr_sizing   False -> cỡ lệnh theo legacy_position_size của bài gốc
  use_bracket      False -> bỏ stop-loss / take-profit
  use_corr_limit   False -> bỏ kiểm tra tương quan

Chống rò rỉ: mọi dữ liệu trong policy lấy qua view (<= asof); SPY truyền ngoài cũng cắt <= asof;
regime tính lại từ SPY <= asof; forecast tra đúng ngày asof (đã tính chỉ từ dữ liệu <= asof).
"""

import logging
from typing import Dict, List, Optional

import pandas as pd

from ..config import BENCHMARK
from ..contracts import MarketState, OrderIntent
from ..data.cache import load_bars
from ..data.forecast import get_forecast, load_forecasts
from ..data.regime import regime_series
from ..risk import RiskConfig, legacy_position_size, make_order_intent
from ..strategies import MovingAverageCrossoverStrategy, RSIStrategy
from .base import wrap_legacy
from .calib_history import DATA_END, symbol_rows
from .calibration import FEATURE_WINDOW, fit_calibrator
from .pipeline import DecisionPipeline
from .regime_switch import RegimeSwitchStrategy

logger = logging.getLogger("paper_bot")

DEFAULT_FLAGS = {"use_regime": True, "use_calibration": True, "use_forecast": True,
                 "use_atr_sizing": True, "use_bracket": True, "use_corr_limit": True}


def _upto(df: pd.DataFrame, asof) -> pd.DataFrame:
    return df.iloc[:df.index.searchsorted(pd.Timestamp(asof), side="right")]


# Tham số chiến lược có thể chỉnh (Strategy Tester); mặc định = bài gốc MA 10/30, RSI 14 (30/70)
DEFAULT_STRATEGY_PARAMS = {"ma_short": 10, "ma_long": 30, "rsi_period": 14,
                           "rsi_oversold": 30, "rsi_overbought": 70}


def _check_strategy_params(params: Optional[dict]) -> Optional[dict]:
    """None -> None (hành vi cũ). Có -> dict đầy đủ (điền mặc định), báo lỗi khoá lạ/sai."""
    if params is None:
        return None
    unknown = set(params) - set(DEFAULT_STRATEGY_PARAMS)
    if unknown:
        raise ValueError(f"strategy_params lạ: {sorted(unknown)}")
    p = {**DEFAULT_STRATEGY_PARAMS, **params}
    if not 0 < p["ma_short"] < p["ma_long"] < FEATURE_WINDOW:
        raise ValueError(f"cần 0 < ma_short < ma_long < {FEATURE_WINDOW}, nhận {p}")
    return p


def _base_strategies(params: Optional[dict] = None):
    p = params or DEFAULT_STRATEGY_PARAMS
    return [MovingAverageCrossoverStrategy(int(p["ma_short"]), int(p["ma_long"])),
            RSIStrategy(int(p["rsi_period"]), p["rsi_oversold"], p["rsi_overbought"])]


def _regime_strategy(params: Optional[dict]):
    """Như RegimeSwitchStrategy() mặc định, nhưng MA/RSI dùng tham số `params`."""
    if params is None:
        return RegimeSwitchStrategy()
    ma, rsi = _base_strategies(params)
    return RegimeSwitchStrategy({"bull_calm": [ma], "bull_volatile": [ma], "bear_calm": [rsi],
                                 "bear_volatile": [ma, rsi], "unknown": [ma, rsi]})


def make_full_policy_factory(flags: Optional[dict] = None, risk_cfg: Optional[RiskConfig] = None,
                             forecasts: Optional[pd.DataFrame] = None,
                             spy_bars: Optional[pd.DataFrame] = None, symbols=None,
                             min_score: float = 0.5, calib_method: str = "isotonic",
                             calib_cache: Optional[dict] = None,
                             strategy_params: Optional[dict] = None):
    """strategy_params: tuỳ chọn, chỉnh MA/RSI (khoá trong DEFAULT_STRATEGY_PARAMS). None = như cũ.
      Áp cho cả chiến lược giao dịch lẫn bảng tín hiệu fit calibrator.
    spy_bars: SPY để tính regime (None -> lấy từ view nếu có, không thì đọc cache tới 2024-12-31).
    symbols: mã được giao dịch (None -> mọi mã trong view trừ SPY).
    min_score: ngưỡng score CHỈ cho lệnh VÀO (BUY). SELL khi đang nắm luôn được thực hiện
      (thoát vị thế là giảm rủi ro - quyết định của Jarvis, phương án (a)).
    calib_cache: dict dùng chung giữa nhiều factory (ablation) để không fit lại calibrator giống hệt;
      khoá = (ngày cuối train, có feature forecast, method). Calibrator không phụ thuộc các flag khác."""
    unknown = set(flags or {}) - set(DEFAULT_FLAGS)
    if unknown:
        raise ValueError(f"flag lạ: {sorted(unknown)}")
    f = {**DEFAULT_FLAGS, **(flags or {})}
    cfg = risk_cfg or RiskConfig()
    sp = _check_strategy_params(strategy_params)

    fc_table = None
    if f["use_forecast"]:
        fc_table = forecasts if forecasts is not None else load_forecasts()
        if fc_table.empty:
            logger.warning("use_forecast=True nhưng không có bảng forecast -> forecast=None")
            fc_table = None
    spy_cache = {"df": spy_bars}

    def spy_upto(asof, view=None) -> pd.DataFrame:
        if view is not None and BENCHMARK in view.symbols():
            return view.bars(BENCHMARK)
        if spy_cache["df"] is None:
            spy_cache["df"] = load_bars(BENCHMARK, end=DATA_END)
        return _upto(spy_cache["df"], asof)

    def policy_factory(train_bars: Dict[str, pd.DataFrame]):
        train_end = max((df.index[-1] for df in train_bars.values() if len(df)), default=None)
        calibrator, ref_width = None, None
        use_fc_feat = fc_table is not None
        if f["use_calibration"] and train_end is not None:
            key = (train_end, use_fc_feat, calib_method)
            if sp is not None:  # tham số khác -> tín hiệu khác -> calibrator khác
                key += (tuple(sorted(sp.items())),)
            if calib_cache is not None and key in calib_cache:
                calibrator = calib_cache[key]
            else:
                calibrator = _fit_on_train(train_bars, spy_upto(train_end), fc_table, use_fc_feat,
                                           calib_method, sp)
                if calib_cache is not None:
                    calib_cache[key] = calibrator
        if fc_table is not None and train_end is not None:
            dates = fc_table.index.get_level_values("date")
            part = fc_table[dates <= train_end]  # chỉ forecast của giai đoạn train
            if len(part):
                ref_width = float((part["q90"] - part["q10"]).median())
        strategies = [_regime_strategy(sp)] if f["use_regime"] else _base_strategies(sp)
        # Ngưỡng của pipeline = 0 (áp cho cả 2 hướng); ngưỡng thật chỉ áp cho BUY trong policy
        pipe = DecisionPipeline(strategies, calibrator=calibrator, min_score=0.0)
        return _make_policy(pipe, f, cfg, fc_table, ref_width, spy_upto, symbols, min_score)

    return policy_factory


def _fit_on_train(train_bars, spy, fc_table, use_fc_feat, method, params=None):
    """Bảng tín hiệu MA/RSI dựng CHỈ từ train_bars (nhãn t+5 cũng nằm trong train) -> calibrator."""
    regimes = regime_series(spy) if len(spy) else pd.Series(dtype=object)
    strategies = [wrap_legacy(s) for s in _base_strategies(params)]
    rows = []
    for sym, bars in train_bars.items():
        if sym == BENCHMARK or len(bars) == 0:
            continue
        rows += symbol_rows(sym, bars, regimes, strategies, fc_table, use_fc_feat)
    table = pd.DataFrame(rows)
    if table.empty or table["y"].nunique() < 2:
        logger.warning("train không đủ tín hiệu để fit calibrator -> chạy không calibration")
        return None
    return fit_calibrator(table, method=method, use_forecast=use_fc_feat)


REGIME_TAIL = 600  # đủ cho MA200 + median vol 252 phiên -> nhãn ngày cuối không đổi, nhanh hơn


def _returns(view, syms: List[str], n: int) -> pd.DataFrame:
    """Lợi suất ngày của `syms`, n phiên gần nhất, chỉ từ view (<= asof)."""
    return pd.DataFrame({s: view.bars(s)["close"].iloc[-(n + 1):].pct_change() for s in syms}).iloc[1:]


def _make_policy(pipe, f, cfg, fc_table, ref_width, spy_upto, symbols, min_score):
    def policy(asof, view, pf) -> List[OrderIntent]:
        spy = spy_upto(asof, view).iloc[-REGIME_TAIL:]
        regime = str(regime_series(spy).iloc[-1]) if len(spy) else "unknown"
        positions = {s: p.qty for s, p in pf.positions.items()}
        cash = pf.cash  # trừ dần khi đặt mua trong ngày
        sells, buys = [], []
        for sym in (symbols or [s for s in view.symbols() if s != BENCHMARK]):
            bars = view.bars(sym)
            if bars.empty or bars.index[-1] != asof:
                continue  # hôm nay mã không có phiên
            state = MarketState(sym, asof, bars.iloc[-FEATURE_WINDOW:], regime=regime)
            if fc_table is not None:
                state.forecast = get_forecast(fc_table, sym, asof)
            sig = pipe.decide(state)
            if sig is None:
                continue
            if sig.action == "BUY" and sig.score < min_score:
                continue  # ngưỡng chỉ chặn lệnh VÀO; SELL (thoát) không bị lọc
            returns = None
            if f["use_corr_limit"] and sig.action == "BUY":
                held = [s for s, q in positions.items() if q > 0 and s != sym]
                if held:
                    returns = _returns(view, [sym] + held, cfg.corr_window)
            intent = make_order_intent(sig, state, pf.equity, cash, positions, set(), cfg,
                                       returns=returns, ref_width=ref_width)
            if intent is None:
                continue
            if intent.side == "sell":
                sells.append(intent)
                continue
            price = float(bars["close"].iloc[-1])
            if not f["use_atr_sizing"]:
                intent.qty = legacy_position_size(cash, price)
            if not f["use_bracket"]:
                intent.stop_loss = intent.take_profit = None
            cash -= intent.qty * price
            positions[sym] = intent.qty  # tính luôn vào max_positions / tương quan của mã sau
            buys.append(intent)
        return sells + buys
    return policy


def main():
    """Chạy thử 1 backtest (không walk-forward): train 3 năm trước --start, test --start → --end.
    uv run python -m paper_bot.decision.full_policy --symbols AAPL MSFT --start 2019-01-01 --end 2019-06-30"""
    import argparse
    import time

    from ..backtest.engine import run_backtest
    from ..backtest.metrics import compute_metrics
    from ..backtest.walkforward import slice_bars

    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default="2019-06-30")
    ap.add_argument("--off", nargs="*", default=[], help="tên flag cần tắt, vd use_bracket")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    assert end < pd.Timestamp("2025-01-01"), "không được chạm HOLDOUT"

    t0 = time.time()
    bars = {s: load_bars(s, end=args.end) for s in args.symbols}
    train = slice_bars(bars, start - pd.DateOffset(years=3), start - pd.Timedelta(days=1))
    factory = make_full_policy_factory({k: False for k in args.off})
    policy = factory(train)
    t1 = time.time()
    res = run_backtest(bars, start, end, policy)
    t2 = time.time()
    print(f"fit calibrator: {t1 - t0:.1f}s | backtest: {t2 - t1:.1f}s | "
          f"{len(res.fills)} fill, {len(res.trades)} trade")
    for k, v in compute_metrics(res).items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
