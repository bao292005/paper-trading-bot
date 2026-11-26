"""API tài khoản paper (CHỈ ĐỌC): /api/account, /api/positions, /api/orders."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


def make_trading_client():
    """TradingClient paper thật. Thiếu key -> RuntimeError (API đổi thành 503)."""
    from alpaca.trading.client import TradingClient

    from ...config import TradingConfig
    try:
        cfg = TradingConfig.from_env()
    except SystemExit as e:  # from_env báo thiếu key bằng SystemExit
        raise RuntimeError(str(e)) from None
    return TradingClient(cfg.api_key, cfg.api_secret, paper=True)  # luôn paper


def get_client(request: Request):
    """Client của app (tạo lười, dùng lại). app.state.trading_client_fn cho phép test đưa client giả vào."""
    state = request.app.state
    if getattr(state, "trading_client", None) is None:
        try:
            state.trading_client = state.trading_client_fn()
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from None
    return state.trading_client


def call_alpaca(fn, *args, **kwargs):
    """Gọi Alpaca; lỗi mạng/API -> 502 với thông báo rõ."""
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Alpaca lỗi: {e}") from None


def num(x) -> Optional[float]:
    return None if x is None else float(x)


def enum_text(x) -> Optional[str]:
    return None if x is None else str(getattr(x, "value", x))


@router.get("/api/account")
def account(request: Request):
    acc = call_alpaca(get_client(request).get_account)
    equity, last = float(acc.equity), num(getattr(acc, "last_equity", None))
    day_pl = equity - last if last is not None else None
    return {"equity": equity, "cash": float(acc.cash), "buying_power": num(getattr(acc, "buying_power", None)),
            "last_equity": last, "day_pl": day_pl,
            "day_pl_pct": day_pl / last * 100 if day_pl is not None and last else None,
            "status": enum_text(getattr(acc, "status", None))}


@router.get("/api/positions")
def positions(request: Request):
    rows = call_alpaca(get_client(request).get_all_positions)
    return {"positions": [{
        "symbol": p.symbol, "qty": float(p.qty), "side": enum_text(getattr(p, "side", None)),
        "avg_entry_price": float(p.avg_entry_price), "current_price": num(getattr(p, "current_price", None)),
        "market_value": num(p.market_value), "unrealized_pl": num(p.unrealized_pl),
        "unrealized_plpc": num(getattr(p, "unrealized_plpc", None))} for p in rows]}


@router.get("/api/orders")
def orders(request: Request):
    """Lệnh đang mở (kể cả chân SL/TP của bracket, trả phẳng không lồng)."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    req = GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=False, limit=100)
    rows = call_alpaca(get_client(request).get_orders, filter=req)
    return {"orders": [{
        "id": str(o.id), "symbol": o.symbol, "side": enum_text(o.side), "qty": num(o.qty),
        "type": enum_text(o.order_type), "status": enum_text(o.status),
        "limit_price": num(getattr(o, "limit_price", None)), "stop_price": num(getattr(o, "stop_price", None)),
        "order_class": enum_text(getattr(o, "order_class", None)),
        "submitted_at": str(o.submitted_at) if getattr(o, "submitted_at", None) else None} for o in rows]}
