"""Vòng full của LiveBot với client giả (offline). Client giả báo lỗi nếu dry-run mà gửi/huỷ lệnh."""

from types import SimpleNamespace as NS

import numpy as np
import pandas as pd

from paper_bot import live
from paper_bot.config import TradingConfig
from paper_bot.contracts import Signal
from paper_bot.decision.base import StateStrategy
from paper_bot.decision.pipeline import DecisionPipeline
from paper_bot.live import LiveBot
from paper_bot.storage import Store


class FakeClient:
    """TradingClient giả. strict=True: mọi lệnh gửi/huỷ đều là LỖI (dùng cho dry-run)."""

    def __init__(self, positions=None, strict=False, clock_error=False):
        self.positions, self.strict, self.clock_error = positions or {}, strict, clock_error
        self.submitted = []

    def get_clock(self):
        if self.clock_error:
            raise ConnectionError("mất mạng")
        return NS(is_open=True)

    def get_account(self):
        return NS(equity="100000", cash="100000", long_market_value="0")

    def get_all_positions(self):
        return [NS(symbol=s, qty=str(q)) for s, q in self.positions.items()]

    def get_orders(self, filter=None):
        return []

    def submit_order(self, req):
        if self.strict:
            raise AssertionError("dry-run KHÔNG được gọi submit_order")
        self.submitted.append(req)
        return NS(id=f"id{len(self.submitted)}")

    def cancel_order_by_id(self, oid):
        raise AssertionError("không được gọi cancel_order_by_id")

    def cancel_orders(self):
        raise AssertionError("không được gọi cancel_orders")


def make_bars(seed=0, n=400):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    idx = pd.date_range(end=live.today_ny(), periods=n, freq="D", name="date")
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": 1e6}, index=idx)


class FakeData:
    def __init__(self, broken=()):
        self.broken = set(broken)

    def get_bars(self, sym, start, end):
        if sym in self.broken:
            raise ConnectionError(f"lỗi tải {sym}")
        return make_bars(seed=seed_of(sym))  # mỗi mã một đường giá (tránh tương quan = 1)


def seed_of(sym):
    return sum(map(ord, sym))


class Fixed(StateStrategy):
    """Chiến lược cố định: {mã: action}."""

    def __init__(self, actions):
        super().__init__("Fixed")
        self.actions = actions

    def generate_from_state(self, state):
        act = self.actions.get(state.symbol)
        if not act:
            return []
        price = float(state.bars["close"].iloc[-1])
        return [Signal(state.symbol, act, price, state.asof, "test", 0.9, strategy="Fixed")]


class NoForecast:
    def predict_many(self, bars, asof, horizon=5):
        return {}


def make_bot(tmp_path, client, actions, dry_run=False, broken=(), disable=()):
    cfg = TradingConfig(api_key="x", api_secret="y", symbols=["AAA", "BBB", "CCC"])
    cfg.dry_run = dry_run
    cfg.disable(disable)
    store = Store(tmp_path / "t.db", run_id="live-dry" if dry_run else "live-paper")
    bot = LiveBot(cfg, trading_client=client, data_manager=FakeData(broken), store=store,
                  forecaster=NoForecast(), calibrator=None, state_path=tmp_path / "state.json")
    bot.pipeline = DecisionPipeline([Fixed(actions)], min_score=0.0)
    return bot


def rows(bot, sql):
    return bot.store.conn.execute(sql).fetchall()


def test_dry_run_never_sends_but_logs_everything(tmp_path):
    client = FakeClient(positions={"BBB": 5}, strict=True)
    bot = make_bot(tmp_path, client, {"AAA": "BUY", "BBB": "SELL"}, dry_run=True)
    bot.run_cycle()
    assert client.submitted == []
    orders = rows(bot, "SELECT symbol, side, status, run_id FROM orders ORDER BY id")
    assert orders == [("BBB", "sell", "dry_run", "live-dry"), ("AAA", "buy", "dry_run", "live-dry")]
    assert len(rows(bot, "SELECT * FROM signals")) == 2
    assert len(rows(bot, "SELECT * FROM equity")) == 1


