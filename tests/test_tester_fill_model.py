"""Test fill_model="minute" của engine bằng nến phút tổng hợp (offline)."""

import pandas as pd
import pytest

from paper_bot.backtest import run_backtest
from paper_bot.backtest.engine import first_hit_in_minutes
from paper_bot.contracts import OrderIntent

IDX = pd.bdate_range("2020-01-06", periods=4, name="date")
# Ngày 3 (IDX[2]) mơ hồ: low 90 <= SL 95 và high 110 >= TP 105, open 100 nằm giữa
DF = pd.DataFrame({"open": [100, 100, 100, 100], "high": [101, 101, 110, 101],
                   "low": [99, 99, 90, 99], "close": [100, 100, 100, 100],
                   "volume": 1.0}, index=IDX)


def minutes(rows, day=IDX[2]):
    """rows: [(open, high, low)] mỗi phút từ 09:30."""
    idx = pd.date_range(day + pd.Timedelta(hours=9, minutes=30), periods=len(rows), freq="min")
    o, h, lo = zip(*rows)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": o, "volume": 1.0}, index=idx)


def policy(asof, view, pf):
    if asof == IDX[0]:
        return [OrderIntent("AAA", "buy", 10, stop_loss=95, take_profit=105)]
    return []


def run(fill_model, loader=None):
    calls = []

    def spy_loader(sym, day):
        calls.append((sym, day))
        return loader(sym, day)
    res = run_backtest({"AAA": DF}, IDX[0], IDX[-1], policy, initial_cash=10_000, cost_bps=0,
                       fill_model=fill_model, minute_loader=spy_loader if loader else None)
    return res, calls


def test_daily_ohlc_giu_nguyen_sl_truoc():
    res, _ = run("daily_ohlc")
    assert res.trades[0].exit_reason == "stop_loss" and res.trades[0].exit_price == 95
    assert res.fill_stats["ambiguous"] == 1 and res.fill_stats["fill_model"] == "daily_ohlc"


def test_minute_tp_cham_truoc():
    res, calls = run("minute", lambda s, d: minutes([(100, 101, 99), (101, 106, 100), (99, 99, 90)]))
    t = res.trades[0]
    assert (t.exit_reason, t.exit_price, t.exit_date) == ("take_profit", 105, IDX[2])
    assert calls == [("AAA", IDX[2])]  # lazy: chỉ tải đúng ngày mơ hồ
    assert res.fill_stats["minute_tp"] == 1 and res.fill_stats["fallback"] == 0


def test_minute_sl_cham_truoc_va_gap_trong_phut():
    # phút 2 mở cửa 93 đã dưới SL 95 -> khớp ở 93
    res, _ = run("minute", lambda s, d: minutes([(100, 101, 99), (93, 94, 92), (100, 110, 100)]))
    assert (res.trades[0].exit_reason, res.trades[0].exit_price) == ("stop_loss", 93)
    assert res.fill_stats["minute_sl"] == 1


def test_cung_mot_phut_thi_sl_truoc():
    assert first_hit_in_minutes(minutes([(100, 110, 90)]), 95, 105) == ("stop_loss", 95)


def test_thieu_du_lieu_phut_thi_fallback():
    empty = minutes([(100, 101, 99)]).iloc[:0]
    res, _ = run("minute", lambda s, d: empty)
    assert res.trades[0].exit_reason == "stop_loss" and res.fill_stats["fallback"] == 1

    def boom(s, d):
        raise RuntimeError("thiếu key")
    res, _ = run("minute", boom)
    assert res.trades[0].exit_reason == "stop_loss" and res.fill_stats["fallback"] == 1


def test_ngay_khong_mo_ho_khong_tai():
    # Bỏ ngày mơ hồ duy nhất (high 101 < TP) -> không được gọi loader
    df = DF.copy()
    df.loc[IDX[2], "high"] = 101
    res = run_backtest({"AAA": df}, IDX[0], IDX[-1], policy, initial_cash=10_000, cost_bps=0,
                       fill_model="minute", minute_loader=lambda s, d: pytest.fail("không được tải"))
    assert res.fill_stats["ambiguous"] == 0


def test_run_tester_truyen_fill_model():
    from paper_bot.backtest.tester.run import run_tester
    import numpy as np
    days = pd.bdate_range("2016-01-01", "2019-12-31")
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, len(days))))
    # biên độ ngày rất rộng -> SL/TP (ATR nhỏ nhân hệ số nhỏ) dễ cùng nằm trong ngày
    bars = {"AAA": pd.DataFrame({"open": close, "high": close * 1.08, "low": close * 0.92,
                                 "close": close, "volume": 1e6}, index=days)}
    bars["SPY"] = bars["AAA"].copy()
    cfg = {"symbols": ["AAA"], "from": "2019-01-01", "to": "2019-12-31", "strategy": "FULL",
           "flags": {"use_forecast": False, "use_calibration": False},
           "strategy_params": {"min_score": 0.0},
           "risk": {"atr_stop_mult": 0.3, "take_profit_mult": 0.3}, "fill_model": "minute"}

    def tp_first(sym, day):  # phút đầu vọt lên cao, sau đó sập
        o = float(bars[sym].at[day, "open"])
        return minutes([(o, o * 1.08, o), (o, o, o * 0.92)], day)
    out = run_tester(cfg, bars=bars, minute_loader=tp_first)
    st = out["fill_stats"]
    assert st["fill_model"] == "minute" and st["ambiguous"] > 0 and st["minute_tp"] == st["ambiguous"]
    daily = run_tester({**cfg, "fill_model": "daily_ohlc"}, bars=bars)
    assert daily["report"]["total_net_profit"] < out["report"]["total_net_profit"]


def test_fill_model_sai_va_holdout():
    with pytest.raises(ValueError):
        run_backtest({"AAA": DF}, IDX[0], IDX[-1], policy, fill_model="tick")
    with pytest.raises(ValueError):
        run_backtest({"AAA": DF}, IDX[0], "2025-01-02", policy, fill_model="minute")
