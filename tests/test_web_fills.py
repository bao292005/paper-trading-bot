"""Test offline /api/account/fills: chuẩn hoá mã crypto, nguồn lệnh, cache, 503, không gửi lệnh."""

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from paper_bot.storage import Store
from paper_bot.web.api import account_fills
from paper_bot.web.app import create_app

NOW = datetime.now(timezone.utc)


def ev(x):
    return SimpleNamespace(value=x)


def order(oid, side="buy", qty="0.02", price="1722.8", typ="market", cls=None, legs=None, symbol="ETHUSD",
          filled_ago=60, submitted_ago=3600):
    now = datetime.now(timezone.utc)  # giờ lúc gọi (không dùng NOW lúc import: cả bộ test chạy lâu sẽ lệch event web)
    return SimpleNamespace(id=oid, symbol=symbol, side=ev(side), filled_qty=qty, filled_avg_price=price,
                           filled_at=None if qty is None else now - timedelta(seconds=filled_ago),
                           submitted_at=now - timedelta(seconds=submitted_ago), created_at=None,
                           order_type=ev(typ), order_class=ev(cls) if cls else None, legs=legs, status=ev("filled"))


class FakeTrading:
    def __init__(self, rows):
        self.rows, self.filters, self.submitted = rows, [], []

    def get_orders(self, filter=None):
        self.filters.append(filter)
        return self.rows

    def submit_order(self, *a, **k):
        self.submitted.append((a, k))


def make(rows, tmp_path):
    fake = FakeTrading(rows)
    return TestClient(create_app(trading_client_fn=lambda: fake, db_path=tmp_path / "t.db")), fake


def get(c, **p):
    return c.get("/api/account/fills", params={"symbol": "ETH/USD", "asset": "crypto", **p})


def test_chuan_hoa_ma_crypto_va_bo_lenh_khong_khop(tmp_path):
    rows = [order("a"), order("b", qty="0"), order("c", qty=None), order("d", symbol="BTCUSD")]
    c, fake = make(rows, tmp_path)
    body = get(c).json()
    assert [f["id"] for f in body["fills"]] == ["a"] and body["symbol"] == "ETH/USD"
    f = body["fills"][0]
    assert f["side"] == "buy" and f["qty"] == 0.02 and f["price"] == 1722.8 and f["type"] == "market"
    assert f["source"] == "other" and f["source_text"] == "khác (dashboard Alpaca)"
    req = fake.filters[0]
    assert req.symbols == ["ETH/USD"] and req.nested is True and req.limit == 500 and req.status.value == "closed"
    assert fake.submitted == []  # chỉ đọc


def test_bracket_tp_sl_va_lenh_vao(tmp_path):
    tp = order("tp", side="sell", price="1725.1", typ="limit", filled_ago=10)
    sl = order("sl", side="sell", qty="0", typ="stop")  # chân SL chưa khớp -> bỏ
    parent = order("p", cls="bracket", legs=[tp, sl], filled_ago=100)
    oco_sl = order("o2", side="sell", price="1700", typ="stop", cls="oco", filled_ago=5)
    oco = order("o1", side="sell", qty="0", typ="limit", cls="oco", legs=[oco_sl])
    c, _ = make([parent, oco], tmp_path)
    got = {f["id"]: f for f in get(c).json()["fills"]}
    assert set(got) == {"p", "tp", "o2"}
    assert got["tp"]["source"] == "bracket_tp" and got["tp"]["exit_kind"] == "tp" and got["tp"]["source_text"] == "bracket TP"
    assert got["p"]["source"] == "other" and got["p"]["exit_kind"] is None  # chân vào của bracket không phải TP/SL
    assert got["o2"]["source"] == "oco_sl" and got["o2"]["exit_kind"] == "sl"


