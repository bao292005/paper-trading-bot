"""Test walk-forward: cửa sổ đúng prereg, không rò rỉ, chốt holdout."""

import numpy as np
import pandas as pd
import pytest

from paper_bot.backtest.baselines import make_b0
from paper_bot.backtest.walkforward import (
    HoldoutError,
    check_holdout,
    make_windows,
    run_walkforward,
)


def long_bars(start="2016-01-01", end="2025-06-30"):
    idx = pd.bdate_range(start, end, name="date")
    c = 100 * np.exp(np.cumsum(np.full(len(idx), 0.0003)))
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": 1e6}, index=idx)


def test_windows_match_prereg():
    ws = make_windows()
    assert len(ws) == 12
    w0, wl = ws[0], ws[-1]
    assert (w0.train_start, w0.train_end) == (pd.Timestamp("2016-01-01"), pd.Timestamp("2018-12-31"))
    assert (w0.test_start, w0.test_end) == (pd.Timestamp("2019-01-01"), pd.Timestamp("2019-06-30"))
    assert (wl.test_start, wl.test_end) == (pd.Timestamp("2024-07-01"), pd.Timestamp("2024-12-31"))
    # Các đoạn test nối liền, không chồng lấn
    for a, b in zip(ws, ws[1:]):
        assert b.test_start == a.test_end + pd.Timedelta(days=1)


def test_holdout_guard():
    check_holdout("2024-12-31", holdout=False)  # không lỗi
    with pytest.raises(HoldoutError):
        check_holdout("2025-01-01", holdout=False)
    with pytest.raises(HoldoutError):
        make_windows(end="2025-06-30")
    with pytest.raises(HoldoutError):
        run_walkforward(lambda tb: make_b0("AAA"), {"AAA": long_bars()}, end="2025-03-31")
    assert len(make_windows("2025-01-01", "2026-10-31", holdout=True)) == 4


def test_no_leakage_and_equity_stitched():
    bars = {"AAA": long_bars()}  # có cả dữ liệu 2025 (holdout)
    train_ranges, max_seen = [], []

    def factory(train_bars):
        df = train_bars["AAA"]
        train_ranges.append((df.index.min(), df.index.max()))
        inner = make_b0("AAA")

        def policy(asof, view, pf):
            max_seen.append(view.bars("AAA").index.max())
            return inner(asof, view, pf)
        return policy

    wf = run_walkforward(factory, bars, initial_cash=10_000, cost_bps=10)
    ws = wf.windows
    # Mỗi train kết thúc TRƯỚC test của nó, và dài ~3 năm
    for (lo, hi), w in zip(train_ranges, ws):
        assert hi < w.test_start and lo >= w.train_start
    # Không bao giờ thấy dữ liệu holdout
    assert max(max_seen) <= pd.Timestamp("2024-12-31")
    eq = wf.result.equity
    assert eq.index[0] >= pd.Timestamp("2019-01-01")
    assert eq.index[-1] <= pd.Timestamp("2024-12-31")
    # Danh mục giữ liên tục qua các cửa sổ: B0 chỉ mua đúng 1 lần
    assert len(wf.result.fills) == 1
    assert len(wf.per_window) == 12
    assert set(["sharpe", "cagr", "max_drawdown"]) <= set(wf.per_window.columns)
