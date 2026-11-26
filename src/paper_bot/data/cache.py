"""Cache nến ngày dạng parquet, định dạng chung cho cả nhóm.

File: data/bars/<SYMBOL>.parquet
  - index: DatetimeIndex tên "date" (ngày, KHÔNG timezone)
  - cột: open, high, low, close, volume (float), giá đã điều chỉnh split + cổ tức
"""

from pathlib import Path
from typing import Optional, Union

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]
# Thư mục gốc project: .../src/paper_bot/data/cache.py -> lên 3 cấp
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BARS_DIR = PROJECT_ROOT / "data" / "bars"
# Crypto: data/bars_crypto/BTC-USD.parquet, index = ngày UTC (nến chốt 00:00 UTC), không timezone
CRYPTO_BARS_DIR = PROJECT_ROOT / "data" / "bars_crypto"
# Múi giờ dùng để lấy "ngày" của nến ngày
DAY_TZ = {"stock": "America/New_York", "crypto": "UTC"}


def normalize_bars(df: pd.DataFrame, tz: str = "America/New_York") -> pd.DataFrame:
    """Đưa DataFrame nến về định dạng chung (index ngày không timezone, 5 cột float).

    Chấp nhận df từ Alpaca: MultiIndex (symbol, timestamp) hoặc index timestamp có timezone.
    tz: múi giờ để lấy ngày (cổ phiếu: New York; crypto: "UTC").
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], name="date"), dtype=float)
    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel("symbol")  # bỏ tầng symbol, chỉ còn timestamp
    out = df[COLUMNS].astype(float).copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        # Cổ phiếu: nến ngày Alpaca có giờ 04:00/05:00 UTC = 00:00 giờ New York -> đổi sang NY rồi lấy ngày.
        # Crypto: nến chốt 00:00 UTC -> giữ UTC.
        idx = idx.tz_convert(tz).tz_localize(None)
    out.index = pd.DatetimeIndex(idx.normalize(), name="date")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def symbol_filename(symbol: str) -> str:
    """Tên file của mã: "BTC/USD" -> "BTC-USD" (không được có "/" trong tên file)."""
    return symbol.upper().replace("/", "-")


def bars_path(symbol: str, bars_dir: Optional[Path] = None, asset: str = "stock") -> Path:
    default = CRYPTO_BARS_DIR if asset == "crypto" else BARS_DIR
    return Path(bars_dir or default) / f"{symbol_filename(symbol)}.parquet"


def save_bars(symbol: str, df: pd.DataFrame, bars_dir: Optional[Path] = None,
              asset: str = "stock") -> Path:
    """Ghi nến của 1 mã (ghi đè file cũ)."""
    path = bars_path(symbol, bars_dir, asset)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalize_bars(df, tz=DAY_TZ[asset]).to_parquet(path)
    return path


def missing_days(df: pd.DataFrame) -> int:
    """Crypto giao dịch 24/7: số ngày lịch thiếu nến giữa ngày đầu và ngày cuối (KHÔNG nội suy)."""
    if df.empty:
        return 0
    full = pd.date_range(df.index[0], df.index[-1], freq="D")
    return int(len(full) - df.index.nunique())


def load_crypto_bars(symbol: str, start=None, end=None, bars_dir: Optional[Path] = None) -> pd.DataFrame:
    """Nến ngày crypto (vd "BTC/USD") từ data/bars_crypto/. Ngày thiếu vẫn thiếu, không nội suy."""
    return load_bars(symbol, start, end, bars_dir, asset="crypto")


def load_bars(
    symbol: str,
    start: Optional[Union[str, pd.Timestamp]] = None,
    end: Optional[Union[str, pd.Timestamp]] = None,
    bars_dir: Optional[Path] = None,
    asset: str = "stock",
) -> pd.DataFrame:
    """Đọc nến từ cache, cắt theo [start, end] (gồm cả hai đầu). Chưa có cache -> DataFrame rỗng.

    asset="crypto" -> đọc data/bars_crypto/ (mặc định "stock" giữ nguyên hành vi cũ).
    """
    path = bars_path(symbol, bars_dir, asset)
    if not path.exists():
        return normalize_bars(None)
    df = pd.read_parquet(path)
    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end)]
    return df
