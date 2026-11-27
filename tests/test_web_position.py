"""Test box Long Position (backend): /api/position*, /api/position/boxes, live_state crypto, PATCH practice.
Client Alpaca giả — KHÔNG bao giờ gọi sàn thật."""

import json
import uuid
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from test_web_orders import count_events
from test_web_replay import FakeLoader

from paper_bot.live import LiveBot, get_crypto_sltp, set_crypto_sltp
from paper_bot.web.api.order_rules import stop_limit_price
from paper_bot.web.api.timeutil import ny_to_unix
from paper_bot.web.app import create_app

HOST = "http://127.0.0.1:8600"
GOOD = {"X-Requested-With": "paper-web", "Origin": HOST}
SL_ID, TP_ID, OLD_ID = (str(uuid.uuid4()) for _ in range(3))


class FakeTrading:
    def __init__(self, db):
        self.db, self.submitted, self.cancelled, self.replaced, self.events_at_submit = db, [], [], [], []
        self.orders, self.positions = [], []

    def submit_order(self, order_data=None, **k):
        self.events_at_submit.append(count_events(self.db))
        self.submitted.append(order_data)
        return NS(id="new-1", status=NS(value="accepted"))

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)

    def replace_order_by_id(self, oid, req):
        self.replaced.append((oid, req))
        return NS(id="r-" + oid)

    def get_asset(self, sym):
        return NS(min_order_size=0.0001, min_trade_increment=0.0001, price_increment=0.5)

    def get_orders(self, filter=None):
        return self.orders

    def get_all_positions(self):
        return self.positions


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "t.db"
    fake = FakeTrading(db)
    app = create_app(trading_client_fn=lambda: fake, db_path=db, live_state_path=tmp_path / "state.json")
    return NS(client=TestClient(app, base_url=HOST), fake=fake, db=db, state=tmp_path / "state.json")


def body(**kw):
    return {"asset": "stock", "symbol": "AAPL", "type": "limit", "qty": 10, "entry": 100.0, "sl": 95.0, "tp": 110.0,
            "confirm": "XAC NHAN", **kw}


def post(env, url, b, headers=GOOD):
    return env.client.post(url, json=b, headers=headers)


def test_co_phieu_bracket_limit_gtc(env):
    r = post(env, "/api/position", body())
    assert r.status_code == 200, r.text
    req = env.fake.submitted[0]
    assert (req.symbol, req.qty, req.limit_price, req.side.value) == ("AAPL", 10, 100.0, "buy")
    assert req.time_in_force.value == "gtc" and req.order_class.value == "bracket"
    assert req.take_profit.limit_price == 110.0 and req.stop_loss.stop_price == 95.0
    assert env.fake.events_at_submit == [1] and count_events(env.db, "web_position_order") == 1


def test_co_phieu_stop_va_market(env):
    assert post(env, "/api/position", body(type="stop")).status_code == 200
    req = env.fake.submitted[0]
    assert req.stop_price == 100.0 and req.limit_price == stop_limit_price(100.0, 110.0) == 100.5
    assert stop_limit_price(100.0, 100.3) == 100.29  # luôn < TP
    assert post(env, "/api/position", body(type="market")).status_code == 200
    assert env.fake.submitted[1].order_class.value == "bracket"


@pytest.mark.parametrize("bad", [{"confirm": None}, {"confirm": "xac nhan"}, {"sl": 100.0}, {"sl": 101.0},
                                 {"tp": 100.0}, {"tp": 90.0}, {"qty": 0}, {"qty": 1.5}, {"type": "short"},
                                 {"symbol": "aapl"}, {"entry": -1}])
def test_bi_chan_khong_gui(env, bad):
    r = post(env, "/api/position", body(**bad))
    assert 400 <= r.status_code < 500
    assert env.fake.submitted == [] and count_events(env.db) == 0


def test_sl_tp_sai_la_422(env):
    assert post(env, "/api/position", body(sl=100.0)).status_code == 422
    assert post(env, "/api/position", body(tp=99.0)).status_code == 422
    assert post(env, "/api/position/legs", body(sl=120.0, sl_id=SL_ID, tp_id=TP_ID)).status_code == 422
    assert env.fake.replaced == [] and env.fake.submitted == []


