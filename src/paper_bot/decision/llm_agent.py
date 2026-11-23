"""LLMAgentStrategy: nhiều "agent" LLM nhỏ, mỗi agent một việc, rồi Decider ra quyết định JSON.

  1. TechnicalAnalyst - tóm tắt chỉ báo (số liệu do CODE tính, LLM chỉ diễn giải)
  2. NewsAnalyst      - tóm tắt state.news (không có tin -> bỏ qua, không gọi LLM)
  3. RiskReviewer     - phản biện, tìm lý do KHÔNG nên vào lệnh
  4. Decider          - trả JSON {"action": "BUY|SELL|HOLD", "prob": 0..1, "reason": "..."}

Chỉ dùng FORWARD (LLM đã "biết" quá khứ nên không backtest được): asof cũ hơn `max_age_days` -> [].
Luật veto (veto.py) vẫn áp lên tín hiệu này trong DecisionPipeline.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd

from ..contracts import MarketState, Signal
from ..data.features import compute_features
from .base import StateStrategy
from .calibration import dist_ma

logger = logging.getLogger("paper_bot")

PROMPTS = {
    "technical": "Bạn là chuyên viên phân tích kỹ thuật. Tóm tắt ngắn (≤5 câu) xu hướng, động lượng, "
                 "biến động của mã dựa trên số liệu được cho. Không bịa số.",
    "news": "Bạn là chuyên viên phân tích tin tức. Tóm tắt ngắn (≤5 câu) các tin dưới đây và tác động "
            "có thể tới giá cổ phiếu trong 5 phiên tới.",
    "risk": "Bạn là người quản lý rủi ro, nhiệm vụ là PHẢN BIỆN. Nêu các lý do chính khiến vào lệnh "
            "lúc này có thể sai (≤5 câu).",
    "decider": "Bạn ra quyết định giao dịch cho 5 phiên tới, có tính phí 0.2% hai chiều. Chỉ trả về "
               'MỘT đối tượng JSON, không thêm chữ nào khác: {"action": "BUY"|"SELL"|"HOLD", '
               '"prob": số 0..1 là xác suất lệnh có lãi sau phí, "reason": "lý do ngắn"}.',
}
HOLD = {"action": "HOLD", "prob": 0.0, "reason": ""}


def parse_decision(text: str) -> dict:
    """Parse JSON CHẶT. Chỉ cho phép bọc trong ```json ... ```. Sai bất kỳ điều gì -> HOLD."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    try:
        d = json.loads(t)
        action = str(d["action"]).upper()
        prob = float(d["prob"])
        reason = str(d.get("reason", ""))
    except (ValueError, TypeError, KeyError, AttributeError):
        return {**HOLD, "reason": "LLM trả về không phải JSON hợp lệ"}
    if action not in ("BUY", "SELL", "HOLD") or not 0.0 <= prob <= 1.0:
        return {**HOLD, "reason": f"giá trị không hợp lệ: action={action}, prob={prob}"}
    return {"action": action, "prob": prob, "reason": reason}


def make_client_from_env():
    """Client OpenAI-compatible từ LLM_BASE_URL / LLM_API_KEY. Thiếu key -> None."""
    from dotenv import load_dotenv

    load_dotenv()
    if not os.getenv("LLM_API_KEY"):
        return None
    from openai import OpenAI

    return OpenAI(base_url=os.getenv("LLM_BASE_URL") or None, api_key=os.getenv("LLM_API_KEY"))


def technical_facts(state: MarketState) -> str:
    """Số liệu kỹ thuật do code tính (LLM không tự tính để tránh bịa)."""
    bars = state.bars
    f = compute_features(bars.iloc[-260:])
    f["dist_ma200"] = dist_ma(bars["close"], 200)
    lines = [f"Mã {state.symbol}, ngày {pd.Timestamp(state.asof).date()}, giá đóng cửa {bars['close'].iloc[-1]:.2f}",
             f"Regime thị trường (SPY): {state.regime}"]
    lines += [f"{k}: {v:.4f}" for k, v in f.items() if pd.notna(v)]
    if state.forecast is not None:
        q = state.forecast.quantiles
        lines.append(f"Dự báo lợi suất {state.forecast.horizon_days} phiên ({state.forecast.model}): "
                     f"q10={q[0.1]:+.4f}, q50={q[0.5]:+.4f}, q90={q[0.9]:+.4f}")
    return "\n".join(lines)


class LLMAgentStrategy(StateStrategy):
    def __init__(self, client=None, model: Optional[str] = None, temperature: float = 0.0,
                 max_age_days: int = 7, max_news: int = 10):
        super().__init__("LLMAgent")
        self.client = client if client is not None else make_client_from_env()
        self.model = model or os.getenv("LLM_MODEL", "deepseek-chat")
        self.temperature = temperature
        self.max_age_days = max_age_days
        self.max_news = max_news
        self.last_trace: dict = {}  # lập luận của lần gọi gần nhất (kể cả HOLD) để ghi log

    def _ask(self, role: str, content: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model, temperature=self.temperature,
            messages=[{"role": "system", "content": PROMPTS[role]}, {"role": "user", "content": content}])
        return resp.choices[0].message.content or ""

    def generate_from_state(self, state: MarketState) -> List[Signal]:
        if self.client is None:
            logger.warning("LLMAgentStrategy: thiếu LLM_API_KEY -> không sinh tín hiệu")
            return []
        if pd.Timestamp(state.asof) < pd.Timestamp(datetime.now() - timedelta(days=self.max_age_days)):
            logger.warning(f"LLMAgentStrategy: asof {state.asof} là quá khứ -> không chạy (chỉ dùng forward)")
            return []

        trace = {"model": self.model, "facts": technical_facts(state)}
        try:
            trace["technical"] = self._ask("technical", trace["facts"])
            news = [n for n in state.news if pd.Timestamp(n.created_at) <= pd.Timestamp(state.asof)]
            if news:
                text = "\n".join(f"- [{n.created_at}] {n.headline}. {n.summary}" for n in news[-self.max_news:])
                trace["news"] = self._ask("news", text)
            else:
                trace["news"] = "Không có tin tức."
            brief = f"KỸ THUẬT:\n{trace['technical']}\n\nTIN TỨC:\n{trace['news']}"
            trace["risk"] = self._ask("risk", f"{trace['facts']}\n\n{brief}")
            trace["decider_raw"] = self._ask("decider", f"{trace['facts']}\n\n{brief}\n\nPHẢN BIỆN:\n{trace['risk']}")
            decision = parse_decision(trace["decider_raw"])
        except Exception as e:  # lỗi mạng/API -> đứng ngoài, không làm sập bot
            logger.error(f"LLMAgentStrategy lỗi khi gọi LLM: {e}")
            decision = {**HOLD, "reason": f"lỗi gọi LLM: {e}"}
        trace["decision"] = decision
        self.last_trace = trace

        if decision["action"] == "HOLD":
            return []
        return [Signal(
            symbol=state.symbol, action=decision["action"], price=float(state.bars["close"].iloc[-1]),
            timestamp=state.asof, reason=f"LLM: {decision['reason']}",
            confidence=decision["prob"],  # prob do LLM tự báo, CHƯA hiệu chỉnh -> để ở confidence
            strategy=self.name, meta={"llm": trace})]
