"""Lưu signal / lệnh / fill / equity / sự kiện vào SQLite (thay cho log text khó phân tích).

Một DB dùng chung cho cả bot thật và backtest; cột `run_id` phân biệt
("live", "paper-2026-11-23", "bt-<tên cấu hình>"...).
"""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path("data/trading.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY, run_id TEXT, asof TEXT, symbol TEXT, strategy TEXT, action TEXT,
  price REAL, confidence REAL, prob REAL, horizon_days INTEGER, veto INTEGER, veto_reason TEXT,
  reason TEXT, regime TEXT, meta TEXT
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY, run_id TEXT, asof TEXT, broker_order_id TEXT, symbol TEXT, side TEXT,
  qty REAL, order_type TEXT, limit_price REAL, stop_loss REAL, take_profit REAL, status TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY, run_id TEXT, order_id TEXT, symbol TEXT, side TEXT,
  qty REAL, price REAL, fee REAL, filled_at TEXT
);
CREATE TABLE IF NOT EXISTS equity (
  run_id TEXT, ts TEXT, equity REAL, cash REAL, exposure REAL, PRIMARY KEY (run_id, ts)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, run_id TEXT, ts TEXT, level TEXT, kind TEXT, message TEXT, data TEXT
);
"""


def _iso(x):
    return x.isoformat() if isinstance(x, datetime) else x


class Store:
    def __init__(self, path=DEFAULT_DB, run_id: str = "live"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)

    def _insert(self, table: str, row: dict) -> int:
        row = {"run_id": self.run_id, **{k: _iso(v) for k, v in row.items()}}
        cols = ", ".join(row)
        marks = ", ".join("?" * len(row))
        cur = self.conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})",
                                list(row.values()))
        self.conn.commit()
        return cur.lastrowid

    def log_signal(self, sig, regime: str = "") -> int:
        d = asdict(sig)
        return self._insert("signals", {
            "asof": d["timestamp"], "symbol": d["symbol"], "strategy": d.get("strategy", ""),
            "action": d["action"], "price": d["price"], "confidence": d["confidence"],
            "prob": d.get("prob"), "horizon_days": d.get("horizon_days"),
            "veto": int(d.get("veto", False)), "veto_reason": d.get("veto_reason", ""),
            "reason": d["reason"], "regime": regime, "meta": json.dumps(d.get("meta", {}), default=str),
        })

    def log_order(self, intent, broker_order_id: str = "", status: str = "submitted") -> int:
        d = asdict(intent)
        return self._insert("orders", {**d, "broker_order_id": broker_order_id, "status": status})

    def log_fill(self, fill) -> int:
        return self._insert("fills", asdict(fill))

    def log_equity(self, ts, equity: float, cash: float, exposure: float) -> None:
        self._insert("equity", {"ts": ts, "equity": equity, "cash": cash, "exposure": exposure})

    def log_event(self, kind: str, message: str, level: str = "INFO", data=None) -> None:
        self._insert("events", {"ts": datetime.now(), "level": level, "kind": kind,
                                "message": message, "data": json.dumps(data or {}, default=str)})
