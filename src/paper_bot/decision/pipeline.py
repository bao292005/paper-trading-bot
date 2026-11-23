"""DecisionPipeline: MarketState -> (0 hoặc 1) Signal cuối cùng cho 1 mã.

  sinh tín hiệu -> veto -> hiệu chỉnh xác suất (nếu có) -> gộp -> lọc ngưỡng theo signal.score

Kế thừa bài gốc: ngưỡng lọc 0.5 (min_confidence) - nay áp lên score (prob nếu đã hiệu chỉnh,
không thì confidence như cũ).
"""

import logging
from typing import Iterable, List, Optional

from ..contracts import MarketState, Signal
from ..strategies import TradingStrategy
from .aggregate import aggregate_explain
from .base import wrap_legacy
from .veto import apply_veto

logger = logging.getLogger("paper_bot")


class DecisionPipeline:
    def __init__(self, strategies: Iterable[TradingStrategy], calibrator=None, min_score: float = 0.5):
        self.strategies = [wrap_legacy(s) for s in strategies]
        self.calibrator = calibrator
        self.min_score = min_score
        # Lưu lại lần chạy gần nhất để ghi DB / giải thích (kể cả tín hiệu bị veto hoặc bị lọc)
        self.last_signals: List[Signal] = []
        self.last_note = ""

    def generate(self, state: MarketState) -> List[Signal]:
        out = []
        for strat in self.strategies:
            out.extend(strat.generate_from_state(state))
        return out

    def calibrate(self, signals: List[Signal], state: MarketState) -> None:
        if self.calibrator is None:
            return
        for sig in signals:
            # Chỉ hiệu chỉnh tín hiệu chưa bị veto và thuộc chiến lược calibrator đã học
            if not sig.veto and self.calibrator.knows(sig):
                sig.prob = self.calibrator.predict_prob(sig, state)

    def decide(self, state: MarketState) -> Optional[Signal]:
        signals = apply_veto(self.generate(state), state)
        self.calibrate(signals, state)
        self.last_signals = signals

        final, note = aggregate_explain(signals)
        if final is not None and final.score < self.min_score:
            note = f"{note}; score {final.score:.2f} < ngưỡng {self.min_score} -> bỏ"
            final = None
        self.last_note = note
        for sig in signals:
            if sig.veto:
                logger.info(f"{state.symbol} [{sig.strategy}] {sig.action} bị veto: {sig.veto_reason}")
        logger.info(f"{state.symbol}: {note}")
        if final is not None:
            final.meta["regime"] = state.regime
            final.meta["pipeline_note"] = note
        return final
