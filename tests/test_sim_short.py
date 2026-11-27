"""Test bán khống trong tài khoản luyện tập (sim): mở/đóng short, limit/stop bán, SL/TP short, gap, SL trước TP,
P/L + phí, chặn đảo chiều, ký quỹ 1x, margin call, crypto lẻ, không nhìn trước, thống kê long/short."""

import pytest

from paper_bot.sim.account import MARGIN_NOTE, SHORT_NOTE
from paper_bot.sim.matching import exit_hit
from paper_bot.sim.stats import report, trades_with_open
from test_sim_account import T0, acc, bar


def test_mo_va_dong_short_tien_equity_pl():
    a = acc()
    a.place_order("sell", "market", 10)
    a.advance([bar(0, 100, 101, 99, 99)], T0 + 60)
    assert a.qty == -10 and a.avg == 100 and a.fills[-1]["reason"] == "entry"
    assert a.cash == pytest.approx(100000 + 1000 - 1.0)            # nhận tiền bán − phí 10bps
    assert a.equity_now() == pytest.approx(a.cash - 10 * 99)
    pos = a.snapshot()["position"]
    assert pos["side"] == "short" and pos["qty"] == -10 and pos["unrealized"] == pytest.approx(10)  # (100−99)×10
    assert SHORT_NOTE in a.notes
    a.close_position()                                              # = lệnh MUA market 10
    a.advance([bar(1, 98, 98, 97, 97)], T0 + 120)
    t = a.trades[-1]
    assert a.qty == 0 and a.fills[-1]["side"] == "buy" and t.side == "short" and t.exit_reason == "manual"
    assert t.pnl == pytest.approx((100 - 98) * 10 - (1.0 + 0.98))
    assert a.equity_now() == pytest.approx(100000 + t.pnl)


def test_limit_ban_va_stop_ban_mo_short_va_gap():
    a = acc()
    lim = a.place_order("sell", "limit", 1, price=105)  # bán khi giá hồi lên
    a.advance([bar(0, 100, 104, 99, 100)], T0 + 60)
    assert lim.status == "pending"
    a.advance([bar(1, 101, 106, 100, 104)], T0 + 120)    # high >= 105
    assert lim.status == "filled" and a.fills[-1]["price"] == 105 and a.qty == -1
    b = acc()
    st = b.place_order("sell", "stop", 1, price=95)      # bán khi giá thủng xuống
    b.advance([bar(0, 100, 101, 96, 97)], T0 + 60)
    assert st.status == "pending"
    b.advance([bar(1, 93, 94, 90, 92)], T0 + 120)        # mở cửa đã dưới 95 (gap) -> khớp ở open
    assert st.status == "filled" and b.fills[-1]["price"] == 93


def test_sl_tp_short_va_sl_truoc_tp():
    assert exit_hit(110, 90, 100, 111, 95, "short") == ("sl", 110)
    assert exit_hit(110, 90, 100, 105, 89, "short") == ("tp", 90)
    assert exit_hit(110, 90, 100, 112, 85, "short") == ("sl", 110)  # chạm cả hai -> SL trước
    assert exit_hit(110, 90, 115, 116, 114, "short") == ("sl", 115)  # gap qua SL -> open
    a = acc()
    with pytest.raises(ValueError, match="SL"):
        a.place_order("sell", "market", 5, sl=95, tp=90)              # short: SL phải > giá vào
    with pytest.raises(ValueError, match="TP"):
        a.place_order("sell", "limit", 5, price=100, sl=110, tp=101)  # short: TP phải < giá vào
    a.place_order("sell", "market", 5, sl=110, tp=90)
    a.advance([bar(0, 100, 120, 80, 100)], T0 + 60)                   # nến khớp lệnh vào: lệnh con chưa xét
    kids = [o for o in a.orders.values() if o.status == "pending"]
    assert a.qty == -5 and sorted((k.role, k.side) for k in kids) == [("sl", "buy"), ("tp", "buy")]
    a.advance([bar(1, 100, 111, 89, 100)], T0 + 120)
    assert a.qty == 0 and a.fills[-1]["reason"] == "stop_loss" and a.fills[-1]["price"] == 110
    assert a.trades[-1].pnl < 0 and not [o for o in a.orders.values() if o.status == "pending"]


def test_tp_short_va_sua_lenh_con():
    a = acc()
    a.place_order("sell", "market", 2, sl=110, tp=90)
    a.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    sl = next(o for o in a.orders.values() if o.role == "sl" and o.status == "pending")
    tp = next(o for o in a.orders.values() if o.role == "tp" and o.status == "pending")
    with pytest.raises(ValueError):
        a.modify_order(sl.id, {"price": 99})   # SL short phải TRÊN giá hiện tại
    with pytest.raises(ValueError):
        a.modify_order(tp.id, {"price": 101})  # TP short phải DƯỚI
    a.modify_order(tp.id, {"price": 95})
    a.advance([bar(1, 97, 98, 94, 96)], T0 + 120)
    assert a.fills[-1]["reason"] == "take_profit" and a.fills[-1]["price"] == 95
    assert a.trades[-1].pnl == pytest.approx((100 - 95) * 2 - (200 + 190) * 0.001)


