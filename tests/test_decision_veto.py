from datetime import datetime

from paper_bot.contracts import Forecast, MarketState, Signal
from paper_bot.decision.veto import apply_veto

from test_decision_base import make_bars


def sig(action):
    return Signal("X", action, 100.0, datetime(2020, 1, 2), "r", 0.8, strategy="S")


def state(closes, regime="unknown", median=None):
    bars = make_bars(closes)
    fc = None if median is None else Forecast("naive", 5, {0.1: median - 0.03, 0.5: median, 0.9: median + 0.03})
    return MarketState("X", bars.index[-1], bars, regime=regime, forecast=fc)


def test_no_veto_normal_case():
    s = apply_veto([sig("BUY")], state([100.0] * 100, median=0.01))[0]
    assert not s.veto and s.veto_reason == ""


def test_rule_a_forecast_below_cost():
    s = apply_veto([sig("BUY")], state([100.0] * 100, median=-0.005))[0]
    assert s.veto and "forecast" in s.veto_reason
    # -0.001 > -0.002 -> chưa đủ xấu, không veto
    assert not apply_veto([sig("BUY")], state([100.0] * 100, median=-0.001))[0].veto
    # SELL không bị luật (a)
    assert not apply_veto([sig("SELL")], state([100.0] * 100, median=-0.05))[0].veto


def test_rule_b_below_ma200_in_bear_volatile():
    closes = [200.0] * 200 + [100.0]  # giá cuối < MA200
    s = apply_veto([sig("BUY")], state(closes, regime="bear_volatile"))[0]
    assert s.veto and "MA200" in s.veto_reason
    # cùng giá nhưng regime khác -> không veto
    assert not apply_veto([sig("BUY")], state(closes, regime="bear_calm"))[0].veto
    # SELL không bị chặn
    assert not apply_veto([sig("SELL")], state(closes, regime="bear_volatile"))[0].veto
    # giá trên MA200 -> không veto
    up = [100.0] * 200 + [300.0]
    assert not apply_veto([sig("BUY")], state(up, regime="bear_volatile"))[0].veto


def test_rule_c_not_enough_bars_blocks_any_order():
    for action in ("BUY", "SELL"):
        s = apply_veto([sig(action)], state([100.0] * 59))[0]
        assert s.veto and "thiếu dữ liệu" in s.veto_reason
    assert not apply_veto([sig("SELL")], state([100.0] * 60))[0].veto


def test_multiple_reasons_joined():
    s = apply_veto([sig("BUY")], state([100.0] * 10, median=-0.01))[0]
    assert "thiếu dữ liệu" in s.veto_reason and "forecast" in s.veto_reason
