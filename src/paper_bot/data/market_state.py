"""Ghép mọi thứ thành MarketState cho 1 mã tại thời điểm asof (chỉ dùng dữ liệu <= asof)."""

from datetime import datetime
from typing import List, Optional

import pandas as pd

from ..contracts import Forecast, MarketState, NewsItem
from .features import compute_features
from .regime import detect_regime


def news_cutoff(asof) -> datetime:
    """Mốc thời gian (UTC naive) mà tin tức phải <= .

    asof là NGÀY (00:00) = quyết định ở giá đóng cửa ngày đó -> mốc là 16:00 giờ New York
    của ngày asof, tin ra sau giờ đóng cửa chưa được dùng. asof có giờ cụ thể thì dùng đúng giờ đó.
    """
    t = pd.Timestamp(asof)
    if t.tzinfo is None and t == t.normalize():
        close_ny = t.tz_localize("America/New_York") + pd.Timedelta(hours=16)
        return close_ny.tz_convert("UTC").tz_localize(None).to_pydatetime()
    return t.to_pydatetime()


def build_market_state(
    symbol: str,
    asof,
    bars: pd.DataFrame,
    spy_bars: pd.DataFrame,
    forecast: Optional[Forecast] = None,
    news: Optional[List[NewsItem]] = None,
) -> MarketState:
    """Cắt bars và spy_bars về <= asof, tính regime + features, lọc tin <= asof."""
    asof_ts = pd.Timestamp(asof).normalize()
    cut = bars[bars.index <= asof_ts]
    cutoff = news_cutoff(asof)
    news_ok = [n for n in (news or []) if n.created_at <= cutoff]
    return MarketState(
        symbol=symbol,
        asof=asof_ts.to_pydatetime(),
        bars=cut,
        regime=detect_regime(spy_bars, asof_ts),
        forecast=forecast,
        news=news_ok,
        features=compute_features(cut),
    )
