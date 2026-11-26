"""Test tham số tuỳ chọn strategy_params của full_policy (thêm cho Strategy Tester)."""

import numpy as np
import pandas as pd
import pytest

from paper_bot.backtest.engine import run_backtest
from paper_bot.backtest.walkforward import slice_bars
from paper_bot.decision.full_policy import DEFAULT_STRATEGY_PARAMS, make_full_policy_factory

DAYS = pd.bdate_range("2019-01-01", "2020-12-31")
TRAIN_END, TEST_START = pd.Timestamp("2020-03-31"), pd.Timestamp("2020-04-01")


def fake_bars(seed, vol=0.02):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, vol, len(DAYS))))
    open_ = close * (1 + rng.normal(0, 0.003, len(DAYS)))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.01,
                         "low": np.minimum(open_, close) * 0.99, "close": close,
                         "volume": 1e6}, index=DAYS)


BARS = {s: fake_bars(i) for i, s in enumerate(["AAA", "BBB", "CCC"])}
SPY = fake_bars(99, vol=0.01)


def orders(strategy_params=None, flags=None, cache=None):
    flags = {"use_forecast": False, **(flags or {})}
    factory = make_full_policy_factory(flags, spy_bars=SPY, min_score=0.0, calib_cache=cache,
                                       strategy_params=strategy_params)
    policy = factory(slice_bars(BARS, DAYS[0], TRAIN_END))
    log = []

    def rec(asof, view, pf):
        out = policy(asof, view, pf)
        log.extend((asof, o.symbol, o.side, o.qty) for o in out)
        return out

    run_backtest(BARS, TEST_START, DAYS[-1], rec)
    return log


def test_mac_dinh_giong_none():
    for flags in ({}, {"use_regime": False}):
        base = orders(None, flags)
        assert base  # có lệnh để so sánh
        assert orders(dict(DEFAULT_STRATEGY_PARAMS), flags) == base
        assert orders({}, flags) == base


def test_doi_tham_so_doi_lenh():
    base = orders(None, {"use_regime": False, "use_calibration": False})
    other = orders({"ma_short": 5, "ma_long": 50, "rsi_period": 7},
                   {"use_regime": False, "use_calibration": False})
    assert other != base


def test_khoa_la_va_gia_tri_sai():
    with pytest.raises(ValueError):
        make_full_policy_factory({"use_forecast": False}, strategy_params={"ma_shrt": 5})
    with pytest.raises(ValueError):
        make_full_policy_factory({"use_forecast": False},
                                 strategy_params={"ma_short": 50, "ma_long": 30})


def test_calib_cache_tach_theo_tham_so():
    cache = {}
    orders(None, cache=cache)
    orders({"ma_short": 5}, cache=cache)
    assert len(cache) == 2
    assert sum(len(k) == 3 for k in cache) == 1  # khoá cũ (không tham số) giữ nguyên dạng
