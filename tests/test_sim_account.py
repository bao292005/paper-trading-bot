"""Test tài khoản luyện tập (sim/account.py + stats.py): khớp lệnh, SL/TP, phí, crypto lẻ, chống nhìn trước, lệnh bán đóng long (short: test_sim_short.py)."""

import sqlite3

import pytest

from paper_bot.sim.account import PracticeAccount
from paper_bot.sim.stats import report, save_session

T0 = 1_700_000_000  # chia hết cho 60? không cần: nến là [start, start+60)


def bar(i, o, h, l, c):
    """Nến 1m thứ i tính từ T0."""
    return {"start": T0 + 60 * i, "end": T0 + 60 * (i + 1), "open": o, "high": h, "low": l, "close": c}


def acc(asset="stock", **kw):
    return PracticeAccount("AAPL" if asset == "stock" else "BTC/USD", asset, cursor=T0, last_price=100.0, **kw)


def test_market_khop_open_nen_ke_tiep_va_phi_stock_10bps():
    a = acc()
    a.place_order("buy", "market", 10)
    fills = a.advance([bar(0, 101, 102, 100, 101.5)], T0 + 60)
    assert len(fills) == 1 and fills[0]["price"] == 101
    assert fills[0]["fee"] == pytest.approx(1010 * 0.001)
    assert a.cash == pytest.approx(100000 - 1010 - 1.01)
    assert a.qty == 10 and a.equity_now() == pytest.approx(a.cash + 10 * 101.5)


def test_crypto_phi_25bps_va_mua_le():
    a = acc("crypto")
    o = a.place_order("buy", "market", 0.0123456789)
    assert o.qty == 0.012346
    a.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    assert a.fills[0]["fee"] == pytest.approx(0.012346 * 100 * 0.0025)
    with pytest.raises(ValueError):
        acc().place_order("buy", "market", 1.5)  # cổ phiếu: phải số nguyên


def test_limit_stop_cho_toi_khi_cham():
    a = acc()
    lim = a.place_order("buy", "limit", 1, price=98)
    a.advance([bar(0, 100, 101, 99, 100)], T0 + 60)
    assert lim.status == "pending"
    a.advance([bar(1, 99, 99, 97, 98)], T0 + 120)
    assert lim.status == "filled" and a.fills[-1]["price"] == 98
    st = a.place_order("buy", "stop", 1, price=105)
    a.advance([bar(2, 107, 108, 106, 107)], T0 + 180)  # gap qua stop -> open
    assert st.status == "filled" and a.fills[-1]["price"] == 107


def test_bracket_sl_truoc_tp_va_oco():
    a = acc()
    a.place_order("buy", "market", 5, sl=95, tp=110)
    a.advance([bar(0, 100, 120, 90, 100)], T0 + 60)  # nến khớp lệnh mua: lệnh con CHƯA xét
    assert a.qty == 5 and len(a.fills) == 1
    a.advance([bar(1, 100, 111, 94, 100)], T0 + 120)  # chạm cả SL lẫn TP -> SL
    assert a.qty == 0 and a.fills[-1]["reason"] == "stop_loss" and a.fills[-1]["price"] == 95
    assert not [o for o in a.orders.values() if o.status == "pending"]  # chân TP bị huỷ (OCO)
    assert a.trades[0].exit_reason == "stop_loss" and a.trades[0].pnl < 0


def test_tp_va_sua_lenh_con():
    a = acc()
    a.place_order("buy", "market", 2, sl=90, tp=110)
    a.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    tp = next(o for o in a.orders.values() if o.role == "tp")
    a.modify_order(tp.id, {"price": 104})
    with pytest.raises(ValueError):
        a.modify_order(tp.id, {"price": 99})  # TP phải trên giá hiện tại
    a.advance([bar(1, 101, 105, 100, 103)], T0 + 120)
    assert a.fills[-1]["reason"] == "take_profit" and a.fills[-1]["price"] == 104
    assert a.trades[0].pnl == pytest.approx((104 - 100) * 2 - (200 + 208) * 0.001)


