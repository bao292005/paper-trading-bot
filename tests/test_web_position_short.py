"""Test SHORT trên Alpaca paper (client giả, KHÔNG gọi sàn thật): bracket BÁN, kiểm shortable/easy_to_borrow,
crypto short -> 422, đang long -> 422, thiếu XAC NHAN -> 403, box short, đóng vị thế; short luyện tập qua /api/practice."""

from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from test_web_orders import count_events
from test_web_position import GOOD, HOST, FakeTrading, post
from test_web_practice import NoAlpaca
from test_web_replay import FakeLoader

from paper_bot.web.api.order_rules import stop_limit_price
from paper_bot.web.api.timeutil import ny_to_unix
from paper_bot.web.app import create_app


class ShortFake(FakeTrading):
    def __init__(self, db):
        super().__init__(db)
        self.flags = {"shortable": True, "easy_to_borrow": True}

    def get_asset(self, sym):
        return NS(min_order_size=0.0001, min_trade_increment=0.0001, price_increment=0.01, **self.flags)


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "t.db"
    fake = ShortFake(db)
    app = create_app(trading_client_fn=lambda: fake, db_path=db, live_state_path=tmp_path / "state.json")
    return NS(client=TestClient(app, base_url=HOST), fake=fake, db=db)


def short(**kw):
    return {"asset": "stock", "symbol": "AAPL", "side": "short", "type": "limit", "qty": 10,
            "entry": 100.0, "sl": 105.0, "tp": 90.0, "confirm": "XAC NHAN", **kw}


def test_bracket_ban_gtc(env):
    r = post(env, "/api/position", short())
    assert r.status_code == 200, r.text
    req = env.fake.submitted[0]
    assert (req.side.value, req.qty, req.limit_price, req.order_class.value, req.time_in_force.value) == (
        "sell", 10, 100.0, "bracket", "gtc")
    assert req.take_profit.limit_price == 90.0 < 100.0 < req.stop_loss.stop_price == 105.0
    assert env.fake.events_at_submit == [1] and count_events(env.db, "web_position_order") == 1


def test_short_stop_va_market(env):
    assert post(env, "/api/position", short(type="stop")).status_code == 200
    req = env.fake.submitted[0]
    assert req.side.value == "sell" and req.stop_price == 100.0
    assert req.limit_price == stop_limit_price(100.0, 90.0, "short") == 99.5  # stop bán: limit thấp hơn 0.5%
    assert stop_limit_price(100.0, 99.8, "short") == 99.81                    # nhưng vẫn > TP
    assert post(env, "/api/position", short(type="market")).status_code == 200
    assert env.fake.submitted[1].side.value == "sell"


@pytest.mark.parametrize("bad", [{"sl": 95.0}, {"tp": 101.0}, {"qty": 1.5}, {"side": "xx"}])
def test_short_sai_muc_hoac_qty(env, bad):
    assert post(env, "/api/position", short(**bad)).status_code == 422
    assert env.fake.submitted == []


def test_khong_shortable_422_khong_gui(env):
    env.fake.flags["easy_to_borrow"] = False
    r = post(env, "/api/position", short())
    assert r.status_code == 422 and "easy_to_borrow" in r.json()["detail"]
    env.fake.flags = {"shortable": False, "easy_to_borrow": True}
    assert "shortable" in post(env, "/api/position", short()).json()["detail"]
    assert env.fake.submitted == [] and count_events(env.db, "web_position_order") == 0


def test_crypto_short_422_khong_gui(env):
    r = post(env, "/api/position", short(asset="crypto", symbol="BTC/USD", qty=0.01, entry=50000.0, sl=51000.0, tp=48000.0))
    assert r.status_code == 422 and "không cho bán khống crypto" in r.json()["detail"]
    assert env.fake.submitted == []


def test_thieu_xac_nhan_403(env):
    assert post(env, "/api/position", short(confirm="xac nhan")).status_code == 403
    assert post(env, "/api/position", short(), headers={**GOOD, "Origin": "http://evil.com"}).status_code == 403
    assert env.fake.submitted == []