def test_real_mode_submits_bracket(tmp_path):
    client = FakeClient()
    bot = make_bot(tmp_path, client, {"AAA": "BUY"})
    bot.run_cycle()
    assert len(client.submitted) == 1
    assert client.submitted[0].order_class is not None  # bracket (có stop-loss + take-profit)
    assert rows(bot, "SELECT status, broker_order_id FROM orders") == [("submitted", "id1")]


def test_disable_bracket_and_atr_sizing(tmp_path):
    client = FakeClient()
    bot = make_bot(tmp_path, client, {"AAA": "BUY"}, disable=["bracket", "atr_sizing"])
    bot.run_cycle()
    sl, tp, qty = rows(bot, "SELECT stop_loss, take_profit, qty FROM orders")[0]
    assert sl is None and tp is None
    assert qty == 1000 // make_bars(seed=seed_of("AAA"))["close"].iloc[-1]  # legacy: min(2% cash, 1000$)/giá


def test_one_symbol_error_does_not_stop_others(tmp_path):
    bot = make_bot(tmp_path, FakeClient(strict=True), {"AAA": "BUY", "CCC": "BUY"},
                   dry_run=True, broken=["BBB"])
    bot.run_cycle()
    assert {r[0] for r in rows(bot, "SELECT symbol FROM orders")} == {"AAA", "CCC"}
    assert rows(bot, "SELECT level FROM events WHERE kind='symbol_error'") == [("ERROR",)]


def test_network_error_at_start_skips_cycle(tmp_path):
    bot = make_bot(tmp_path, FakeClient(clock_error=True), {"AAA": "BUY"})
    bot.run_cycle()  # không được ném lỗi
    assert rows(bot, "SELECT * FROM orders") == []
    assert len(rows(bot, "SELECT * FROM events WHERE kind='cycle_error'")) == 1


def test_loop_survives_network_error(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, FakeClient(clock_error=True), {})

    def stop(_):
        raise KeyboardInterrupt  # dừng loop sau vòng đầu

    monkeypatch.setattr(live.time, "sleep", stop)
    bot.run_continuous(interval_minutes=1)  # bắt lỗi mạng, rồi Ctrl+C giả -> thoát êm
    assert len(rows(bot, "SELECT * FROM events WHERE kind='cycle_error'")) == 1


def test_low_score_buy_skipped_but_sell_kept(tmp_path):
    bot = make_bot(tmp_path, FakeClient(positions={"BBB": 5}, strict=True), {}, dry_run=True)
    bot.config.min_confidence = 0.95  # Fixed cho confidence 0.9
    bot.pipeline = DecisionPipeline([Fixed({"AAA": "BUY", "BBB": "SELL"})], min_score=0.0)
    bot.run_cycle()
    assert rows(bot, "SELECT symbol, side FROM orders") == [("BBB", "sell")]


def test_corr_limit_blocks_same_price_path(tmp_path, monkeypatch):
    # BBB đang nắm có đường giá giống hệt AAA -> tương quan 1 -> không mua AAA
    monkeypatch.setattr(FakeData, "get_bars", lambda self, s, a, b: make_bars(seed=1))
    bot = make_bot(tmp_path, FakeClient(positions={"BBB": 5}, strict=True), {"AAA": "BUY"}, dry_run=True)
    bot.run_cycle()
    assert rows(bot, "SELECT * FROM orders") == []
    bot2 = make_bot(tmp_path, FakeClient(positions={"BBB": 5}, strict=True), {"AAA": "BUY"},
                    dry_run=True, disable=["corr_limit"])
    bot2.run_cycle()
    assert len(rows(bot2, "SELECT * FROM orders")) == 1


def test_build_strategies_flags_and_llm_without_key(monkeypatch):
    from paper_bot.decision import llm_agent

    monkeypatch.setattr(llm_agent, "make_client_from_env", lambda: None)
    cfg = TradingConfig(api_key="x", api_secret="y")
    cfg.use_llm = True
    assert [s.name for s in live.build_strategies(cfg)] == ["RegimeSwitch"]
    cfg.disable(["regime"])
    assert [s.name for s in live.build_strategies(cfg)] == ["MA_Crossover", "RSI"]
