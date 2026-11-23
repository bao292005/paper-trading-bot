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


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Đưa DataFrame nến về định dạng chung (index ngày không timezone, 5 cột float).

    Chấp nhận df từ Alpaca: MultiIndex (symbol, timestamp) hoặc index timestamp có timezone.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], name="date"), dtype=float)
    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel("symbol")  # bỏ tầng symbol, chỉ còn timestamp
    out = df[COLUMNS].astype(float).copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        # Nến ngày của Alpaca có giờ 04:00/05:00 UTC = 00:00 giờ New York -> đổi sang NY rồi lấy ngày
        idx = idx.tz_convert("America/New_York").tz_localize(None)
    out.index = pd.DatetimeIndex(idx.normalize(), name="date")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def bars_path(symbol: str, bars_dir: Optional[Path] = None) -> Path:
    return Path(bars_dir or BARS_DIR) / f"{symbol.upper()}.parquet"


def save_bars(symbol: str, df: pd.DataFrame, bars_dir: Optional[Path] = None) -> Path:
    """Ghi nến của 1 mã (ghi đè file cũ)."""
    path = bars_path(symbol, bars_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalize_bars(df).to_parquet(path)
    return path


def load_bars(
    symbol: str,
    start: Optional[Union[str, pd.Timestamp]] = None,
    end: Optional[Union[str, pd.Timestamp]] = None,
    bars_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Đọc nến từ cache, cắt theo [start, end] (gồm cả hai đầu). Chưa có cache -> DataFrame rỗng."""
    path = bars_path(symbol, bars_dir)
    if not path.exists():
        return normalize_bars(None)
    df = pd.read_parquet(path)
    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end)]
    return df
