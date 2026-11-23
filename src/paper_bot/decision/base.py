"""Nền cho P2: chiến lược nhận MarketState thay vì chỉ DataFrame.

Kế thừa bài gốc: vẫn là Strategy pattern (TradingStrategy), vẫn có generate_signals(data, symbol).
"""

from abc import abstractmethod
from dataclasses import fields
from typing import List

import pandas as pd

from ..contracts import MarketState, Signal
from ..strategies import TradingSignal, TradingStrategy


def state_from_bars(data: pd.DataFrame, symbol: str) -> MarketState:
    """Tạo MarketState tối thiểu từ DataFrame (dùng khi gọi theo kiểu bài gốc)."""
    asof = data.index[-1] if len(data) else pd.Timestamp.now()
    return MarketState(symbol=symbol, asof=asof, bars=data)


def to_signal(sig: TradingSignal, strategy: str, **extra) -> Signal:
    """Chuyển TradingSignal (bài gốc) -> Signal (contracts), giữ nguyên confidence."""
    base = {f.name: getattr(sig, f.name) for f in fields(TradingSignal)}
    return Signal(**base, strategy=strategy, **extra)


class StateStrategy(TradingStrategy):
    """Chiến lược dựa trên MarketState. Lớp con chỉ cần viết generate_from_state."""

    @abstractmethod
    def generate_from_state(self, state: MarketState) -> List[Signal]:
        pass

    def generate_signals(self, data: pd.DataFrame, symbol: str) -> List[TradingSignal]:
        # Tương thích bài gốc: bot.py cũ vẫn gọi được
        return list(self.generate_from_state(state_from_bars(data, symbol)))


class LegacyWrapper(StateStrategy):
    """Bọc chiến lược gốc (MA, RSI, Bollinger) - KHÔNG sửa logic của chúng."""

    def __init__(self, strategy: TradingStrategy):
        super().__init__(strategy.name)
        self.inner = strategy

    def generate_from_state(self, state: MarketState) -> List[Signal]:
        out = []
        for sig in self.inner.generate_signals(state.bars, state.symbol):
            s = to_signal(sig, self.name)
            # Bài gốc dùng datetime.now(); backtest cần thời điểm quyết định = asof
            s.timestamp = state.asof
            out.append(s)
        return out


def wrap_legacy(strategy: TradingStrategy) -> StateStrategy:
    """Bọc chiến lược bài gốc thành StateStrategy. Đã là StateStrategy thì trả nguyên."""
    if isinstance(strategy, StateStrategy):
        return strategy
    return LegacyWrapper(strategy)
