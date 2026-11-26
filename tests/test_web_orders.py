"""Test offline đặt/huỷ lệnh và nút chạy bot từ web: client giả, run_command giả. KHÔNG bao giờ gọi Alpaca thật."""

import sqlite3
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from paper_bot.web.api import orders as orders_api
from paper_bot.web.app import create_app

HOST = "http://127.0.0.1:8600"
GOOD = {"X-Requested-With": "paper-web", "Origin": HOST}
OID = str(uuid.uuid4())


class FakeTrading:
    def __init__(self, db_path):
        self.db_path, self.submitted, self.cancelled, self.events_at_submit = db_path, [], [], []

    def submit_order(self, order_data=None, **k):
        # Ghi lại số event đã có trong DB lúc gửi: phải >= 1 (event được ghi TRƯỚC khi gửi)
        self.events_at_submit.append(count_events(self.db_path))
        self.submitted.append(order_data)
        return SimpleNamespace(id="o-1", status=SimpleNamespace(value="accepted"))

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)


def count_events(db_path, kind=None):
    conn = sqlite3.connect(db_path)
    try:
        q, p = "SELECT COUNT(*) FROM events WHERE run_id = 'web-manual'", ()
        if kind:
            q, p = q + " AND kind = ?", (kind,)
        return conn.execute(q, p).fetchone()[0]
    except sqlite3.OperationalError:  # bảng chưa tạo
        return 0
    finally:
        conn.close()


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "t.db"
    fake = FakeTrading(db)
    client = TestClient(create_app(trading_client_fn=lambda: fake, db_path=db), base_url=HOST)
    return SimpleNamespace(client=client, fake=fake, db=db)


def order(**kw):
    base = {"symbol": "AAPL", "side": "buy", "type": "limit", "qty": 10, "limit_price": 150.0,
            "confirm": "XAC NHAN"}
    return {**base, **kw}


def post(env, body, headers=GOOD):
    return env.client.post("/api/orders", json=body, headers=headers)


# ---------------- đặt lệnh: đủ điều kiện ----------------
def test_lenh_hop_le_gui_dung_1_lan_va_ghi_event_truoc(env):
    res = post(env, order())
    assert res.status_code == 200 and res.json()["id"] == "o-1"
    assert len(env.fake.submitted) == 1 and env.fake.events_at_submit == [1]
    req = env.fake.submitted[0]
    assert (req.symbol, req.qty, req.limit_price) == ("AAPL", 10, 150.0) and req.order_class is None
    assert count_events(env.db, "web_manual_order") == 1


def test_bracket_mua_va_ban(env):
    assert post(env, order(stop_loss=140, take_profit=170)).status_code == 200
    req = env.fake.submitted[0]
    assert req.order_class.value == "bracket" and req.stop_loss.stop_price == 140 and req.take_profit.limit_price == 170
    assert post(env, order(side="sell", stop_loss=160, take_profit=130)).status_code == 200
    assert len(env.fake.submitted) == 2


def test_market_khong_sltp_va_market_co_ref_price(env):
    assert post(env, order(type="market", limit_price=None)).status_code == 200
    assert post(env, order(type="market", limit_price=None, stop_loss=95, ref_price=100)).status_code == 200
    assert env.fake.submitted[1].order_class.value == "oto"


# ---------------- đặt lệnh: bị chặn, KHÔNG gọi submit ----------------
BAD_BODIES = [
    {"confirm": None}, {"confirm": ""}, {"confirm": "xac nhan"}, {"confirm": " XAC NHAN"}, {"confirm": "XAC NHAN "},
    {"confirm": True},
    {"symbol": "aapl"}, {"symbol": "TOOLONG"}, {"symbol": "AAPL;"}, {"symbol": "AAPL\n"}, {"symbol": ""}, {"symbol": 5},
    {"qty": 0}, {"qty": -1}, {"qty": 10001}, {"qty": 1.5}, {"qty": "5"}, {"qty": True}, {"qty": None},
    {"limit_price": None}, {"limit_price": 0}, {"limit_price": -1}, {"limit_price": "150"}, {"limit_price": True},
    {"side": "short"}, {"side": "BUY"}, {"type": "stop"},
    {"stop_loss": 150}, {"stop_loss": 151}, {"take_profit": 150}, {"take_profit": 149},  # mua: SL<giá<TP
    {"side": "sell", "stop_loss": 150}, {"side": "sell", "take_profit": 150},  # bán: TP<giá<SL
    {"side": "sell", "stop_loss": 140}, {"side": "sell", "take_profit": 160},
    {"type": "market", "limit_price": 150}, {"type": "market", "limit_price": None, "stop_loss": 95},
    {"stop_loss": 0}, {"take_profit": -5},
]


@pytest.mark.parametrize("patch", BAD_BODIES, ids=lambda p: str(p)[:40])
def test_input_sai_bi_4xx_khong_gui(env, patch):
    res = post(env, order(**patch))
    assert 400 <= res.status_code < 500, res.text
    assert env.fake.submitted == []


@pytest.mark.parametrize("raw", ["Infinity", "-Infinity", "NaN"])
def test_gia_vo_cung_hoac_nan_bi_chan(env, raw):
    """JSON thô (httpx không tự mã hoá được inf/nan): json.loads của server vẫn chấp nhận chữ này."""
    content = ('{"symbol":"AAPL","side":"buy","type":"limit","qty":10,"limit_price":%s,"confirm":"XAC NHAN"}' % raw)
    res = env.client.post("/api/orders", content=content, headers={**GOOD, "Content-Type": "application/json"})
    assert 400 <= res.status_code < 500 and env.fake.submitted == []


