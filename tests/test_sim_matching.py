"""Test quy tắc khớp lệnh trên 1 nến (sim/matching.py)."""

import pytest

from paper_bot.sim.matching import exit_hit, fill_price


def test_market_khop_o_open():
    assert fill_price("market", "buy", None, 100, 105, 95) == 100
    assert fill_price("market", "sell", None, 100, 105, 95) == 100


@pytest.mark.parametrize("side,price,o,h,l,want", [
    ("buy", 98, 100, 101, 97, 98),     # chạm low -> khớp đúng giá limit
    ("buy", 98, 100, 101, 99, None),   # chưa chạm
    ("buy", 98, 96, 97, 95, 96),       # mở gap xuống dưới giá limit -> khớp open (tốt hơn)
    ("sell", 102, 100, 103, 99, 102),
    ("sell", 102, 104, 105, 103, 104),  # gap lên
    ("sell", 102, 100, 101, 99, None),
])
def test_limit(side, price, o, h, l, want):
    assert fill_price("limit", side, price, o, h, l) == want


@pytest.mark.parametrize("side,price,o,h,l,want", [
    ("buy", 102, 100, 103, 99, 102),
    ("buy", 102, 104, 105, 103, 104),   # gap qua giá stop -> khớp open (xấu hơn)
    ("buy", 102, 100, 101, 99, None),
    ("sell", 98, 100, 101, 97, 98),
    ("sell", 98, 95, 96, 94, 95),       # gap xuống
])
def test_stop(side, price, o, h, l, want):
    assert fill_price("stop", side, price, o, h, l) == want


def test_sl_truoc_tp_khi_ca_hai_cham_cung_nen():
    assert exit_hit(95, 105, 100, 106, 94) == ("sl", 95)
    assert exit_hit(95, 105, 100, 106, 96) == ("tp", 105)
    assert exit_hit(95, 105, 100, 104, 96) is None
    assert exit_hit(95, 105, 93, 94, 92) == ("sl", 93)    # gap xuống dưới SL -> open
    assert exit_hit(95, 105, 107, 108, 106) == ("tp", 107)
    assert exit_hit(None, 105, 100, 106, 50) == ("tp", 105)


def test_loai_lenh_la():
    with pytest.raises(ValueError):
        fill_price("trailing", "buy", 1, 1, 1, 1)
