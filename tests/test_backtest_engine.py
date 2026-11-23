"""Test backtest engine bằng dữ liệu tổng hợp nhỏ (offline)."""

import pandas as pd
import pytest

from paper_bot.backtest import run_backtest
from paper_bot.contracts import OrderIntent


def make_bars(opens, closes, highs=None, lows=None, start="2020-01-01"):
    """Nến tổng hợp: mặc định high/low = max/min(open, close)."""
    idx = pd.bdate_range(start, periods=len(opens), name="date")
    highs = highs or [max(o, c) for o, c in zip(opens, closes)]
    lows = lows or [min(o, c) for o, c in zip(opens, closes)]
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes,
                         "volume": 1000.0}, index=idx)


def buy_on_first_day(qty, symbol="AAA", **kw):
    """Policy: ngày đầu tiên đặt 1 lệnh mua, sau đó không làm gì."""
    state = {"done": False}

    def policy(asof, view, pf):
        if state["done"]:
            return []
        state["done"] = True
        return [OrderIntent(symbol, "buy", qty, **kw)]
    return policy


def test_fill_at_next_open_no_cost():
    df = make_bars([10, 11, 12], [10.5, 11.5, 12.5])
    res = run_backtest({"AAA": df}, df.index[0], df.index[-1], buy_on_first_day(10),
                       initial_cash=1000, cost_bps=0)
    fill = res.fills[0]
    assert fill.price == 11  # open ngày t+1, không phải close ngày t
    assert fill.filled_at == df.index[1]
    assert res.equity["equity"].iloc[0] == 1000
    assert res.equity["cash"].iloc[1] == 1000 - 110
    assert res.equity["equity"].iloc[-1] == pytest.approx(890 + 10 * 12.5)


def test_cost_applied_both_sides():
    df = make_bars([100, 100, 100], [100, 100, 100])

    def policy(asof, view, pf):
        if asof == df.index[0]:
            return [OrderIntent("AAA", "buy", 10)]
        if asof == df.index[1]:
            return [OrderIntent("AAA", "sell", 10)]
        return []

    res = run_backtest({"AAA": df}, df.index[0], df.index[-1], policy,
                       initial_cash=10_000, cost_bps=10)
    # 2 chiều x 1000$ x 0.1% = 2$
    assert sum(f.fee for f in res.fills) == pytest.approx(2.0)
    assert res.equity["equity"].iloc[-1] == pytest.approx(10_000 - 2.0)
    assert res.trades[0].exit_reason == "signal"
    assert res.trades[0].pnl == pytest.approx(-2.0)  # pnl đã trừ phí


def test_end_trade_includes_estimated_sell_fee():
    df = make_bars([100, 100, 100], [100, 100, 100])
    res = run_backtest({"AAA": df}, df.index[0], df.index[-1], buy_on_first_day(10),
                       initial_cash=10_000, cost_bps=10)
    t = res.trades[-1]
    assert t.exit_reason == "end"
    assert t.fee == pytest.approx(2.0)  # 1$ phí mua thật + 1$ phí bán ước tính
    assert res.equity["equity"].iloc[-1] == pytest.approx(10_000 - 1.0)


def test_partial_sell_splits_entry_fee():
    df = make_bars([100] * 4, [100] * 4)

    def policy(asof, view, pf):
        if asof == df.index[0]:
            return [OrderIntent("AAA", "buy", 10)]
        if asof == df.index[1]:
            return [OrderIntent("AAA", "sell", 4)]
        return []

    res = run_backtest({"AAA": df}, df.index[0], df.index[-1], policy,
                       initial_cash=10_000, cost_bps=10)
    part, rest = res.trades
    assert (part.qty, part.exit_reason, rest.qty) == (4, "signal", 6)
    assert part.fee == pytest.approx(0.4 + 0.4)
    assert rest.fee == pytest.approx(0.6 + 0.6)


