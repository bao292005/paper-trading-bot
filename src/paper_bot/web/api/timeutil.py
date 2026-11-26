"""Hàm thời gian / mã dùng chung cho API web.

Quy ước thời gian gửi cho biểu đồ (lightweight-charts): `time` = unix GIÂY (UTC).
  - nến phút/giờ: thời điểm bắt đầu nến (giờ New York đổi sang UTC thật);
  - nến ngày (1D): 12:00 UTC của ngày đó (để mọi múi giờ đều hiện đúng ngày).
"""

import re
from typing import List, Optional

import pandas as pd
from fastapi import HTTPException

NY = "America/New_York"
SYMBOL_RE = re.compile(r"[A-Z]{1,6}")  # fullmatch, không dùng $ (vì $ cho phép "\n" ở cuối)
TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def check_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
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


def index_to_unix(index: pd.DatetimeIndex, tf: str) -> List[int]:
    return [day_to_unix(t) if tf == "1D" else ny_to_unix(t) for t in index]


def parse_time(value: Optional[str]) -> Optional[pd.Timestamp]:
    """Tham số thời gian từ URL -> giờ NY không timezone. Nhận 'YYYY-MM-DD', ISO, hoặc unix giây."""
    if value is None or value == "":
        return None
    try:
        if value.isdigit():
            return pd.Timestamp(int(value), unit="s", tz="UTC").tz_convert(NY).tz_localize(None)
        ts = pd.Timestamp(value)
    except (ValueError, OverflowError):
        raise HTTPException(400, f"thời gian không hợp lệ: {value!r}") from None
    return ts.tz_convert(NY).tz_localize(None) if ts.tzinfo is not None else ts


def snap_to_bar(ts: pd.Timestamp, tf: str) -> int:
    """Đưa một thời điểm (giờ NY không tz) về `time` của nến chứa nó trên biểu đồ khung tf.

    Ngoài giờ giao dịch thì dồn vào nến đầu/cuối của phiên. Ngày không có giờ (00:00,
    vd tín hiệu backtest theo ngày) coi như cuối ngày (15:59).
    """
    ts = pd.Timestamp(ts)
    if tf == "1D":
        return day_to_unix(ts)
    day = ts.normalize()
    if ts == day:
        ts = day + pd.Timedelta(hours=15, minutes=59)
    minutes = int((ts - day - pd.Timedelta(hours=9, minutes=30)) // pd.Timedelta(minutes=1))
    minutes = min(max(minutes, 0), 389)
    step = TF_MINUTES[tf]
    start = day + pd.Timedelta(hours=9, minutes=30) + pd.Timedelta(minutes=minutes // step * step)
    return ny_to_unix(start)