def test_guard(env):
    assert post(env, "/api/position", body(), headers={"Origin": HOST}).status_code == 403
    assert post(env, "/api/position", body(), headers={**GOOD, "Origin": "http://evil.com"}).status_code == 403
    assert env.fake.submitted == []


def test_sua_chan_sl_tp_bang_replace(env):
    r = post(env, "/api/position/legs", body(sl=96.0, tp=112.0, sl_id=SL_ID, tp_id=TP_ID))
    assert r.status_code == 200, r.text
    (a, ra), (b, rb) = env.fake.replaced
    assert (a, ra.stop_price, b, rb.limit_price) == (SL_ID, 96.0, TP_ID, 112.0)
    assert env.fake.submitted == [] and count_events(env.db, "web_position_legs") == 1


def test_sua_chan_khong_xac_nhan_hoac_thieu_id(env):
    assert post(env, "/api/position/legs", body(confirm="", sl_id=SL_ID, tp_id=TP_ID)).status_code == 403
    assert post(env, "/api/position/legs", body()).status_code == 422
    assert post(env, "/api/position/legs", body(sl_id="abc", tp_id=TP_ID)).status_code == 422
    assert env.fake.replaced == []


def test_dat_lai_lenh_cho_huy_roi_gui(env):
    r = post(env, "/api/position/repost", body(entry=101.0, old_order_id=OLD_ID))
    assert r.status_code == 200, r.text
    assert env.fake.cancelled == [OLD_ID] and env.fake.submitted[0].limit_price == 101.0
    assert post(env, "/api/position/repost", body(old_order_id="x")).status_code == 422
    assert post(env, "/api/position/repost", body(confirm="no", old_order_id=OLD_ID)).status_code == 403
    assert env.fake.cancelled == [OLD_ID] and len(env.fake.submitted) == 1


def crypto(**kw):
    return body(**{"asset": "crypto", "symbol": "BTC/USD", "qty": 0.01, "entry": 60000.0, "sl": 58000.0, "tp": 65000.0,
                   **kw})


def test_crypto_khong_bracket_ghi_live_state(env):
    r = post(env, "/api/position", crypto())
    assert r.status_code == 200, r.text
    req = env.fake.submitted[0]
    assert req.order_class is None and req.time_in_force.value == "gtc" and req.limit_price == 60000.0
    st = get_crypto_sltp("BTC/USD", env.state)
    assert st["pending"]["order_id"] == "new-1" and (st["pending"]["stop_loss"], st["pending"]["take_profit"]) == (58000, 65000)
    assert post(env, "/api/position", crypto(type="stop")).status_code == 422
    assert post(env, "/api/position", crypto(tp=59000.0)).status_code == 422
    assert len(env.fake.submitted) == 1


def test_crypto_sua_sltp_vao_live_state(env):
    set_crypto_sltp("BTC/USD", 1.0, 2.0, path=env.state)
    raw = json.loads(env.state.read_text())
    raw["live-crypto-paper"]["sltp"]["BTC/USD"]["stop_order_id"] = "s-9"
    env.state.write_text(json.dumps(raw))
    r = post(env, "/api/position/legs", crypto())
    assert r.status_code == 200 and r.json()["section"] == "sltp"
    s = get_crypto_sltp("BTC/USD", env.state)["sltp"]
    assert (s["stop_loss"], s["take_profit"], s["stop_order_id"]) == (58000, 65000, "s-9")
    assert post(env, "/api/position/legs", crypto(symbol="DOGE/XYZ")).status_code == 422
    assert env.fake.submitted == [] and env.fake.replaced == []


def leg(side, otype, oid, **kw):
    return NS(id=oid, side=NS(value=side), order_type=NS(value=otype), submitted_at=None, **kw)


