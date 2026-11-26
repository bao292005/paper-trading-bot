"""Test offline cho data/intraday.py (client giả, cache ghi vào tmp_path)."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from paper_bot.data.cache import COLUMNS, save_bars
from paper_bot.data.intraday import (NY, load_intraday, regular_session, resample_bars)


def make_raw(days):
    """Nến phút 04:00-19:59 giờ NY (gồm cả ngoài giờ) cho các ngày cho trước; giá tăng dần theo phút."""
    parts = [pd.date_range(f"{d} 04:00", f"{d} 19:59", freq="1min", tz=NY) for d in days]
    ts = parts[0].append(parts[1:]) if len(parts) > 1 else parts[0]
    i = np.arange(len(ts), dtype=float)
    return pd.DataFrame({"open": i, "high": i + 0.5, "low": i - 0.5, "close": i + 0.1,
                         "volume": np.ones(len(ts))}, index=ts.tz_convert("UTC"))


def utc(t):
    """alpaca-py đổi start/end của request thành UTC không timezone -> gắn lại UTC."""
    return pd.Timestamp(t).tz_localize("UTC")


class FakeClient:
    """Giả StockHistoricalDataClient: lọc theo start/end của request, ghi lại các lần gọi."""

    def __init__(self, raw, fail_feeds=()):
        self.raw, self.fail_feeds, self.calls = raw, fail_feeds, []

    def get_stock_bars(self, request):
        self.calls.append(request)
        if request.feed.value in self.fail_feeds:
            raise RuntimeError(f"feed {request.feed.value} bị từ chối")
        start, end = utc(request.start), utc(request.end)
        df = self.raw[(self.raw.index >= start) & (self.raw.index <= end)].copy()
        df.index = pd.MultiIndex.from_product([[request.symbol_or_symbols], df.index],
                                              names=["symbol", "timestamp"])
        return SimpleNamespace(df=df)


def minute_bars(day="2024-03-04", n=390, start="09:30"):
    idx = pd.date_range(f"{day} {start}", periods=n, freq="1min")
    i = np.arange(n, dtype=float)
    return pd.DataFrame({"open": i, "high": i + 0.5, "low": i - 0.5, "close": i + 0.1,
                         "volume": np.ones(n)}, index=pd.DatetimeIndex(idx, name="time"))


def test_resample_5m_dung_ohlcv():
    out = resample_bars(minute_bars(), "5m")
    assert len(out) == 78
    first = out.iloc[0]  # nến phút 0..4
    assert (first.open, first.high, first.low, first.close, first.volume) == (0, 4.5, -0.5, 4.1, 5)
    assert out.index[0] == pd.Timestamp("2024-03-04 09:30") and out.index[-1] == pd.Timestamp("2024-03-04 15:55")


def test_resample_15m_va_1h_can_theo_0930():
    assert len(resample_bars(minute_bars(), "15m")) == 26
    h = resample_bars(minute_bars(), "1h")
    assert list(h.index.strftime("%H:%M")) == ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]
    assert h.iloc[-1].volume == 30  # nến 15:30 chỉ có 30 phút tới 16:00
    assert h.iloc[0].volume == 60 and h.iloc[0].open == 0 and h.iloc[0].close == 59.1


def test_resample_nhieu_ngay_va_bo_khung_trong():
    df = pd.concat([minute_bars("2024-03-04"), minute_bars("2024-03-05")])
    out = resample_bars(df, "1h")
    assert len(out) == 14 and out.index[7] == pd.Timestamp("2024-03-05 09:30")


def test_resample_1m_giu_nguyen_va_tf_la_bao_loi():
    df = minute_bars()
    assert resample_bars(df, "1m") is df
    with pytest.raises(ValueError):
        resample_bars(df, "2m")


def test_regular_session_bo_ngoai_gio_va_cuoi_tuan():
    raw = make_raw(["2024-03-04", "2024-03-09"])  # thứ 2 và thứ 7
    ny = raw.tz_convert(NY).tz_localize(None)
    out = regular_session(ny)
    assert len(out) == 390  # chỉ thứ 2, 09:30-15:59
    assert out.index[0].strftime("%H:%M") == "09:30" and out.index[-1].strftime("%H:%M") == "15:59"


def test_load_intraday_thang_da_qua_dung_cache_lan_2(tmp_path):
    client = FakeClient(make_raw(["2024-03-04", "2024-03-05"]))
    kw = dict(client=client, minute_dir=tmp_path, now="2024-06-10 12:00")
    df = load_intraday("aaa", "1m", "2024-03-01", "2024-03-31", **kw)
    assert len(df) == 780 and df.index.tz is None
    assert df.index[0] == pd.Timestamp("2024-03-04 09:30")  # giờ NY, không timezone
    assert (tmp_path / "AAA" / "2024-03.parquet").exists()
    n = len(client.calls)
    again = load_intraday("AAA", "5m", "2024-03-01", "2024-03-31", **kw)
    assert len(client.calls) == n  # không tải lại
    assert len(again) == 2 * 78


def test_sip_loi_thi_dung_iex(tmp_path):
    client = FakeClient(make_raw(["2024-03-04"]), fail_feeds=("sip",))
    df = load_intraday("AAA", "1m", "2024-03-04", "2024-03-04", client=client,
                       minute_dir=tmp_path, now="2024-06-10 12:00")
    assert [c.feed.value for c in client.calls] == ["sip", "iex"]
    assert len(df) == 390


def test_ca_hai_feed_loi_khong_ghi_cache(tmp_path):
    client = FakeClient(make_raw(["2024-03-04"]), fail_feeds=("sip", "iex"))
    df = load_intraday("AAA", "1m", "2024-03-04", "2024-03-04", client=client,
                       minute_dir=tmp_path, now="2024-06-10 12:00")
    assert df.empty and list(df.columns) == COLUMNS
    assert not (tmp_path / "AAA").exists()  # lần sau còn thử lại được


def test_thang_hien_tai_tai_phan_thieu(tmp_path):
    client = FakeClient(make_raw(["2024-03-04", "2024-03-05"]))
    kw = dict(client=client, minute_dir=tmp_path)
    # Giữa phiên thứ 2 04/03 lúc 10:00: chỉ lấy tới 10:00
    a = load_intraday("AAA", "1m", "2024-03-04", "2024-03-05", now="2024-03-04 10:00", **kw)
    assert a.index[-1] == pd.Timestamp("2024-03-04 10:00")
    # 12:00 cùng ngày: chỉ tải từ 10:01 trở đi, không tải lại phần cũ
    b = load_intraday("AAA", "1m", "2024-03-04", "2024-03-05", now="2024-03-04 12:00", **kw)
    assert utc(client.calls[-1].start).tz_convert(NY).tz_localize(None) == pd.Timestamp("2024-03-04 10:01")
    assert b.index[-1] == pd.Timestamp("2024-03-04 12:00") and b.index.is_unique


def test_thang_hien_tai_da_dong_cua_va_du_thi_khong_goi_api(tmp_path):
    days = ["2024-03-04", "2024-03-05", "2024-03-06", "2024-03-07", "2024-03-08"]
    client = FakeClient(make_raw(days))
    kw = dict(client=client, minute_dir=tmp_path)
    load_intraday("AAA", "1m", "2024-03-04", "2024-03-08", now="2024-03-08 18:00", **kw)
    n = len(client.calls)
    load_intraday("AAA", "1m", "2024-03-04", "2024-03-08", now="2024-03-08 20:00", **kw)  # sau đóng cửa
    load_intraday("AAA", "1m", "2024-03-04", "2024-03-08", now="2024-03-09 11:00", **kw)  # cuối tuần
    assert len(client.calls) == n


def test_1d_doc_tu_cache_ngay(tmp_path):
    idx = pd.bdate_range("2024-01-02", periods=10, name="date")
    save_bars("AAA", pd.DataFrame({c: np.arange(10, dtype=float) + 1 for c in COLUMNS}, index=idx),
              bars_dir=tmp_path)
    out = load_intraday("AAA", "1D", "2024-01-03", "2024-01-05", bars_dir=tmp_path)
    assert len(out) == 3 and out.index[-1] == pd.Timestamp("2024-01-05")  # end chỉ ngày -> gồm cả ngày đó


def test_tf_la_bao_loi():
    with pytest.raises(ValueError):
        load_intraday("AAA", "2m", "2024-03-01", "2024-03-02")
