"""Thống kê phiên luyện tập (dùng lại mt5_report) + lưu vào DB qua storage.Store."""

from types import SimpleNamespace

import pandas as pd

from ..backtest.tester.report import mt5_report
from ..config import asset_profile
from ..contracts import Fill
from .account import EPS, PracticeAccount
from .models import SimTrade, to_ts


def trades_with_open(acc: PracticeAccount) -> list:
    """Trade đã đóng + vị thế còn mở coi như đóng ở giá cuối (trừ phí bán ước tính), như MT5 khi hết test."""
    out = list(acc.trades)
    q = abs(acc.qty)
    if q > EPS and acc.last_price is not None:
        fee = acc.fee_pool + q * acc.last_price * acc.fee_bps / 1e4
        out.append(SimTrade(acc.symbol, q, to_ts(acc.opened), acc.avg, to_ts(acc.cursor),
                            acc.last_price, "end", fee, acc.side))
    return out


def equity_frame(acc: PracticeAccount) -> pd.DataFrame:
    rows = acc.equity or [(acc.cursor, acc.equity_now(), acc.cash, 0.0)]
    idx = pd.DatetimeIndex([to_ts(t) for t, *_ in rows], name="date")
    return pd.DataFrame({"equity": [r[1] for r in rows], "cash": [r[2] for r in rows],
                         "exposure": [r[3] for r in rows]}, index=idx)


def report(acc: PracticeAccount) -> dict:
    """Báo cáo MT5 cho phiên. Ghi chú: equity ghi theo từng bước replay (không theo ngày) nên Sharpe chỉ để tham khảo."""
    res = SimpleNamespace(trades=trades_with_open(acc), equity=equity_frame(acc))
    rep = mt5_report(res, acc.initial, asset_profile(acc.asset)["periods_per_year"])
    rep["period_start"], rep["period_end"] = to_ts(acc.start).isoformat(), to_ts(acc.cursor).isoformat()
    return rep


def summary(acc: PracticeAccount, session_id: str) -> dict:
    return {"session_id": session_id, "symbol": acc.symbol, "asset": acc.asset, "start": acc.start,
            "end": acc.cursor, "bars_seen": acc.bars_seen, "initial": acc.initial,
            "final_equity": acc.equity_now(), "fills": len(acc.fills), "notes": sorted(acc.notes)}


def save_session(acc: PracticeAccount, session_id: str, db_path) -> str:
    """Ghi fills + equity + 1 event 'practice_end' (tóm tắt + báo cáo + bảng lệnh) với run_id practice-<id>."""
    from ..storage import Store

    run_id = f"practice-{session_id}"
    store = Store(db_path, run_id=run_id)
    try:
        for f in acc.fills:
            store.log_fill(Fill(order_id=f["order_id"], symbol=acc.symbol, side=f["side"], qty=f["qty"],
                                price=f["price"], filled_at=to_ts(f["time"]).to_pydatetime(), fee=f["fee"]))
        for t, eq, cash, expo in acc.equity:
            store.log_equity(to_ts(t).to_pydatetime(), eq, cash, expo)
        data = {**summary(acc, session_id), "report": report(acc),
                "trades": [t.to_dict() for t in trades_with_open(acc)],
                "equity": [[t, eq] for t, eq, *_ in acc.equity]}
        store.log_event("practice_end", f"Phiên luyện tập {acc.symbol} ({acc.asset})", data=data)
    finally:
        store.conn.close()
    return run_id