def test_policy_never_sees_future():
    df = make_bars(list(range(10, 20)), list(range(10, 20)))
    seen = []

    def policy(asof, view, pf):
        bars = view.bars("AAA")
        seen.append((asof, bars.index.max(), len(bars)))
        assert bars.index.max() <= asof
        assert view.bars("KHONG_CO").empty
        return []

    run_backtest({"AAA": df}, df.index[3], df.index[-1], policy)
    # Bắt đầu từ ngày thứ 4 nhưng vẫn thấy dữ liệu khởi động trước đó
    assert seen[0] == (df.index[3], df.index[3], 4)
    assert all(last == asof for asof, last, _ in seen)


def test_buy_reduced_to_fit_cash():
    df = make_bars([100, 100, 100], [100, 100, 100])
    res = run_backtest({"AAA": df}, df.index[0], df.index[-1], buy_on_first_day(1000),
                       initial_cash=1000, cost_bps=10)
    # 1000 / (100 * 1.001) = 9.99 -> 9 cổ phiếu
    assert res.fills[0].qty == 9
    assert (res.equity["cash"] >= 0).all()


def test_sell_without_position_is_ignored():
    df = make_bars([10, 10, 10], [10, 10, 10])
    res = run_backtest({"AAA": df}, df.index[0], df.index[-1],
                       lambda a, v, p: [OrderIntent("AAA", "sell", 5)], initial_cash=100)
    assert res.fills == []  # không bán khống
    assert res.equity["equity"].iloc[-1] == 100


def run_bracket(day2, day1=(100, 101, 99, 100)):
    """Mua ở open ngày 1 (=100), SL 95, TP 110. day = (open, high, low, close)."""
    rows = [(100, 100, 100, 100), day1, day2, (100, 100, 100, 100)]
    o, h, low, c = (list(x) for x in zip(*rows))
    df = make_bars(o, c, highs=h, lows=low)
    res = run_backtest({"AAA": df}, df.index[0], df.index[-1],
                       buy_on_first_day(10, stop_loss=95, take_profit=110),
                       initial_cash=10_000, cost_bps=0)
    return df, res.trades[0]


def test_stop_loss_intraday():
    df, t = run_bracket((99, 100, 94, 96))
    assert (t.exit_reason, t.exit_price, t.exit_date) == ("stop_loss", 95, df.index[2])


def test_take_profit_intraday():
    _, t = run_bracket((101, 111, 100, 105))
    assert (t.exit_reason, t.exit_price) == ("take_profit", 110)


def test_sl_first_when_both_hit_same_day():
    _, t = run_bracket((100, 111, 94, 105))
    assert (t.exit_reason, t.exit_price) == ("stop_loss", 95)


def test_gap_down_fills_at_open():
    _, t = run_bracket((90, 92, 88, 91))
    assert (t.exit_reason, t.exit_price) == ("stop_loss", 90)


def test_gap_up_take_profit_fills_at_open():
    _, t = run_bracket((115, 116, 113, 114))
    assert (t.exit_reason, t.exit_price) == ("take_profit", 115)


def test_no_stop_check_on_entry_day():
    # Ngày vào lệnh low=90 < SL nhưng không tính; ngày sau bình thường -> giữ tới cuối
    _, t = run_bracket((100, 101, 99, 100), day1=(100, 101, 90, 100))
    assert t.exit_reason == "end"


def test_writes_to_store(tmp_path):
    from paper_bot.storage import Store
    df = make_bars([10, 11, 12], [10, 11, 12])
    store = Store(tmp_path / "bt.db", run_id="bt-test")
    run_backtest({"AAA": df}, df.index[0], df.index[-1], buy_on_first_day(1), store=store)
    n_eq = store.conn.execute("SELECT COUNT(*) FROM equity WHERE run_id='bt-test'").fetchone()[0]
    n_fill = store.conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    assert (n_eq, n_fill) == (3, 1)


def test_open_position_reported_at_end():
    df = make_bars([10, 11, 12], [10, 11, 12])
    res = run_backtest({"AAA": df}, df.index[0], df.index[-1], buy_on_first_day(1),
                       initial_cash=100, cost_bps=0)
    assert res.trades[-1].exit_reason == "end"
    assert res.trades[-1].pnl == pytest.approx(1.0)
