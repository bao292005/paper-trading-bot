"""Test end-to-end DecisionPipeline với MarketState tổng hợp."""

from paper_bot.contracts import Forecast, MarketState
from paper_bot.decision.llm_agent import LLMAgentStrategy
from paper_bot.decision.pipeline import DecisionPipeline
from paper_bot.decision.regime_switch import RegimeSwitchStrategy
from paper_bot.strategies import MovingAverageCrossoverStrategy, RSIStrategy

from test_decision_base import falling, make_bars
from test_decision_llm_agent import FakeClient, recent_state


def fc(median):
    return Forecast("naive", 5, {0.1: median - 0.03, 0.5: median, 0.9: median + 0.03})


def state(closes, regime="unknown", forecast=None):
    bars = make_bars(closes)
    return MarketState("X", bars.index[-1], bars, regime=regime, forecast=forecast)


class FakeCalibrator:
    def __init__(self, p):
        self.p = p

    def knows(self, sig):
        return sig.strategy in ("MA_Crossover", "RSI")

    def predict_prob(self, sig, state):
        return self.p


def test_regime_switch_buy_passes():
    pipe = DecisionPipeline([RegimeSwitchStrategy()])
    out = pipe.decide(state(falling(100), regime="bear_calm"))  # RSI quá bán -> BUY 0.9
    assert out.action == "BUY" and out.strategy == "RSI" and abs(out.score - 0.9) < 1e-9
    assert out.meta["regime"] == "bear_calm" and "cùng hướng" in out.meta["pipeline_note"]


def test_veto_blocks_and_is_recorded():
    pipe = DecisionPipeline([RegimeSwitchStrategy()])
    assert pipe.decide(state(falling(100), "bear_calm", fc(-0.01))) is None
    assert pipe.last_signals[0].veto and "forecast" in pipe.last_signals[0].veto_reason
    # thiếu dữ liệu (< 60 nến) -> veto mọi lệnh
    assert pipe.decide(state(falling(40), "bear_calm")) is None


def test_calibrator_prob_used_for_threshold():
    st = state(falling(100), "bear_calm")
    low = DecisionPipeline([RegimeSwitchStrategy()], calibrator=FakeCalibrator(0.3))
    assert low.decide(st) is None and "ngưỡng" in low.last_note
    assert low.last_signals[0].prob == 0.3 and abs(low.last_signals[0].confidence - 0.9) < 1e-9
    high = DecisionPipeline([RegimeSwitchStrategy()], calibrator=FakeCalibrator(0.7))
    out = high.decide(st)
    assert out.action == "BUY" and out.prob == 0.7


def test_legacy_conflict_stays_out():
    # phẳng -> giảm -> nhảy vọt: MA BUY 0.7 vs RSI SELL ~0.84, chênh < 0.2 -> đứng ngoài
    closes = [140.0] * 30 + [140 - i for i in range(40)] + [300.0]
    pipe = DecisionPipeline([MovingAverageCrossoverStrategy(), RSIStrategy()])
    assert pipe.decide(state(closes)) is None
    assert {s.action for s in pipe.last_signals} == {"BUY", "SELL"}
    assert "đứng ngoài" in pipe.last_note


def test_llm_signal_is_vetoed_and_not_calibrated():
    st = recent_state()
    pipe = DecisionPipeline([LLMAgentStrategy(client=FakeClient())], calibrator=FakeCalibrator(0.9))
    out = pipe.decide(st)
    assert out.strategy == "LLMAgent" and out.prob is None and out.score == 0.66  # không hiệu chỉnh LLM
    st.forecast = fc(-0.01)
    assert pipe.decide(st) is None and pipe.last_signals[0].veto
