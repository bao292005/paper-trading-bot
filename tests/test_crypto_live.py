"""LiveBot chế độ crypto với client giả (offline). KHÔNG gọi mạng, KHÔNG gửi lệnh thật."""

from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest

from paper_bot import live
from paper_bot.config import CRYPTO_UNIVERSE, TradingConfig
from paper_bot.contracts import OrderIntent, Signal
from paper_bot.decision.base import StateStrategy
from paper_bot.decision.pipeline import DecisionPipeline
from paper_bot.execution import AlpacaExecutor, validate_crypto_order
from paper_bot.live import LiveBot, last_closed_day_crypto, now_utc, run_id_for
from paper_bot.storage import Store


class FakeCryptoClient:
    """TradingClient giả cho crypto. strict=True: mọi lệnh gửi/huỷ đều là LỖI (dùng cho dry-run).
    autofill=True: lệnh mua khớp ngay (vị thế xuất hiện, như market order thật)."""

    def __init__(self, positions=None, strict=False, autofill=False):
        self.positions, self.strict, self.autofill = dict(positions or {}), strict, autofill
        self.submitted, self.cancelled = [], []
        self.order_status = "new"  # trạng thái lệnh broker báo khi hỏi get_order_by_id

    def get_clock(self):
        raise AssertionError("crypto 24/7 KHÔNG được hỏi giờ thị trường")

    def get_account(self):
        return NS(equity="100000", cash="100000", long_market_value="0")

    def get_all_positions(self):
        return [NS(symbol=s, qty=str(q)) for s, q in self.positions.items()]

    def get_orders(self, filter=None):
        return []

    def get_asset(self, sym):
        return NS(min_order_size="0.001", min_trade_increment="0.001", price_increment="0.01")

    def get_order_by_id(self, oid):
        return NS(status=self.order_status, filled_qty="0")

    def submit_order(self, req):
        if self.strict:
            raise AssertionError("dry-run KHÔNG được gọi submit_order")
        self.submitted.append(req)
        if self.autofill and str(req.side.value) == "buy":
            self.positions[req.symbol.replace("/", "")] = float(req.qty)
        return NS(id=f"id{len(self.submitted)}")

    def cancel_order_by_id(self, oid):
        if self.strict:
            raise AssertionError("dry-run KHÔNG được gọi cancel_order_by_id")
        self.cancelled.append(oid)

    def cancel_orders(self):
        raise AssertionError("không được gọi cancel_orders")


def make_bars(seed=0, n=400):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    idx = pd.date_range(end=last_closed_day_crypto(now_utc()), periods=n, freq="D", name="date")
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": 1e6}, index=idx)


class FakeCryptoData:
    def __init__(self, price=None):
        self.price = price  # giá mới nhất giả (None = lỗi lấy giá)

    def get_bars(self, sym, start, end):
        return make_bars(seed=sum(map(ord, sym)))

    def get_latest_price(self, sym):
        return self.price


class Fixed(StateStrategy):
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


SYMS = ["BTC/USD", "ETH/USD", "SOL/USD"]


def make_bot(tmp_path, client, actions=None, dry_run=False, price=None, symbols=None, exchange_stop=False):
    cfg = TradingConfig(api_key="x", api_secret="y", symbols=symbols or list(SYMS), asset="crypto")
    cfg.dry_run = dry_run
    store = Store(tmp_path / "t.db", run_id=run_id_for("crypto", dry_run))
    bot = LiveBot(cfg, trading_client=client, data_manager=FakeCryptoData(price), store=store,
                  forecaster=NoForecast(), calibrator=None, state_path=tmp_path / "state.json",
                  exchange_stop=exchange_stop)
    bot.pipeline = DecisionPipeline([Fixed(actions or {})], min_score=0.0)
    return bot


def rows(bot, sql):
    return bot.store.conn.execute(sql).fetchall()


# ---------- dry-run ----------
def test_dry_run_never_sends_and_never_asks_clock(tmp_path):
    client = FakeCryptoClient(positions={"BTCUSD": 0.5}, strict=True)
    bot = make_bot(tmp_path, client, {"ETH/USD": "BUY", "BTC/USD": "SELL"}, dry_run=True)
    bot.run_cycle()
    assert client.submitted == [] and client.cancelled == []
    assert rows(bot, "SELECT symbol, side, status, run_id FROM orders ORDER BY id") == [
        ("BTC/USD", "sell", "dry_run", "live-crypto-dry"), ("ETH/USD", "buy", "dry_run", "live-crypto-dry")]
    assert len(rows(bot, "SELECT * FROM equity")) == 1


def test_dry_run_sl_hit_logs_but_does_not_sell(tmp_path):
    client = FakeCryptoClient(positions={"BTCUSD": 0.25}, strict=True)
    bot = make_bot(tmp_path, client, dry_run=True, price=90.0)
    bot.state["sltp"] = {"BTC/USD": {"stop_loss": 95.0, "take_profit": 120.0}}
    bot.run_cycle()
    assert client.submitted == []
    assert rows(bot, "SELECT kind FROM events WHERE kind='sl_hit'") == [("sl_hit",)]
    assert rows(bot, "SELECT side, qty, status FROM orders") == [("sell", 0.25, "dry_run")]