def test_lenh_ban_dong_long_khong_mo_short():
    """Đang long: lệnh bán = lệnh đóng (reduce). Hết vị thế thì lệnh bán còn chờ bị huỷ, KHÔNG mở short."""
    a = acc()
    with pytest.raises(ValueError):
        a.close_position()
    a.place_order("buy", "market", 3)
    a.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    s1 = a.place_order("sell", "market", 3)
    s2 = a.place_order("sell", "limit", 3, price=100)  # cùng lúc 2 lệnh bán: lệnh sau bị huỷ khi hết vị thế
    a.advance([bar(1, 101, 101, 99, 100)], T0 + 120)
    assert s1.status == "filled" and s2.status == "cancelled" and a.qty == 0


def test_khong_nhin_truoc():
    a = acc()
    a.place_order("buy", "limit", 1, price=90)
    future = bar(5, 85, 86, 80, 85)  # nến kết thúc SAU con trỏ mới -> không được dùng
    fills = a.advance([bar(0, 100, 100, 99, 100), future], T0 + 120)
    assert fills == [] and a.cursor == T0 + 120 and a.last_price == 100
    with pytest.raises(ValueError):
        a.advance([], T0)  # không lùi con trỏ
    # nến bắt đầu trước con trỏ (đã đi qua) cũng bị bỏ
    assert a.advance([bar(0, 80, 80, 80, 80)], T0 + 180) == []


def test_khong_du_tien_va_bracket_sai():
    a = acc(cash=1000)
    with pytest.raises(ValueError, match="tiền"):
        a.place_order("buy", "market", 20)
    with pytest.raises(ValueError, match="SL"):
        a.place_order("buy", "limit", 1, price=100, sl=101)
    o = a.place_order("buy", "market", 9)
    a.advance([bar(0, 200, 200, 200, 200)], T0 + 60)  # giá nhảy -> không đủ tiền lúc khớp
    assert o.status == "rejected" and a.cash == 1000


def test_huy_va_dong_vi_the():
    a = acc()
    o = a.place_order("buy", "limit", 1, price=50)
    a.cancel_order(o.id)
    with pytest.raises(KeyError):
        a.cancel_order(o.id)
    a.place_order("buy", "market", 4, sl=80)
    a.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    a.close_position()
    a.advance([bar(1, 102, 102, 102, 102)], T0 + 120)
    assert a.qty == 0 and a.fills[-1]["price"] == 102 and a.trades[-1].exit_reason == "manual"
    assert not [x for x in a.orders.values() if x.status == "pending"]


def test_bao_cao_va_luu_db(tmp_path):
    a = acc()
    a.place_order("buy", "market", 10, tp=105)
    a.advance([bar(0, 100, 100, 100, 100)], T0 + 60, steps=1)
    a.advance([bar(1, 100, 106, 100, 105)], T0 + 120, steps=1)
    a.place_order("buy", "market", 10)
    a.advance([bar(2, 104, 104, 103, 103)], T0 + 180, steps=1)  # vị thế còn mở cuối phiên
    rep = report(a)
    assert rep["total_trades"] == 2 and rep["open_at_end"] == 1 and rep["profit_trades"] == 1
    assert rep["initial_deposit"] == 100000
    run_id = save_session(a, "abc123", tmp_path / "t.db")
    conn = sqlite3.connect(tmp_path / "t.db")
    assert run_id == "practice-abc123"
    assert conn.execute("SELECT COUNT(*) FROM fills WHERE run_id=?", (run_id,)).fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM equity WHERE run_id=?", (run_id,)).fetchone()[0] == 3
    assert conn.execute("SELECT kind FROM events WHERE run_id=?", (run_id,)).fetchone()[0] == "practice_end"
