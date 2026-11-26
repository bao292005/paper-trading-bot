"""Test báo cáo kiểu MT5 (tester/report.py) bằng bộ trade tính tay."""

import json

import pandas as pd
import pytest

from paper_bot.backtest import run_backtest
from paper_bot.backtest.engine import BacktestResult, Trade
from paper_bot.backtest.tester.report import (balance_curve, drawdown_stats, mt5_report,
                                              report_markdown, streak_stats)
from paper_bot.contracts import OrderIntent

D = pd.Timestamp


def hand_result():
    """5 trade, P/L sau phí theo thứ tự đóng: +20, -10, -20, +28, +5 (vốn 1000)."""
    trades = [
        Trade("A", 10, D("2020-01-01"), 10, D("2020-01-03"), 12, "signal"),           # +20
        Trade("B", 10, D("2020-01-02"), 10, D("2020-01-06"), 9, "stop_loss"),         # -10
        Trade("C", 10, D("2020-01-03"), 10, D("2020-01-07"), 8, "stop_loss"),         # -20
        Trade("D", 10, D("2020-01-06"), 10, D("2020-01-08"), 13, "take_profit", 2.0),  # +28
        Trade("E", 10, D("2020-01-07"), 5, D("2020-01-09"), 5.5, "end"),              # +5
    ]
    idx = pd.bdate_range("2020-01-01", "2020-01-09", name="date")  # 7 ngày
    eq = pd.DataFrame({"equity": [1000, 1005, 1030, 1000, 960, 1018, 1023],
                       "cash": 0.0, "exposure": 0.0}, index=idx)
    # Đưa trade theo thứ tự lộn xộn để kiểm tra báo cáo tự sắp theo ngày đóng
    return BacktestResult(eq, [trades[i] for i in (3, 0, 4, 2, 1)], [])


def test_balance_curve_only_changes_on_close():
    res = hand_result()
    bal = balance_curve(res.trades, 1000, res.equity.index)
    assert list(bal) == [1000, 1000, 1020, 1010, 990, 1018, 1023]
    # Không truyền days: chỉ các ngày có lệnh đóng
    assert list(balance_curve(res.trades, 1000)) == [1020, 1010, 990, 1018, 1023]


def test_drawdown_maximal_vs_relative():
    # đáy 50 (sụt 50$ = 50%), sau đó đỉnh 1000 -> 600 (sụt 400$ = 40%)
    dd = drawdown_stats([100, 50, 1000, 600], 100)
    assert dd["absolute"] == 50
    assert dd["maximal"] == 400 and dd["maximal_pct"] == pytest.approx(0.4)
    assert dd["relative_pct"] == pytest.approx(0.5) and dd["relative"] == 50


def test_streaks():
    pnls = [20, -10, -20, 28, 5, 0, 1, 1, 1]
    w = streak_stats(pnls, 1)
    assert w["max_count"] == 3 and w["max_count_money"] == 3       # chuỗi dài nhất 1,1,1
    assert w["max_money"] == 33 and w["max_money_count"] == 2      # chuỗi nhiều $ nhất 28,5
    lo = streak_stats(pnls, -1)
    assert lo["max_count"] == 2 and lo["max_money"] == -30
    assert streak_stats([], 1)["max_count"] == 0


def test_mt5_report_hand_numbers():
    r = mt5_report(hand_result(), 1000)
    assert r["total_net_profit"] == pytest.approx(23)
    assert r["gross_profit"] == pytest.approx(53) and r["gross_loss"] == pytest.approx(-30)
    assert r["profit_factor"] == pytest.approx(53 / 30)
    assert r["expected_payoff"] == pytest.approx(4.6)
    assert r["final_balance"] == pytest.approx(1023)
    # balance: 1000 -> 1020 -> 1010 -> 990 -> 1018 -> 1023
    assert r["balance_drawdown_absolute"] == pytest.approx(10)
    assert r["balance_drawdown_maximal"] == pytest.approx(30)
    assert r["balance_drawdown_maximal_pct"] == pytest.approx(30 / 1020)
    # equity: đỉnh 1030 -> đáy 960
    assert r["equity_drawdown_absolute"] == pytest.approx(40)
    assert r["equity_drawdown_maximal"] == pytest.approx(70)
    assert r["equity_drawdown_maximal_pct"] == pytest.approx(70 / 1030)
    assert r["recovery_factor"] == pytest.approx(23 / 70)
    assert r["total_trades"] == 5 and r["long_trades"] == 5 and r["short_trades"] == 0
    assert r["profit_trades"] == 3 and r["loss_trades"] == 2
    assert r["profit_trades_pct"] == pytest.approx(0.6)
    assert r["largest_profit_trade"] == pytest.approx(28)
    assert r["largest_loss_trade"] == pytest.approx(-20)
    assert r["average_profit_trade"] == pytest.approx(53 / 3)
    assert r["average_loss_trade"] == pytest.approx(-15)
    assert r["max_consecutive_wins"] == 2 and r["max_consecutive_wins_money"] == pytest.approx(33)
    assert r["max_consecutive_losses"] == 2 and r["max_consecutive_losses_money"] == pytest.approx(-30)
    assert r["average_holding_days"] == pytest.approx((2 + 4 + 4 + 2 + 2) / 5)
    assert r["total_fees"] == pytest.approx(2)
    assert r["open_at_end"] == 1
    json.dumps(r, allow_nan=False)  # phải serialize được ra JSON chuẩn
    md = report_markdown(r)
    assert "| Total net profit | 23.00 |" in md and "Profit factor" in md


def test_no_trades_and_no_losses():
    idx = pd.bdate_range("2020-01-01", periods=3, name="date")
    eq = pd.DataFrame({"equity": [1000.0] * 3, "cash": 1000.0, "exposure": 0.0}, index=idx)
    r = mt5_report(BacktestResult(eq, [], []), 1000)
    assert r["total_trades"] == 0 and r["profit_factor"] is None and r["expected_payoff"] is None
    assert r["recovery_factor"] is None
    json.dumps(r, allow_nan=False)
    report_markdown(r)
    one = [Trade("A", 1, idx[0], 10, idx[1], 11, "signal")]
    assert mt5_report(BacktestResult(eq, one, []), 1000)["profit_factor"] is None


def test_matches_engine_when_all_closed():
    """Với run_backtest thật: lệnh đã đóng hết -> final_balance = equity cuối."""
    idx = pd.bdate_range("2020-01-01", periods=4, name="date")
    df = pd.DataFrame({"open": [100, 101, 105, 103], "high": [101, 106, 106, 104],
                       "low": [99, 100, 102, 100], "close": [100, 105, 104, 102],
                       "volume": 1.0}, index=idx)

    def policy(asof, view, pf):
        if asof == idx[0]:
            return [OrderIntent("AAA", "buy", 10)]
        if asof == idx[2]:
            return [OrderIntent("AAA", "sell", 10)]
        return []

    res = run_backtest({"AAA": df}, idx[0], idx[-1], policy, initial_cash=10_000, cost_bps=10)
    r = mt5_report(res, 10_000)
    assert r["final_balance"] == pytest.approx(res.equity["equity"].iloc[-1])
    assert r["total_fees"] == pytest.approx(sum(f.fee for f in res.fills))
