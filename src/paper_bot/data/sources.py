"""Nguồn dữ liệu: AlpacaDataManager (nến), NewsSource (tin), StreamSource (realtime)."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from ..contracts import NewsItem
from ..managers import DataManager
from .cache import COLUMNS, load_bars, normalize_bars

logger = logging.getLogger("paper_bot")

SIP_DELAY = pd.Timedelta(minutes=15)  # gói free: SIP từ chối truy vấn có end trong 15 phút gần nhất


def feed_order(end, now=None) -> tuple:
    """Thứ tự thử feed: end trong 15 phút gần nhất (hoặc tương lai) -> chỉ IEX (SIP chắc chắn lỗi);
    ngược lại SIP trước, lỗi thì IEX. end/now: Timestamp có tz, hoặc naive = UTC."""
    def utc(t):
        t = pd.Timestamp(t)
        return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    now = utc(now) if now is not None else pd.Timestamp.now(tz="UTC")
    return (DataFeed.IEX,) if utc(end) > now - SIP_DELAY else (DataFeed.SIP, DataFeed.IEX)


def fetch_daily_bars(
    client: StockHistoricalDataClient,
    symbols: List[str],
    start: datetime,
    end: datetime,
    feed: DataFeed = DataFeed.IEX,
    adjustment: Adjustment = Adjustment.ALL,
) -> Dict[str, pd.DataFrame]:
    """Tải nến ngày cho nhiều mã trong 1 request. Trả {symbol: df chuẩn hoá}.

    Lỗi API thì để lỗi nổi lên (caller quyết định thử feed khác).
    """
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        adjustment=adjustment,
        feed=feed,
    )
    df = client.get_stock_bars(request).df
    result = {}
    for sym in symbols:
        if df.empty or sym not in df.index.get_level_values("symbol"):
            result[sym] = normalize_bars(None)
        else:
            result[sym] = normalize_bars(df.xs(sym, level="symbol")[COLUMNS])
    return result


def fetch_crypto_daily_bars(client, symbols: List[str], start: datetime,
                            end: datetime) -> Dict[str, pd.DataFrame]:
    """Nến ngày crypto (CryptoHistoricalDataClient, không cần key). Ngày = ngày UTC (nến chốt 00:00 UTC).

    Trả {symbol: df chuẩn hoá}; mã không có dữ liệu -> df rỗng. Lỗi API thì để nổi lên.
    """
    from alpaca.data.requests import CryptoBarsRequest

    request = CryptoBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Day,
                                start=start, end=end)
    df = client.get_crypto_bars(request).df
    result = {}
    for sym in symbols:
        if df.empty or sym not in df.index.get_level_values("symbol"):
            result[sym] = normalize_bars(None)
        else:
            result[sym] = normalize_bars(df.xs(sym, level="symbol")[COLUMNS], tz="UTC")
    return result


class AlpacaDataManager(DataManager):
    """Kế thừa DataManager của bài gốc: giữ nguyên get_historical_data(symbol, days),
    thêm get_bars(symbol, start, end) đọc cache trước, thiếu mới gọi API."""

    GAP_DAYS = 5  # cache lệch đầu/cuối quá 5 ngày lịch (cuối tuần, nghỉ lễ) coi là thiếu

    def __init__(self, client: StockHistoricalDataClient, bars_dir: Optional[Path] = None):
        super().__init__(client)
        self.bars_dir = bars_dir

    def get_bars(self, symbol: str, start, end) -> pd.DataFrame:
        """Nến ngày đã điều chỉnh trong [start, end], chỉ gồm dữ liệu <= end (chống rò rỉ).

        Dữ liệu tải từ API KHÔNG ghi đè cache (cache chỉ do fetch_history.py tạo).
        """
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        cached = load_bars(symbol, bars_dir=self.bars_dir)
        gap = pd.Timedelta(days=self.GAP_DAYS)
        if not cached.empty and cached.index[0] <= start + gap and cached.index[-1] >= end - gap:
            return cached[(cached.index >= start) & (cached.index <= end)]
        return self._fetch_api(symbol, start, end)

    def _fetch_api(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        for feed in feed_order(end + pd.Timedelta(days=1)):
            try:
                # end + 1 ngày để API trả đủ nến ngày `end`, rồi cắt lại bên dưới
                df = fetch_daily_bars(self.client, [symbol], start.to_pydatetime(),
                                      (end + pd.Timedelta(days=1)).to_pydatetime(), feed=feed)[symbol]
                return df[(df.index >= start) & (df.index <= end)]
            except Exception as e:
                logger.warning(f"Lỗi lấy {symbol} feed {feed.value}: {e}")
        return normalize_bars(None)


def _utc_naive(t: datetime) -> datetime:
    """Đưa datetime về UTC không timezone (datetime naive coi như đã là UTC)."""
    if t.tzinfo is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    return t


class NewsSource:
    """Tin tức Alpaca. Thời gian trả ra là UTC naive để so sánh được với asof naive."""

    def __init__(self, client: NewsClient):
        self.client = client

    def get_news(self, symbols: List[str], start: datetime, end: datetime,
                 limit: int = 50) -> List[NewsItem]:
        """Tin trong [start, end], CHỈ tin có created_at <= end. Lỗi API -> list rỗng."""
        request = NewsRequest(symbols=",".join(symbols), start=start, end=end,
                              limit=limit, sort="desc")
        try:
            articles = self.client.get_news(request).data.get("news", [])
        except Exception as e:
            logger.error(f"Lỗi lấy tin {symbols}: {e}")
            return []
        end_utc = _utc_naive(end)
        items = [
            NewsItem(headline=a.headline, created_at=_utc_naive(a.created_at), source=a.source,
                     summary=a.summary, symbols=list(a.symbols))
            for a in articles
        ]
        # Lọc lần nữa phía mình, không tin hoàn toàn vào API
        return sorted((n for n in items if n.created_at <= end_utc), key=lambda n: n.created_at)


class StreamSource:
    """Lớp mỏng bọc StockDataStream: nhận nến mới và lưu nến mới nhất của từng mã.

    Chỉ kết nối khi gọi run(). Test chỉ cần gọi thẳng on_bar().
    """

    def __init__(self, api_key: str, api_secret: str, stream=None):
        self.api_key, self.api_secret = api_key, api_secret
        self._stream = stream  # cho phép đưa stream giả vào
        self.latest: Dict[str, dict] = {}  # {symbol: {timestamp, open, high, low, close, volume}}

    async def on_bar(self, bar) -> None:
        """Callback của stream: cập nhật nến mới nhất."""
        self.latest[bar.symbol] = {
            "timestamp": bar.timestamp, "open": float(bar.open), "high": float(bar.high),
            "low": float(bar.low), "close": float(bar.close), "volume": float(bar.volume),
        }

    def latest_bars(self) -> pd.DataFrame:
        """Nến mới nhất của mọi mã, index = symbol."""
        if not self.latest:
            return pd.DataFrame(columns=["timestamp"] + COLUMNS)
        return pd.DataFrame.from_dict(self.latest, orient="index")

    def run(self, symbols: List[str]) -> None:
        """Đăng ký nến và chạy (chặn tiến trình cho tới khi dừng)."""
        if self._stream is None:
            from alpaca.data.live import StockDataStream  # import lazy
            self._stream = StockDataStream(self.api_key, self.api_secret)
        self._stream.subscribe_bars(self.on_bar, *symbols)
        self._stream.run()
