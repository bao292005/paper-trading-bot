"""Test metrics với chuỗi biết trước kết quả."""

import math
from datetime import datetime

import pandas as pd
import pytest

from paper_bot.backtest import metrics as m
from paper_bot.backtest.engine import BacktestResult, Trade
from paper_bot.contracts import Fill


def series(values, start="2020-01-01", freq="D"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=freq),
                     dtype=float)


def test_sharpe_known_value():
    r = pd.Series([0.01, -0.01, 0.01, -0.01])  # mean 0 -> sharpe 0
    assert m.sharpe(r) == pytest.approx(0.0)
    r = pd.Series([0.02, 0.0, 0.02, 0.0])  # mean 0.01, std 0.011547
    assert m.sharpe(r) == pytest.approx(0.01 / 0.0115470054 * math.sqrt(252))


def test_sharpe_constant_returns_is_zero():
    assert m.sharpe(pd.Series([0.01] * 5)) == 0.0
    assert m.sharpe(pd.Series([0.01])) == 0.0


def test_cagr_doubling_in_two_years():
    eq = pd.Series([100.0, 200.0], index=pd.to_datetime(["2020-01-01", "2021-12-31"]))
    years = (eq.index[1] - eq.index[0]).days / 365.25
    assert m.cagr(eq) == pytest.approx(2 ** (1 / years) - 1)


def test_max_drawdown():
    eq = series([100, 120, 90, 130, 65, 70])
    assert m.max_drawdown(eq) == pytest.approx(65 / 130 - 1)  # -50%
    assert m.max_drawdown(series([1, 2, 3])) == 0.0


def test_win_rate_uses_pnl_after_fee():
    d = pd.Timestamp("2020-01-01")
    trades = [Trade("A", 1, d, 10, d, 12, "signal"),
              Trade("A", 1, d, 10, d, 10.5, "signal", fee=1.0),  # lãi 0.5 nhưng phí 1 -> lỗ
              Trade("A", 1, d, 10, d, 9, "stop_loss")]
    assert m.win_rate(trades) == pytest.approx(1 / 3)
    assert m.win_rate([]) == 0.0


def test_turnover_and_compute_metrics():
    idx = pd.to_datetime(["2020-01-01", "2021-01-01"])  # 1 năm (366 ngày lịch)
    eq = pd.DataFrame({"equity": [1000.0, 1000.0], "cash": [0, 0], "exposure": [1.0, 0.5]},
                      index=idx)
    t = datetime(2020, 1, 1)
    fills = [Fill("1", "A", "buy", 10, 100, t), Fill("2", "A", "sell", 10, 100, t)]
    res = BacktestResult(equity=eq, trades=[], fills=fills)
    out = m.compute_metrics(res)
    assert out["turnover"] == pytest.approx(1000 / 1000 / (366 / 365.25))
    assert out["exposure"] == pytest.approx(0.75)
    assert out["n_trades"] == 0
    assert out["max_drawdown"] == 0.0
