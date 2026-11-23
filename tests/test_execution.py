"""Test execution.py bằng client giả (không gọi mạng, không đặt lệnh thật)."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from paper_bot.contracts import Fill, OrderIntent
from paper_bot.execution import AlpacaExecutor, KillSwitch
from paper_bot.risk import RiskConfig
from paper_bot.storage import Store


class FakeClient:
    """Giả TradingClient: chỉ ghi lại những gì được gọi."""

    def __init__(self, positions=None, orders=None, fail=False, cancel_fail=False):
        self.positions = positions or []  # list SimpleNamespace(symbol, qty)
        self.orders = orders or []  # list SimpleNamespace(id, symbol, side)
        self.fail = fail
        self.cancel_fail = cancel_fail
        self.submitted = []
        self.cancelled = []

    def submit_order(self, request):
        if self.fail:
            raise APIError("insufficient buying power")
        self.submitted.append(request)
        return SimpleNamespace(id=f"oid-{len(self.submitted)}")

    def cancel_orders(self):  # kill switch KHÔNG được gọi hàm này (sẽ huỷ cả stop-loss)
        raise AssertionError("không được huỷ mọi lệnh")

    def cancel_order_by_id(self, order_id):
        if self.cancel_fail:
            raise APIError("order not cancelable")
        self.cancelled.append(order_id)
        self.orders = [o for o in self.orders if o.id != order_id]

    def get_all_positions(self):
        return self.positions

    def get_orders(self, filter=None):
        return self.orders


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db", run_id="test")


def rows(store, sql):
    return store.conn.execute(sql).fetchall()


BUY = OrderIntent("AAPL", "buy", 10, stop_loss=96.0, take_profit=106.0, reason="test")


# ---- KillSwitch ----
CFG = RiskConfig()  # 3%/ngày, 15% drawdown


def test_kill_switch_binh_thuong():
    assert KillSwitch(CFG).check(99_000, 100_000, 100_000) == (False, "ok")


def test_kill_switch_lo_ngay():
    tripped, why = KillSwitch(CFG).check(96_000, 100_000, 100_000)  # -4% > 3%
    assert tripped and "ngày" in why


def test_kill_switch_dung_nguong_chua_ngat():
    assert not KillSwitch(CFG).check(97_000, 100_000, 100_000)[0]  # đúng -3%: chưa vượt


def test_kill_switch_drawdown():
    # trong ngày chỉ -1% nhưng cách đỉnh -20%
    tripped, why = KillSwitch(CFG).check(99_000, 100_000, 123_750)
    assert tripped and "đỉnh" in why


# ---- submit ----
def test_submit_bracket(store):
    client = FakeClient()
    broker_id = AlpacaExecutor(client, store).submit(BUY)
    req = client.submitted[0]
    assert broker_id == "oid-1"
    assert isinstance(req, MarketOrderRequest)
    assert req.order_class == OrderClass.BRACKET and req.time_in_force == TimeInForce.GTC
    assert req.take_profit.limit_price == 106.0 and req.stop_loss.stop_price == 96.0
    assert rows(store, "SELECT broker_order_id, status, stop_loss FROM orders") == [("oid-1", "submitted", 96.0)]


def test_submit_khong_stop_la_lenh_thuong(store):
    client = FakeClient()
    AlpacaExecutor(client, store).submit(OrderIntent("AAPL", "sell", 5))
    req = client.submitted[0]
    assert req.order_class is None and req.side == OrderSide.SELL and req.time_in_force == TimeInForce.DAY


def test_submit_limit(store):
    client = FakeClient()
    AlpacaExecutor(client, store).submit(OrderIntent("AAPL", "buy", 5, order_type="limit", limit_price=99.123))
    req = client.submitted[0]
    assert isinstance(req, LimitOrderRequest) and req.limit_price == 99.12


def test_submit_qty_0_khong_gui(store):
    client = FakeClient()
    assert AlpacaExecutor(client, store).submit(OrderIntent("AAPL", "buy", 0)) is None
    assert client.submitted == []


def test_submit_loi_api_ghi_rejected(store):
    client = FakeClient(fail=True)
    assert AlpacaExecutor(client, store).submit(BUY) is None
    assert rows(store, "SELECT status FROM orders") == [("rejected",)]
    assert rows(store, "SELECT kind, level FROM events") == [("order_error", "ERROR")]


# ---- kill switch trong executor ----
def open_order(oid, symbol, side):
    return SimpleNamespace(id=oid, symbol=symbol, side=OrderSide(side))


def test_executor_ngat_chi_huy_lenh_mua_giu_stop(store):
    client = FakeClient(orders=[
        open_order("buy-1", "MSFT", "buy"),  # lệnh vào chưa khớp (bracket cha) -> huỷ
        open_order("stop-1", "AAPL", "sell"),  # stop-loss bảo vệ vị thế đã nắm -> giữ
        open_order("tp-1", "AAPL", "sell"),  # take-profit -> giữ
    ])
    ex = AlpacaExecutor(client, store)
    assert ex.check_kill_switch(equity=90_000, day_start_equity=100_000, peak_equity=100_000)
    assert client.cancelled == ["buy-1"]
    assert [o.id for o in client.orders] == ["stop-1", "tp-1"]
    assert client.submitted == []  # không tự bán
    msg, data = rows(store, "SELECT message, data FROM events WHERE kind='kill_switch'")[0]
    assert "không tự bán" in msg and '"kept": 2' in data


def test_executor_ngat_chan_mua_moi(store):
    client = FakeClient()
    ex = AlpacaExecutor(client, store)
    ex.check_kill_switch(90_000, 100_000, 100_000)
    assert ex.submit(BUY) is None  # mua bị chặn
    assert client.submitted == []
    assert rows(store, "SELECT status FROM orders") == [("blocked",)]


def test_executor_huy_loi_van_tiep_tuc(store):
    client = FakeClient(orders=[open_order("buy-1", "MSFT", "buy")], cancel_fail=True)
    ex = AlpacaExecutor(client, store)
    assert ex.check_kill_switch(90_000, 100_000, 100_000)  # vẫn ngắt dù huỷ lỗi
    assert rows(store, "SELECT kind FROM events ORDER BY id")[0] == ("cancel_error",)


def test_executor_van_cho_ban_khi_ngat(store):
    client = FakeClient()
    ex = AlpacaExecutor(client, store)
    ex.check_kill_switch(90_000, 100_000, 100_000)
    assert ex.submit(OrderIntent("AAPL", "sell", 5)) == "oid-1"


def test_executor_ngat_chi_xu_ly_mot_lan(store):
    client = FakeClient(orders=[open_order("buy-1", "MSFT", "buy")])
    ex = AlpacaExecutor(client, store)
    ex.check_kill_switch(90_000, 100_000, 100_000)
    ex.check_kill_switch(89_000, 100_000, 100_000)
    assert client.cancelled == ["buy-1"]
    assert len(rows(store, "SELECT id FROM events WHERE kind='kill_switch'")) == 1


def test_executor_khong_ngat_khi_binh_thuong(store):
    client = FakeClient(orders=[open_order("buy-1", "MSFT", "buy")])
    ex = AlpacaExecutor(client, store)
    assert not ex.check_kill_switch(100_500, 100_000, 100_500)
    assert client.cancelled == []


def test_executor_resume(store):
    ex = AlpacaExecutor(FakeClient(), store)
    ex.check_kill_switch(90_000, 100_000, 100_000)
    ex.resume()
    assert ex.submit(BUY) == "oid-1"


# ---- reconcile ----
def fill(order_id, side="buy", qty=10, symbol="AAPL"):
    return Fill(order_id, symbol, side, qty, 100.0, datetime(2024, 3, 4))


def pos(symbol, qty):
    return SimpleNamespace(symbol=symbol, qty=str(qty))  # alpaca trả qty dạng chuỗi


def test_reconcile_khop(store):
    client = FakeClient(positions=[pos("AAPL", 10)])
    ex = AlpacaExecutor(client, store)
    ex.submit(BUY)  # oid-1, vẫn đang chờ trên broker
    client.orders = [SimpleNamespace(id="oid-1", symbol="AAPL")]
    store.log_fill(fill("oid-1"))
    assert ex.reconcile() == []
    assert rows(store, "SELECT level FROM events WHERE kind='reconcile'") == [("INFO",)]


def test_reconcile_lenh_la_tren_broker(store):
    client = FakeClient(orders=[SimpleNamespace(id="ghost", symbol="TSLA")])
    diffs = AlpacaExecutor(client, store).reconcile()
    assert diffs == [{"kind": "unknown_order", "order_id": "ghost"}]
    assert rows(store, "SELECT level FROM events WHERE kind='reconcile'") == [("WARNING",)]


def test_reconcile_lenh_trong_db_mat_tren_broker(store):
    client = FakeClient()
    ex = AlpacaExecutor(client, store)
    ex.submit(BUY)  # oid-1; broker không còn chờ và không có fill
    assert ex.reconcile() == [{"kind": "missing_order", "order_id": "oid-1", "symbol": "AAPL"}]


def test_reconcile_lenh_da_khop_khong_bao(store):
    client = FakeClient(positions=[pos("AAPL", 10)])
    ex = AlpacaExecutor(client, store)
    ex.submit(BUY)
    store.log_fill(fill("oid-1"))  # đã khớp: không còn chờ nhưng có fill
    assert ex.reconcile() == []


def test_reconcile_lech_vi_the(store):
    client = FakeClient(positions=[pos("AAPL", 7), pos("MSFT", 3)])
    store.log_fill(fill("a", qty=10))
    store.log_fill(fill("b", "sell", qty=2))  # DB: AAPL = 8; broker: 7; MSFT chỉ có ở broker
    diffs = AlpacaExecutor(client, store).reconcile()
    assert {"kind": "position_mismatch", "symbol": "AAPL", "db_qty": 8.0, "broker_qty": 7.0} in diffs
    assert {"kind": "position_mismatch", "symbol": "MSFT", "db_qty": 0.0, "broker_qty": 3.0} in diffs
    assert len(diffs) == 2


def test_reconcile_chi_xet_dung_run_id(store, tmp_path):
    other = Store(store.path, run_id="khac")
    other.log_fill(fill("z", symbol="NVDA"))  # fill của run khác không được tính
    assert AlpacaExecutor(FakeClient(), store).reconcile() == []
