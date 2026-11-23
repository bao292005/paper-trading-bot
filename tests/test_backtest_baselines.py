"""Test baseline B0/B1/B2 với dữ liệu tổng hợp."""

import numpy as np
import pandas as pd
import pytest

from paper_bot.backtest import run_backtest
from paper_bot.backtest.baselines import make_b0, make_b1, make_b2


def bars_from_close(closes, start="2020-01-01"):
    """Nến với open = close hôm trước (không gap), high/low bao quanh."""
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    idx = pd.bdate_range(start, periods=len(c), name="date")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c),
                         "close": c, "volume": 1e6}, index=idx)


def test_b0_buys_once_and_holds():
    spy = bars_from_close(np.linspace(100, 150, 60))
    res = run_backtest({"SPY": spy}, spy.index[0], spy.index[-1], make_b0(),
                       initial_cash=10_000, cost_bps=10)
    assert len(res.fills) == 1 and res.fills[0].side == "buy"
    assert res.equity["exposure"].iloc[-1] > 0.98
    assert res.equity["equity"].iloc[-1] > 14_000  # giá tăng 50%


def test_b1_equal_weight_and_monthly_rebalance():
    n = 70  # ~3 tháng làm việc
    a = bars_from_close(np.linspace(100, 200, n))  # A tăng mạnh
    b = bars_from_close(np.full(n, 50.0))          # B đi ngang
    res = run_backtest({"A": a, "B": b}, a.index[0], a.index[-1], make_b1(["A", "B"]),
                       initial_cash=10_000, cost_bps=0)
    # Sau lần mua đầu: hai mã có giá trị gần bằng nhau
    first_day = res.fills[0].filled_at
    vals = {f.symbol: f.qty * f.price for f in res.fills if f.filled_at == first_day}
    assert vals["A"] == pytest.approx(vals["B"], rel=0.02)
    # Mỗi tháng chỉ khớp lệnh vào MỘT ngày (phiên thứ 2 của tháng)
    fill_days = sorted({pd.Timestamp(f.filled_at) for f in res.fills})
    months = [(d.year, d.month) for d in fill_days]
    n_months = len({(d.year, d.month) for d in a.index})  # = 4 (tháng 1 → 4)
    assert len(months) == len(set(months)) == n_months
    # A tăng -> lần cân bằng lại phải BÁN bớt A (bán một phần)
    assert any(f.symbol == "A" and f.side == "sell" for f in res.fills)


def test_b2_rsi_buy_sizing_then_sell():
    # Giảm đều 40 phiên (RSI=0 -> BUY) rồi tăng đều 40 phiên (RSI=100 -> SELL)
    closes = list(np.linspace(100, 80, 40)) + list(np.linspace(80.5, 100, 40))
    df = bars_from_close(closes)
    res = run_backtest({"AAA": df}, df.index[0], df.index[-1], make_b2(["AAA"]),
                       initial_cash=100_000, cost_bps=10)
    buy = res.fills[0]
    # Tín hiệu RSI đầu tiên ở phiên thứ 15 (cần 14 delta); sizing min(2%*100k, 1000)/close
    signal_day = df.index[14]
    assert buy.side == "buy"
    assert buy.qty == int(1000 / df.at[signal_day, "close"])
    assert pd.Timestamp(buy.filled_at) == df.index[15]  # khớp phiên kế tiếp
    # Không mua thêm khi đang nắm; cuối cùng bán toàn bộ theo tín hiệu
    assert sum(f.side == "buy" for f in res.fills) == 1
    assert res.trades[0].exit_reason == "signal"
    assert res.trades[0].qty == buy.qty


def _fills_key(res):
    return [(f.symbol, f.side, f.qty, f.price, pd.Timestamp(f.filled_at)) for f in res.fills]


def test_b2_fast_matches_original_synthetic():
    rng = np.random.default_rng(42)
    data = {f"S{i}": bars_from_close(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))))
            for i in range(5)}
    syms = list(data)
    idx = data["S0"].index
    slow = run_backtest(data, idx[0], idx[-1], make_b2(syms, fast=False))
    fast = run_backtest(data, idx[0], idx[-1], make_b2(syms, fast=True))
    assert len(slow.fills) > 20  # đủ nhiều lệnh để so sánh có ý nghĩa
    assert _fills_key(fast) == _fills_key(slow)


def test_b2_fast_matches_original_real_data():
    """Đối chiếu trên một đoạn dữ liệu thật nhỏ (giai đoạn phát triển). Thiếu cache thì skip."""
    from paper_bot.config import UNIVERSE_50
    from paper_bot.data.cache import load_bars
    syms = UNIVERSE_50[:10]
    data = {s: load_bars(s, start="2018-06-01", end="2019-12-31") for s in syms}
    if any(df.empty for df in data.values()):
        pytest.skip("chưa có cache data/bars")
    slow = run_backtest(data, "2019-01-01", "2019-12-31", make_b2(syms, fast=False))
    fast = run_backtest(data, "2019-01-01", "2019-12-31", make_b2(syms, fast=True))
    assert len(slow.fills) > 10
    assert _fills_key(fast) == _fills_key(slow)
    assert fast.equity["equity"].iloc[-1] == pytest.approx(slow.equity["equity"].iloc[-1])


def test_fast_ma_exact_tie_matches_pandas():
    """PFE 2023-08-04: MA10 == MA30 đúng bằng nhau -> bản gốc (pandas) không ra tín hiệu."""
    from paper_bot.backtest.baselines import _fast_ma
    from paper_bot.data.cache import load_bars
    from paper_bot.strategies import MovingAverageCrossoverStrategy
    asof = pd.Timestamp("2023-08-04")
    d = load_bars("PFE", start=asof - pd.Timedelta(days=99), end=asof)
    if d.empty:
        pytest.skip("chưa có cache data/bars")
    assert MovingAverageCrossoverStrategy().generate_signals(d, "PFE") == []
    assert _fast_ma(d["close"].to_numpy()) is None


def test_b2_uses_only_past_data():
    """Đổi dữ liệu TƯƠNG LAI không được làm đổi lệnh trong quá khứ."""
    closes = list(np.linspace(100, 80, 40)) + list(np.linspace(80.5, 100, 40))
    df1 = bars_from_close(closes)
    df2 = df1.copy()
    df2.iloc[20:, :4] = df2.iloc[20:, :4] * 3  # bóp méo từ phiên 21 trở đi
    end = df1.index[18]
    r1 = run_backtest({"AAA": df1}, df1.index[0], end, make_b2(["AAA"]))
    r2 = run_backtest({"AAA": df2}, df2.index[0], end, make_b2(["AAA"]))
    assert [(f.qty, f.price) for f in r1.fills] == [(f.qty, f.price) for f in r2.fills]