def test_boxes_lenh_cho_va_vi_the(env):
    sl, tp = leg("sell", "stop", SL_ID, stop_price="95"), leg("sell", "limit", TP_ID, limit_price="110")
    pend = NS(id=OLD_ID, symbol="AAPL", side=NS(value="buy"), status=NS(value="new"), order_type=NS(value="limit"),
              limit_price="100", qty="10", submitted_at=None, legs=[leg("sell", "stop", "a", stop_price="94"),
                                                                    leg("sell", "limit", "b", limit_price="111")])
    env.fake.orders = [pend, NS(symbol="AAPL", **{k: v for k, v in vars(sl).items()}), NS(symbol="AAPL", **vars(tp))]
    env.fake.positions = [NS(symbol="AAPL", qty="5", avg_entry_price="99.5")]
    r = env.client.get("/api/position/boxes", params={"symbol": "AAPL"}).json()
    p, q = r["boxes"]
    assert (p["kind"], p["entry"], p["sl"], p["tp"], p["ids"]) == ("pending", 100, 94, 111, {"entry": OLD_ID})
    assert (q["kind"], q["entry"], q["qty"], q["ids"]) == ("position", 99.5, 5, {"sl": SL_ID, "tp": TP_ID})
    assert env.client.get("/api/position/boxes", params={"symbol": "MSFT"}).json()["boxes"] == []


def test_boxes_crypto_lay_sltp_tu_live_state(env):
    set_crypto_sltp("BTC/USD", 58000, 65000, path=env.state)
    env.fake.positions = [NS(symbol="BTCUSD", qty="0.01", avg_entry_price="60000")]
    r = env.client.get("/api/position/boxes", params={"symbol": "BTC/USD", "asset": "crypto"}).json()
    assert [(b["kind"], b["sl"], b["tp"]) for b in r["boxes"]] == [("position", 58000, 65000)]
    assert r["inc"] == {"price": 0.5, "qty": 0.0001}


# ---------- live_state: ghi nguyên tử + bot đọc lại ----------
def test_set_crypto_sltp_va_bot_doc_lai(tmp_path):
    path = tmp_path / "s.json"
    with pytest.raises(ValueError):
        set_crypto_sltp("BTC/USD", 10, 5, path=path)
    assert set_crypto_sltp("BTC/USD", 5, 10, order_id="o1", path=path) == "pending"
    assert not path.with_suffix(".tmp").exists()  # file tạm đã được os.replace
    fake = NS(state_path=path, store=NS(run_id="live-crypto-paper"), state={"halted": True, "pending": {}})
    LiveBot.reload_sltp(fake)
    assert fake.state["pending"]["BTC/USD"]["order_id"] == "o1" and fake.state["halted"] is True
    path.write_text("{hỏng")
    LiveBot.reload_sltp(fake)  # file hỏng: giữ bản trong bộ nhớ
    assert fake.state["pending"]["BTC/USD"]["stop_loss"] == 5


# ---------- practice: PATCH lệnh mua chờ (giá vào/SL/TP) ----------
def test_practice_patch_lenh_vao(tmp_path):
    c = TestClient(create_app(load_intraday_fn=FakeLoader(), trading_client_fn=lambda: None, db_path=tmp_path / "t.db"),
                   base_url=HOST)
    sid = c.post("/api/practice/sessions", json={"symbol": "AAPL", "cursor": ny_to_unix("2024-03-05 10:00")},
                 headers=GOOD).json()["id"]
    o = c.post(f"/api/practice/{sid}/orders", json={"side": "buy", "type": "limit", "qty": 3, "price": 105, "sl": 100,
                                                    "tp": 115}, headers=GOOD).json()["order"]
    r = c.patch(f"/api/practice/{sid}/orders/{o['id']}", json={"price": 106, "sl": 101, "tp": 116}, headers=GOOD)
    assert r.status_code == 200, r.text
    got = next(x for x in r.json()["orders"] if x["id"] == o["id"])
    assert (got["price"], got["sl"], got["tp"]) == (106, 101, 116)
    assert c.patch(f"/api/practice/{sid}/orders/{o['id']}", json={"sl": 107}, headers=GOOD).status_code == 422
