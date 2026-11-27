"""SLTPMonitor (web server tự canh SL/TP crypto) với client + giá GIẢ. KHÔNG gọi Alpaca thật."""

import sqlite3
import time
from types import SimpleNamespace as NS

import pytest
from test_crypto_live import FakeCryptoClient, make_bot

from paper_bot import live
from paper_bot.web.sltp_monitor import SLTPMonitor


class FakeBroker:
    """Client giả: vị thế theo mã không gạch chéo (như Alpaca), lệnh bán khớp ngay (hoặc theo `status`)."""

    def __init__(self, qty=0.5, status="filled", fail=None):
        self.qty, self.status, self.fail, self.submitted = qty, status, fail, []

    def get_open_position(self, sym):
        if self.fail:
            raise self.fail
        assert "/" not in sym  # Alpaca: /positions/BTCUSD
        if not self.qty:
            raise Exception('{"code":40410000,"message":"position does not exist"}')
        return NS(qty=str(self.qty))

    def submit_order(self, order_data=None):
        self.submitted.append(order_data)
        return NS(id=f"o{len(self.submitted)}")

    def get_order_by_id(self, oid):
        return NS(status=NS(value=self.status), filled_avg_price="64990.5", filled_qty=str(self.qty))


def make(tmp_path, broker, price=60000.0, sl=58000.0, tp=65000.0):
    st = tmp_path / "state.json"
    if sl:
        live.set_crypto_sltp("BTC/USD", sl, tp, path=st)
    box = {"p": price}

    def price_fn(sym):
        if isinstance(box["p"], Exception):
            raise box["p"]
        return box["p"], "mid"
    mon = SLTPMonitor(lambda: broker, price_fn=price_fn, state_path=st, db_path=tmp_path / "t.db", interval=0.01)
    return mon, box, st


def events(tmp_path, kind):
    con = sqlite3.connect(tmp_path / "t.db")
    try:
        return con.execute("SELECT run_id, data FROM events WHERE kind=?", (kind,)).fetchall()
    finally:
        con.close()


@pytest.mark.parametrize("price, kind", [(65100.0, "tp_hit"), (57900.0, "sl_hit")])
def test_cham_tp_sl_ban_dung_qty_broker(tmp_path, price, kind):
    b = FakeBroker(qty=0.123456)
    mon, _, st = make(tmp_path, b, price=price)
    mon.tick()
    (req,) = b.submitted
    assert (req.symbol, req.side.value, float(req.qty), req.time_in_force.value) == ("BTC/USD", "sell", 0.123456, "gtc")
    (row,) = events(tmp_path, "sltp_hit")
    assert row[0] == "web-sltp" and f'"kind": "{kind}"' in row[1] and '"fill_price": 64990.5' in row[1]
    assert live.crypto_sltp_entries(st) == {}  # khớp xong thì xoá entry


def test_khong_cham_khong_lam_gi(tmp_path):
    b = FakeBroker()
    mon, _, st = make(tmp_path, b, price=60000.0)
    mon.tick()
    assert b.submitted == [] and "BTC/USD" in live.crypto_sltp_entries(st)
    assert mon.status()["entries"][0]["price"]["price"] == 60000.0 and mon.last_check


def test_vi_the_ve_0_xoa_entry(tmp_path):
    b = FakeBroker(qty=0)
    mon, _, st = make(tmp_path, b, price=1.0)
    mon.tick()
    assert b.submitted == [] and live.crypto_sltp_entries(st) == {}
    assert len(events(tmp_path, "sltp_cleared")) == 1


def test_loi_mang_khong_sap(tmp_path):
    b = FakeBroker(fail=ConnectionError("mất mạng"))
    mon, box, st = make(tmp_path, b, price=1.0)
    mon.tick()
    assert b.submitted == [] and "mất mạng" in mon.last_error and "BTC/USD" in live.crypto_sltp_entries(st)
    b.fail, box["p"] = None, TimeoutError("quote chậm")
    mon.tick()
    assert b.submitted == [] and "quote chậm" in mon.last_error


def test_gui_lenh_loi_bo_giu_cho_roi_thu_lai(tmp_path):
    b = FakeBroker()
    b.submit_order = lambda order_data=None: (_ for _ in ()).throw(ConnectionError("503"))
    mon, _, st = make(tmp_path, b, price=1.0)
    mon.tick()
    assert "triggering" not in live.crypto_sltp_entries(st)["BTC/USD"]  # lần sau được thử lại


