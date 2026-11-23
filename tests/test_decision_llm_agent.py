"""Test LLMAgentStrategy bằng FakeClient - KHÔNG gọi LLM thật."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from paper_bot.contracts import MarketState, NewsItem
from paper_bot.decision import llm_agent
from paper_bot.decision.llm_agent import PROMPTS, LLMAgentStrategy, parse_decision


class FakeClient:
    """Giả client OpenAI: trả lời theo system prompt (agent nào), ghi lại các lần gọi."""

    def __init__(self, decision='{"action": "BUY", "prob": 0.66, "reason": "xu hướng tăng"}', fail=False):
        self.decision, self.fail, self.calls = decision, fail, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, temperature):
        if self.fail:
            raise ConnectionError("mất mạng")
        system = messages[0]["content"]
        role = next(r for r, p in PROMPTS.items() if p == system)
        self.calls.append(role)
        text = self.decision if role == "decider" else f"tóm tắt {role}"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def recent_state(news=()):
    idx = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=250, name="date")
    c = pd.Series(range(100, 350), index=idx, dtype=float)
    bars = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1e6})
    return MarketState("AAPL", idx[-1], bars, regime="bull_calm", news=list(news))


def test_parse_decision_strict():
    assert parse_decision('{"action":"sell","prob":0.7,"reason":"x"}')["action"] == "SELL"
    assert parse_decision('```json\n{"action":"BUY","prob":0.6,"reason":"x"}\n```')["action"] == "BUY"
    for bad in ["", "tôi nghĩ nên mua", '{"action":"BUY"}', '{"action":"LONG","prob":0.6}',
                '{"action":"BUY","prob":1.5}', 'Đây: {"action":"BUY","prob":0.6}', "[1,2]"]:
        assert parse_decision(bad)["action"] == "HOLD", bad


def test_buy_signal_with_full_trace():
    client = FakeClient()
    sigs = LLMAgentStrategy(client=client, model="fake").generate_from_state(recent_state())
    assert len(sigs) == 1
    s = sigs[0]
    assert s.action == "BUY" and s.strategy == "LLMAgent" and s.confidence == 0.66 and s.prob is None
    trace = s.meta["llm"]
    for key in ("facts", "technical", "news", "risk", "decider_raw", "decision"):
        assert key in trace
    assert "rsi14" in trace["facts"]
    # không có tin -> không gọi NewsAnalyst
    assert client.calls == ["technical", "risk", "decider"]


def test_news_agent_called_and_future_news_ignored():
    st = recent_state()
    now = pd.Timestamp(st.asof)
    st.news = [NewsItem("tin cũ", now - timedelta(days=1)), NewsItem("tin TƯƠNG LAI", now + timedelta(days=1))]
    client = FakeClient()
    strat = LLMAgentStrategy(client=client)
    strat.generate_from_state(st)
    assert client.calls == ["technical", "news", "risk", "decider"]


def test_bad_json_and_hold_give_no_signal():
    strat = LLMAgentStrategy(client=FakeClient(decision="mua đi"))
    assert strat.generate_from_state(recent_state()) == []
    assert strat.last_trace["decision"]["action"] == "HOLD"
    hold = LLMAgentStrategy(client=FakeClient(decision='{"action":"HOLD","prob":0.5,"reason":"chờ"}'))
    assert hold.generate_from_state(recent_state()) == []


def test_client_error_gives_hold():
    strat = LLMAgentStrategy(client=FakeClient(fail=True))
    assert strat.generate_from_state(recent_state()) == []
    assert "lỗi" in strat.last_trace["decision"]["reason"]


def test_history_not_allowed():
    client = FakeClient()
    st = recent_state()
    st.asof = datetime(2020, 1, 2)
    assert LLMAgentStrategy(client=client).generate_from_state(st) == []
    assert client.calls == []


def test_missing_key_returns_empty(monkeypatch):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert llm_agent.make_client_from_env() is None
    assert LLMAgentStrategy().generate_from_state(recent_state()) == []
