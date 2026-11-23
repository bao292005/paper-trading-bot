"""Test hàm ghi/đọc kết quả và các hàm load của dashboard (offline, DB tạm)."""

from datetime import datetime

import pandas as pd
import pytest

from paper_bot import dashboard as dash
from paper_bot.backtest import run_backtest
from paper_bot.backtest.report import load_equity, report_from_store, save_result
from paper_bot.contracts import OrderIntent, Signal
from paper_bot.storage import Store


def small_result():
    idx = pd.bdate_range("2020-01-01", periods=5, name="date")
    df = pd.DataFrame({"open": [10, 11, 12, 9, 13.0], "high": [10, 11, 12, 12, 13.0],
                       "low": [10, 11, 12, 9, 13.0], "close": [10, 11, 12, 9, 13.0],
                       "volume": 1.0}, index=idx)
    first = {"done": False}

    def policy(asof, view, pf):
        if first["done"]:
            return []
        first["done"] = True
        return [OrderIntent("AAA", "buy", 50)]
    return run_backtest({"AAA": df}, idx[0], idx[-1], policy, initial_cash=1000, cost_bps=0)


def test_save_and_report(tmp_path):
    db = tmp_path / "t.db"
    res = small_result()
    store = Store(db, run_id="bt-X")
    save_result(res, store, {"sharpe": 1.0})
    save_result(res, store)  # chạy lại không bị nhân đôi
    eq = load_equity(store, "bt-X")
    assert len(eq) == 5
    # Mua 50 cp @11 -> cash 450; equity 1000, 1000, 1050, 900, 1100 -> DD = 900/1050 - 1
    assert eq["drawdown"].min() == pytest.approx(900 / 1050 - 1)
    rep = report_from_store(store, "bt-X")
    assert rep["total_equity"] == pytest.approx(res.equity["equity"].iloc[-1])
    assert rep["n_fills"] == 1
    assert rep["max_drawdown"] == pytest.approx(eq["drawdown"].min())
    assert report_from_store(store, "khong-co") == {}


def test_save_refuses_non_backtest_run(tmp_path):
    with pytest.raises(ValueError):
        save_result(small_result(), Store(tmp_path / "t.db", run_id="live"))


def test_dashboard_loaders(tmp_path):
    db = tmp_path / "t.db"
    save_result(small_result(), Store(db, run_id="bt-B0"))
    live = Store(db, run_id="live")
    live.log_signal(Signal("AAA", "BUY", 10.0, datetime(2020, 1, 2), "RSI oversold", 0.7,
                           strategy="RSI", prob=0.55, veto=True, veto_reason="bear regime"))
    live.log_event("kill_switch", "Dừng giao dịch", level="WARNING")

    conn = dash.connect_readonly(db)
    assert dash.list_runs(conn) == ["bt-B0", "live"]
    sig = dash.load_table(conn, "signals", "live")
    assert sig.loc[0, "veto_reason"] == "bear regime" and sig.loc[0, "prob"] == 0.55
    assert dash.load_table(conn, "events", "live").loc[0, "kind"] == "kill_switch"
    assert len(dash.load_table(conn, "fills", "bt-B0")) == 1
    with pytest.raises(ValueError):
        dash.load_table(conn, "sqlite_master", "live")
    curves = dash.load_equity_many(conn, ["bt-B0"])
    assert "drawdown" in curves["bt-B0"].columns
    summ = dash.summary_table(conn, ["bt-B0", "live"])  # live không có equity -> bỏ
    assert list(summ.index) == ["bt-B0"]
    assert dash.connect_readonly(tmp_path / "khong-co.db") is None


def test_load_result_docs(tmp_path):
    (tmp_path / "a.md").write_text("# A", encoding="utf-8")
    assert dash.load_result_docs(tmp_path) == {"a.md": "# A"}
