"""API markers: /api/markers = tín hiệu (bảng signals) + lệnh khớp (bảng fills) của 1 run, từ DB (chỉ đọc)."""

from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request

from ... import dashboard
from ...data.intraday import TIMEFRAMES
from .timeutil import check_symbol, local_tz, snap_to_bar

router = APIRouter()
LIMIT = 2000
GREEN, RED, GREY, BLUE, ORANGE = "#26a69a", "#ef5350", "#9e9e9e", "#2196f3", "#ff9800"


def _ny_naive(value, asset: str = "stock") -> Optional[pd.Timestamp]:
    """Chuỗi thời gian trong DB -> giờ địa phương của asset (stock: NY, crypto: UTC) không timezone.
    Không có tz thì coi đã là giờ địa phương."""
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    return ts.tz_convert(local_tz(asset)).tz_localize(None) if ts.tzinfo is not None else ts


def _clean(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def signal_marker(row: dict, tf: str, asset: str = "stock") -> Optional[dict]:
    ts = _ny_naive(row["asof"], asset)
    if ts is None:
        return None
    buy, veto = str(row["action"]).upper() == "BUY", bool(row.get("veto"))
    prob = _clean(row.get("prob"))
    return {"kind": "signal", "time": snap_to_bar(ts, tf, asset),
            "position": "belowBar" if buy else "aboveBar", "shape": "arrowUp" if buy else "arrowDown",
            "color": GREY if veto else (GREEN if buy else RED),
            "text": ("B" if buy else "S") + (" veto" if veto else ""),
            "tooltip": {"asof": str(row["asof"]), "strategy": row.get("strategy"), "action": row["action"],
                        "price": _clean(row.get("price")), "confidence": _clean(row.get("confidence")),
                        "prob": prob, "veto": veto, "veto_reason": row.get("veto_reason") or "",
                        "reason": row.get("reason") or "", "regime": row.get("regime") or ""}}


def fill_marker(row: dict, tf: str, asset: str = "stock") -> Optional[dict]:
    ts = _ny_naive(row["filled_at"], asset)
    if ts is None:
        return None
    buy = str(row["side"]).lower().endswith("buy")  # "buy" hoặc "OrderSide.BUY"
    return {"kind": "fill", "time": snap_to_bar(ts, tf, asset),
            "position": "belowBar" if buy else "aboveBar", "shape": "circle",
            "color": BLUE if buy else ORANGE, "text": f"{row['qty']:g}@{row['price']:.2f}",
            "tooltip": {"filled_at": str(row["filled_at"]), "side": "buy" if buy else "sell", "qty": row["qty"],
                        "price": row["price"], "fee": _clean(row.get("fee")), "order_id": row.get("order_id")}}


@router.get("/api/runs")
def runs(request: Request):
    """Các run_id có trong DB (cho ô chọn bot). DB chưa có -> danh sách rỗng."""
    conn = dashboard.connect_readonly(request.app.state.db_path)
    if conn is None:
        return {"runs": []}
    try:
        return {"runs": dashboard.list_runs(conn)}
    finally:
        conn.close()


@router.get("/api/markers")
def markers(request: Request, symbol: str, run_id: str = Query(..., min_length=1, max_length=80),
            tf: str = "1D", asset: str = "stock"):
    """Trả {"signals": [...], "fills": [...]} đã sắp theo time, mỗi mục có position/shape/color/text/tooltip.
    Thời điểm được dồn vào nến chứa nó của khung `tf`. DB chưa có -> danh sách rỗng."""
    symbol = check_symbol(symbol, asset)
    if tf not in TIMEFRAMES:
        raise HTTPException(400, f"tf phải thuộc {list(TIMEFRAMES)}")
    conn = dashboard.connect_readonly(request.app.state.db_path)
    if conn is None:
        return {"signals": [], "fills": []}
    try:
        def rows(table, order):
            cur = conn.execute(f"SELECT * FROM {table} WHERE run_id = ? AND symbol = ? ORDER BY {order} LIMIT ?",
                               (run_id, symbol, LIMIT))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

        sigs, fills = rows("signals", "asof"), rows("fills", "filled_at")
    finally:
        conn.close()
    sig_out = sorted(filter(None, (signal_marker(r, tf, asset) for r in sigs)), key=lambda m: m["time"])
    fill_out = sorted(filter(None, (fill_marker(r, tf, asset) for r in fills)), key=lambda m: m["time"])
    return {"signals": sig_out, "fills": fill_out}
