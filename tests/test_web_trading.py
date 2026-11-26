"""Test offline API tài khoản (client giả) và /api/markers (DB tạm)."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from paper_bot.contracts import Fill, Signal
from paper_bot.storage import Store
from paper_bot.web.api.timeutil import day_to_unix, ny_to_unix
from paper_bot.web.app import create_app


class FakeTrading:
    """Giả TradingClient: chỉ có các hàm đọc; có submit_order để bắt nếu lỡ bị gọi."""

    def __init__(self):
        self.filters, self.submitted = [], []

    def get_account(self):
        return SimpleNamespace(equity="10100", cash="4000", buying_power="8000", last_equity="10000", status="ACTIVE")

    def get_all_positions(self):
        return [SimpleNamespace(symbol="AAPL", qty="10", side="long", avg_entry_price="150", current_price="155",
                                market_value="1550", unrealized_pl="50", unrealized_plpc="0.0333")]

    def get_orders(self, filter=None):
        self.filters.append(filter)
        mk = lambda **k: SimpleNamespace(**{"limit_price": None, "stop_price": None, "order_class": None,
                                            "submitted_at": None, **k})
        return [mk(id="a1", symbol="AAPL", side=SimpleNamespace(value="sell"), qty="10",
                   order_type=SimpleNamespace(value="stop"), status=SimpleNamespace(value="held"), stop_price="140",
                   order_class=SimpleNamespace(value="bracket")),
                mk(id="a2", symbol="MSFT", side="buy", qty="5", order_type="limit", status="new", limit_price="300.5")]

    def submit_order(self, *a, **k):
        self.submitted.append((a, k))


@pytest.fixture
def fake():
    return FakeTrading()


@pytest.fixture
def client(fake, tmp_path):
    return TestClient(create_app(trading_client_fn=lambda: fake, db_path=tmp_path / "t.db"))


def test_account_tinh_pl_ngay(client):
    body = client.get("/api/account").json()
    assert body["equity"] == 10100 and body["cash"] == 4000 and body["buying_power"] == 8000
    assert body["day_pl"] == 100 and body["day_pl_pct"] == pytest.approx(1.0) and body["status"] == "ACTIVE"


def test_positions(client):
    p = client.get("/api/positions").json()["positions"]
    assert p == [{"symbol": "AAPL", "qty": 10, "side": "long", "avg_entry_price": 150, "current_price": 155,
                  "market_value": 1550, "unrealized_pl": 50, "unrealized_plpc": 0.0333}]


def test_orders_phang_gom_chan_sl_tp(client, fake):
    orders = client.get("/api/orders").json()["orders"]
    assert [o["id"] for o in orders] == ["a1", "a2"]
    assert orders[0]["stop_price"] == 140 and orders[0]["order_class"] == "bracket" and orders[0]["status"] == "held"
    assert orders[1]["limit_price"] == 300.5 and orders[1]["side"] == "buy"
    assert fake.filters[0].nested is False  # phẳng, không lồng chân lệnh
    assert fake.submitted == []  # các API đọc không bao giờ gửi lệnh


def test_thieu_key_tra_503_ro_rang(tmp_path):
    def no_key():
        raise RuntimeError("Thiếu ALPACA_API_KEY / ALPACA_API_SECRET")

    c = TestClient(create_app(trading_client_fn=no_key, db_path=tmp_path / "t.db"))
    for url in ("/api/account", "/api/positions", "/api/orders"):
        r = c.get(url)
        assert r.status_code == 503 and "ALPACA_API_KEY" in r.json()["detail"]


def test_loi_alpaca_tra_502(tmp_path):
    class Broken(FakeTrading):
        def get_account(self):
            raise ConnectionError("mất mạng")

    c = TestClient(create_app(trading_client_fn=Broken, db_path=tmp_path / "t.db"))
    r = c.get("/api/account")
    assert r.status_code == 502 and "mất mạng" in r.json()["detail"]


def make_db(path):
    st = Store(path, run_id="r1")
    st.log_signal(Signal("AAPL", "BUY", 150.0, datetime(2024, 3, 4, 10, 7), "RSI quá bán", 0.7,
                         strategy="rsi", prob=0.61, veto=False), regime="bull")
    st.log_signal(Signal("AAPL", "SELL", 160.0, datetime(2024, 3, 5), "MA cắt xuống", 0.6,
                         strategy="ma", veto=True, veto_reason="forecast rộng"))
    st.log_signal(Signal("MSFT", "BUY", 300.0, datetime(2024, 3, 4, 10, 7), "khác mã", 0.6))
    st.log_fill(Fill("o1", "AAPL", "buy", 10, 150.25, datetime(2024, 3, 4, 15, 8, tzinfo=timezone.utc)))  # 10:08 NY
    st.log_fill(Fill("o2", "AAPL", "sell", 10, 160.0, datetime(2024, 3, 5)))
    Store(path, run_id="r2").log_signal(Signal("AAPL", "BUY", 1.0, datetime(2024, 3, 4), "run khác", 0.5))
    st.conn.close()


def test_markers_signals_va_fills_theo_run_va_ma(tmp_path):
    make_db(tmp_path / "t.db")
    c = TestClient(create_app(db_path=tmp_path / "t.db"))
    body = c.get("/api/markers", params={"symbol": "AAPL", "run_id": "r1", "tf": "5m"}).json()
    buy, sell = body["signals"]  # chỉ AAPL của run r1, đã sắp theo thời gian
    assert buy["time"] == ny_to_unix("2024-03-04 10:05") and buy["position"] == "belowBar" and buy["shape"] == "arrowUp"
    assert buy["tooltip"]["reason"] == "RSI quá bán" and buy["tooltip"]["prob"] == 0.61 and buy["tooltip"]["regime"] == "bull"
    assert sell["time"] == ny_to_unix("2024-03-05 15:55") and sell["text"] == "S veto"  # ngày không giờ -> cuối ngày
    assert sell["color"] == "#9e9e9e" and sell["tooltip"]["veto_reason"] == "forecast rộng"
    f_buy, f_sell = body["fills"]
    assert f_buy["time"] == ny_to_unix("2024-03-04 10:05") and f_buy["text"] == "10@150.25"  # 15:08 UTC = 10:08 NY
    assert f_buy["tooltip"]["side"] == "buy" and f_sell["tooltip"]["side"] == "sell"


def test_markers_khung_ngay_dung_time_12h_utc(tmp_path):
    make_db(tmp_path / "t.db")
    c = TestClient(create_app(db_path=tmp_path / "t.db"))
    body = c.get("/api/markers", params={"symbol": "AAPL", "run_id": "r1"}).json()
    assert [m["time"] for m in body["signals"]] == [day_to_unix("2024-03-04"), day_to_unix("2024-03-05")]


def test_markers_khong_co_db_hoac_run_la(tmp_path):
    c = TestClient(create_app(db_path=tmp_path / "chua-co.db"))
    assert c.get("/api/markers", params={"symbol": "AAPL", "run_id": "r1"}).json() == {"signals": [], "fills": []}
    make_db(tmp_path / "t.db")
    c = TestClient(create_app(db_path=tmp_path / "t.db"))
    assert c.get("/api/markers", params={"symbol": "AAPL", "run_id": "khong-co"}).json() == {"signals": [], "fills": []}
    assert c.get("/api/markers", params={"symbol": "aapl;--", "run_id": "r1"}).status_code == 400
    assert c.get("/api/markers", params={"symbol": "AAPL"}).status_code == 422  # thiếu run_id
