"""API /api/sltp (SL/TP cho mọi vị thế): cổ phiếu OCO trên sàn, crypto ghi live_state. Client GIẢ, không gọi Alpaca."""

from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from test_web_orders import count_events

from paper_bot import live
from paper_bot.web.api import sltp as sltp_api
from paper_bot.web.app import create_app

HOST = "http://127.0.0.1:8600"
GOOD = {"X-Requested-With": "paper-web", "Origin": HOST}


def order(oid, side, otype, cls="simple", status="new", legs=None, stop=None, limit=None, sym="AAPL"):
    return NS(id=oid, symbol=sym, side=NS(value=side), order_type=NS(value=otype), order_class=NS(value=cls),
              status=NS(value=status), legs=legs, stop_price=stop, limit_price=limit)


def oco(sl=95.0, tp=110.0, side="sell"):
    return order("oco-tp", side, "limit", "oco", legs=[order("oco-sl", side, "stop", "oco", stop=sl)], limit=tp)


class FakeTrading:
    def __init__(self, qty=10, sym="AAPL", price=100.0, replace_fail=False):
        self.positions = [NS(symbol=sym, qty=str(qty), current_price=str(price))] if qty else []
        self.orders, self.submitted, self.cancelled, self.replaced = [], [], [], []
        self.replace_fail = replace_fail

    def get_all_positions(self):
        return self.positions

    def get_orders(self, filter=None):
        return self.orders

    def submit_order(self, order_data=None):
        self.submitted.append(order_data)
        return NS(id="new-oco", status=NS(value="accepted"))

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)
        self.orders = [o for o in self.orders if o.id != oid]  # huỷ mẹ OCO = huỷ cả cặp

    def replace_order_by_id(self, oid, req):
        if self.replace_fail:
            raise ConnectionError("replace lỗi")
        self.replaced.append((oid, req))
        return NS(id="r-" + oid)


@pytest.fixture
def mk(tmp_path, monkeypatch):
    monkeypatch.setattr(sltp_api, "WAIT_SLEEP", 0)

    def make(**kw):
        fake = FakeTrading(**kw)
        app = create_app(trading_client_fn=lambda: fake, db_path=tmp_path / "t.db",
                         live_state_path=tmp_path / "state.json", sltp_price_fn=lambda s: (1.0, "mid"))
        return NS(c=TestClient(app, base_url=HOST), fake=fake, db=tmp_path / "t.db", st=tmp_path / "state.json")
    return make


def body(**kw):
    return {"asset": "stock", "symbol": "AAPL", "sl": 95.0, "tp": 110.0, "confirm": "XAC NHAN", **kw}


def post(e, b, url="/api/sltp"):
    return e.c.post(url, json=b, headers=GOOD)


def test_co_phieu_chua_co_sl_tp_dat_oco(mk):
    e = mk()
    r = post(e, body())
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "oco_new"
    (req,) = e.fake.submitted
    assert (req.symbol, req.qty, req.side.value, req.time_in_force.value, req.order_class.value) == \
        ("AAPL", 10, "sell", "gtc", "oco")
    assert req.take_profit.limit_price == 110.0 and req.stop_loss.stop_price == 95.0
    assert count_events(e.db, "web_sltp_set") == 1


def test_short_dat_oco_chieu_mua(mk):
    e = mk(qty=-5)
    assert post(e, body(sl=105.0, tp=90.0)).status_code == 200
    req = e.fake.submitted[0]
    assert (req.qty, req.side.value) == (5, "buy")
    assert post(e, body()).status_code == 422  # short mà SL < TP


def test_co_oco_thi_replace_tung_chan(mk):
    e = mk()
    e.fake.orders = [oco()]
    r = post(e, body(sl=96.0, tp=112.0))
    assert r.status_code == 200 and r.json()["mode"] == "replace"
    (a, ra), (b, rb) = e.fake.replaced
    assert (a, ra.stop_price, b, rb.limit_price) == ("oco-sl", 96.0, "oco-tp", 112.0)
    assert e.fake.submitted == []


def test_bracket_da_khop_sua_chan(mk):
    e = mk()
    legs = [order("b-tp", "sell", "limit", "bracket", limit=110.0), order("b-sl", "sell", "stop", "bracket", stop=95.0)]
    e.fake.orders = [order("parent", "buy", "limit", "bracket", status="filled", legs=legs)]
    assert post(e, body(sl=97.0)).status_code == 200
    assert [x[0] for x in e.fake.replaced] == ["b-sl", "b-tp"]


def test_replace_loi_thi_huy_roi_dat_lai(mk):
    e = mk(replace_fail=True)
    e.fake.orders = [oco()]
    r = post(e, body(sl=96.0))
    assert r.status_code == 200 and r.json()["mode"] == "cancel_replace"
    assert e.fake.cancelled == ["oco-tp"] and e.fake.submitted[0].stop_loss.stop_price == 96.0


def test_lenh_vao_dang_cho_khong_phai_sl_tp(mk):
    e = mk()
    e.fake.orders = [order("buy1", "sell", "limit")]  # lệnh bán thường của người dùng: không động vào
    assert post(e, body()).status_code == 200
    assert e.fake.cancelled == [] and e.fake.submitted[0].order_class.value == "oco"