# ---------- lệnh mua: không bracket, GTC, SL/TP cất vào state ----------
def test_buy_has_no_bracket_gtc_fractional_and_arms_sltp(tmp_path):
    client = FakeCryptoClient(autofill=True)
    bot = make_bot(tmp_path, client, {"ETH/USD": "BUY"})
    bot.run_cycle()
    assert len(client.submitted) == 1
    req = client.submitted[0]
    assert req.symbol == "ETH/USD" and req.order_class is None
    assert str(req.time_in_force.value) == "gtc"
    assert round(float(req.qty), 3) == float(req.qty)  # làm tròn theo min_trade_increment 0.001
    sltp = bot.state["sltp"]["ETH/USD"]
    assert sltp["stop_loss"] < sltp["take_profit"]
    assert bot.state.get("pending") == {}
    assert live.load_state(bot.state_path, "live-crypto-paper")["sltp"]["ETH/USD"]["stop_loss"] == sltp["stop_loss"]


def test_pending_dropped_when_buy_order_dead(tmp_path):
    client = FakeCryptoClient(autofill=False)  # lệnh mua chưa khớp
    bot = make_bot(tmp_path, client, {"ETH/USD": "BUY"})
    bot.run_cycle()
    assert "ETH/USD" in bot.state["pending"] and not bot.state.get("sltp")  # còn "new" -> giữ pending
    client.order_status = "canceled"  # sau đó broker huỷ lệnh
    bot.promote_pending(bot.snapshot())
    assert bot.state["pending"] == {} and not bot.state.get("sltp")
    assert len(rows(bot, "SELECT * FROM events WHERE kind='sltp_dropped'")) == 1


# ---------- SL/TP tự quản bán đúng qty THỰC ----------
@pytest.mark.parametrize("price, kind", [(90.0, "sl_hit"), (130.0, "tp_hit")])
def test_sl_tp_sells_real_broker_qty(tmp_path, price, kind):
    client = FakeCryptoClient(positions={"BTCUSD": 0.123456})  # qty thực (đã trừ phí bằng coin)
    bot = make_bot(tmp_path, client, price=price)
    bot.state["sltp"] = {"BTC/USD": {"stop_loss": 95.0, "take_profit": 120.0}}
    bot.run_cycle()
    assert len(client.submitted) == 1
    req = client.submitted[0]
    assert req.symbol == "BTC/USD" and str(req.side.value) == "sell" and float(req.qty) == 0.123456
    assert len(rows(bot, f"SELECT * FROM events WHERE kind='{kind}'")) == 1
    assert "BTC/USD" not in bot.state["sltp"]


def test_no_sell_between_sl_and_tp(tmp_path):
    client = FakeCryptoClient(positions={"BTCUSD": 1.0})
    bot = make_bot(tmp_path, client, price=100.0)
    bot.state["sltp"] = {"BTC/USD": {"stop_loss": 95.0, "take_profit": 120.0}}
    bot.run_cycle()
    assert client.submitted == []
    assert "BTC/USD" in bot.state["sltp"]


def test_sltp_cleared_when_position_closed(tmp_path):
    bot = make_bot(tmp_path, FakeCryptoClient(), price=90.0)
    bot.state["sltp"] = {"BTC/USD": {"stop_loss": 95.0, "take_profit": 120.0}}
    bot.run_cycle()
    assert not bot.state["sltp"]


def test_sl_check_survives_missing_price(tmp_path):
    client = FakeCryptoClient(positions={"BTCUSD": 1.0})
    bot = make_bot(tmp_path, client, price=None)
    bot.state["sltp"] = {"BTC/USD": {"stop_loss": 95.0, "take_profit": 120.0}}
    bot.run_cycle()  # không ném lỗi
    assert client.submitted == []


# ---------- stop_limit trên sàn (tuỳ chọn) ----------
def test_exchange_stop_placed_and_cancelled_before_sell(tmp_path):
    client = FakeCryptoClient(autofill=True)
    bot = make_bot(tmp_path, client, {"ETH/USD": "BUY"}, exchange_stop=True)
    bot.run_cycle()
    assert len(client.submitted) == 2  # lệnh mua + stop_limit bán
    assert type(client.submitted[1]).__name__ == "StopLimitOrderRequest"
    stop_id = bot.state["sltp"]["ETH/USD"]["stop_order_id"]
    bot.send(OrderIntent("ETH/USD", "sell", 1.0, reason="test"))
    assert client.cancelled == [stop_id]


def test_exchange_stop_off_by_default(tmp_path):
    client = FakeCryptoClient(autofill=True)
    bot = make_bot(tmp_path, client, {"ETH/USD": "BUY"})
    bot.run_cycle()
    assert len(client.submitted) == 1 and "stop_order_id" not in bot.state["sltp"]["ETH/USD"]


