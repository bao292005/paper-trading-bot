"""Test risk.py - số liệu tính tay được, chạy offline."""

import logging
import math
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from paper_bot.contracts import Forecast, MarketState, Signal
from paper_bot.risk import (
    RiskConfig,
    atr,
    atr_position_size,
    bracket_levels,
    correlation_ok,
    legacy_position_size,
    make_order_intent,
    ref_width_from,
    uncertainty_scale,
)


def make_bars(n=20, high=101.0, low=99.0, close=100.0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n, name="date")
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": 1e6}, index=idx)


def make_forecast(q10=-0.03, q90=0.035) -> Forecast:  # width = 0.065
    return Forecast("naive", 5, {0.1: q10, 0.5: 0.002, 0.9: q90})


# ---- atr ----
def test_atr_hang_so():
    # high-low = 2 mỗi phiên, close không đổi -> TR = 2 -> ATR = 2
    assert atr(make_bars(), 14) == pytest.approx(2.0)


def test_atr_tinh_gap():
    bars = make_bars()
    bars.iloc[-1, bars.columns.get_loc("high")] = 106.0  # TR cuối = max(106-99, |106-100|, |99-100|) = 7
    # trung bình 14 phiên gần nhất: (13*2 + 7)/14
    assert atr(bars, 14) == pytest.approx((13 * 2 + 7) / 14)


def test_atr_thieu_du_lieu():
    assert math.isnan(atr(make_bars(n=10), 14))


def test_atr_khong_nhin_tuong_lai():
    # ATR tại dòng cuối không đổi dù phiên "sau đó" có biến động lớn
    bars = make_bars(n=20)
    before = atr(bars.iloc[:15], 14)
    bars.iloc[16:, bars.columns.get_loc("high")] = 200.0
    assert atr(bars.iloc[:15], 14) == before


# ---- legacy ----
def test_legacy_bi_chan_boi_1000usd():
    # min(100000*2%=2000, 1000)=1000 -> 1000/150 = 6.67 -> 6
    assert legacy_position_size(100_000, 150) == 6


def test_legacy_theo_suc_mua():
    # min(10000*2%=200, 1000)=200 -> 200/50 = 4
    assert legacy_position_size(10_000, 50) == 4


def test_legacy_toi_thieu_1_co():
    assert legacy_position_size(100, 500) == 1  # đúng như bài gốc (ép 1 cổ)


# ---- atr_position_size ----
CFG = RiskConfig()  # risk 1%, stop 2 ATR, trần 10% vốn


def test_sizing_bi_chan_boi_tran_vi_the():
    # by_risk = 1000/(2*2)=250; by_cap = 10000/100=100 -> 100
    assert atr_position_size(100_000, 100, 2.0, CFG, cash=100_000) == 100


def test_sizing_theo_rui_ro():
    # atr=10: by_risk = 1000/20 = 50; cap = 100 -> 50
    assert atr_position_size(100_000, 100, 10.0, CFG, cash=100_000) == 50


def test_sizing_bi_chan_boi_tien_mat():
    assert atr_position_size(100_000, 100, 10.0, CFG, cash=2_000) == 20


def test_sizing_khong_du_tien_thi_bang_0():
    assert atr_position_size(100_000, 100, 10.0, CFG, cash=50) == 0  # không ép 1 cổ


def test_sizing_atr_xau():
    assert atr_position_size(100_000, 100, float("nan"), CFG, cash=1e5) == 0
    assert atr_position_size(100_000, 100, 0.0, CFG, cash=1e5) == 0
    assert atr_position_size(100_000, 0.0, 2.0, CFG, cash=1e5) == 0


def test_sizing_mat_toi_da_bang_risk_per_trade():
    # nếu chạm stop (2*ATR) với số cổ tính ra thì lỗ <= 1% vốn
    qty = atr_position_size(100_000, 100, 10.0, CFG, cash=100_000)
    assert qty * CFG.atr_stop_mult * 10.0 <= 100_000 * CFG.risk_per_trade


# ---- uncertainty_scale ----
def test_scale_khong_forecast():
    assert uncertainty_scale(None, 0.05) == 1.0


def test_scale_khong_giam_khi_hep():
    assert uncertainty_scale(make_forecast(), ref_width=0.065) == 1.0
    assert uncertainty_scale(make_forecast(), ref_width=0.10) == 1.0


def test_scale_giam_ti_le():
    assert uncertainty_scale(make_forecast(), ref_width=0.0325) == pytest.approx(0.5)


def test_scale_san_025():
    assert uncertainty_scale(make_forecast(), ref_width=0.01) == 0.25  # 0.01/0.065 = 0.15 -> chặn 0.25


def test_scale_thieu_ref_width():
    assert uncertainty_scale(make_forecast(), None) == 1.0