def test_dang_long_thi_khong_short(env):
    env.fake.positions = [NS(symbol="AAPL", qty="5", avg_entry_price="99")]
    r = post(env, "/api/position", short())
    assert r.status_code == 422 and "đóng vị thế long" in r.json()["detail"]
    assert env.fake.submitted == []


def test_sua_chan_short_theo_chieu(env):
    b = {"asset": "stock", "symbol": "AAPL", "side": "short", "entry": 100.0, "sl": 104.0, "tp": 92.0,
         "sl_id": "a1b2c3d4-0000-4000-8000-000000000001", "confirm": "XAC NHAN"}
    assert post(env, "/api/position/legs", b).status_code == 200
    assert env.fake.replaced[0][1].stop_price == 104.0
    assert post(env, "/api/position/legs", {**b, "sl": 96.0}).status_code == 422


def leg(side, t, oid, **kw):
    return NS(id=oid, side=NS(value=side), order_type=NS(value=t), submitted_at=None, **kw)


def test_box_short_cho_va_vi_the(env):
    pend = NS(id="p1", symbol="AAPL", side=NS(value="sell"), status=NS(value="new"), order_type=NS(value="limit"),
              limit_price="100", qty="10", submitted_at=None,
              legs=[leg("buy", "stop", "s", stop_price="105"), leg("buy", "limit", "t", limit_price="90")])
    env.fake.orders = [pend, NS(symbol="AAPL", **vars(leg("buy", "stop", "s2", stop_price="110"))),
                       NS(symbol="AAPL", **vars(leg("buy", "limit", "t2", limit_price="95")))]
    env.fake.positions = [NS(symbol="AAPL", qty="-5", avg_entry_price="102")]
    boxes = env.client.get("/api/position/boxes", params={"symbol": "AAPL"}).json()["boxes"]
    assert [(b["kind"], b["side"], b["entry"], b["sl"], b["tp"]) for b in boxes] == [
        ("pending", "short", 100.0, 105.0, 90.0), ("position", "short", 102.0, 110.0, 95.0)]
    assert boxes[1]["qty"] == -5 and boxes[1]["ids"] == {"sl": "s2", "tp": "t2"}


def test_dong_short_gui_lenh_mua(env):
    env.fake.positions = [NS(symbol="AAPL", qty="-7", avg_entry_price="102")]
    assert post(env, "/api/position/close", {"symbol": "AAPL"}).status_code == 403  # thiếu XAC NHAN
    r = post(env, "/api/position/close", {"symbol": "AAPL", "confirm": "XAC NHAN"})
    assert r.status_code == 200 and r.json()["side"] == "buy"
    req = env.fake.submitted[0]
    assert (req.side.value, req.qty, req.time_in_force.value) == ("buy", 7, "day")
    assert count_events(env.db, "web_close_position") == 1
    assert post(env, "/api/position/close", {"symbol": "MSFT", "confirm": "XAC NHAN"}).status_code == 404
    assert post(env, "/api/position/close", {"symbol": "bad!", "confirm": "XAC NHAN"}).status_code == 422


def test_practice_short_qua_api(tmp_path):
    c = TestClient(create_app(load_intraday_fn=FakeLoader(), trading_client_fn=NoAlpaca, db_path=tmp_path / "t.db"),
                   base_url=HOST)

    def p(url, b=None):
        r = c.post(url, json=b or {}, headers=GOOD)
        assert r.status_code == 200, r.text
        return r.json()

    sid = p("/api/practice/sessions", {"symbol": "AAPL", "cursor": ny_to_unix("2024-03-05 10:00")})["id"]
    p(f"/api/practice/{sid}/orders", {"side": "sell", "type": "market", "qty": 10, "sl": 200, "tp": 50})
    r = p(f"/api/practice/{sid}/advance", {"to": ny_to_unix("2024-03-05 10:05"), "steps": 5})
    assert r["position"]["qty"] == -10 and r["position"]["side"] == "short"
    assert sorted(o["role"] for o in r["orders"]) == ["sl", "tp"]
    assert all(o["side"] == "buy" for o in r["orders"])
