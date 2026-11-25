"""Bước 3 ghép bot (vòng có broker giả): nến đã đóng, mã nắm ngoài danh sách, forecast cache,
đối soát khi khởi động, sync fill, kill switch (dry-run không huỷ lệnh) + lưu/khôi phục trạng thái."""

from types import SimpleNamespace as NS

from alpaca.trading.enums import QueryOrderStatus
from test_live_cycle import FakeClient, FakeData, make_bot, rows

from paper_bot import live


class LossClient(FakeClient):
    """Equity hôm nay 100k, cuối phiên trước 110k -> lỗ 9% trong ngày > 3% -> kill switch."""

    def get_account(self):
        return NS(equity="100000", cash="100000", long_market_value="0", last_equity="110000")


class ClosedClient(FakeClient):
    """Trả danh sách lệnh đã đóng khi hỏi status=CLOSED."""

    closed: list = []

    def get_orders(self, filter=None):
        return self.closed if filter is not None and filter.status == QueryOrderStatus.CLOSED else []


def order(oid, sym, side, qty, legs=()):
    return NS(id=oid, symbol=sym, side=NS(value=side), filled_qty=str(qty),
              filled_avg_price="10.5" if qty else None, filled_at="2026-11-24T15:00:00Z", legs=list(legs))


def test_only_closed_bars_used(tmp_path):
    # FakeClient: thị trường đang mở -> nến hôm nay (chưa đóng) bị bỏ, quyết định trên nến hôm qua
    bot = make_bot(tmp_path, FakeClient(strict=True), {"AAA": "BUY"}, dry_run=True)
    bot.run_cycle()
    yesterday = (live.today_ny() - live.pd.Timedelta(days=1)).date().isoformat()
    assert {r[0][:10] for r in rows(bot, "SELECT asof FROM signals")} == {yesterday}


def test_held_symbol_outside_list_loaded_for_corr_only(tmp_path):
    asked = []
    orig = FakeData.get_bars

    class Spy(FakeData):
        def get_bars(self, sym, start, end):
            asked.append(sym)
            return orig(self, sym, start, end)

    bot = make_bot(tmp_path, FakeClient(positions={"ZZZ": 5}, strict=True), {"ZZZ": "SELL"}, dry_run=True)
    bot.data = Spy()
    bot.run_cycle()
    assert "ZZZ" in asked  # có tải nến để tính tương quan
    assert rows(bot, "SELECT * FROM signals WHERE symbol='ZZZ'") == []  # nhưng không ra quyết định


def test_forecast_cached_within_day(tmp_path):
    class Counting:
        calls = []

        def predict_many(self, bars, asof, horizon=5):
            self.calls.append(sorted(bars))
            return {s: None for s in bars}

    bot = make_bot(tmp_path, FakeClient(strict=True), {}, dry_run=True)
    bot.config.flags["use_forecast"] = True
    bot.forecaster = Counting()
    bot.run_cycle()
    bot.run_cycle()
    assert bot.forecaster.calls == [["AAA", "BBB", "CCC"]]  # vòng 2 cùng nến -> dùng cache


def test_reconcile_once_in_real_mode_skipped_in_dry(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, FakeClient(), {})
    calls = []
    monkeypatch.setattr(bot.executor, "reconcile", lambda: calls.append(1) or [])
    bot.run_cycle()
    bot.run_cycle()
    assert calls == [1]
    dry = make_bot(tmp_path, FakeClient(strict=True), {}, dry_run=True)
    monkeypatch.setattr(dry.executor, "reconcile", lambda: calls.append(2) or [])
    dry.run_cycle()
    assert calls == [1]


def test_sync_fills_no_duplicates_and_only_ours(tmp_path):
    client = ClosedClient()
    bot = make_bot(tmp_path, client, {"AAA": "BUY"})
    bot.run_cycle()  # gửi 1 lệnh -> broker id "id1"
    client.closed = [order("id1", "AAA", "buy", 3, legs=[order("leg1", "AAA", "sell", 3),
                                                         order("leg2", "AAA", "sell", 0)]),
                     order("other", "XYZ", "buy", 7)]  # lệnh không do bot gửi -> bỏ
    assert bot.sync_fills() == 2
    assert bot.sync_fills() == 0  # chạy lại: không trùng
    got = rows(bot, "SELECT order_id, side, qty, price FROM fills ORDER BY order_id")
    assert got == [("id1", "buy", 3.0, 10.5), ("leg1", "sell", 3.0, 10.5)]


def test_dry_run_kill_switch_never_cancels_and_persists(tmp_path):
    client = LossClient(strict=True)  # cancel_order* / submit_order -> AssertionError
    bot = make_bot(tmp_path, client, {"AAA": "BUY"}, dry_run=True)
    bot.run_cycle()
    assert bot.executor.halted
    assert rows(bot, "SELECT level FROM events WHERE kind='kill_switch'") == [("CRITICAL",)]
    state = live.load_state(tmp_path / "state.json", "live-dry")
    assert state["halted"] is True and state["day_start_equity"] == 110000.0
    assert state["peak_equity"] == 110000.0  # last_equity là đỉnh thật đã đạt
    # khởi động lại: khôi phục trạng thái ngắt -> lệnh MUA bị chặn (ghi "blocked"), vẫn không gọi broker
    bot2 = make_bot(tmp_path, LossClient(strict=True), {"AAA": "BUY"}, dry_run=True)
    assert bot2.executor.halted
    bot2.run_cycle()
    assert rows(bot2, "SELECT status FROM orders ORDER BY id DESC LIMIT 1") == [("blocked",)]


def test_real_mode_kill_switch_uses_executor(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, LossClient(), {})
    seen = []
    monkeypatch.setattr(bot.executor, "check_kill_switch", lambda e, d, p: seen.append((e, d, p)) or True)
    bot.run_cycle()
    assert seen == [(100000.0, 110000.0, 110000.0)]
