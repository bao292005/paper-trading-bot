"""Test API tài khoản luyện tập /api/practice/*: guard, khớp giả lập, chống nhìn trước, SL/TP, lưu DB (dữ liệu giả)."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from test_web_replay import FakeLoader

from paper_bot.web.api.timeutil import ny_to_unix, utc_to_unix
from paper_bot.web.app import create_app

HOST = "http://127.0.0.1:8600"
GOOD = {"X-Requested-With": "paper-web", "Origin": HOST}
DEL = {**GOOD, "Content-Type": "application/json"}  # guard đòi JSON cả với DELETE (sendJSON luôn gửi)


class NoAlpaca:
    def __getattr__(self, name):
        raise AssertionError("replay KHÔNG được gọi Alpaca")


@pytest.fixture
def env(tmp_path):
    loader = FakeLoader()
    app = create_app(load_intraday_fn=loader, trading_client_fn=NoAlpaca, db_path=tmp_path / "t.db")
    return TestClient(app, base_url=HOST), loader


def post(c, url, body=None, code=200):
    r = c.post(url, json=body or {}, headers=GOOD)
    assert r.status_code == code, r.text
    return r.json()


def new(c, cursor="2024-03-05 10:00", **kw):
    return post(c, "/api/practice/sessions", {"symbol": "AAPL", "cursor": ny_to_unix(cursor), **kw})


def adv(c, sid, t, **kw):
    return post(c, f"/api/practice/{sid}/advance", {"to": ny_to_unix(t), **kw})


def test_guard_va_json(env):
    c, _ = env
    body = {"symbol": "AAPL", "cursor": ny_to_unix("2024-03-05 10:00")}
    assert c.post("/api/practice/sessions", json=body).status_code == 403
    assert c.post("/api/practice/sessions", json=body, headers={**GOOD, "Origin": "http://evil.com"}).status_code == 403
    assert c.post("/api/practice/sessions", content="x", headers=GOOD).status_code == 415
    sid = new(c)["id"]
    assert c.delete(f"/api/practice/{sid}/orders/o1").status_code == 403
    assert c.patch(f"/api/practice/{sid}/orders/o1", json={}).status_code == 403


def test_market_khop_open_phut_ke_tiep(env):
    c, _ = env
    s = new(c)
    assert s["cash"] == 100000 and s["last_price"] == pytest.approx(110.29) and s["fee_bps"] == 10
    post(c, f"/api/practice/{s['id']}/orders", {"side": "buy", "type": "market", "qty": 10})
    r = adv(c, s["id"], "2024-03-05 10:05", steps=5)
    assert [f["price"] for f in r["new_fills"]] == [pytest.approx(110.295)]
    assert r["new_fills"][0]["time"] == ny_to_unix("2024-03-05 10:00")
    assert r["position"]["qty"] == 10 and r["cursor"] == ny_to_unix("2024-03-05 10:05") and r["bars_seen"] == 5
    assert r["cash"] == pytest.approx(100000 - 1102.95 * 1.001)


def test_khong_nhin_truoc(env):
    c, loader = env
    sid = new(c)["id"]
    post(c, f"/api/practice/{sid}/orders", {"side": "buy", "type": "stop", "qty": 1, "price": 110.50})
    n = len(loader.calls)
    r = adv(c, sid, "2024-03-05 10:10")
    assert r["new_fills"] == [] and len(r["orders"]) == 1  # giá chạm 110.50 lúc 10:18 -> chưa được khớp
    to_local = pd.Timestamp("2024-03-05 10:10")
    assert all(end < to_local for _, tf, _, end, _ in loader.calls[n:] if tf == "1m")  # không tải phút >= to
    r = adv(c, sid, "2024-03-05 10:20")
    assert r["new_fills"][0]["price"] == pytest.approx(110.50) and r["new_fills"][0]["time"] == ny_to_unix("2024-03-05 10:18")
    assert post(c, f"/api/practice/{sid}/advance", {"to": ny_to_unix("2024-03-05 10:00")}, 422)


def test_khong_ban_khong_va_loi(env):
    c, _ = env
    sid = new(c)["id"]
    assert "bán khống" in post(c, f"/api/practice/{sid}/orders", {"side": "sell", "type": "market", "qty": 1}, 422)["detail"]
    post(c, f"/api/practice/{sid}/close", code=422)
    post(c, f"/api/practice/{sid}/orders", {"side": "buy", "type": "limit", "qty": 1.5, "price": 100}, 422)
    assert c.delete(f"/api/practice/{sid}/orders/o9", headers=DEL).status_code == 404
    assert c.get("/api/practice/sessions/zzz").status_code == 404
    post(c, "/api/practice/sessions", {"symbol": "AAPL", "cursor": "abc"}, 422)


def test_bracket_sua_tp_bang_patch_roi_huy(env):
    c, _ = env
    sid = new(c)["id"]
    post(c, f"/api/practice/{sid}/orders", {"side": "buy", "type": "market", "qty": 10, "sl": 109, "tp": 111})
    r = adv(c, sid, "2024-03-05 10:05")
    tp = next(o for o in r["orders"] if o["role"] == "tp")
    assert c.patch(f"/api/practice/{sid}/orders/{tp['id']}", json={"price": 110.0}, headers=GOOD).status_code == 422
    assert c.patch(f"/api/practice/{sid}/orders/{tp['id']}", json={"price": 110.40}, headers=GOOD).status_code == 200
    r = adv(c, sid, "2024-03-05 10:20")
    assert r["new_fills"][0]["reason"] == "take_profit" and r["new_fills"][0]["price"] == pytest.approx(110.40)
    assert r["position"] is None and r["orders"] == []  # OCO: SL bị huỷ
    lim = post(c, f"/api/practice/{sid}/orders", {"side": "buy", "type": "limit", "qty": 1, "price": 100})["order"]
    assert c.delete(f"/api/practice/{sid}/orders/{lim['id']}", headers=DEL).json()["orders"] == []


def test_fallback_nen_ngay(env):
    c, _ = env
    sid = new(c, "2024-02-15 12:00")["id"]
    post(c, f"/api/practice/{sid}/orders", {"side": "buy", "type": "market", "qty": 1})
    r = adv(c, sid, "2024-02-20 16:00")
    assert r["new_fills"][0]["time"] == ny_to_unix("2024-02-16 09:30")
    assert any("nến ngày" in n for n in r["notes"])


def test_crypto_qty_le_phi_25bps(env):
    c, _ = env
    s = post(c, "/api/practice/sessions", {"symbol": "BTC/USD", "asset": "crypto", "cursor": utc_to_unix("2024-03-05 10:00")})
    assert s["fee_bps"] == 25
    post(c, f"/api/practice/{s['id']}/orders", {"side": "buy", "type": "market", "qty": 0.5})
    r = post(c, f"/api/practice/{s['id']}/advance", {"to": utc_to_unix("2024-03-05 10:02")})
    p = 50000 + 1440 + 600
    assert r["new_fills"][0]["price"] == p and r["new_fills"][0]["fee"] == pytest.approx(0.5 * p * 0.0025)


def test_ket_thuc_luu_db_va_lich_su(env):
    c, _ = env
    sid = new(c)["id"]
    post(c, f"/api/practice/{sid}/orders", {"side": "buy", "type": "market", "qty": 10, "tp": 110.40})
    adv(c, sid, "2024-03-05 10:05")
    adv(c, sid, "2024-03-05 10:20")
    st = c.get(f"/api/practice/{sid}/stats").json()
    assert st["report"]["total_trades"] == 1 and len(st["equity"]) == 2
    e = post(c, f"/api/practice/{sid}/end")
    assert e["ended"] and e["run_id"] == f"practice-{sid}" and post(c, f"/api/practice/{sid}/end")["run_id"] == e["run_id"]
    post(c, f"/api/practice/{sid}/orders", {"side": "buy", "type": "market", "qty": 1}, 409)
    h = c.get("/api/practice/history").json()["sessions"]
    assert h[0]["run_id"] == e["run_id"] and h[0]["symbol"] == "AAPL" and h[0]["trades"] == 1
    one = c.get(f"/api/practice/history/{e['run_id']}").json()
    assert one["report"]["total_trades"] == 1 and one["equity"][-1]["time"] == ny_to_unix("2024-03-05 10:20")
    assert e["run_id"] in c.get("/api/runs").json()["runs"]
    assert c.get("/api/practice/history/bad").status_code == 422
