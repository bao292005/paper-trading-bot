"""Gắn SL/TP cho vị thế luyện tập đang có (kể cả vị thế mở không kèm SL/TP): sim + API /api/practice/{sid}/sltp."""

import pytest
from test_sim_account import T0, acc, bar
from test_web_practice import env, new, post  # noqa: F401  (env là fixture)


def pending(a):
    return sorted((o.role, o.price, o.qty) for o in a.orders.values() if o.status == "pending")


def test_gan_sl_tp_cho_long_roi_cham_tp():
    a = acc()
    a.place_order("buy", "market", 10)
    a.advance([bar(0, 100, 101, 99, 100)], T0 + 60)
    a.set_position_sltp(95, 105)
    assert pending(a) == [("sl", 95, 10), ("tp", 105, 10)]
    a.advance([bar(1, 101, 106, 100, 104)], T0 + 120)
    assert a.qty == 0 and a.fills[-1]["reason"] == "take_profit" and pending(a) == []  # OCO: SL bị huỷ


def test_sua_thi_thay_cap_cu_go_thi_het():
    a = acc()
    a.place_order("buy", "market", 10, sl=90, tp=110)
    a.advance([bar(0, 100, 101, 99, 100)], T0 + 60)
    a.set_position_sltp(97, 104)
    assert pending(a) == [("sl", 97, 10), ("tp", 104, 10)]
    a.set_position_sltp(None, None)
    assert pending(a) == []


def test_short_va_sai_phia():
    a = acc()
    a.place_order("sell", "market", 5)
    a.advance([bar(0, 100, 101, 99, 100)], T0 + 60)
    with pytest.raises(ValueError):
        a.set_position_sltp(95, 105)  # short: SL phải trên giá
    a.set_position_sltp(105, 95)
    a.advance([bar(1, 101, 106, 100, 104)], T0 + 120)
    assert a.qty == 0 and a.fills[-1]["reason"] == "stop_loss"


def test_khong_co_vi_the():
    with pytest.raises(ValueError):
        acc().set_position_sltp(95, 105)


def test_api_practice_sltp(env):  # noqa: F811
    c, _ = env
    sid = new(c)["id"]
    post(c, f"/api/practice/{sid}/sltp", {"sl": 100, "tp": 120}, 422)  # chưa có vị thế
    post(c, f"/api/practice/{sid}/orders", {"side": "buy", "type": "market", "qty": 10})
    post(c, f"/api/practice/{sid}/advance", {"to": new(c)["cursor"] + 300})
    assert c.post(f"/api/practice/{sid}/sltp", json={"sl": 100, "tp": 120}).status_code == 403  # guard
    post(c, f"/api/practice/{sid}/sltp", {"sl": 120, "tp": 130}, 422)  # SL trên giá
    r = post(c, f"/api/practice/{sid}/sltp", {"sl": 100, "tp": 120})
    assert sorted(o["role"] for o in r["orders"]) == ["sl", "tp"]
    assert post(c, f"/api/practice/{sid}/sltp", {"sl": None, "tp": None})["orders"] == []
