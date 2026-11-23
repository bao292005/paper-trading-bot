"""Ghi kết quả backtest vào SQLite (storage.Store) và đọc lại thành báo cáo.

report_from_store() là bản mở rộng của TradingBot.generate_performance_report() (bài gốc chỉ có
equity, buying_power, số vị thế, lãi trong ngày): đọc bảng equity/fills của một run_id bất kỳ
(bot thật hay backtest) và tính thêm Sharpe, max drawdown, exposure, số lệnh.
"""

import json
import sqlite3
from datetime import datetime

import pandas as pd

from .metrics import cagr, daily_returns, max_drawdown, sharpe


def _conn(store_or_conn) -> sqlite3.Connection:
    return getattr(store_or_conn, "conn", store_or_conn)


def save_result(result, store, metrics: dict = None) -> None:
    """Ghi equity + fill (+ 1 event tóm tắt) của một BacktestResult vào run_id của `store`.

    Chỉ cho phép run_id bắt đầu bằng "bt-"; dữ liệu cũ của CHÍNH run_id đó bị xoá trước
    (chạy lại không bị trùng). Ghi một lần rồi commit (nhanh hơn log từng dòng).
    """
    run_id = store.run_id
    if not run_id.startswith("bt-"):
        raise ValueError(f"run_id backtest phải bắt đầu bằng 'bt-', nhận được {run_id!r}")
    conn = store.conn
    for table in ("equity", "fills", "events"):
        conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
    conn.executemany(
        "INSERT INTO equity (run_id, ts, equity, cash, exposure) VALUES (?, ?, ?, ?, ?)",
        [(run_id, ts.isoformat(), float(r.equity), float(r.cash), float(r.exposure))
         for ts, r in result.equity.iterrows()])
    conn.executemany(
        "INSERT INTO fills (run_id, order_id, symbol, side, qty, price, fee, filled_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(run_id, f.order_id, f.symbol, f.side, f.qty, f.price, f.fee, f.filled_at.isoformat())
         for f in result.fills])
    conn.execute(
        "INSERT INTO events (run_id, ts, level, kind, message, data) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, datetime.now().isoformat(), "INFO", "backtest",
         f"Kết quả backtest {run_id}", json.dumps(metrics or {}, default=str)))
    conn.commit()


def load_equity(store_or_conn, run_id: str) -> pd.DataFrame:
    """Bảng equity của run_id: index ngày, cột equity, cash, exposure, drawdown."""
    df = pd.read_sql_query("SELECT ts, equity, cash, exposure FROM equity WHERE run_id = ? "
                           "ORDER BY ts", _conn(store_or_conn), params=(run_id,))
    df.index = pd.to_datetime(df.pop("ts"), format="ISO8601")
    df.index.name = "ts"
    df["drawdown"] = df["equity"] / df["equity"].cummax() - 1
    return df


def report_from_store(store_or_conn, run_id: str) -> dict:
    """Báo cáo hiệu quả của một run_id. Không có dữ liệu -> dict rỗng."""
    eq = load_equity(store_or_conn, run_id)
    if eq.empty:
        return {}
    conn = _conn(store_or_conn)
    n_fills = conn.execute("SELECT COUNT(*) FROM fills WHERE run_id = ?", (run_id,)).fetchone()[0]
    n_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE run_id = ?", (run_id,)).fetchone()[0]
    e = eq["equity"]
    return {
        "run_id": run_id,
        "start": eq.index[0].date().isoformat(),
        "end": eq.index[-1].date().isoformat(),
        "total_equity": float(e.iloc[-1]),
        "total_return": float(e.iloc[-1] / e.iloc[0] - 1),
        "cagr": cagr(e) if len(e) > 1 else 0.0,
        "sharpe": sharpe(daily_returns(e)),
        "max_drawdown": max_drawdown(e),
        "exposure": float(eq["exposure"].mean()),
        "n_fills": int(n_fills),
        "n_orders": int(n_orders),
    }
