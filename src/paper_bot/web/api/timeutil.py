"""Hàm thời gian / mã dùng chung cho API web.

Quy ước thời gian gửi cho biểu đồ (lightweight-charts): `time` = unix GIÂY (UTC).
  - nến phút/giờ: thời điểm bắt đầu nến (giờ New York đổi sang UTC thật);
  - nến ngày (1D): 12:00 UTC của ngày đó (để mọi múi giờ đều hiện đúng ngày).
"""

import re
from typing import List, Optional

import pandas as pd
from fastapi import HTTPException

from ...config import CRYPTO_UNIVERSE

NY = "America/New_York"
SYMBOL_RE = re.compile(r"[A-Z]{1,6}")  # fullmatch, không dùng $ (vì $ cho phép "\n" ở cuối)
TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def check_asset(asset: str) -> str:
    if asset not in ("stock", "crypto"):
        raise HTTPException(400, "asset phải là stock hoặc crypto")
    return asset


def check_symbol(symbol: str, asset: str = "stock") -> str:
    s = (symbol or "").strip().upper()
    if check_asset(asset) == "crypto":  # chỉ nhận đúng 5 mã trong whitelist, dạng "BTC/USD"
        if s not in CRYPTO_UNIVERSE:
            raise HTTPException(400, f"mã crypto không hợp lệ: {symbol!r} (chỉ {', '.join(CRYPTO_UNIVERSE)})")
        return s
    if not SYMBOL_RE.fullmatch(s):
        raise HTTPException(400, f"mã không hợp lệ: {symbol!r} (chữ A-Z, tối đa 6 ký tự)")
    return s


def unix_seconds(ts: pd.Timestamp) -> int:
    """Timestamp CÓ timezone -> unix giây."""
    return int((ts - _EPOCH) // pd.Timedelta(seconds=1))


def ny_to_unix(ts) -> int:
    """Giờ New York không timezone -> unix giây."""
    return unix_seconds(pd.Timestamp(ts).tz_localize(NY))


def day_to_unix(day) -> int:
    return unix_seconds(pd.Timestamp(day).normalize().tz_localize("UTC") + pd.Timedelta(hours=12))


def utc_to_unix(ts) -> int:
    """Giờ UTC không timezone -> unix giây (crypto: index nến là UTC naive)."""
    return unix_seconds(pd.Timestamp(ts).tz_localize("UTC"))


def local_tz(asset: str = "stock") -> str:
    """Múi giờ của index nến / tham số URL: stock = New York, crypto = UTC."""
    return "UTC" if asset == "crypto" else NY


def index_to_unix(index: pd.DatetimeIndex, tf: str, asset: str = "stock") -> List[int]:
    if tf == "1D":
        return [day_to_unix(t) for t in index]
    return [utc_to_unix(t) if asset == "crypto" else ny_to_unix(t) for t in index]


def parse_time(value: Optional[str], asset: str = "stock") -> Optional[pd.Timestamp]:
    """Tham số thời gian từ URL -> giờ địa phương của asset (stock: NY, crypto: UTC) không timezone.
    Nhận 'YYYY-MM-DD', ISO, hoặc unix giây."""
    if value is None or value == "":
        return None
    tz = local_tz(asset)
    try:
        if value.isdigit():
            return pd.Timestamp(int(value), unit="s", tz="UTC").tz_convert(tz).tz_localize(None)
        ts = pd.Timestamp(value)
    except (ValueError, OverflowError):
        raise HTTPException(400, f"thời gian không hợp lệ: {value!r}") from None
    return ts.tz_convert(tz).tz_localize(None) if ts.tzinfo is not None else ts


def snap_to_bar(ts: pd.Timestamp, tf: str, asset: str = "stock") -> int:
    """Đưa một thời điểm (giờ địa phương của asset, không tz) về `time` của nến chứa nó trên biểu đồ khung tf.

    Cổ phiếu: ngoài giờ giao dịch thì dồn vào nến đầu/cuối của phiên. Crypto: 24/7, nến bắt đầu ở mốc tròn UTC.
    Ngày không có giờ (00:00, vd tín hiệu backtest theo ngày) coi như cuối ngày (15:59 / 23:59).
    """
    ts = pd.Timestamp(ts)
    if tf == "1D":
        return day_to_unix(ts)
    day = ts.normalize()
    if asset == "crypto":
        if ts == day:
            ts = day + pd.Timedelta(hours=23, minutes=59)
        step = TF_MINUTES[tf]
        minutes = int((ts - day) // pd.Timedelta(minutes=1))
        return utc_to_unix(day + pd.Timedelta(minutes=minutes // step * step))
    if ts == day:
        ts = day + pd.Timedelta(hours=15, minutes=59)
    minutes = int((ts - day - pd.Timedelta(hours=9, minutes=30)) // pd.Timedelta(minutes=1))
    minutes = min(max(minutes, 0), 389)
    step = TF_MINUTES[tf]
    start = day + pd.Timedelta(hours=9, minutes=30) + pd.Timedelta(minutes=minutes // step * step)
    return ny_to_unix(start)
