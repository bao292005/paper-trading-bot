"""Gộp nhiều tín hiệu của 1 mã thành 1 quyết định (thay logic gom trong bot.py).

Bài gốc: mâu thuẫn BUY vs SELL -> luôn đứng ngoài; cùng hướng -> trung bình confidence.
Cải tiến: mâu thuẫn thì chọn bên mạnh hơn rõ rệt (chênh tổng score >= 0.2), không thì đứng ngoài.
"""

import logging
from typing import List, Optional, Tuple

from ..contracts import Signal

logger = logging.getLogger("paper_bot")

MIN_GAP = 0.2  # chênh lệch tổng score tối thiểu để phá thế mâu thuẫn


def _merge(signals: List[Signal], note: str = "") -> Signal:
    """Gộp các tín hiệu CÙNG hướng: confidence/prob trung bình, reason nối lại."""
    n = len(signals)
    last = signals[-1]
    probs = [s.prob for s in signals]
    # Chỉ trung bình prob khi mọi tín hiệu đều đã hiệu chỉnh (tránh trộn prob với confidence)
    prob = sum(probs) / n if all(p is not None for p in probs) else None
    reason = " + ".join(f"[{s.strategy}] {s.reason}" for s in signals)
    if note:
        reason = f"{reason} || {note}"
    return Signal(
        symbol=last.symbol,
        action=last.action,
        price=last.price,
        timestamp=last.timestamp,
        reason=reason,
        confidence=sum(s.confidence for s in signals) / n,
        strategy="+".join(s.strategy for s in signals),
        prob=prob,
        horizon_days=last.horizon_days,
        meta={"members": [{"strategy": s.strategy, "action": s.action, "score": s.score,
                           "reason": s.reason, "meta": s.meta} for s in signals],
              "aggregate_note": note},
    )


def aggregate_explain(signals: List[Signal]) -> Tuple[Optional[Signal], str]:
    """Như aggregate() nhưng trả kèm lý do (kể cả khi đứng ngoài)."""
    active = [s for s in signals if not s.veto]  # tín hiệu bị veto không được bỏ phiếu
    if not active:
        return None, "không có tín hiệu (hoặc đều bị veto)"

    buys = [s for s in active if s.action == "BUY"]
    sells = [s for s in active if s.action == "SELL"]
    if not buys or not sells:
        return _merge(active), "cùng hướng"

    buy_score = sum(s.score for s in buys)
    sell_score = sum(s.score for s in sells)
    gap = round(abs(buy_score - sell_score), 9)  # round để tránh lỗi số thực 0.19999...
    if gap < MIN_GAP:
        why = f"mâu thuẫn BUY={buy_score:.2f} vs SELL={sell_score:.2f}, chênh {gap:.2f} < {MIN_GAP} -> đứng ngoài"
        return None, why
    winner = buys if buy_score > sell_score else sells
    why = f"mâu thuẫn BUY={buy_score:.2f} vs SELL={sell_score:.2f}, chọn {winner[0].action} (chênh {gap:.2f})"
    return _merge(winner, why), why


def aggregate(signals: List[Signal]) -> Optional[Signal]:
    sig, why = aggregate_explain(signals)
    if signals:
        logger.info(f"{signals[0].symbol}: gộp tín hiệu - {why}")
    return sig
