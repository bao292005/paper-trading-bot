"""Nến phút / giờ / ngày cho web app biểu đồ.

Đầu ra của load_intraday: cột open/high/low/close/volume (float), index DatetimeIndex
giờ New York, KHÔNG timezone, chỉ phiên chính 09:30-16:00 (nến phút cuối là 15:59).
Với tf "1D" index là ngày (00:00) lấy từ cache ngày data/bars.

Cache nến phút: data/bars_1m/<SYMBOL>/<YYYY-MM>.parquet (đã lọc phiên chính, giờ NY naive).
  - Tháng đã qua mà có file -> không tải lại.
  - Tháng hiện tại -> luôn tải phần thiếu (từ nến cuối đã cache trở đi).
"""

import logging
from datetime import datetime, time
from pathlib import Path
from typing import Optional

import pandas as pd

from .cache import COLUMNS, PROJECT_ROOT, load_bars

logger = logging.getLogger("paper_bot")

NY = "America/New_York"
MINUTE_DIR = PROJECT_ROOT / "data" / "bars_1m"
TIMEFRAMES = ("1m", "5m", "15m", "1h", "1D")
_RULES = {"5m": "5min", "15m": "15min", "1h": "60min"}
_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], name="time"), dtype=float)


def regular_session(df: pd.DataFrame) -> pd.DataFrame:
    """Giữ nến phiên chính (bắt đầu từ 09:30 đến 15:59, thứ 2-6). Bỏ pre/after-market."""
    if df.empty:
        return df
    df = df.between_time("09:30", "15:59")
    return df[df.index.day_of_week < 5]


def resample_bars(df_1m: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Gộp nến phút lên tf. Mốc bắt đầu mỗi ngày là 09:30 (1h -> 09:30, 10:30, ... 15:30)."""
    if tf == "1m":
        return df_1m
    if tf not in _RULES:
        raise ValueError(f"tf không hợp lệ cho resample: {tf}")
    if df_1m.empty:
        return _empty()
    out = df_1m.resample(_RULES[tf], origin="start_day", offset="9h30min",
                         label="left", closed="left").agg(_AGG)
    out = out.dropna(subset=["open"])  # bỏ khung không có nến nào
    out.index.name = "time"
    return out


def _to_ny_naive(df: pd.DataFrame) -> pd.DataFrame:
    """Df Alpaca (MultiIndex symbol,timestamp hoặc index UTC) -> index giờ NY không timezone."""
    if df is None or df.empty:
        return _empty()
    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel("symbol")
    out = df[COLUMNS].astype(float).copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_convert(NY).tz_localize(None)
    out.index = pd.DatetimeIndex(idx, name="time")
    return out[~out.index.duplicated(keep="last")].sort_index()


def fetch_minute_bars(client, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> Optional[pd.DataFrame]:
    """Tải nến phút [start, end] (giờ NY naive) từ Alpaca: thử SIP trước, lỗi thì IEX.

    Trả None nếu cả hai feed đều lỗi (khác với DataFrame rỗng = không có dữ liệu).
    """
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    for feed in (DataFeed.SIP, DataFeed.IEX):
        try:
            request = StockBarsRequest(
                symbol_or_symbols=symbol, timeframe=TimeFrame.Minute,
                start=start.tz_localize(NY).to_pydatetime(), end=end.tz_localize(NY).to_pydatetime(),
                adjustment=Adjustment.ALL, feed=feed)
            return regular_session(_to_ny_naive(client.get_stock_bars(request).df))
        except Exception as e:
            logger.warning(f"Lỗi tải nến phút {symbol} feed {feed.value}: {e}")
    return None


def _now_ny() -> pd.Timestamp:
    return pd.Timestamp.now(tz=NY).tz_localize(None)


def _up_to_date(last_ts: pd.Timestamp, now: pd.Timestamp) -> bool:
    """Cache tháng hiện tại đã đủ chưa? Đang trong phiên thì luôn coi là chưa đủ."""
    if now.weekday() < 5 and time(9, 30) <= now.time() < time(16, 0):
        return False
    day = now.normalize()
    if now.weekday() >= 5 or now.time() < time(9, 30):
        day -= pd.Timedelta(days=1)  # chưa có phiên hôm nay -> lấy phiên trước
    while day.weekday() >= 5:
        day -= pd.Timedelta(days=1)
    return last_ts.normalize() >= day  # (ngày lễ sẽ bị tải lại mỗi lần, chấp nhận)


def _ensure_month(symbol, period, client_fn, minute_dir, now) -> pd.DataFrame:
    """Đọc cache tháng, tải phần thiếu nếu cần, ghi lại cache. Trả df của tháng đó."""
    path = Path(minute_dir) / symbol / f"{period}.parquet"
    cached = pd.read_parquet(path) if path.exists() else None
    is_current = period == now.to_period("M")
    if cached is not None and not is_current:
        return cached  # tháng đã qua: không tải lại
    if cached is not None and not cached.empty and _up_to_date(cached.index[-1], now):
        return cached
    start = period.start_time if cached is None or cached.empty else cached.index[-1] + pd.Timedelta(minutes=1)
    end = min(period.end_time.normalize() + pd.Timedelta(days=1), now)
    new = fetch_minute_bars(client_fn(), symbol, start, end)
    if new is None:  # lỗi mạng/key: dùng tạm cache cũ, không ghi gì
        return cached if cached is not None else _empty()
    df = new if cached is None else pd.concat([cached, new])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


def _make_client():
    from alpaca.data.historical import StockHistoricalDataClient

    from ..config import TradingConfig
    try:
        cfg = TradingConfig.from_env()
    except SystemExit as e:  # from_env báo thiếu key bằng SystemExit
        raise RuntimeError(str(e)) from None
    return StockHistoricalDataClient(cfg.api_key, cfg.api_secret)


def load_intraday(symbol: str, tf: str, start, end, client=None,
                  bars_dir: Optional[Path] = None, minute_dir: Optional[Path] = None,
                  now: Optional[datetime] = None) -> pd.DataFrame:
    """Nến của `symbol` khung `tf` trong [start, end] (giờ NY). end chỉ có ngày (00:00) -> gồm cả ngày đó.

    client/bars_dir/minute_dir/now chỉ để test; mặc định dùng Alpaca thật và thư mục data/.
    """
    if tf not in TIMEFRAMES:
        raise ValueError(f"tf phải thuộc {TIMEFRAMES}, nhận: {tf}")
    symbol = symbol.upper()
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if end == end.normalize():
        end = end + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    if tf == "1D":
        return load_bars(symbol, start=start.normalize(), end=end, bars_dir=bars_dir)
    now = pd.Timestamp(now) if now is not None else _now_ny()
    holder = {}

    def client_fn():  # tạo client chậm: cache đủ thì không cần key
        if client is not None:
            return client
        holder.setdefault("c", _make_client())
        return holder["c"]

    parts = [_ensure_month(symbol, p, client_fn, minute_dir or MINUTE_DIR, now)
             for p in pd.period_range(start, min(end, now), freq="M")]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return _empty()
    df = pd.concat(parts)
    df = df[(df.index >= start) & (df.index <= end)]
    return resample_bars(df, tf)
