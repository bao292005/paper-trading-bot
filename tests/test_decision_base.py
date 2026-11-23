import pandas as pd

from paper_bot.contracts import MarketState, Signal
from paper_bot.decision.base import StateStrategy, wrap_legacy
from paper_bot.strategies import (
    BollingerBandsStrategy,
    MovingAverageCrossoverStrategy,
    RSIStrategy,
)


def make_bars(closes):
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="B", name="date")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6})


def falling(n=60):
    return [200 - i for i in range(n)]


def test_wrap_rsi_returns_signal_keeps_confidence():
    bars = make_bars(falling())  # giảm liên tục -> RSI = 0 -> BUY conf 0.9
    strat = wrap_legacy(RSIStrategy())
    state = MarketState("AAPL", bars.index[-1], bars)
    sigs = strat.generate_from_state(state)
    assert len(sigs) == 1
    s = sigs[0]
    assert isinstance(s, Signal)
    assert s.action == "BUY" and s.strategy == "RSI"
    assert s.confidence == RSIStrategy().generate_signals(bars, "AAPL")[0].confidence
    assert s.timestamp == bars.index[-1]  # dùng asof, không phải datetime.now()


def test_wrap_ma_crossover():
    closes = [100 - i for i in range(40)] + [300]  # giảm rồi nhảy vọt -> MA10 cắt lên MA30
    bars = make_bars(closes)
    sigs = wrap_legacy(MovingAverageCrossoverStrategy()).generate_from_state(
        MarketState("X", bars.index[-1], bars))
    assert [s.action for s in sigs] == ["BUY"]
    assert sigs[0].strategy == "MA_Crossover" and sigs[0].confidence == 0.7


def test_wrap_bollinger_and_legacy_api():
    bars = make_bars([100.0] * 25 + [80.0])  # thủng dải dưới
    strat = wrap_legacy(BollingerBandsStrategy())
    # Gọi theo kiểu bài gốc vẫn chạy
    sigs = strat.generate_signals(bars, "X")
    assert sigs[0].action == "BUY" and sigs[0].strategy == "BollingerBands"


def test_not_enough_data_returns_empty():
    bars = make_bars([100.0] * 5)
    assert wrap_legacy(MovingAverageCrossoverStrategy()).generate_signals(bars, "X") == []


def test_wrap_state_strategy_is_noop():
    class Dummy(StateStrategy):
        def generate_from_state(self, state):
            return []

    d = Dummy("d")
    assert wrap_legacy(d) is d