def test_chan_dao_chieu_trong_mot_lenh():
    a = acc()
    a.place_order("buy", "market", 10)
    a.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    with pytest.raises(ValueError, match="đóng vị thế trước"):
        a.place_order("sell", "market", 15)
    with pytest.raises(ValueError, match="SL/TP"):
        a.place_order("sell", "market", 5, sl=105)  # lệnh đóng không gắn SL/TP
    b = acc()
    b.place_order("sell", "market", 5)
    b.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    with pytest.raises(ValueError, match="đóng vị thế trước"):
        b.place_order("buy", "market", 8)
    part = b.place_order("buy", "market", 2)        # giảm short 5 -> 3
    b.advance([bar(1, 100, 100, 100, 100)], T0 + 120)
    assert part.status == "filled" and b.qty == -3


def test_lenh_mo_bi_tu_choi_khi_vi_the_nguoc_chieu_luc_khop():
    a = acc()
    a.place_order("sell", "market", 2)              # mở short
    buy = a.place_order("buy", "limit", 2, price=90)  # lúc đặt đang flat -> là lệnh MỞ long
    a.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    a.advance([bar(1, 95, 95, 89, 90)], T0 + 120)
    assert buy.status == "rejected" and "ngược chiều" in buy.note and a.qty == -2


def test_ky_quy_1x():
    a = acc(cash=1000)
    with pytest.raises(ValueError, match="ký quỹ"):
        a.place_order("sell", "market", 11)          # 11 × 100 > equity 1000
    o = a.place_order("sell", "market", 10)          # đúng bằng equity: được
    a.advance([bar(0, 101, 101, 101, 101)], T0 + 60)  # giá nhảy lên lúc khớp -> 1010 > equity
    assert o.status == "rejected" and "ký quỹ" in o.note and a.qty == 0


def test_margin_call_tu_dong_khi_equity_ve_0():
    a = acc(cash=1000)
    a.place_order("sell", "market", 5)
    a.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    liq = a.cash / 5                                  # giá mà equity = 0
    assert liq == pytest.approx((1000 + 500 - 0.5) / 5)
    a.advance([bar(1, 150, 250, 150, 240)], T0 + 120)  # chưa chạm 299.9 -> vẫn giữ
    assert a.qty == -5
    a.advance([bar(2, 250, 310, 250, 300)], T0 + 180)
    f = a.fills[-1]
    assert a.qty == 0 and f["reason"] == "margin_call" and f["price"] == pytest.approx(liq)
    assert a.trades[-1].exit_reason == "margin_call" and MARGIN_NOTE in a.notes
    b = acc(cash=1000)
    b.place_order("sell", "market", 5)
    b.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    b.advance([bar(1, 400, 410, 390, 400)], T0 + 120)  # gap qua mức thanh lý -> khớp ở open, equity âm
    assert b.qty == 0 and b.fills[-1]["price"] == 400 and b.equity_now() < 0


def test_short_crypto_le():
    a = acc("crypto")
    o = a.place_order("sell", "market", 0.0123456789)
    assert o.qty == 0.012346
    a.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    assert a.qty == -0.012346 and a.fills[0]["fee"] == pytest.approx(0.012346 * 100 * 0.0025)
    a.close_position()
    a.advance([bar(1, 90, 90, 90, 90)], T0 + 120)
    assert a.qty == 0 and a.trades[-1].pnl > 0


def test_short_khong_nhin_truoc():
    a = acc()
    a.place_order("sell", "limit", 1, price=110)
    future = bar(5, 115, 120, 114, 118)  # nến kết thúc SAU con trỏ mới -> không được dùng
    assert a.advance([bar(0, 100, 101, 99, 100), future], T0 + 120) == [] and a.qty == 0


def test_thong_ke_tach_long_short():
    a = acc()
    a.place_order("sell", "market", 10, tp=95)
    a.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    a.advance([bar(1, 98, 99, 94, 95)], T0 + 120)    # short chốt lời
    a.place_order("buy", "market", 10)
    a.advance([bar(2, 95, 95, 94, 94)], T0 + 180)    # long còn mở cuối phiên (lỗ nhẹ)
    rep = report(a)
    assert rep["total_trades"] == 2 and rep["short_trades"] == 1 and rep["long_trades"] == 1
    assert rep["short_won_pct"] == 1 and rep["long_won_pct"] == 0
    a2 = acc()
    a2.place_order("sell", "market", 3)
    a2.advance([bar(0, 100, 100, 100, 100)], T0 + 60)
    (t,) = trades_with_open(a2)
    assert t.side == "short" and t.qty == 3 and t.exit_reason == "end"
