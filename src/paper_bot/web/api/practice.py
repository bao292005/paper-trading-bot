"""Tài khoản luyện tập (bar replay). Lệnh ở đây CHỈ khớp giả lập trong bộ nhớ — KHÔNG BAO GIỜ gửi lên Alpaca.

Mọi POST/DELETE/PATCH qua guard (Origin localhost + X-Requested-With + JSON); không cần 'XAC NHAN'.
Khớp lệnh bằng nến 1m nằm giữa con trỏ cũ và `to` (không bao giờ dùng nến kết thúc sau `to`).
Không có nến 1m -> dùng nến ngày (ghi chú trong `notes`). Kết thúc phiên -> lưu DB run_id practice-<id>.
"""

import json
import math
import re
import uuid

import numpy as np
import pandas as pd
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ... import dashboard
from ...sim import PracticeAccount
from ...sim.stats import report, save_session, trades_with_open
from .market import load_df
from .order_rules import guard
from .replay import day_close, day_open, minute_rows, now_unix, to_local, to_unix
from .timeutil import check_symbol

router = APIRouter()
SID_RE = re.compile(r"[a-f0-9]{12}")
MAX_SESSIONS = 20
FALLBACK_NOTE = "Không có nến 1m: khớp lệnh theo nến ngày (kém chính xác)"


