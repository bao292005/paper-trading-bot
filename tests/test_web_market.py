"""Test offline API dữ liệu: /api/symbols, /api/bars, /api/indicators (load_intraday giả)."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from paper_bot.data.intraday import resample_bars
from paper_bot.web.api.timeutil import day_to_unix, ny_to_unix, snap_to_bar
from paper_bot.web.app import create_app


def minute_df(days):
    parts = []
    for d in days:
        idx = pd.date_range(f"{d} 09:30", periods=390, freq="1min", name="time")
        i = np.arange(390, dtype=float) + 100
        parts.append(pd.DataFrame({"open": i, "high": i + 1, "low": i - 1, "close": i + 0.5,
                                   "volume": np.full(390, 10.0)}, index=idx))
    return pd.concat(parts)


class FakeLoader:
    """Giả load_intraday: 250 ngày bán nguyệt cho 1D, 2 ngày nến phút cho các tf còn lại."""

    def __init__(self):
        self.calls = []

    def __call__(self, symbol, tf, start, end):
        self.calls.append((symbol, tf, pd.Timestamp(start), pd.Timestamp(end)))
        if tf == "1D":
            idx = pd.bdate_range("2024-01-02", periods=250, name="date")
            i = np.arange(250, dtype=float) + 50
            df = pd.DataFrame({"open": i, "high": i + 1, "low": i - 1, "close": i + 0.5, "volume": 1000.0}, index=idx)
            return df[(df.index >= pd.Timestamp(start).normalize()) & (df.index <= pd.Timestamp(end))]
        df = resample_bars(minute_df(["2024-03-04", "2024-03-05"]), tf)
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]


@pytest.fixture
def client(tmp_path):
    app = create_app(load_intraday_fn=FakeLoader(), trading_client_fn=lambda: None,
                     db_path=tmp_path / "x.db", bars_dir=tmp_path)
    return TestClient(app)


def test_symbols_co_mac_dinh_va_cache(client, tmp_path):
    (tmp_path / "ZZZ.parquet").write_bytes(b"")
    body = client.get("/api/symbols").json()
    assert {"AAPL", "SPY", "ZZZ"} <= set(body["symbols"]) and body["timeframes"] == ["1m", "5m", "15m", "1h", "1D"]


def test_bars_1m_dinh_dang_va_time_unix_utc(client):
    r = client.get("/api/bars", params={"symbol": "aapl", "tf": "1m", "start": "2024-03-04", "end": "2024-03-04 09:34:59"})
    body = r.json()
    assert r.status_code == 200 and body["symbol"] == "AAPL" and len(body["bars"]) == 5
    first = body["bars"][0]
    assert first["time"] == 1709562600  # 2024-03-04 09:30 NY = 14:30 UTC
    assert set(first) == {"time", "open", "high", "low", "close"}
    assert body["volume"][0]["time"] == first["time"] and body["volume"][0]["value"] == 10.0
    assert body["volume"][0]["color"].startswith("#") and body["oldest"] == first["time"]


def test_bars_1d_time_la_12h_utc(client):
    body = client.get("/api/bars", params={"symbol": "AAPL", "tf": "1D", "start": "2024-01-02", "end": "2024-01-04"}).json()
    assert [b["time"] for b in body["bars"]] == [day_to_unix(d) for d in ("2024-01-02", "2024-01-03", "2024-01-04")]
    assert body["bars"][0]["time"] == 1704196800  # 2024-01-02 12:00 UTC


def test_bars_before_loai_nen_tu_moc_do(client):
    cut = ny_to_unix("2024-03-04 09:35")
    body = client.get("/api/bars", params={"symbol": "AAPL", "tf": "1m", "start": "2024-03-04", "before": str(cut)}).json()
    assert len(body["bars"]) == 5 and body["bars"][-1]["time"] == cut - 60


def test_bars_loi_dau_vao(client):
    assert client.get("/api/bars", params={"symbol": "AA PL", "tf": "1m"}).status_code == 400
    assert client.get("/api/bars", params={"symbol": "AAPL", "tf": "2m"}).status_code == 400
    assert client.get("/api/bars", params={"symbol": "AAPL", "tf": "1m", "start": "khong-phai-ngay"}).status_code == 400
    assert client.get("/api/bars", params={"symbol": "AAPL", "before": "abc"}).status_code == 422


def test_bars_thieu_key_tra_503(tmp_path):
    def boom(*a):
        raise RuntimeError("Thiếu ALPACA_API_KEY")

    c = TestClient(create_app(load_intraday_fn=boom, db_path=tmp_path / "x.db"))
    r = c.get("/api/bars", params={"symbol": "AAPL", "tf": "1m", "start": "2024-03-04"})
    assert r.status_code == 503 and "ALPACA_API_KEY" in r.json()["detail"]


def test_indicators_ma_rsi_cat_dung_khoang(client):
    body = client.get("/api/indicators", params={"symbol": "AAPL", "tf": "1D", "start": "2024-12-02", "end": "2024-12-06"}).json()
    assert set(body) == {"ma10", "ma30", "ma200", "rsi14"}
    assert len(body["ma200"]) == 5  # MA200 có ngay từ đầu khoảng nhờ warmup
    assert body["ma10"][0]["time"] == day_to_unix("2024-12-02")
    # close tăng đều mỗi phiên 1 đơn vị -> MA10 = close hiện tại - 4.5
    day0 = pd.bdate_range("2024-01-02", periods=250).get_loc(pd.Timestamp("2024-12-02"))
    assert body["ma10"][0]["value"] == pytest.approx(50 + day0 + 0.5 - 4.5)
    assert all(p["value"] == 100.0 for p in body["rsi14"])  # chỉ tăng -> RSI 100


def test_snap_to_bar_cac_khung():
    t = pd.Timestamp("2024-03-04 10:07:30")
    assert snap_to_bar(t, "1m") == ny_to_unix("2024-03-04 10:07")
    assert snap_to_bar(t, "5m") == ny_to_unix("2024-03-04 10:05")
    assert snap_to_bar(t, "1h") == ny_to_unix("2024-03-04 09:30")
    assert snap_to_bar(pd.Timestamp("2024-03-04 20:00"), "5m") == ny_to_unix("2024-03-04 15:55")  # sau giờ -> nến cuối
    assert snap_to_bar(pd.Timestamp("2024-03-04"), "1h") == ny_to_unix("2024-03-04 15:30")  # ngày không giờ -> cuối ngày
    assert snap_to_bar(t, "1D") == day_to_unix("2024-03-04")
