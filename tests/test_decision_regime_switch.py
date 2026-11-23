from paper_bot.contracts import MarketState
from paper_bot.decision.regime_switch import RegimeSwitchStrategy
from paper_bot.strategies import RSIStrategy

from test_decision_base import falling, make_bars


def crossover_bars():
    # giảm rồi nhảy vọt: MA cắt lên (BUY) và RSI quá mua (SELL)
    return make_bars([100 - i for i in range(40)] + [300])


def run(regime, bars):
    st = MarketState("X", bars.index[-1], bars, regime=regime)
    return RegimeSwitchStrategy().generate_from_state(st)


def test_bull_uses_only_ma():
    for regime in ("bull_calm", "bull_volatile"):
        sigs = run(regime, crossover_bars())
        assert [(s.strategy, s.action) for s in sigs] == [("MA_Crossover", "BUY")]
        assert sigs[0].meta["regime"] == regime
        assert "theo xu hướng" in sigs[0].reason


def test_bear_calm_uses_only_rsi():
    sigs = run("bear_calm", crossover_bars())
    assert [(s.strategy, s.action) for s in sigs] == [("RSI", "SELL")]
    # thị trường giảm, RSI quá bán -> BUY được phép
    sigs = run("bear_calm", make_bars(falling()))
    assert [(s.strategy, s.action) for s in sigs] == [("RSI", "BUY")]


def test_bear_volatile_sell_only():
    sigs = run("bear_volatile", crossover_bars())
    assert [(s.strategy, s.action) for s in sigs] == [("RSI", "SELL")]
    assert run("bear_volatile", make_bars(falling())) == []  # BUY bị bỏ


def test_unknown_and_invalid_regime_use_both():
    for regime in ("unknown", "la_la"):
        sigs = run(regime, crossover_bars())
        assert sorted(s.strategy for s in sigs) == ["MA_Crossover", "RSI"]
        assert sigs[0].meta["regime_used"] == "unknown"


def test_custom_mapping():
    strat = RegimeSwitchStrategy({"unknown": [RSIStrategy()]})
    bars = make_bars(falling())
    sigs = strat.generate_from_state(MarketState("X", bars.index[-1], bars, regime="bull_calm"))
    assert [s.strategy for s in sigs] == ["RSI"]
