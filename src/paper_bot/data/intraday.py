"""Nến phút / giờ / ngày cho web app biểu đồ.

Đầu ra của load_intraday: cột open/high/low/close/volume (float), index DatetimeIndex
giờ New York, KHÔNG timezone, chỉ phiên chính 09:30-16:00 (nến phút cuối là 15:59).
Với tf "1D" index là ngày (00:00) lấy từ cache ngày data/bars.

Cache nến phút: data/bars_1m/<SYMBOL>/<YYYY-MM>.parquet (đã lọc phiên chính, giờ NY naive).
  - Tháng đã qua mà có file -> không tải lại.
  - Tháng hiện tại -> luôn tải phần thiếu (từ nến cuối đã cache trở đi).

Crypto (asset="crypto", mã dạng "BTC/USD"): giao dịch 24/7 nên KHÔNG lọc phiên, index giờ UTC naive,
nến 1h/15m/5m bắt đầu ở giờ tròn UTC. Cache: data/bars_crypto_1m/<BTC-USD>/<YYYY-MM>.parquet.
"""

import logging
from datetime import datetime, time
from pathlib import Path
from typing import Optional

import pandas as pd

from .cache import COLUMNS, PROJECT_ROOT, load_bars, symbol_filename

logger = logging.getLogger("paper_bot")

NY = "America/New_York"
MINUTE_DIR = PROJECT_ROOT / "data" / "bars_1m"
CRYPTO_MINUTE_DIR = PROJECT_ROOT / "data" / "bars_crypto_1m"
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


def resample_bars(df_1m: pd.DataFrame, tf: str, asset: str = "stock") -> pd.DataFrame:
    """Gộp nến phút lên tf. Cổ phiếu: mốc mỗi ngày là 09:30 (1h -> 09:30, 10:30, ... 15:30).
    Crypto: mốc là giờ tròn (00:00, 01:00, ... theo UTC)."""
    if tf == "1m":
        return df_1m
    if tf not in _RULES:
        raise ValueError(f"tf không hợp lệ cho resample: {tf}")
    if df_1m.empty:
        return _empty()
    offset = "0min" if asset == "crypto" else "9h30min"
    out = df_1m.resample(_RULES[tf], origin="start_day", offset=offset,
                         label="left", closed="left").agg(_AGG)
    out = out.dropna(subset=["open"])  # bỏ khung không có nến nào
    out.index.name = "time"
    return out


def _to_ny_naive(df: pd.DataFrame, tz: str = NY) -> pd.DataFrame:
    """Df Alpaca (MultiIndex symbol,timestamp hoặc index UTC) -> index giờ `tz` (mặc định NY) không timezone."""
    if df is None or df.empty:
        return _empty()
    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel("symbol")
    out = df[COLUMNS].astype(float).copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_convert(tz).tz_localize(None)
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


def fetch_crypto_minute_bars(client, symbol: str, start: pd.Timestamp,
                             end: pd.Timestamp) -> Optional[pd.DataFrame]:
    """Tải nến phút crypto [start, end] (giờ UTC naive). Không lọc phiên (24/7). Lỗi -> None."""
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame

    try:
        request = CryptoBarsRequest(
            symbol_or_symbols=symbol, timeframe=TimeFrame.Minute,
            start=start.tz_localize("UTC").to_pydatetime(), end=end.tz_localize("UTC").to_pydatetime())
        return _to_ny_naive(client.get_crypto_bars(request).df, tz="UTC")
    except Exception as e:
        logger.warning(f"Lỗi tải nến phút crypto {symbol}: {e}")
    return None


def _now_ny() -> pd.Timestamp:
    return pd.Timestamp.now(tz=NY).tz_localize(None)


