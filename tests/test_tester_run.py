"""Test run_tester (tester/run.py) trên dữ liệu tổng hợp, offline."""

import json

import numpy as np
import pandas as pd
import pytest

from paper_bot.backtest.baselines import make_b2
from paper_bot.backtest.engine import run_backtest
from paper_bot.backtest.tester.run import run_tester
from paper_bot.backtest.tester.strategies import make_b2_params
from paper_bot.backtest.walkforward import HoldoutError

DAYS = pd.bdate_range("2016-01-01", "2020-06-30")
SYMS = ["AAA", "BBB", "CCC"]


def fake_bars(seed, vol=0.02):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, vol, len(DAYS))))
    open_ = close * (1 + rng.normal(0, 0.003, len(DAYS)))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.01,
                         "low": np.minimum(open_, close) * 0.99, "close": close,
                         "volume": 1e6}, index=DAYS)


def world():
    bars = {s: fake_bars(i) for i, s in enumerate(SYMS)}
    bars["SPY"] = fake_bars(99, vol=0.01)
    return bars


CFG = {"symbols": SYMS, "from": "2019-01-01", "to": "2019-12-31", "strategy": "B2_goc",
       "deposit": 100_000, "cost_bps": 10}


def strip_timing(out):
    return {k: v for k, v in out.items() if k != "timing"}


def test_b2_tham_so_mac_dinh_giong_make_b2():
    bars = {s: fake_bars(i) for i, s in enumerate(SYMS)}
    a = run_backtest(bars, "2019-01-01", "2019-12-31", make_b2(SYMS))
    b = run_backtest(bars, "2019-01-01", "2019-12-31", make_b2_params(SYMS))
    assert len(a.fills) > 5
    assert [(f.symbol, f.side, f.qty, f.price) for f in a.fills] == \
           [(f.symbol, f.side, f.qty, f.price) for f in b.fills]


def test_single_b2_ket_qua_day_du_va_json():
    out = run_tester(CFG, bars=world())
    json.dumps(out, allow_nan=False)
    rep, trades, replay = out["report"], out["trades"], out["replay"]
    assert rep["total_trades"] == len(trades) > 0
    assert out["curve"][-1]["equity"] == pytest.approx(rep["final_equity"])
    # lệnh còn mở được "đóng" ở ngày cuối (như MT5) -> balance cuối = final_balance
    assert out["curve"][-1]["balance"] == pytest.approx(rep["final_balance"])
    assert len(replay) == len(out["curve"]) and replay[0]["date"] == "2019-01-01"
    assert replay[-1]["date"] <= "2019-12-31"
    # mỗi fill đều xuất hiện trong replay, lệnh mua có lý do vào lệnh
    assert sum(len(r["fills"]) for r in replay) == sum(
        1 + (t["exit_reason"] != "end") for t in trades)
    assert all(t["entry_note"] for t in trades)
    assert all(t["stop_loss"] is None for t in trades)  # B2 không có stop
    # lệnh quyết định ngày d -> khớp ở ngày sau
    first = next(i for i, r in enumerate(replay) if r["orders"])
    assert replay[first + 1]["fills"]


def test_khong_ro_ri_sau_ngay_to():
    a = run_tester(CFG, bars=world())
    bars2 = world()
    for df in bars2.values():
        df.loc[df.index > "2019-12-31", ["open", "high", "low", "close"]] *= 3.0
    b = run_tester(CFG, bars=bars2)
    assert strip_timing(a) == strip_timing(b)


def test_khong_ro_ri_full_ca_forecast():
    """FULL: sửa nến, SPY và forecast sau `to` -> kết quả không đổi."""
    rng = np.random.default_rng(7)
    idx = pd.MultiIndex.from_product([SYMS, DAYS], names=["symbol", "date"])
    q50 = rng.normal(0, 0.01, len(idx))
    fc = pd.DataFrame({"q10": q50 - 0.03, "q50": q50, "q90": q50 + 0.03}, index=idx)
    cfg = {**CFG, "from": "2019-07-01", "strategy": "FULL", "strategy_params": {"min_score": 0.0}}
    a = run_tester(cfg, bars=world(), forecasts=fc)
    bars2, fc2 = world(), fc.copy()
    for df in bars2.values():
        df.loc[df.index > "2019-12-31", ["open", "high", "low", "close"]] *= 0.3
    fc2.loc[fc2.index.get_level_values("date") > "2019-12-31", ["q10", "q50", "q90"]] -= 0.5
    b = run_tester(cfg, bars=bars2, forecasts=fc2)
    assert a["trades"] and strip_timing(a) == strip_timing(b)


def test_holdout_bi_chan():
    with pytest.raises(HoldoutError):
        run_tester({**CFG, "to": "2025-01-02"}, bars=world())


def test_cau_hinh_sai():
    with pytest.raises(ValueError):
        run_tester({**CFG, "strategy": "XYZ"}, bars=world())
    with pytest.raises(ValueError):
        run_tester({**CFG, "risk": {"atr_stop_mult": 2}}, bars=world())  # B2 không có stop
    with pytest.raises(ValueError):
        run_tester({**CFG, "mode": "abc"}, bars=world())
    with pytest.raises(ValueError):
        run_tester({**CFG, "symbols": ["ZZZ"]}, bars=world())


def test_walkforward_b2():
    out = run_tester({**CFG, "mode": "walkforward"}, bars=world())
    json.dumps(out, allow_nan=False)
    assert len(out["per_window"]) == 2
    assert out["per_window"][0]["test_start"] == "2019-01-01"
    # B2 không fit gì -> walk-forward giống single
    single = run_tester(CFG, bars=world())
    assert out["report"] == single["report"]


def test_full_single_co_sl_tp_va_tham_so():
    cfg = {**CFG, "strategy": "FULL", "flags": {"use_forecast": False},
           "strategy_params": {"min_score": 0.0}, "risk": {"atr_stop_mult": 1.5}}
    out = run_tester(cfg, bars=world())
    json.dumps(out, allow_nan=False)
    trades = [t for t in out["trades"]]
    assert trades and all(t["stop_loss"] is not None for t in trades)
    lv = next(r["levels"] for r in out["replay"] if r["levels"])
    assert lv[0]["stop_loss"] < lv[0]["entry_price"] < lv[0]["take_profit"]
