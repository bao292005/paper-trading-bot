"""Nhận diện bối cảnh thị trường (regime) từ SPY.

  - Xu hướng: close > MA200 -> bull, ngược lại bear
  - Biến động: vol20 > median(vol20 của 252 ngày TRƯỚC đó) -> volatile, ngược lại calm
  - Thiếu dữ liệu (chưa đủ 200/252 ngày) -> "unknown"
"""

import numpy as np
import pandas as pd

from ..contracts import REGIMES
from .features import vol_series

TREND_WINDOW = 200
VOL_LOOKBACK = 252


def regime_series(spy_bars: pd.DataFrame, vol_lookback: int = VOL_LOOKBACK) -> pd.Series:
    """Nhãn regime cho từng ngày. Chỉ dùng rolling về quá khứ nên không nhìn tương lai.
    vol_lookback: 252 (cổ phiếu) | 365 (crypto, BTC/USD làm benchmark)."""
    close = spy_bars["close"]
    ma200 = close.rolling(TREND_WINDOW).mean()
    vol20 = vol_series(spy_bars, 20)
    # shift(1): median của vol_lookback ngày trước hôm nay, không gồm hôm nay
    vol_median = vol20.shift(1).rolling(vol_lookback).median()

    trend = np.where(close > ma200, "bull", "bear")
    vol = np.where(vol20 > vol_median, "volatile", "calm")
    labels = pd.Series(trend + "_" + vol, index=spy_bars.index, dtype=object)
    missing = ma200.isna() | vol20.isna() | vol_median.isna()
    labels[missing] = "unknown"
    assert set(labels.unique()) <= set(REGIMES)
    return labels


def detect_regime(spy_bars: pd.DataFrame, asof, vol_lookback: int = VOL_LOOKBACK) -> str:
    """Regime tại ngày `asof` (chỉ dùng nến <= asof)."""
    bars = spy_bars[spy_bars.index <= pd.Timestamp(asof)]
    if bars.empty:
        return "unknown"
    return str(regime_series(bars, vol_lookback).iloc[-1])
