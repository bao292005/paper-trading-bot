"""Một lần chạy Strategy Tester: run_tester(config) -> dict (JSON được).

config (dict), khoá và mặc định:
  symbols         danh sách mã (bắt buộc)
  from, to        "2019-01-01", "2024-12-31". to >= 2025-01-01 -> HoldoutError
  strategy        "B2_goc" | "FULL"
  flags           dict cờ FULL (use_regime, use_bracket, ...), B2_goc không có
  strategy_params ma_short, ma_long, rsi_period, rsi_oversold, rsi_overbought, min_score
                  (B2_goc thêm history_days)
  risk            FULL: trường bất kỳ của RiskConfig (atr_stop_mult, take_profit_mult, risk_per_trade...)
                  B2_goc: risk_per_trade, max_position_size
  deposit, cost_bps   100000, 10
  mode            "single" (1 lần run_backtest; FULL fit calibrator trên 3 năm trước `from`)
                  | "walkforward" (train 3 năm / test 6 tháng như prereg)
  fill_model      "daily_ohlc" (SL trước khi mơ hồ) | "minute" (xem nến phút ngày mơ hồ)

Chống rò rỉ: mọi dữ liệu (nến, SPY, forecast) bị cắt <= `to` trước khi chạy; engine chỉ cho
policy thấy dữ liệu <= ngày đang xét.
"""

import time
from typing import Dict, Optional

import pandas as pd

from ...config import BENCHMARK
from ..engine import FILL_MODELS, run_backtest
from ..walkforward import DEV_END, DEV_START, check_holdout, run_walkforward, slice_bars
from .recorder import Recorder
from .report import mt5_report
from .strategies import make_factory

DEFAULTS = {"from": DEV_START, "to": DEV_END, "strategy": "B2_goc", "flags": {},
            "strategy_params": {}, "risk": {}, "deposit": 100_000.0, "cost_bps": 10.0,
            "mode": "single", "fill_model": "daily_ohlc"}
MODES = ("single", "walkforward")
WARMUP_YEARS = 3  # dữ liệu trước `from` để tính chỉ báo / fit calibrator


def normalize_config(config: dict) -> dict:
    """Điền mặc định + kiểm tra. Raise HoldoutError nếu `to` chạm HOLDOUT."""
    unknown = set(config) - set(DEFAULTS) - {"symbols"}
    if unknown:
        raise ValueError(f"khoá cấu hình lạ: {sorted(unknown)}")
    cfg = {**DEFAULTS, **config}
    if not cfg.get("symbols"):
        raise ValueError("cần ít nhất 1 mã trong 'symbols'")
    cfg["symbols"] = [str(s).upper() for s in cfg["symbols"]]
    start, end = pd.Timestamp(cfg["from"]), pd.Timestamp(cfg["to"])
    check_holdout(end, holdout=False)  # KHÔNG bao giờ cho phép holdout trong tester
    if start >= end:
        raise ValueError(f"'from' ({start.date()}) phải trước 'to' ({end.date()})")
    if cfg["mode"] not in MODES:
        raise ValueError(f"mode phải thuộc {MODES}")
    if cfg["fill_model"] not in FILL_MODELS:
        raise ValueError(f"fill_model phải thuộc {FILL_MODELS}")
    cfg["from"], cfg["to"] = start.date().isoformat(), end.date().isoformat()
    cfg["deposit"], cfg["cost_bps"] = float(cfg["deposit"]), float(cfg["cost_bps"])
    return cfg


def load_data(cfg: dict, bars: Optional[Dict[str, pd.DataFrame]] = None,
              forecasts: Optional[pd.DataFrame] = None):
    """(bars giao dịch, spy, forecasts), tất cả đã cắt <= `to`.
    bars/forecasts truyền vào (test offline); bars có thể chứa SPY. Không truyền -> đọc cache."""
    end = pd.Timestamp(cfg["to"])
    first = pd.Timestamp(cfg["from"]) - pd.DateOffset(years=WARMUP_YEARS)
    if bars is None:
        from ...data.cache import load_bars
        need = cfg["symbols"] + ([BENCHMARK] if cfg["strategy"] == "FULL" else [])
        bars = {s: load_bars(s, end=end) for s in need}
    bars = {s: df.loc[(df.index >= first) & (df.index <= end)] for s, df in bars.items()}
    missing = [s for s in cfg["symbols"] if s not in bars or bars[s].empty]
    if missing:
        raise ValueError(f"không có dữ liệu cho: {missing}")
    spy = bars.get(BENCHMARK)
    trade_bars = {s: bars[s] for s in cfg["symbols"]}

    fc = None
    if cfg["strategy"] == "FULL" and cfg["flags"].get("use_forecast", True):
        if forecasts is None:
            from ...data.forecast import load_forecasts
            forecasts = load_forecasts()
        fc = forecasts[forecasts.index.get_level_values("date") <= end] if len(forecasts) else forecasts
    return trade_bars, spy, fc


def _jsonable(v):
    """Đổi giá trị pandas/numpy sang kiểu JSON (ngày -> chuỗi ISO, số numpy -> int/float)."""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "item"):
        return v.item()
    return v


class _NoRecorder:
    """Thay Recorder khi detail=False: không ghi gì."""
    def wrap(self, policy):
        return policy

    def payload(self, res, deposit):
        return {}


def run_tester(config: dict, bars: Optional[Dict[str, pd.DataFrame]] = None,
               forecasts: Optional[pd.DataFrame] = None, minute_loader=None,
               detail: bool = True) -> dict:
    """Chạy 1 lần theo `config`. bars/forecasts/minute_loader: tuỳ chọn (test offline),
    mặc định đọc cache / data.intraday. detail=False: bỏ curve/trades/replay (dùng cho optimize)."""
    t0 = time.time()
    cfg = normalize_config(config)
    trade_bars, spy, fc = load_data(cfg, bars, forecasts)
    factory = make_factory(cfg["strategy"], cfg["symbols"], cfg["flags"], cfg["strategy_params"],
                           cfg["risk"], spy_bars=spy, forecasts=fc)
    rec = Recorder() if detail else _NoRecorder()
    kw = {"initial_cash": cfg["deposit"], "cost_bps": cfg["cost_bps"],
          "fill_model": cfg["fill_model"], "minute_loader": minute_loader}
    t_load = time.time()
    per_window = []
    if cfg["mode"] == "single":
        start = pd.Timestamp(cfg["from"])
        train = slice_bars(trade_bars, start - pd.DateOffset(years=WARMUP_YEARS),
                           start - pd.Timedelta(days=1))
        res = run_backtest(trade_bars, start, cfg["to"], rec.wrap(factory(train)), **kw)
    else:
        wf = run_walkforward(lambda tb: rec.wrap(factory(tb)), trade_bars, cfg["from"], cfg["to"],
                             **kw)
        res = wf.result
        per_window = [{k: _jsonable(v) for k, v in row.items()}
                      for row in wf.per_window.to_dict("records")]
    t_run = time.time()

    rep = mt5_report(res, cfg["deposit"])
    out = {"config": cfg, "report": rep, **rec.payload(res, cfg["deposit"]),
           "per_window": per_window, "fill_stats": dict(res.fill_stats)}
    out["timing"] = {"load_s": t_load - t0, "run_s": t_run - t_load,
                     "total_s": time.time() - t0}
    return out
