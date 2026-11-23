"""Test offline cho AlpacaDataManager, NewsSource, StreamSource (dùng client giả)."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd

from paper_bot.data.cache import COLUMNS, save_bars
from paper_bot.data.sources import AlpacaDataManager, NewsSource, StreamSource
from paper_bot.managers import DataManager


def make_bars(start="2024-01-02", n=30):
    idx = pd.bdate_range(start, periods=n, name="date")
    return pd.DataFrame({c: np.arange(n, dtype=float) + 1 for c in COLUMNS}, index=idx)


class FakeBarsClient:
    """Giả StockHistoricalDataClient: trả df dạng MultiIndex (symbol, timestamp)."""

    def __init__(self, df):
        self.df, self.calls = df, 0

    def get_stock_bars(self, request):
        self.calls += 1
        sym = request.symbol_or_symbols[0]
        out = self.df.copy()
        out.index = pd.MultiIndex.from_product(
            [[sym], out.index.tz_localize("UTC") + pd.Timedelta(hours=5)], names=["symbol", "timestamp"])
        return SimpleNamespace(df=out)


def test_giu_get_historical_data_cua_bai_goc():
    assert AlpacaDataManager.get_historical_data is DataManager.get_historical_data


def test_get_bars_doc_cache_khong_goi_api(tmp_path):
    save_bars("AAA", make_bars(), bars_dir=tmp_path)
    client = FakeBarsClient(make_bars())
    dm = AlpacaDataManager(client, bars_dir=tmp_path)
    out = dm.get_bars("AAA", "2024-01-05", "2024-01-12")
    assert client.calls == 0
    assert out.index[0] == pd.Timestamp("2024-01-05") and out.index[-1] == pd.Timestamp("2024-01-12")


def test_get_bars_thieu_cache_goi_api_va_cat_theo_end(tmp_path):
    client = FakeBarsClient(make_bars())
    dm = AlpacaDataManager(client, bars_dir=tmp_path)  # cache rỗng
    out = dm.get_bars("AAA", "2024-01-02", "2024-01-10")
    assert client.calls == 1
    assert out.index.max() <= pd.Timestamp("2024-01-10")  # không lấy dữ liệu sau end
    assert len(out) == 7


class FakeNewsClient:
    def __init__(self, articles):
        self.articles = articles

    def get_news(self, request):
        return SimpleNamespace(data={"news": self.articles})


def article(headline, created_at):
    return SimpleNamespace(headline=headline, created_at=created_at, source="x",
                           summary="", symbols=["AAA"])


def test_news_chi_tra_tin_truoc_end():
    utc = timezone.utc
    arts = [article("cũ", datetime(2024, 1, 3, 12, tzinfo=utc)),
            article("sau end", datetime(2024, 1, 6, 12, tzinfo=utc))]
    src = NewsSource(FakeNewsClient(arts))
    out = src.get_news(["AAA"], datetime(2024, 1, 1), datetime(2024, 1, 5))
    assert [n.headline for n in out] == ["cũ"]


def test_news_loi_api_tra_rong():
    class Boom:
        def get_news(self, request):
            raise RuntimeError("mạng lỗi")
    assert NewsSource(Boom()).get_news(["AAA"], datetime(2024, 1, 1), datetime(2024, 1, 5)) == []


def test_stream_on_bar_cap_nhat_nen_moi_nhat():
    src = StreamSource("k", "s")  # chưa mở kết nối
    bar = lambda close: SimpleNamespace(symbol="AAA", timestamp=datetime(2024, 1, 2), open=1,
                                        high=3, low=0.5, close=close, volume=100)
    asyncio.run(src.on_bar(bar(2.0)))
    asyncio.run(src.on_bar(bar(2.5)))
    assert src.latest["AAA"]["close"] == 2.5
    assert list(src.latest_bars().index) == ["AAA"]
