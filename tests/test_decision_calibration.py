from datetime import datetime

import numpy as np
import pandas as pd

from paper_bot.contracts import Forecast, MarketState, Signal
from paper_bot.decision import calib_history
from paper_bot.decision.calibration import (
    brier, feature_names, fit_calibrator, make_label, reliability_table, signal_features,
)

from test_decision_base import make_bars


def sig(action="BUY", strategy="RSI", conf=0.7):
    return Signal("X", action, 100.0, datetime(2020, 1, 2), "r", conf, strategy=strategy)


def test_features_match_names_and_direction():
    bars = make_bars([100 + i for i in range(250)])  # tăng đều -> giá trên MA
    st = MarketState("X", bars.index[-1], bars, regime="bull_calm")
    fb = signal_features(sig("BUY"), st)
    fs = signal_features(sig("SELL"), st)
    assert set(fb) == set(feature_names(False))
    assert fb["dist_ma200_dir"] > 0 and fs["dist_ma200_dir"] < 0  # đảo dấu theo hướng
    assert fb["regime_bull_calm"] == 1 and fb["strat_RSI"] == 1


def test_features_with_forecast_optional():
    bars = make_bars([100.0] * 70)
    fc = Forecast("naive", 5, {0.1: -0.02, 0.5: 0.01, 0.9: 0.03})
    st = MarketState("X", bars.index[-1], bars, forecast=fc)
    f = signal_features(sig("SELL"), st, use_forecast=True)
    assert set(f) == set(feature_names(True))
    assert f["fc_median_dir"] == -0.01 and abs(f["fc_width"] - 0.05) < 1e-12
    st.forecast = None
    assert signal_features(sig(), st, use_forecast=True)["has_fc"] == 0


def test_make_label():
    close = pd.Series([100.0, 100, 100, 100, 100, 101, 100])
    assert make_label(close, 0, "BUY") == 1     # +1% > 0.2%
    assert make_label(close, 0, "SELL") == 0
    assert make_label(close, 1, "BUY") == 0     # 0% không bù phí
    assert make_label(close, 2, "BUY") is None  # chưa có t+5


def test_fit_calibrator_learns_signal():
    rng = np.random.default_rng(0)
    n = 3000
    rows = pd.DataFrame({c: 0.0 for c in feature_names(False)}, index=range(n))
    rows["rsi_dir"] = rng.normal(size=n)
    rows["y"] = (rng.random(n) < 1 / (1 + np.exp(-2 * rows["rsi_dir"]))).astype(int)
    cal = fit_calibrator(rows)
    p = cal.predict_rows(rows)
    assert brier(p, rows["y"]) < brier(np.full(n, rows["y"].mean()), rows["y"])
    # predict_prob trên 1 tín hiệu thật trả số trong [0, 1]
    bars = make_bars([100.0 - i * 0.1 for i in range(100)])
    prob = cal.predict_prob(sig(), MarketState("X", bars.index[-1], bars))
    assert 0 <= prob <= 1


def test_brier_and_reliability():
    assert brier([1, 0], [1, 0]) == 0 and brier([0.5], [1]) == 0.25
    t = reliability_table([0.05, 0.15, 0.95, 1.0], [0, 0, 1, 1])
    assert len(t) == 10 and t["n"].sum() == 4 and t.loc[9, "n"] == 2


def test_walk_forward_windows():
    w = calib_history.walk_forward_windows()
    assert len(w) == 12
    assert w[0][0] == pd.Timestamp("2016-01-01") and w[0][1] == pd.Timestamp("2019-01-01")
    assert w[-1][1] == pd.Timestamp("2024-07-01")


def test_build_table_no_holdout_and_labels(monkeypatch):
    # dữ liệu giả: dao động hình sin -> có cả tín hiệu MA, RSI, Bollinger
    idx = pd.bdate_range("2023-06-01", "2025-06-30", name="date")
    c = pd.Series(100 + 10 * np.sin(np.arange(len(idx)) / 8), index=idx)
    fake = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1e6})

    def fake_load(symbol, start=None, end=None, bars_dir=None):
        return fake[fake.index <= pd.Timestamp(end)] if end else fake

    monkeypatch.setattr(calib_history, "load_bars", fake_load)
    table = calib_history.build_signal_table(["AAA"], start="2024-01-01")
    assert len(table) > 50
    assert table["label_date"].max() <= pd.Timestamp("2024-12-31")
    assert table["date"].min() >= pd.Timestamp("2024-01-01")
    assert set(table["strategy"]) == {"MA_Crossover", "RSI", "BollingerBands"}
