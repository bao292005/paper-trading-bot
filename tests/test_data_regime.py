"""Test offline cho features và regime (dữ liệu tự tạo)."""

import numpy as np
import pandas as pd

from paper_bot.contracts import REGIMES
from paper_bot.data.features import atr_series, compute_features, vol_series
from paper_bot.data.regime import detect_regime, regime_series


def make_bars(closes, spread=1.0):
    idx = pd.bdate_range("2020-01-01", periods=len(closes), name="date")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c + spread / 2, "low": c - spread / 2,
                         "close": c, "volume": 1e6}, index=idx)


def test_atr_gia_co_dinh():
    bars = make_bars([100.0] * 30, spread=2.0)  # giá phẳng, high-low = 2 -> ATR = 2
    atr = atr_series(bars, 14)
    assert atr.iloc[:14].isna().all()  # chưa đủ 14 ngày (ngày đầu thiếu prev_close)
    assert np.isclose(atr.iloc[-1], 2.0)


def test_vol20_khop_tinh_tay():
    rng = np.random.default_rng(0)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 60)))
    bars = make_bars(closes)
    expected = np.log(closes[-21:][1:] / closes[-21:][:-1]).std(ddof=1)
    assert np.isclose(vol_series(bars, 20).iloc[-1], expected)


def test_compute_features_thieu_du_lieu_thi_bo():
    assert compute_features(make_bars([100.0] * 5)) == {}  # chưa đủ cho chỉ báo nào dài hạn
    feats = compute_features(make_bars(np.linspace(100, 120, 80)))
    assert {"atr14", "vol20", "rsi14", "ret5", "ret20", "dist_ma50"} <= set(feats)


def trending_spy(n=600, drift=0.0005, seed=1):
    rng = np.random.default_rng(seed)
    return make_bars(100 * np.exp(np.cumsum(rng.normal(drift, 0.008, n))))


def test_regime_unknown_dau_chuoi_va_nhan_hop_le():
    labels = regime_series(trending_spy())
    assert (labels.iloc[:250] == "unknown").all()  # cần 200 ngày MA + 252 ngày median vol
    assert set(labels) <= set(REGIMES)
    assert labels.iloc[-1] != "unknown"


def test_regime_bull_va_bear():
    up = np.concatenate([np.linspace(100, 200, 400), np.linspace(200, 100, 200)])
    labels = regime_series(make_bars(up))
    assert labels.iloc[399].startswith("bull")   # đỉnh, giá trên MA200
    assert labels.iloc[-1].startswith("bear")    # giảm sâu, giá dưới MA200


def test_regime_volatile_khi_vol_tang_manh():
    rng = np.random.default_rng(2)
    r = np.concatenate([rng.normal(0.0005, 0.005, 500), rng.normal(0.0005, 0.03, 25)])
    labels = regime_series(make_bars(100 * np.exp(np.cumsum(r))))
    assert labels.iloc[-1].endswith("volatile")
    assert labels.iloc[400].endswith("calm") or labels.iloc[400].endswith("volatile")


def test_regime_khong_ro_ri_tuong_lai():
    spy = trending_spy()
    asof = spy.index[400]
    before = detect_regime(spy, asof)
    full_labels = regime_series(spy)
    # Phá hỏng toàn bộ dữ liệu SAU asof: nhãn tại asof không được đổi
    broken = spy.copy()
    broken.loc[broken.index > asof, ["open", "high", "low", "close"]] = 1.0
    assert regime_series(broken).loc[asof] == full_labels.loc[asof] == before


def test_detect_regime_rong_hoac_asof_som():
    spy = trending_spy()
    assert detect_regime(spy.iloc[0:0], "2024-01-01") == "unknown"
    assert detect_regime(spy, spy.index[10]) == "unknown"