def clean(x):
    """Bỏ NaN/inf (JSON không nhận) và kiểu numpy."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(x) else None
    return x


def sessions(request: Request) -> dict:
    st = request.app.state
    if not hasattr(st, "practice"):
        st.practice = {}
    return st.practice


def get_session(request: Request, sid: str) -> dict:
    s = sessions(request).get(sid) if SID_RE.fullmatch(sid or "") else None
    if s is None:
        raise HTTPException(404, "không có phiên luyện tập này (server khởi động lại thì phiên cũ mất)")
    return s


def open_session(request, sid) -> PracticeAccount:
    s = get_session(request, sid)
    if s["ended"]:
        raise HTTPException(409, "phiên đã kết thúc")
    return s["acc"]


def bars_between(request, acc: PracticeAccount, to: int) -> list:
    """Nến 1m bắt đầu trong [con trỏ, to). Không có -> nến ngày đã đóng trong khoảng đó."""
    a, b = to_local(acc.cursor, acc.asset), to_local(to, acc.asset)
    m = minute_rows(request, acc.symbol, acc.asset, a, b)
    out = [{"start": to_unix(t, acc.asset), "end": to_unix(t, acc.asset) + 60, "open": float(r.open),
            "high": float(r.high), "low": float(r.low), "close": float(r.close)} for t, r in m.iterrows()]
    if out or to <= acc.cursor:
        return out
    daily = load_df(request, acc.symbol, "1D", a.normalize(), b, acc.asset)
    for d, r in daily.iterrows():
        s, e = to_unix(day_open(d, acc.asset), acc.asset), to_unix(day_close(d, acc.asset), acc.asset)
        if s >= acc.cursor and e <= to:
            out.append({"start": s, "end": e, "open": float(r.open), "high": float(r.high),
                        "low": float(r.low), "close": float(r.close)})
    if out:
        acc.notes.add(FALLBACK_NOTE)
    return out


def last_close(request, symbol, asset, cursor) -> float:
    t = to_local(cursor, asset)
    m = minute_rows(request, symbol, asset, t - pd.Timedelta(days=5), t)
    if not m.empty:
        return float(m["close"].iloc[-1])
    d = load_df(request, symbol, "1D", t - pd.Timedelta(days=15), t - pd.Timedelta(days=1), asset)
    if d.empty:
        raise HTTPException(404, "không có dữ liệu trước điểm bắt đầu")
    return float(d["close"].iloc[-1])


def state(sid, s) -> dict:
    return clean({"id": sid, "ended": s["ended"], "run_id": s.get("run_id"), **s["acc"].snapshot()})


def num(body, key, default=None):
    v = body.get(key, default)
    if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
        raise HTTPException(422, f"{key} phải là số")
    return v


@router.post("/api/practice/sessions", dependencies=[Depends(guard)])
def create_session(request: Request, body: dict = Body(...)):
    asset = body.get("asset", "stock")
    symbol = check_symbol(body.get("symbol"), asset)
    cursor = num(body, "cursor")
    if not isinstance(cursor, int) or cursor <= 0:
        raise HTTPException(422, "cursor phải là unix giây")
    cursor = min(cursor, now_unix()) // 60 * 60
    cash = num(body, "cash", 100000)
    if not 100 <= cash <= 1e9:
        raise HTTPException(422, "cash phải trong 100..1e9")
    acc = PracticeAccount(symbol, asset, cash=cash, cursor=cursor, last_price=last_close(request, symbol, asset, cursor))
    store = sessions(request)
    for old in [k for k, v in store.items() if v["ended"]][: max(0, len(store) - MAX_SESSIONS + 1)]:
        del store[old]  # dọn phiên đã kết thúc khi quá nhiều
    if len(store) >= MAX_SESSIONS:
        raise HTTPException(429, f"tối đa {MAX_SESSIONS} phiên đang chạy")
    sid = uuid.uuid4().hex[:12]
    store[sid] = {"acc": acc, "ended": False}
    return state(sid, store[sid])


@router.get("/api/practice/sessions/{sid}")
def read_session(request: Request, sid: str):
    return state(sid, get_session(request, sid))


@router.post("/api/practice/{sid}/advance", dependencies=[Depends(guard)])
def advance(request: Request, sid: str, body: dict = Body(...)):
    acc = open_session(request, sid)
    to, steps = num(body, "to"), num(body, "steps", 1)
    if not isinstance(to, int) or not isinstance(steps, int) or steps < 0:
        raise HTTPException(422, "to/steps phải là số nguyên")
    to = min(to, now_unix()) // 60 * 60
    if to < acc.cursor:
        raise HTTPException(422, "không lùi con trỏ được (chọn lại điểm bắt đầu = phiên mới)")
    new = acc.advance(bars_between(request, acc, to), to, steps)
    return {**state(sid, get_session(request, sid)), "new_fills": clean(new)}


def _sim(fn, *args):
    """Lỗi nghiệp vụ của tài khoản giả lập -> 422 (lệnh sai) / 404 (không có lệnh chờ)."""
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(404, "không có lệnh chờ này") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.post("/api/practice/{sid}/orders", dependencies=[Depends(guard)])
def place(request: Request, sid: str, body: dict = Body(...)):
    acc = open_session(request, sid)
    o = _sim(acc.place_order, body.get("side"), body.get("type", "market"), body.get("qty"),
             body.get("price"), body.get("sl"), body.get("tp"))
    return {**state(sid, get_session(request, sid)), "order": clean(o.to_dict())}


@router.delete("/api/practice/{sid}/orders/{oid}", dependencies=[Depends(guard)])
def cancel(request: Request, sid: str, oid: str):
    acc = open_session(request, sid)
    _sim(acc.cancel_order, oid)
    return state(sid, get_session(request, sid))


@router.patch("/api/practice/{sid}/orders/{oid}", dependencies=[Depends(guard)])
def modify(request: Request, sid: str, oid: str, body: dict = Body(...)):
    acc = open_session(request, sid)
    changes = {k: body[k] for k in ("price", "sl", "tp") if k in body}
    _sim(acc.modify_order, oid, changes)
    return state(sid, get_session(request, sid))


@router.post("/api/practice/{sid}/close", dependencies=[Depends(guard)])
def close(request: Request, sid: str):
    acc = open_session(request, sid)
    _sim(acc.close_position)
    return state(sid, get_session(request, sid))


def stats_of(acc: PracticeAccount) -> dict:
    return clean({"report": report(acc), "equity": [{"time": t, "value": eq} for t, eq, *_ in acc.equity],
                  "trades": [t.to_dict() for t in trades_with_open(acc)], "fills": acc.fills,
                  "notes": sorted(acc.notes)})


@router.get("/api/practice/{sid}/stats")
def stats(request: Request, sid: str):
    return stats_of(get_session(request, sid)["acc"])


@router.post("/api/practice/{sid}/end", dependencies=[Depends(guard)])
def end(request: Request, sid: str):
    """Kết thúc phiên: lưu DB (fills + equity + event practice_end). Gọi lại lần 2 -> trả kết quả cũ."""
    s = get_session(request, sid)
    if not s["ended"]:
        s["run_id"] = save_session(s["acc"], sid, request.app.state.db_path)
        s["ended"] = True
    return {**state(sid, s), **stats_of(s["acc"])}


def _ended_events(request, run_id=None) -> list:
    conn = dashboard.connect_readonly(request.app.state.db_path)
    if conn is None:
        return []
    try:
        q = "SELECT run_id, ts, data FROM events WHERE kind = 'practice_end'"
        rows = conn.execute(q + (" AND run_id = ?" if run_id else "") + " ORDER BY id DESC LIMIT 200",
                            (run_id,) if run_id else ()).fetchall()
    finally:
        conn.close()
    return [(r, ts, json.loads(d or "{}")) for r, ts, d in rows]


@router.get("/api/practice/history")
def history(request: Request):
    """Các phiên đã kết thúc (đọc từ DB) để xem lại."""
    out = []
    for run_id, ts, d in _ended_events(request):
        rep = d.get("report") or {}
        out.append({"run_id": run_id, "saved_at": ts, "symbol": d.get("symbol"), "asset": d.get("asset"),
                    "start": d.get("start"), "end": d.get("end"), "bars_seen": d.get("bars_seen"),
                    "final_equity": d.get("final_equity"), "initial": d.get("initial"),
                    "net_profit": rep.get("total_net_profit"), "trades": rep.get("total_trades")})
    return clean({"sessions": out})


@router.get("/api/practice/history/{run_id}")
def history_one(request: Request, run_id: str):
    if not re.fullmatch(r"practice-[a-f0-9]{12}", run_id):
        raise HTTPException(422, "run_id không hợp lệ")
    rows = _ended_events(request, run_id)
    if not rows:
        raise HTTPException(404, "không có phiên này trong DB")
    _, ts, d = rows[0]
    eq = [{"time": t, "value": v} for t, v in d.get("equity", [])]
    return clean({**d, "saved_at": ts, "equity": eq, "run_id": run_id})