# ---- ref_width_from ----
def test_ref_width_trung_vi():
    fs = [make_forecast(-0.01, 0.01), make_forecast(-0.02, 0.02), None, make_forecast(-0.10, 0.10)]
    assert ref_width_from(fs) == pytest.approx(0.04)  # widths 0.02, 0.04, 0.20 -> trung vị 0.04


def test_ref_width_rong():
    assert ref_width_from([]) is None
    assert ref_width_from([None]) is None


# ---- bracket_levels ----
def test_bracket_levels():
    # giá 100, ATR 2: stop = 100 - 2*2 = 96; take-profit = 100 + 3*2 = 106
    assert bracket_levels(100.0, 2.0, CFG) == (96.0, 106.0)


def test_bracket_stop_khong_am():
    stop, _ = bracket_levels(1.0, 5.0, CFG)
    assert stop > 0


# ---- correlation_ok ----
def make_returns() -> pd.DataFrame:
    a = np.random.default_rng(0).normal(0, 0.01, 100)
    return pd.DataFrame({"A": a, "B": a * 1.1, "C": -a}, index=pd.bdate_range("2024-01-01", periods=100))


def test_corr_cao_bi_chan():
    ok, why = correlation_ok("B", ["A"], make_returns(), CFG)
    assert not ok and "A" in why


def test_corr_am_thi_duoc():
    ok, _ = correlation_ok("C", ["A"], make_returns(), CFG)
    assert ok


def test_corr_chua_nam_gi_thi_duoc():
    assert correlation_ok("B", [], make_returns(), CFG)[0]


def test_corr_thieu_du_lieu_ma_thi_bo_qua():
    assert correlation_ok("ZZZ", ["A"], make_returns(), CFG)[0]
    assert correlation_ok("B", ["ZZZ"], make_returns(), CFG)[0]


# ---- make_order_intent ----
NOW = datetime(2024, 3, 1)


def make_signal(action="BUY", symbol="AAPL", **kw) -> Signal:
    return Signal(symbol, action, 100.0, NOW, "test", 0.7, **kw)


def make_state(symbol="AAPL", forecast=None) -> MarketState:
    return MarketState(symbol, NOW, make_bars(), forecast=forecast)


def intent(signal=None, state=None, positions=None, open_orders=None, equity=100_000, cash=100_000, **kw):
    return make_order_intent(signal or make_signal(), state or make_state(), equity, cash,
                             positions or {}, open_orders or set(), CFG, **kw)


def test_intent_mua_co_bracket():
    o = intent()
    assert (o.side, o.qty, o.stop_loss, o.take_profit) == ("buy", 100, 96.0, 106.0)
    assert o.symbol == "AAPL" and o.asof == NOW and o.order_type == "market"


def test_intent_khong_mua_trung():
    assert intent(positions={"AAPL": 5}) is None


def test_intent_khong_ban_khi_chua_co():
    assert intent(make_signal("SELL")) is None


def test_intent_ban_het_vi_the():
    o = intent(make_signal("SELL"), positions={"AAPL": 7})
    assert (o.side, o.qty, o.stop_loss) == ("sell", 7, None)


def test_intent_co_lenh_cho_thi_bo_qua():
    assert intent(open_orders={"AAPL"}) is None
    assert intent(make_signal("SELL"), positions={"AAPL": 7}, open_orders={"AAPL"}) is None


def test_intent_veto():
    assert intent(make_signal(veto=True, veto_reason="RSI")) is None


def test_intent_het_cho_vi_the():
    held = {f"S{i}": 1 for i in range(CFG.max_positions)}
    assert intent(positions=held) is None
    held.pop("S0")
    assert intent(positions=held) is not None  # còn 1 chỗ


def test_intent_vi_the_so_0_khong_tinh_la_dang_nam():
    held = {f"S{i}": 0 for i in range(CFG.max_positions)}
    assert intent(positions=held) is not None


def test_intent_tuong_quan_cao_bi_chan():
    rets = make_returns().rename(columns={"B": "AAPL"})
    assert intent(positions={"A": 10}, returns=rets) is None
    assert intent(positions={"C": 10}, returns=rets) is not None


def test_intent_uncertainty_giam_co():
    # width 0.065, ref 0.0325 -> scale 0.5 -> 100 * 0.5 = 50
    o = intent(state=make_state(forecast=make_forecast()), ref_width=0.0325)
    assert o.qty == 50


def test_intent_het_tien_thi_none():
    assert intent(cash=50) is None  # không ép 1 cổ


def test_intent_atr_thieu_du_lieu():
    state = MarketState("AAPL", NOW, make_bars(n=5))
    assert intent(state=state) is None


def test_intent_ghi_log_ly_do(caplog):
    with caplog.at_level(logging.INFO, logger="paper_bot"):
        intent(positions={"AAPL": 5})
    assert "không mua trùng" in caplog.text
