"""API /api/account/fills (CHỈ ĐỌC): các lệnh ĐÃ KHỚP của tài khoản Alpaca paper cho 1 mã, để vẽ lên chart.

Lấy từ Alpaca (get_orders status=closed, nested=True, lấy filled_qty > 0) rồi gán "nguồn" cho từng lệnh:
  bracket/OCO TP-SL (theo order_class + loại chân) > server SL/TP (event sltp_hit trùng order_id)
  > web (event web_manual_order/web_position_order/web_close_position cách giờ gửi lệnh <= 10s)
  > bot (order_id có trong bảng orders của run live-*) > khác (đặt từ dashboard Alpaca).
Cache 10 giây theo (mã, asset, since). Thiếu key -> 503 (get_client). Không bao giờ gửi/huỷ lệnh.
"""

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from ... import dashboard
from .timeutil import check_symbol
from .trading import _sym_asset, _unix, call_alpaca, enum_text, get_client, num

router = APIRouter()
CACHE_SECONDS, LIMIT, DEFAULT_DAYS, MATCH_SECONDS = 10.0, 500, 30, 10
AFTER_MARGIN = timedelta(days=7)  # `after` của Alpaca lọc theo ngày GỬI lệnh; lệnh GTC gửi trước đó vẫn có thể khớp trong khoảng
now = time.monotonic  # test thay được để thử hết hạn cache
WEB_EVENTS = ("web_manual_order", "web_position_order", "web_close_position")
SOURCE_TEXT = {"bracket_tp": "bracket TP", "bracket_sl": "bracket SL", "oco_tp": "OCO TP", "oco_sl": "OCO SL",
               "server": "server SL/TP", "web": "web", "bot": "bot", "other": "khác (dashboard Alpaca)"}


def parse_since(value: Optional[str]) -> datetime:
    """unix giây hoặc ISO -> datetime UTC. Không truyền = 30 ngày gần nhất."""
    if not value:
        return datetime.now(timezone.utc) - timedelta(days=DEFAULT_DAYS)
    try:
        ts = datetime.fromtimestamp(int(value), timezone.utc) if value.isdigit() else datetime.fromisoformat(value)
    except (ValueError, OverflowError, OSError):
        raise HTTPException(400, f"since không hợp lệ: {value!r}") from None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


# ---------- dữ liệu phụ từ DB (chỉ đọc) ----------
def load_context(db_path, symbol: str) -> dict:
    """{"server": {order_id: "sl"|"tp"}, "web": [(unix, mã|None)], "bot": {order_id}}. Không có DB/bảng -> rỗng."""
    ctx = {"server": {}, "web": [], "bot": set()}
    conn = dashboard.connect_readonly(db_path)
    if conn is None:
        return ctx
    try:
        marks = ",".join("?" * len(WEB_EVENTS))
        rows = conn.execute(f"SELECT kind, ts, data FROM events WHERE (kind = 'sltp_hit' AND run_id = 'web-sltp') "
                            f"OR (kind IN ({marks}) AND run_id = 'web-manual') ORDER BY id DESC LIMIT 3000", WEB_EVENTS).fetchall()
        for kind, ts, data in rows:
            try:
                d = json.loads(data or "{}")
                if kind == "sltp_hit":
                    ctx["server"][str(d.get("order_id"))] = "sl" if str(d.get("kind", "")).startswith("sl") else "tp"
                else:  # ts là giờ máy (naive) lúc ghi event
                    sym = _sym_asset(d["symbol"])[0] if d.get("symbol") else None
                    ctx["web"].append((datetime.fromisoformat(ts).astimezone().timestamp(), sym))
            except (ValueError, TypeError, KeyError):
                continue
        ids = conn.execute("SELECT broker_order_id FROM orders WHERE run_id LIKE 'live-%' AND symbol IN (?, ?)",
                           (symbol, symbol.replace("/", ""))).fetchall()
        ctx["bot"] = {str(r[0]) for r in ids if r[0]}
    except sqlite3.Error:
        pass  # DB chưa có bảng: bỏ qua, nguồn rơi về "khác"
    finally:
        conn.close()
    return ctx


