"""Test GET /api/replay/bars: cắt đúng tại con trỏ, nến khung lớn đang hình thành, steps, chỉ báo (dữ liệu giả)."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from paper_bot.web.api.timeutil import day_to_unix, ny_to_unix, utc_to_unix
from paper_bot.web.app import create_app

MIN_DAYS = ["2024-03-04", "2024-03-05", "2024-03-06"]  # có nến phút (cổ phiếu)


def stock_minutes():
    parts = []
    for n, d in enumerate(MIN_DAYS):
        idx = pd.date_range(f"{d} 09:30", periods=390, freq="1min", name="time")
        c = 100 + n * 10 + np.arange(390) * 0.01  # giá tăng dần theo phút
        parts.append(pd.DataFrame({"open": c - 0.005, "high": c + 0.02, "low": c - 0.02, "close": c,
                                   "volume": np.full(390, 10.0)}, index=idx))
    return pd.concat(parts)


def crypto_minutes():
    idx = pd.date_range("2024-03-04 00:00", periods=2 * 1440, freq="1min", name="time")
    c = 50000 + np.arange(len(idx)) * 1.0
    return pd.DataFrame({"open": c, "high": c + 5, "low": c - 5, "close": c + 1, "volume": 1.0}, index=idx)


def daily_from(m, early):
    """Nến ngày: `early` ngày giả trước đó + gộp nến phút."""
    agg = m.groupby(m.index.normalize()).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    first = agg.index[0]
    idx = pd.bdate_range(end=first - pd.Timedelta(days=1), periods=early)
    v = np.linspace(80, 99, early)
    old = pd.DataFrame({"open": v, "high": v + 1, "low": v - 1, "close": v + 0.5, "volume": 1000.0}, index=idx)
    out = pd.concat([old, agg])
    out.index.name = "date"
    return out


class FakeLoader:
    """Giả load_intraday (có tham số asset). Chỉ cần "1m" và "1D"."""

    def __init__(self):
        self.m = {"stock": stock_minutes(), "crypto": crypto_minutes()}
        self.d = {a: daily_from(m, 250) for a, m in self.m.items()}
        self.calls = []

    def __call__(self, symbol, tf, start, end, asset="stock"):
        self.calls.append((symbol, tf, pd.Timestamp(start), pd.Timestamp(end), asset))
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        if tf == "1D":
            df = self.d[asset]
            return df[(df.index >= start.normalize()) & (df.index <= end)]
        assert tf == "1m"
        df = self.m[asset]
        return df[(df.index >= start) & (df.index <= end)]


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(load_intraday_fn=FakeLoader(), trading_client_fn=lambda: None, db_path=tmp_path / "x.db"))


def get(client, **kw):
    params = {"symbol": "AAPL", "asset": "stock", **kw}
    r = client.get("/api/replay/bars", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_1h_nen_dang_hinh_thanh_chi_gom_phut_truoc_con_tro(client):
    cur = ny_to_unix("2024-03-05 10:37")
    b = get(client, tf="1h", cursor=cur)
    last = b["bars"][-1]
    assert last["time"] == ny_to_unix("2024-03-05 10:30") and b["cursor"] == cur
    m = stock_minutes().loc["2024-03-05 10:30":"2024-03-05 10:36"]
    assert last["high"] == pytest.approx(m["high"].max()) and last["close"] == pytest.approx(m["close"].iloc[-1])
    assert all(x["time"] < cur for x in b["bars"])
    b5 = get(client, tf="5m", cursor=cur)
    assert b5["ind"]["ma10"][-1]["time"] == b5["bars"][-1]["time"] == ny_to_unix("2024-03-05 10:35")  # chỉ báo không vượt con trỏ
    assert b5["ind"]["ma10"][-1]["value"] == pytest.approx(np.mean([x["close"] for x in b5["bars"][-10:]]))


def test_1m_khong_lo_nen_tuong_lai(client):
    cur = ny_to_unix("2024-03-05 10:37") + 25  # làm tròn xuống phút
    b = get(client, tf="1m", cursor=cur)
    assert b["cursor"] == ny_to_unix("2024-03-05 10:37")
    assert b["bars"][-1]["time"] == ny_to_unix("2024-03-05 10:36")


def test_1d_ngay_dang_chay_gop_tu_nen_phut(client):
    b = get(client, tf="1D", cursor=ny_to_unix("2024-03-05 10:37"))
    last, prev = b["bars"][-1], b["bars"][-2]
    assert last["time"] == day_to_unix("2024-03-05") and prev["time"] == day_to_unix("2024-03-04")
    m = stock_minutes().loc["2024-03-05 09:30":"2024-03-05 10:36"]
    assert last["close"] == pytest.approx(m["close"].iloc[-1]) and last["high"] == pytest.approx(m["high"].max())
    closes = [x["close"] for x in b["bars"][-10:]]
    assert b["ind"]["ma10"][-1]["value"] == pytest.approx(np.mean(closes))
    assert not b["fallback"]


def test_1d_ngay_khong_co_nen_phut_bao_fallback(client):
    b = get(client, tf="1D", cursor=ny_to_unix("2024-02-15 12:00"))
    assert b["bars"][-1]["time"] == day_to_unix("2024-02-14") and b["fallback"]


def test_steps_tien_theo_nen_khung(client):
    cur = ny_to_unix("2024-03-05 10:37")
    assert get(client, tf="5m", cursor=cur, steps=1)["cursor"] == ny_to_unix("2024-03-05 10:40")
    assert get(client, tf="5m", cursor=cur, steps=3)["cursor"] == ny_to_unix("2024-03-05 10:50")
    b = get(client, tf="1D", cursor=cur, steps=1)
    assert b["cursor"] == ny_to_unix("2024-03-05 16:00") and b["bars"][-1]["close"] == pytest.approx(110 + 389 * 0.01)
    # cuối phiên -> phút kế tiếp là 09:30 hôm sau
    assert get(client, tf="1m", cursor=ny_to_unix("2024-03-05 16:00"), steps=1)["cursor"] == ny_to_unix("2024-03-06 09:31")
    end = get(client, tf="1D", cursor=ny_to_unix("2024-03-06 16:00"), steps=1)
    assert end["end"] and end["cursor"] == ny_to_unix("2024-03-06 16:00")


def test_after_va_before(client):
    cur = ny_to_unix("2024-03-05 10:37")
    upd = get(client, tf="5m", cursor=cur, after=ny_to_unix("2024-03-05 10:30"))
    assert [x["time"] for x in upd["bars"]] == [ny_to_unix("2024-03-05 10:30"), ny_to_unix("2024-03-05 10:35")]
    old = get(client, tf="5m", cursor=cur, before=ny_to_unix("2024-03-05 09:30"))
    assert old["bars"] and all(x["time"] < ny_to_unix("2024-03-05 09:30") for x in old["bars"])
    d = get(client, tf="1D", cursor=cur, before=day_to_unix("2024-03-04"))
    assert d["bars"][-1]["time"] == day_to_unix("2024-03-01")


def test_crypto_24_7(client):
    cur = utc_to_unix("2024-03-05 10:37")
    b = get(client, symbol="BTC/USD", asset="crypto", tf="1h", cursor=cur)
    assert b["bars"][-1]["time"] == utc_to_unix("2024-03-05 10:00")
    assert b["bars"][-1]["close"] == pytest.approx(50000 + 1440 + 10 * 60 + 36 + 1)
    assert get(client, symbol="BTC/USD", asset="crypto", tf="1h", cursor=cur, steps=1)["cursor"] == utc_to_unix("2024-03-05 11:00")


def test_tham_so_sai(client):
    assert client.get("/api/replay/bars", params={"symbol": "AAPL", "cursor": "abc"}).status_code == 422
    assert client.get("/api/replay/bars", params={"symbol": "AAPL", "cursor": "1", "tf": "2h"}).status_code == 400
    assert client.get("/api/replay/bars", params={"symbol": "A$", "cursor": "1"}).status_code == 400
