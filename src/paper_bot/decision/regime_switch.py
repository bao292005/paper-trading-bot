"""Regime switching: theo bối cảnh thị trường (state.regime) chọn tập chiến lược phù hợp.

Kế thừa bài gốc: MA (theo xu hướng) và RSI (đảo chiều) bổ trợ nhau - ở đây ta dùng
mỗi cái đúng lúc nó mạnh nhất thay vì luôn chạy cả hai.
"""

from typing import Dict, Iterable, List, Optional

from ..contracts import MarketState, Signal
from ..strategies import MovingAverageCrossoverStrategy, RSIStrategy, TradingStrategy
from .base import StateStrategy, wrap_legacy

# Lý do chọn chiến lược theo từng regime (ghi vào reason/meta để giải thích được)
WHY = {
    "bull_calm": "thị trường tăng, ít biến động -> theo xu hướng (MA)",
    "bull_volatile": "thị trường tăng, biến động -> theo xu hướng (MA)",
    "bear_calm": "thị trường giảm, ít biến động -> bắt hồi (RSI)",
    "bear_volatile": "thị trường giảm mạnh -> chỉ cho BÁN, không mở vị thế mới",
    "unknown": "chưa rõ regime -> MA + RSI như bài gốc",
}


def default_mapping() -> Dict[str, List[TradingStrategy]]:
    ma, rsi = MovingAverageCrossoverStrategy(), RSIStrategy()
    return {
        "bull_calm": [ma],
        "bull_volatile": [ma],
        "bear_calm": [rsi],
        "bear_volatile": [ma, rsi],  # vẫn chạy cả hai nhưng chỉ giữ SELL
        "unknown": [ma, rsi],
    }


class RegimeSwitchStrategy(StateStrategy):
    def __init__(self, mapping: Optional[Dict[str, Iterable[TradingStrategy]]] = None,
                 sell_only: Iterable[str] = ("bear_volatile",)):
        super().__init__("RegimeSwitch")
        mapping = mapping if mapping is not None else default_mapping()
        # Bọc mọi chiến lược gốc thành StateStrategy
        self.mapping = {r: [wrap_legacy(s) for s in strats] for r, strats in mapping.items()}
        self.sell_only = set(sell_only)

    def generate_from_state(self, state: MarketState) -> List[Signal]:
        regime = state.regime if state.regime in self.mapping else "unknown"
        strategies = self.mapping.get(regime, [])
        why = WHY.get(regime, f"regime {regime}")
        names = [s.name for s in strategies]

        out = []
        for strat in strategies:
            for sig in strat.generate_from_state(state):
                if regime in self.sell_only and sig.action != "SELL":
                    continue  # bear_volatile: bỏ BUY
                sig.reason = f"{sig.reason} | regime={state.regime}: {why}"
                sig.meta["regime"] = state.regime
                sig.meta["regime_used"] = regime
                sig.meta["regime_why"] = why
                sig.meta["regime_strategies"] = names
                out.append(sig)
        return out