# ---------- chuyển lệnh Alpaca -> dòng khớp ----------
def flatten(orders):
    """Lệnh lồng -> (order, order_class, là_chân_đóng). Bracket/OTO: chỉ chân là lệnh đóng; OCO: mẹ (TP) và chân (SL) đều là."""
    seen = set()
    for o in orders:
        cls, legs = enum_text(getattr(o, "order_class", None)) or "", list(getattr(o, "legs", None) or [])
        for item, is_exit in [(o, cls == "oco")] + [(leg, True) for leg in legs]:
            if str(item.id) not in seen:
                seen.add(str(item.id))
                yield item, cls, is_exit


def source_of(oid: str, cls: str, exit_kind: Optional[str], submitted: Optional[float], symbol: str, ctx: dict):
    """-> (nguồn, exit_kind: 'tp'|'sl'|None)."""
    if exit_kind:  # chân bracket/OCO
        return f"{'oco' if cls == 'oco' else 'bracket'}_{exit_kind}", exit_kind
    if oid in ctx["server"]:
        return "server", ctx["server"][oid]
    if submitted is not None and any(abs(t - submitted) <= MATCH_SECONDS and s in (None, symbol) for t, s in ctx["web"]):
        return "web", None
    return ("bot" if oid in ctx["bot"] else "other"), None


def to_row(o, cls: str, is_exit: bool, symbol: str, ctx: dict) -> Optional[dict]:
    qty, price, t = num(getattr(o, "filled_qty", None)) or 0.0, num(getattr(o, "filled_avg_price", None)), _unix(getattr(o, "filled_at", None))
    if qty <= 0 or price is None or t is None:
        return None
    typ = enum_text(getattr(o, "order_type", None) or getattr(o, "type", None)) or ""
    exit_kind = ("tp" if typ == "limit" else "sl") if is_exit else None
    submitted = _unix(getattr(o, "submitted_at", None) or getattr(o, "created_at", None))
    source, exit_kind = source_of(str(o.id), cls, exit_kind, submitted, symbol, ctx)
    return {"id": str(o.id), "time": t, "filled_at": datetime.fromtimestamp(t, timezone.utc).isoformat(),
            "side": enum_text(o.side), "qty": qty, "price": price, "type": typ, "order_class": cls or None,
            "exit_kind": exit_kind, "source": source, "source_text": SOURCE_TEXT[source]}


@router.get("/api/account/fills")
def account_fills(request: Request, symbol: str, asset: str = "stock", since: Optional[str] = None):
    """Lệnh đã khớp của 1 mã (tăng dần theo giờ khớp). since = unix giây/ISO, bỏ trống = 30 ngày gần nhất."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    sym, start = check_symbol(symbol, asset), parse_since(since)
    client = get_client(request)  # thiếu key -> 503 kèm lý do
    cache = getattr(request.app.state, "fills_cache", None)
    if cache is None:
        cache = request.app.state.fills_cache = {}
    key, t_now = (sym, asset, since or ""), now()
    hit = cache.get(key)
    if hit and t_now - hit[0] < CACHE_SECONDS:
        return hit[1]
    for k in [k for k, v in cache.items() if t_now - v[0] >= CACHE_SECONDS]:
        del cache[k]
    req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=[sym], after=start - AFTER_MARGIN, limit=LIMIT, nested=True)
    rows = [o for o in call_alpaca(client.get_orders, filter=req) if _sym_asset(o.symbol)[0] == sym]
    ctx = load_context(request.app.state.db_path, sym)
    fills = [r for r in (to_row(o, c, e, sym, ctx) for o, c, e in flatten(rows)) if r and r["time"] >= int(start.timestamp())]
    fills.sort(key=lambda r: (r["time"], r["id"]))
    out = {"symbol": sym, "asset": asset, "since": int(start.timestamp()), "fills": fills, "truncated": len(rows) >= LIMIT}
    cache[key] = (t_now, out)
    return out