def _now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def _up_to_date(last_ts: pd.Timestamp, now: pd.Timestamp, asset: str = "stock") -> bool:
    """Cache tháng hiện tại đã đủ chưa? Đang trong phiên thì luôn coi là chưa đủ.
    Crypto chạy 24/7: đủ khi nến cuối đã tới phút trước `now`."""
    if asset == "crypto":
        return last_ts >= now.floor("min") - pd.Timedelta(minutes=1)
    if now.weekday() < 5 and time(9, 30) <= now.time() < time(16, 0):
        return False
    day = now.normalize()
    if now.weekday() >= 5 or now.time() < time(9, 30):
        day -= pd.Timedelta(days=1)  # chưa có phiên hôm nay -> lấy phiên trước
    while day.weekday() >= 5:
        day -= pd.Timedelta(days=1)
    return last_ts.normalize() >= day  # (ngày lễ sẽ bị tải lại mỗi lần, chấp nhận)


def _ensure_month(symbol, period, client_fn, minute_dir, now, asset="stock") -> pd.DataFrame:
    """Đọc cache tháng, tải phần thiếu nếu cần, ghi lại cache. Trả df của tháng đó."""
    path = Path(minute_dir) / symbol_filename(symbol) / f"{period}.parquet"
    cached = pd.read_parquet(path) if path.exists() else None
    is_current = period == now.to_period("M")
    if cached is not None and not is_current:
        return cached  # tháng đã qua: không tải lại
    if cached is not None and not cached.empty and _up_to_date(cached.index[-1], now, asset):
        return cached
    start = period.start_time if cached is None or cached.empty else cached.index[-1] + pd.Timedelta(minutes=1)
    end = min(period.end_time.normalize() + pd.Timedelta(days=1), now)
    fetch = fetch_crypto_minute_bars if asset == "crypto" else fetch_minute_bars
    new = fetch(client_fn(), symbol, start, end)
    if new is None:  # lỗi mạng/key: dùng tạm cache cũ, không ghi gì
        return cached if cached is not None else _empty()
    df = new if cached is None else pd.concat([cached, new])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


def _make_client(asset: str = "stock"):
    from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient

    from ..config import TradingConfig
    if asset == "crypto":
        return CryptoHistoricalDataClient()  # dữ liệu crypto không cần key
    try:
        cfg = TradingConfig.from_env()
    except SystemExit as e:  # from_env báo thiếu key bằng SystemExit
        raise RuntimeError(str(e)) from None
    return StockHistoricalDataClient(cfg.api_key, cfg.api_secret)


def load_intraday(symbol: str, tf: str, start, end, client=None,
                  bars_dir: Optional[Path] = None, minute_dir: Optional[Path] = None,
                  now: Optional[datetime] = None, asset: Optional[str] = None) -> pd.DataFrame:
    """Nến của `symbol` khung `tf` trong [start, end]. end chỉ có ngày (00:00) -> gồm cả ngày đó.

    asset: "stock" (giờ NY, phiên chính) | "crypto" (giờ UTC, 24/7). None -> tự nhận: có "/" là crypto.
    start/end/now hiểu theo giờ của asset (NY hoặc UTC), không timezone.
    client/bars_dir/minute_dir/now chỉ để test; mặc định dùng Alpaca thật và thư mục data/.
    """
    if tf not in TIMEFRAMES:
        raise ValueError(f"tf phải thuộc {TIMEFRAMES}, nhận: {tf}")
    symbol = symbol.upper()
    if asset is None:
        asset = "crypto" if "/" in symbol else "stock"
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if end == end.normalize():
        end = end + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    if tf == "1D":
        return load_bars(symbol, start=start.normalize(), end=end, bars_dir=bars_dir, asset=asset)
    crypto = asset == "crypto"
    if now is not None:
        now = pd.Timestamp(now)
    else:
        now = _now_utc() if crypto else _now_ny()
    holder = {}

    def client_fn():  # tạo client chậm: cache đủ thì không cần key
        if client is not None:
            return client
        holder.setdefault("c", _make_client(asset))
        return holder["c"]

    default_dir = CRYPTO_MINUTE_DIR if crypto else MINUTE_DIR
    parts = [_ensure_month(symbol, p, client_fn, minute_dir or default_dir, now, asset)
             for p in pd.period_range(start, min(end, now), freq="M")]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return _empty()
    df = pd.concat(parts)
    df = df[(df.index >= start) & (df.index <= end)]
    return resample_bars(df, tf, asset)