@pytest.mark.parametrize("bad, code", [({"confirm": ""}, 403), ({"confirm": "xac nhan"}, 403),
                                       ({"sl": 120.0}, 422), ({"tp": 90.0}, 422), ({"symbol": "aapl"}, 422),
                                       ({"sl": -1}, 422), ({"symbol": "MSFT"}, 404), ({"asset": "fx"}, 422)])
def test_bi_chan_khong_gui(mk, bad, code):
    e = mk()
    assert post(e, body(**bad)).status_code == code
    assert e.fake.submitted == [] and count_events(e.db) == 0


def test_gia_da_vuot_qua_can_xac_nhan_them(mk):
    e = mk(price=94.0)  # giá đã dưới SL 95: đặt là khớp ngay
    assert post(e, body()).status_code == 409 and e.fake.submitted == []
    assert post(e, body(allow_cross="yes")).status_code == 409  # phải đúng true
    assert post(e, body(allow_cross=True)).status_code == 200


def test_thieu_header_403(mk):
    e = mk()
    assert e.c.post("/api/sltp", json=body(), headers={"Origin": HOST}).status_code == 403


def test_go_sl_tp_co_phieu(mk):
    e = mk()
    e.fake.orders = [oco()]
    hdr = GOOD  # DELETE cũng qua guard
    assert e.c.request("DELETE", "/api/sltp", json={"symbol": "AAPL"}, headers=hdr).status_code == 403
    r = e.c.request("DELETE", "/api/sltp", json={"symbol": "AAPL", "confirm": "XAC NHAN"}, headers=hdr)
    assert r.status_code == 200 and e.fake.cancelled == ["oco-tp"]
    r = e.c.request("DELETE", "/api/sltp", json={"symbol": "AAPL", "confirm": "XAC NHAN"}, headers=hdr)
    assert r.status_code == 404


def test_crypto_ghi_live_state(mk):
    e = mk(sym="BTCUSD", qty=0.5, price=60000.0)
    r = post(e, body(asset="crypto", symbol="BTC/USD", sl=58000.0, tp=65000.0))
    assert r.status_code == 200 and r.json()["kind"] == "server" and e.fake.submitted == []
    ent = live.crypto_sltp_entries(e.st)["BTC/USD"]
    assert (ent["stop_loss"], ent["take_profit"], ent["side"], ent["qty_mode"], ent["created_by"]) == \
        (58000.0, 65000.0, "long", "all", "web")
    lv = e.c.get("/api/sltp").json()["levels"]["BTC/USD"]
    assert (lv["sl"], lv["tp"], lv["source"]) == (58000.0, 65000.0, "server")
    assert post(e, body(asset="crypto", symbol="BTC/USD", sl=66000.0, tp=67000.0)).status_code == 409
    r = e.c.request("DELETE", "/api/sltp", json={"asset": "crypto", "symbol": "BTC/USD", "confirm": "XAC NHAN"},
                    headers=GOOD)
    assert r.status_code == 200 and live.crypto_sltp_entries(e.st) == {}


def test_crypto_dang_kich_hoat_thi_khong_sua(mk):
    e = mk(sym="BTCUSD", qty=0.5, price=60000.0)
    live.set_crypto_sltp("BTC/USD", 58000, 65000, path=e.st)
    live.claim_crypto_sltp("BTC/USD", "web-sltp", e.st)
    assert post(e, body(asset="crypto", symbol="BTC/USD", sl=57000.0, tp=65000.0)).status_code == 409
    r = e.c.request("DELETE", "/api/sltp", json={"asset": "crypto", "symbol": "BTC/USD", "confirm": "XAC NHAN"},
                    headers=GOOD)
    assert r.status_code == 409


def test_levels_co_phieu_nguon_san(mk):
    e = mk()
    e.fake.orders = [oco()]
    lv = e.c.get("/api/sltp").json()["levels"]["AAPL"]
    assert (lv["sl"], lv["tp"], lv["source"], lv["kind"]) == (95.0, 110.0, "sàn", "oco")


def test_box_vi_the_co_oco(mk):
    e = mk()
    e.fake.positions[0].avg_entry_price = "100.0"
    e.fake.orders = [oco()]
    (b,) = e.c.get("/api/position/boxes", params={"symbol": "AAPL"}).json()["boxes"]
    assert (b["kind"], b["side"], b["sl"], b["tp"], b["ids"]) == \
        ("position", "long", 95.0, 110.0, {"sl": "oco-sl", "tp": "oco-tp"})


def test_status_va_cong_tac(mk):
    e = mk()
    s = e.c.get("/api/sltp/status").json()
    assert s["enabled"] is True and s["running"] is False and "now" in s
    assert post(e, {"enabled": False}, "/api/sltp/monitor").status_code == 403
    assert post(e, {"enabled": "no", "confirm": "XAC NHAN"}, "/api/sltp/monitor").status_code == 422
    assert post(e, {"enabled": False, "confirm": "XAC NHAN"}, "/api/sltp/monitor").status_code == 200
    assert e.c.get("/api/sltp/status").json()["enabled"] is False