# ---------- làm tròn qty ----------
def test_fit_crypto_qty_rounds_down_to_increment(tmp_path):
    bot = make_bot(tmp_path, FakeCryptoClient())
    assert bot.fit_crypto_qty("BTC/USD", 0.12399, 100.0) == 0.123
    assert bot.fit_crypto_qty("BTC/USD", 0.3, 100.0) == 0.3  # chống sai số số thực (0.3/0.001)
    assert bot.fit_crypto_qty("BTC/USD", 0.0009, 100.0) == 0.0  # < min_order_size -> bỏ
    assert bot.fit_crypto_qty("BTC/USD", 5000.0, 100.0) == 2000.0  # trần 200k$/lệnh


# ---------- whitelist + lệnh sai ----------
def test_unknown_crypto_symbol_blocked_in_livebot(tmp_path):
    with pytest.raises(ValueError, match="whitelist"):
        make_bot(tmp_path, FakeCryptoClient(), symbols=["DOGE/USD"])
    with pytest.raises(ValueError):
        make_bot(tmp_path, FakeCryptoClient(), symbols=["BTCUSD"])  # sai dạng


def test_executor_rejects_unknown_symbol_and_sltp_without_calling_broker(tmp_path):
    client = FakeCryptoClient()
    ex = AlpacaExecutor(client, Store(tmp_path / "e.db", run_id="t"), asset="crypto")
    assert ex.submit(OrderIntent("DOGE/USD", "buy", 1.0)) is None
    assert ex.submit(OrderIntent("BTC/USD", "buy", 0.1, stop_loss=90.0, take_profit=120.0)) is None
    assert client.submitted == []
    statuses = [r[0] for r in ex.store.conn.execute("SELECT status FROM orders")]
    assert statuses == ["rejected", "rejected"]


def test_validate_crypto_order_rules():
    validate_crypto_order("BTC/USD", 0.0001)  # hợp lệ
    for bad in (dict(symbol="SHIB/USD", qty=1), dict(symbol="BTC/USD", qty=0), dict(symbol="BTC/USD", qty=-1),
                dict(symbol="BTC/USD", qty=1, order_type="stop"), dict(symbol="BTC/USD", qty=1, order_type="limit"),
                dict(symbol="BTC/USD", qty=1, stop_loss=1.0), dict(symbol="BTC/USD", qty=1, take_profit=1.0),
                dict(symbol="BTC/USD", qty=1000, order_type="limit", limit_price=300.0),
                dict(symbol="BTC/USD", qty=0.0001, min_qty=0.001), dict(symbol="BTC/USD", qty=float("nan"))):
        with pytest.raises(ValueError):
            validate_crypto_order(**bad)


# ---------- kill switch / state / run_id ----------
def test_kill_switch_keeps_sltp_state(tmp_path):
    bot = make_bot(tmp_path, FakeCryptoClient(), dry_run=True)
    bot.state["sltp"] = {"BTC/USD": {"stop_loss": 95.0, "take_profit": 120.0}}
    bot.check_kill_switch(NS(equity="100000", last_equity="100000"))
    assert bot.state["sltp"]["BTC/USD"]["stop_loss"] == 95.0
    assert "peak_equity" in bot.state


def test_kill_switch_blocks_buy_but_not_sltp_sell(tmp_path):
    client = FakeCryptoClient(positions={"BTCUSD": 0.5})
    bot = make_bot(tmp_path, client, {"ETH/USD": "BUY"}, price=90.0)
    bot.executor.halted = True
    bot.state["sltp"] = {"BTC/USD": {"stop_loss": 95.0, "take_profit": 120.0}}
    bot.run_cycle()
    assert [str(r.side.value) for r in client.submitted] == ["sell"]  # chặn MUA, vẫn cắt lỗ


def test_run_ids_and_loop_runs_without_clock(tmp_path, monkeypatch):
    assert run_id_for("crypto", False) == "live-crypto-paper" and run_id_for("crypto", True) == "live-crypto-dry"
    assert run_id_for("stock", False) == "live-paper" and run_id_for("stock", True) == "live-dry"
    bot = make_bot(tmp_path, FakeCryptoClient(), {}, dry_run=True)

    def stop(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(live.time, "sleep", stop)
    bot.run_continuous(interval_minutes=1)  # crypto: chạy vòng ngay, không hỏi get_clock (sẽ AssertionError)
    assert len(rows(bot, "SELECT * FROM equity")) == 1


def test_snapshot_ignores_stock_positions_in_same_account(tmp_path):
    client = FakeCryptoClient(positions={"NVDA": 10, "BTCUSD": 0.5})
    bot = make_bot(tmp_path, client, price=100.0)
    assert bot.snapshot()["positions"] == {"BTC/USD": 0.5}
    bot.run_cycle()  # không tải nến NVDA, không ném lỗi
    assert rows(bot, "SELECT * FROM events WHERE kind='symbol_error'") == []


def test_last_closed_day_is_yesterday_utc():
    assert last_closed_day_crypto(pd.Timestamp("2026-11-26 03:00")) == pd.Timestamp("2026-11-25")
    assert set(CRYPTO_UNIVERSE) >= set(SYMS)