def test_nguon_server_web_bot(tmp_path):
    db = tmp_path / "t.db"
    Store(path=db, run_id="web-sltp").log_event("sltp_hit", "x", data={"order_id": "srv", "kind": "sl_hit"})
    Store(path=db, run_id="web-manual").log_event("web_manual_order", "buy", data={"symbol": "ETH/USD"})
    Store(path=db, run_id="web-manual").log_event("web_position_order", "buy", data={"symbol": "BTC/USD"})
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO orders (run_id, broker_order_id, symbol, side) VALUES ('live-crypto-paper', 'bot1', 'ETH/USD', 'buy')")
    conn.execute("INSERT INTO orders (run_id, broker_order_id, symbol, side) VALUES ('bt-x', 'bt1', 'ETH/USD', 'buy')")
    conn.commit()
    conn.close()
    rows = [order("srv", side="sell", typ="market"),
            order("w", submitted_ago=3),               # gửi cách event web 3 giây, cùng mã -> web
            order("w_far", submitted_ago=300),         # cách quá 10 giây -> không phải web
            order("bot1"), order("bt1"),               # bot chỉ tính run live-*
            order("x", submitted_ago=7200)]
    got = {f["id"]: f for f in get(TestClient(create_app(trading_client_fn=lambda: FakeTrading(rows), db_path=db))).json()["fills"]}
    assert got["srv"]["source"] == "server" and got["srv"]["exit_kind"] == "sl" and got["srv"]["source_text"] == "server SL/TP"
    assert got["w"]["source"] == "web"
    assert got["w_far"]["source"] == "other"
    assert got["bot1"]["source"] == "bot" and got["bt1"]["source"] == "other" and got["x"]["source"] == "other"


def test_event_web_khac_ma_khong_khop(tmp_path):
    db = tmp_path / "t.db"
    Store(path=db, run_id="web-manual").log_event("web_manual_order", "buy", data={"symbol": "BTC/USD"})
    c = TestClient(create_app(trading_client_fn=lambda: FakeTrading([order("a", submitted_ago=2)]), db_path=db))
    assert get(c).json()["fills"][0]["source"] == "other"


def test_since_loc_theo_gio_khop_va_sai_dinh_dang(tmp_path):
    rows = [order("old", filled_ago=86400), order("new", filled_ago=60)]
    c, fake = make(rows, tmp_path)
    since = str(int((NOW - timedelta(hours=1)).timestamp()))
    body = get(c, since=since).json()
    assert [f["id"] for f in body["fills"]] == ["new"] and body["since"] == int(since)
    assert fake.filters[0].after < datetime.fromtimestamp(int(since), timezone.utc)  # lùi thêm vài ngày cho lệnh GTC cũ
    assert get(c, since="không phải giờ").status_code == 400
    assert get(c, symbol="DOGE/USD").status_code == 400


def test_cache_10_giay(tmp_path, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(account_fills, "now", lambda: clock["t"])
    c, fake = make([order("a")], tmp_path)
    get(c)
    clock["t"] += 9
    get(c)
    assert len(fake.filters) == 1  # trong 10s dùng cache
    get(c, since="1700000000")
    assert len(fake.filters) == 2  # since khác -> khoá cache khác
    clock["t"] += 2
    get(c)
    assert len(fake.filters) == 3  # quá 10s -> hỏi lại


def test_thieu_key_503(tmp_path):
    def no_key():
        raise RuntimeError("Thiếu ALPACA_API_KEY / ALPACA_API_SECRET")

    c = TestClient(create_app(trading_client_fn=no_key, db_path=tmp_path / "t.db"))
    r = get(c)
    assert r.status_code == 503 and "ALPACA_API_KEY" in r.json()["detail"]


def test_co_phieu_va_loi_alpaca_502(tmp_path):
    c, _ = make([order("s", symbol="AAPL", qty="10", price="150")], tmp_path)
    r = c.get("/api/account/fills", params={"symbol": "AAPL"})
    assert r.json()["fills"][0]["qty"] == 10 and r.json()["asset"] == "stock"

    class Boom(FakeTrading):
        def get_orders(self, filter=None):
            raise OSError("mạng đứt")

    c2 = TestClient(create_app(trading_client_fn=lambda: Boom([]), db_path=tmp_path / "t.db"))
    assert get(c2).status_code == 502
