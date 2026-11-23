"""Test MarketState: chống rò rỉ (đổi dữ liệu SAU asof không làm đổi kết quả)."""

from datetime import datetime

import numpy as np
import pandas as pd

from paper_bot.contracts import Forecast, NewsItem
from paper_bot.data.market_state import build_market_state, news_cutoff


def make_bars(n=600, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n)))
    idx = pd.bdate_range("2020-01-01", periods=n, name="date")
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": 1e6}, index=idx)


def break_future(df, asof):
    out = df.copy()
    out.loc[out.index > asof, ["open", "high", "low", "close"]] = 1.0
    return out


def test_state_cat_bars_va_dong_cuoi_la_asof():
    bars, spy = make_bars(), make_bars(seed=1)
    asof = bars.index[500]
    st = build_market_state("AAA", asof, bars, spy)
    assert st.bars.index[-1] == asof and st.asof == asof.to_pydatetime()
    assert st.regime != "unknown" and "atr14" in st.features


def test_state_khong_ro_ri_du_lieu_sau_asof():
    bars, spy = make_bars(), make_bars(seed=1)
    asof = bars.index[500]
    a = build_market_state("AAA", asof, bars, spy)
    b = build_market_state("AAA", asof, break_future(bars, asof), break_future(spy, asof))
    pd.testing.assert_frame_equal(a.bars, b.bars)
    assert a.regime == b.regime and a.features == b.features


def test_state_loc_tin_sau_asof():
    bars, spy = make_bars(), make_bars(seed=1)
    asof = bars.index[500]  # nửa đêm; mốc tin = 16:00 New York ngày đó
    cutoff = news_cutoff(asof)
    news = [NewsItem("trước", cutoff - pd.Timedelta(hours=1)),
            NewsItem("đúng mốc", cutoff),
            NewsItem("sau đóng cửa", cutoff + pd.Timedelta(minutes=1)),
            NewsItem("ngày sau", datetime(2030, 1, 1))]
    st = build_market_state("AAA", asof, bars, spy, news=news)
    assert [n.headline for n in st.news] == ["trước", "đúng mốc"]


def test_news_cutoff_theo_gio_new_york():
    # mùa đông 16:00 NY = 21:00 UTC, mùa hè 16:00 NY = 20:00 UTC
    assert news_cutoff("2024-01-10") == datetime(2024, 1, 10, 21)
    assert news_cutoff("2024-07-10") == datetime(2024, 7, 10, 20)


def test_state_giu_nguyen_forecast_duoc_dua_vao():
    bars, spy = make_bars(), make_bars(seed=1)
    fc = Forecast("naive", 5, {0.1: -0.02, 0.5: 0.0, 0.9: 0.02})
    st = build_market_state("AAA", bars.index[500], bars, spy, forecast=fc)
    assert st.forecast is fc
