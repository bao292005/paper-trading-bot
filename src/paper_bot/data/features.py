"""Chỉ báo từ nến ngày. Mọi hàm dùng rolling/shift về quá khứ nên tại ngày t chỉ thấy dữ liệu <= t."""

from typing import Dict

import numpy as np
import pandas as pd


def atr_series(bars: pd.DataFrame, window: int = 14) -> pd.Series:
    """ATR = trung bình `window` ngày của True Range (biên độ thực, tính cả gap qua đêm)."""
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"] - prev_close).abs(),
    ], axis=1).max(axis=1, skipna=False)  # ngày đầu thiếu prev_close -> NaN
    return tr.rolling(window).mean()


def vol_series(bars: pd.DataFrame, window: int = 20) -> pd.Series:
    """Độ lệch chuẩn lợi suất log `window` ngày (chưa nhân căn 252)."""
    log_ret = np.log(bars["close"]).diff()
    return log_ret.rolling(window).std()


def rsi_series(bars: pd.DataFrame, window: int = 14) -> pd.Series:
    """RSI kiểu trung bình đơn giản (giống bài gốc)."""
    delta = bars["close"].diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss  # loss = 0 và gain > 0 -> inf -> RSI = 100; cả hai = 0 -> NaN
    return 100 - 100 / (1 + rs)


def compute_features(bars: pd.DataFrame) -> Dict[str, float]:
    """Giá trị chỉ báo ở dòng CUỐI của bars (caller phải cắt bars <= asof trước).

    Thiếu dữ liệu thì chỉ báo đó bị bỏ khỏi dict (không điền số giả).
    """
    if bars.empty:
        return {}
    close = bars["close"]
    series = {
        "atr14": atr_series(bars, 14),
        "vol20": vol_series(bars, 20),
        "rsi14": rsi_series(bars, 14),
        "ret5": np.log(close).diff(5),    # lợi suất log 5 ngày
        "ret20": np.log(close).diff(20),  # lợi suất log 20 ngày
        "dist_ma50": close / close.rolling(50).mean() - 1,  # cách MA50 bao nhiêu %
    }
    out = {}
    for name, s in series.items():
        value = s.iloc[-1]
        if pd.notna(value) and np.isfinite(value):
            out[name] = float(value)
    return out