def test_thieu_truong_confirm_hoan_toan(env):
    body = order()
    del body["confirm"]
    assert 400 <= post(env, body).status_code < 500 and env.fake.submitted == []


def test_body_khong_phai_object(env):
    res = env.client.post("/api/orders", json=[1, 2], headers=GOOD)
    assert 400 <= res.status_code < 500 and env.fake.submitted == []


@pytest.mark.parametrize("headers", [
    {"Origin": HOST},                                              # thiếu X-Requested-With
    {"X-Requested-With": "XMLHttpRequest", "Origin": HOST},        # sai giá trị
    {"X-Requested-With": "paper-web", "Origin": "http://evil.example"},
    {"X-Requested-With": "paper-web", "Origin": "http://127.0.0.1.evil.example"},
    {"X-Requested-With": "paper-web", "Origin": "null"},
    {"X-Requested-With": "paper-web", "Host": "evil.example"},
], ids=["no-xrw", "bad-xrw", "origin-evil", "origin-prefix", "origin-null", "host-evil"])
def test_header_la_bi_chan_khong_gui(env, headers):
    assert 400 <= post(env, order(), headers=headers).status_code < 500
    assert env.fake.submitted == []


def test_sai_content_type_bi_chan(env):
    res = env.client.post("/api/orders", content='{"symbol":"AAPL"}', headers={**GOOD, "Content-Type": "text/plain"})
    assert res.status_code == 415 and env.fake.submitted == []


def test_khong_ghi_duoc_event_thi_khong_gui(env, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("paper_bot.storage.Store.log_event", boom)
    assert post(env, order()).status_code == 500 and env.fake.submitted == []


def test_thieu_key_alpaca_tra_503_va_khong_ghi_event(tmp_path):
    def no_keys():
        raise RuntimeError("thiếu key Alpaca")

    client = TestClient(create_app(trading_client_fn=no_keys, db_path=tmp_path / "t.db"), base_url=HOST)
    res = client.post("/api/orders", json=order(), headers=GOOD)
    assert res.status_code == 503 and count_events(tmp_path / "t.db") == 0


# ---------------- huỷ lệnh ----------------
def cancel(env, oid=OID, body=None, headers=GOOD):
    return env.client.request("DELETE", f"/api/orders/{oid}", json={"confirm": "XAC NHAN"} if body is None else body,
                              headers=headers)


def test_huy_lenh_hop_le(env):
    assert cancel(env).status_code == 200
    assert env.fake.cancelled == [OID] and count_events(env.db, "web_cancel_order") == 1


@pytest.mark.parametrize("kwargs", [
    {"body": {}}, {"body": {"confirm": "xac nhan"}}, {"oid": "abc"}, {"oid": "1; DROP"},
    {"headers": {"Origin": "http://evil.example", "X-Requested-With": "paper-web"}},
    {"headers": {"Origin": HOST}},
], ids=["no-confirm", "bad-confirm", "bad-id", "inject-id", "origin-evil", "no-xrw"])
def test_huy_lenh_bi_chan(env, kwargs):
    assert 400 <= cancel(env, **kwargs).status_code < 500 and env.fake.cancelled == []


# ---------------- chạy bot 1 vòng ----------------
@pytest.fixture
def runner(env, monkeypatch):
    calls = []

    def fake_run(cmd, allow_real=False, **k):
        calls.append((cmd, allow_real))
        return {"returncode": 0, "stdout": "ok", "stderr": "", "timed_out": False}

    monkeypatch.setattr(orders_api, "run_command", fake_run)
    env.calls = calls
    return env


def run_bot(env, body, headers=GOOD):
    return env.client.post("/api/bot/run", json=body, headers=headers)


def test_chay_bot_mac_dinh_la_dry_run(runner):
    res = run_bot(runner, {"symbols": ["AAPL", "NVDA"]})
    assert res.status_code == 200 and res.json()["dry_run"] is True
    cmd, allow_real = runner.calls[0]
    assert "--dry-run" in cmd and allow_real is False and cmd[3] == "once"
    assert runner.fake.submitted == []


@pytest.mark.parametrize("body", [
    {"symbols": ["AAPL"], "dry_run": False},
    {"symbols": ["AAPL"], "dry_run": False, "confirm": "xac nhan"},
    {"symbols": ["AAPL"], "dry_run": "false", "confirm": "XAC NHAN"},
    {"symbols": ["AAPL"], "dry_run": 0, "confirm": "XAC NHAN"},
    {"symbols": ["aapl"]}, {"symbols": []}, {"symbols": "AAPL"}, {"symbols": ["AAPL; rm -rf /"]}, {},
], ids=lambda b: str(b)[:50])
def test_chay_bot_input_sai_hoac_that_thieu_confirm(runner, body):
    assert 400 <= run_bot(runner, body).status_code < 500 and runner.calls == []


def test_chay_bot_that_khi_co_confirm(runner):
    res = run_bot(runner, {"symbols": ["AAPL"], "dry_run": False, "confirm": "XAC NHAN"})
    assert res.status_code == 200
    cmd, allow_real = runner.calls[0]
    assert "--dry-run" not in cmd and allow_real is True
    assert count_events(runner.db, "web_run_bot") == 1


def test_chay_bot_header_la_bi_chan(runner):
    res = run_bot(runner, {"symbols": ["AAPL"]}, headers={"Origin": "http://evil.example", "X-Requested-With": "paper-web"})
    assert res.status_code == 403 and runner.calls == []
