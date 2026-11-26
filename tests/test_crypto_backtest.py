"""Test BƯỚC 2 crypto: mua lẻ + phí trừ bằng coin, walk-forward train mở rộng, Sharpe 365,
baseline C0/C1/C2, RiskConfig theo asset, regime vol 365, tester asset."""

import math

import numpy as np
import pandas as pd
import pytest

from paper_bot.backtest.baselines import make_b0, make_b1, make_b2
from paper_bot.backtest.engine import run_backtest
from paper_bot.backtest.metrics import sharpe
from paper_bot.backtest.tester.run import normalize_config
from paper_bot.backtest.walkforward import HoldoutError, make_windows, run_walkforward
from paper_bot.config import asset_profile
from paper_bot.contracts import OrderIntent
from paper_bot.data.regime import regime_series
from paper_bot.risk import RiskConfig, floor_qty, legacy_position_size


def daily(start="2021-01-01", end="2024-12-31", price=100.0, drift=0.0003):
    """Nến crypto 24/7 (mỗi ngày lịch), open = close."""
    idx = pd.date_range(start, end, freq="D", name="date")
    c = price * np.exp(np.cumsum(np.full(len(idx), drift)))
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": 1e3}, index=idx)


def test_floor_qty():
    assert floor_qty(3.9) == 3
    assert floor_qty(0.1234567, 6) == 0.123456
    assert floor_qty(0.3, 6) == 0.3  # không bị sai số 0.299999


def test_fractional_buy_fee_in_coin_then_sell():
    flat = daily("2022-01-01", "2022-01-10", price=100.0, drift=0.0)
    sent = {"done": False}

    def policy(asof, view, pf):
        if sent["done"]:
            return []
        sent["done"] = True
        return [OrderIntent("BTC/USD", "buy", 5.5, asof=asof)]

    res = run_backtest({"BTC/USD": flat}, "2022-01-01", "2022-01-10", policy, initial_cash=1000,
                       cost_bps=25, qty_decimals=6)
    buy = res.fills[0]
    assert buy.qty == 5.5 and math.isclose(buy.fee, 5.5 * 0.0025 * 100, rel_tol=1e-6)
    eq = res.equity["equity"]
    assert math.isclose(res.equity["cash"].iloc[-1], 1000 - 550)  # trả đủ USD, phí trừ coin
    assert math.isclose(eq.iloc[-1], 450 + floor_qty(5.5 * 0.9975, 6) * 100, rel_tol=1e-9)


def test_crypto_windows_expanding():
    p = asset_profile("crypto")
    ws = make_windows(p["dev_start"], p["dev_end"], train_years=None, train_start=p["warmup_start"])
    assert len(ws) == 6
    assert all(w.train_start == pd.Timestamp("2021-01-01") for w in ws)
    assert (ws[0].test_start, ws[0].test_end) == (pd.Timestamp("2022-01-01"), pd.Timestamp("2022-06-30"))
    assert ws[-1].test_end == pd.Timestamp("2024-12-31")
    with pytest.raises(HoldoutError):
        run_walkforward(lambda tb: make_b0("BTC/USD"), {"BTC/USD": daily(end="2025-03-01")},
                        end="2025-01-01", asset="crypto")


def test_walkforward_crypto_runs_fractional():
    bars = {"BTC/USD": daily(price=30000)}
    wf = run_walkforward(lambda tb: make_b0("BTC/USD", qty_decimals=6), bars, asset="crypto",
                         cost_bps=25)
    assert wf.result.equity.index[0] == pd.Timestamp("2022-01-01")
    assert wf.result.equity.index[-1] == pd.Timestamp("2024-12-31")
    assert wf.result.fills[0].qty != int(wf.result.fills[0].qty)  # mua lẻ
    assert len(wf.per_window) == 6


def test_sharpe_365():
    r = pd.Series([0.01, -0.005, 0.002, 0.007])
    assert math.isclose(sharpe(r, 365) / sharpe(r, 252), math.sqrt(365 / 252))


def test_c1_keeps_cash_for_missing_symbol():
    a = daily("2022-01-01", "2022-01-20")
    b = daily("2022-01-05", "2022-01-20")  # ngày đầu tháng chưa có nến
    res = run_backtest({"A/USD": a, "B/USD": b}, "2022-01-01", "2022-01-20",
                       make_b1(["A/USD", "B/USD"], qty_decimals=6), initial_cash=10_000,
                       cost_bps=25, qty_decimals=6)
    assert {f.symbol for f in res.fills} == {"A/USD"}
    assert 4900 < res.fills[0].qty * res.fills[0].price < 5000  # equity/2, không dồn cả cho A


def test_c2_fractional_size_and_stock_unchanged():
    assert legacy_position_size(100_000, 50_000, qty_decimals=6) == 0.02  # 1000$/50000
    assert legacy_position_size(100_000, 50_000) == 1  # cổ phiếu: tối thiểu 1 cổ như cũ
    up_down = daily("2021-06-01", "2022-06-30", price=40000, drift=0.002)
    up_down.iloc[-120:, :4] = up_down.iloc[-120:, :4].mul(np.linspace(1, 0.6, 120), axis=0)
    res = run_backtest({"BTC/USD": up_down}, "2022-01-01", "2022-06-30",
                       make_b2(["BTC/USD"], qty_decimals=6), cost_bps=25, qty_decimals=6)
    assert all(f.qty < 1 for f in res.fills)


def test_risk_config_for_asset():
    assert RiskConfig.for_asset("stock") == RiskConfig()
    c = RiskConfig.for_asset("crypto")
    assert (c.max_pair_corr, c.max_positions, c.max_position_pct, c.qty_decimals) == (0.9, 5, 0.2, 6)


def test_regime_vol_lookback():
    bars = daily(drift=0.001)
    n252 = (regime_series(bars) == "unknown").sum()
    n365 = (regime_series(bars, 365) == "unknown").sum()
    assert n365 == n252 + (365 - 252)


def test_tester_crypto_defaults_and_holdout():
    cfg = normalize_config({"symbols": ["btc/usd"], "asset": "crypto"})
    assert (cfg["from"], cfg["to"], cfg["cost_bps"]) == ("2022-01-01", "2024-12-31", 25.0)
    assert cfg["symbols"] == ["BTC/USD"]
    assert normalize_config({"symbols": ["AAPL"]})["cost_bps"] == 10.0
    with pytest.raises(HoldoutError):
        normalize_config({"symbols": ["BTC/USD"], "asset": "crypto", "to": "2025-02-01"})
    with pytest.raises(ValueError):
        normalize_config({"symbols": ["X"], "asset": "forex"})