def test_lenh_chua_khop_khong_ban_lan_2(tmp_path):
    b = FakeBroker(status="new")
    mon, _, st = make(tmp_path, b, price=1.0)
    mon.tick()
    mon.tick()
    assert len(b.submitted) == 1
    trig = live.crypto_sltp_entries(st)["BTC/USD"]["triggering"]
    assert (trig["by"], trig["order_id"]) == ("web-sltp", "o1")
    # server khởi động lại (mất bộ nhớ) vẫn theo dõi tiếp, không bán lần 2
    mon2 = SLTPMonitor(lambda: b, price_fn=lambda s: 1.0, state_path=st, db_path=tmp_path / "t.db")
    mon2.tick()
    assert len(b.submitted) == 1 and "BTC/USD" in mon2.inflight
    b.status = "filled"
    mon2.tick()
    assert live.crypto_sltp_entries(st) == {} and len(events(tmp_path, "sltp_hit")) == 1


def test_lenh_bi_huy_bo_giu_cho(tmp_path):
    b = FakeBroker(status="canceled")
    mon, _, st = make(tmp_path, b, price=1.0)
    mon.tick()
    assert "triggering" not in live.crypto_sltp_entries(st)["BTC/USD"] and len(events(tmp_path, "sltp_failed")) == 1


def test_livebot_dang_giu_cho_thi_monitor_bo_qua(tmp_path):
    b = FakeBroker()
    mon, _, st = make(tmp_path, b, price=1.0)
    assert live.claim_crypto_sltp("BTC/USD", "livebot", st)
    mon.tick()
    assert b.submitted == []
    assert not live.claim_crypto_sltp("BTC/USD", "x", st)  # giữ chỗ còn hiệu lực


def test_giu_cho_qua_han_thi_lay_lai_duoc(tmp_path):
    st = tmp_path / "s.json"
    live.set_crypto_sltp("BTC/USD", 1, 2, path=st)
    assert live.claim_crypto_sltp("BTC/USD", "livebot", st)
    old = time.time() - live.TRIGGER_STALE_SECONDS - 1
    live._edit_sltp(st, live.RUN_ID_CRYPTO_PAPER, lambda s: s["BTC/USD"]["triggering"].update(at=old))
    assert live.claim_crypto_sltp("BTC/USD", "web-sltp", st)


def test_monitor_giu_cho_thi_livebot_bo_qua(tmp_path):
    """Monitor đang bán (triggering có order_id) -> LiveBot chạm SL cũng KHÔNG bán thêm."""
    client = FakeCryptoClient(positions={"BTCUSD": 0.5})
    bot = make_bot(tmp_path, client, price=1.0)
    st = tmp_path / "state.json"
    live.set_crypto_sltp("BTC/USD", 58000, 65000, path=st)
    live.claim_crypto_sltp("BTC/USD", "web-sltp", st)
    live.mark_crypto_sltp_order("BTC/USD", "o1", st)
    bot.reload_sltp()
    bot.check_sl_tp(bot.snapshot())
    assert client.submitted == []


def test_livebot_giu_cho_truoc_khi_ban(tmp_path):
    """LiveBot đọc entry (chưa triggering) nhưng monitor giữ chỗ ngay trước khi bot bán -> bot bỏ qua."""
    client = FakeCryptoClient(positions={"BTCUSD": 0.5})
    bot = make_bot(tmp_path, client, price=1.0)
    st = tmp_path / "state.json"
    live.set_crypto_sltp("BTC/USD", 58000, 65000, path=st)
    bot.reload_sltp()
    live.claim_crypto_sltp("BTC/USD", "web-sltp", st)  # monitor nhanh tay hơn
    bot.check_sl_tp(bot.snapshot())
    assert client.submitted == []


def test_kill_switch_van_duoc_ban(tmp_path):
    b = FakeBroker()
    mon, _, st = make(tmp_path, b, price=1.0)
    live.save_state(st, live.RUN_ID_CRYPTO_PAPER, {**live.load_state(st, live.RUN_ID_CRYPTO_PAPER), "halted": True})
    mon.tick()
    assert len(b.submitted) == 1 and b.submitted[0].side.value == "sell"  # chỉ bán, giảm rủi ro
    assert mon.status()["halted"] is True


def test_cong_tac_va_luong_nen(tmp_path):
    b = FakeBroker()
    mon, _, st = make(tmp_path, b, price=1.0)
    assert mon.enabled is True  # mặc định BẬT
    mon.set_enabled(False)
    assert SLTPMonitor(lambda: b, state_path=st).enabled is False  # lưu trạng thái
    mon.start()
    time.sleep(0.1)
    assert b.submitted == [] and mon.running
    mon.set_enabled(True)
    for _ in range(100):
        if b.submitted:
            break
        time.sleep(0.02)
    mon.stop()
    assert len(b.submitted) == 1 and not mon.running
