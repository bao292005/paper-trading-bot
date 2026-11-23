"""Luật rule-based có quyền PHỦ QUYẾT (veto) tín hiệu, kể cả tín hiệu của LLM.

Tín hiệu bị veto KHÔNG bị xoá: chỉ đặt veto=True + veto_reason để vẫn ghi DB và giải thích được.
"""

from typing import List

from ..contracts import MarketState, Signal

COST = 0.002      # phí 2 chiều ~ 2 x 10 bps (prereg)
MIN_BARS = 60     # ít hơn số nến này thì không tin chỉ báo
MA_LONG = 200


def veto_reasons(sig: Signal, state: MarketState, cost: float = COST,
                 min_bars: int = MIN_BARS) -> List[str]:
    """Danh sách lý do veto cho 1 tín hiệu (rỗng = không bị veto)."""
    reasons = []
    bars = state.bars

    # (c) thiếu dữ liệu -> chặn mọi lệnh
    if len(bars) < min_bars:
        reasons.append(f"thiếu dữ liệu: {len(bars)} < {min_bars} nến")

    if sig.action == "BUY":
        # (a) dự báo lợi suất trung vị còn không bù nổi phí
        fc = state.forecast
        if fc is not None and fc.median < -cost:
            reasons.append(f"forecast median {fc.median:+.4f} < -phí {cost}")

        # (b) giá dưới MA200 của chính mã VÀ thị trường bear_volatile
        if state.regime == "bear_volatile" and len(bars) >= MA_LONG:
            ma200 = float(bars["close"].iloc[-MA_LONG:].mean())
            price = float(bars["close"].iloc[-1])
            if price < ma200:
                reasons.append(f"giá {price:.2f} < MA200 {ma200:.2f} trong bear_volatile")
    return reasons


def apply_veto(signals: List[Signal], state: MarketState, cost: float = COST,
               min_bars: int = MIN_BARS) -> List[Signal]:
    """Đánh dấu veto lên từng tín hiệu (sửa tại chỗ) và trả lại danh sách."""
    for sig in signals:
        reasons = veto_reasons(sig, state, cost, min_bars)
        if reasons:
            sig.veto = True
            sig.veto_reason = "; ".join(reasons)
    return signals
