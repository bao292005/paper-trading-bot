"""Ghi (có kiểm soát): POST /api/orders, DELETE /api/orders/{id}, POST /api/bot/run.

Mọi endpoint ở đây: qua guard (Origin/Host localhost + X-Requested-With + JSON), phải có confirm == 'XAC NHAN',
chỉ dùng TradingClient paper, và ghi event vào DB (run_id 'web-manual') TRƯỚC khi gửi.
"""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ...ui.core import build_once_command, run_command
from .order_rules import guard, require_confirm, validate_crypto_web_order, validate_order, validate_order_id
from .trading import call_alpaca, enum_text, get_client

router = APIRouter()
MANUAL_RUN_ID = "web-manual"
OUTPUT_LIMIT = 20000  # ký tự stdout/stderr trả về trình duyệt


def log_event(request: Request, kind: str, message: str, data: dict) -> None:
    """Ghi event trước khi gửi. Ghi không được thì HỦY việc gửi (không có dấu vết thì không làm)."""
    from ...storage import Store

    try:
        Store(path=request.app.state.db_path, run_id=MANUAL_RUN_ID).log_event(kind, message, level="WARNING", data=data)
    except Exception as e:
        raise HTTPException(500, f"không ghi được event vào DB, lệnh KHÔNG được gửi: {e}") from None


def build_crypto_request(o: Dict[str, Any]):
    """Lệnh crypto đã kiểm tra -> request alpaca-py: GTC, qty lẻ, KHÔNG bracket (SL/TP do bot tự quản)."""
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, StopLimitOrderRequest

    common = dict(symbol=o["symbol"], qty=o["qty"], side=OrderSide(o["side"]), time_in_force=TimeInForce.GTC)
    if o["type"] == "stop_limit":
        return StopLimitOrderRequest(limit_price=o["limit_price"], stop_price=o["stop_price"], **common)
    if o["type"] == "limit":
        return LimitOrderRequest(limit_price=o["limit_price"], **common)
    return MarketOrderRequest(**common)


def _crypto_min_qty(client, symbol: str) -> float:
    """Lượng tối thiểu của sàn (Assets API). Không hỏi được thì 0 (sàn sẽ tự từ chối nếu quá nhỏ)."""
    try:
        return float(getattr(client.get_asset(symbol), "min_order_size", 0) or 0)
    except Exception:
        return 0.0


def build_request(o: Dict[str, Any]):
    """Lệnh đã kiểm tra -> request của alpaca-py (DAY; có SL/TP thì bracket, chỉ 1 chân thì OTO)."""
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, StopLossRequest, TakeProfitRequest

    extra: Dict[str, Any] = {}
    if o["take_profit"] is not None:
        extra["take_profit"] = TakeProfitRequest(limit_price=o["take_profit"])
    if o["stop_loss"] is not None:
        extra["stop_loss"] = StopLossRequest(stop_price=o["stop_loss"])
    if extra:
        extra["order_class"] = OrderClass.BRACKET if len(extra) == 2 else OrderClass.OTO
    common = dict(symbol=o["symbol"], qty=o["qty"], side=OrderSide(o["side"]), time_in_force=TimeInForce.DAY, **extra)
    if o["type"] == "limit":
        return LimitOrderRequest(limit_price=o["limit_price"], **common)
    return MarketOrderRequest(**common)


@router.post("/api/orders", dependencies=[Depends(guard)])
def place_order(request: Request, body: Dict[str, Any] = Body(...)):
    require_confirm(body)
    if body.get("asset", "stock") == "crypto":
        client = get_client(request)
        order = validate_crypto_web_order(body, _crypto_min_qty(client, body.get("symbol", "")))
        req = build_crypto_request(order)
    elif body.get("asset", "stock") == "stock":
        order = validate_order(body)
        client = get_client(request)  # thiếu key -> 503 (trước khi ghi event/gửi)
        req = build_request(order)
    else:
        raise HTTPException(422, "asset phải là stock hoặc crypto")
    log_event(request, "web_manual_order", f"{order['side']} {order['qty']} {order['symbol']} ({order['type']})", order)
    res = call_alpaca(client.submit_order, order_data=req)
    return {"ok": True, "id": str(getattr(res, "id", "")), "status": enum_text(getattr(res, "status", None)),
            "symbol": order["symbol"]}


@router.delete("/api/orders/{order_id}", dependencies=[Depends(guard)])
def cancel_order(order_id: str, request: Request, body: Dict[str, Any] = Body(...)):
    require_confirm(body)
    oid = validate_order_id(order_id)
    client = get_client(request)
    log_event(request, "web_cancel_order", f"huỷ lệnh {oid}", {"order_id": oid})
    call_alpaca(client.cancel_order_by_id, oid)
    return {"ok": True, "id": oid}


@router.post("/api/bot/run", dependencies=[Depends(guard)])
def run_bot_once(request: Request, body: Dict[str, Any] = Body(...)):
    """Chạy `paper-bot once`. Mặc định dry-run; chạy THẬT (gửi lệnh paper) khi dry_run=false + confirm đúng.
    Toàn bộ chốt chặn dùng lại của ui.core (build_once_command + run_command), không viết lại."""
    dry = body.get("dry_run", True)
    if not isinstance(dry, bool):
        raise HTTPException(422, "dry_run phải là true/false")
    if not dry:
        require_confirm(body)
    try:
        cmd = build_once_command(body.get("symbols"), "full", dry_run=dry, ack=not dry,
                                 confirm_text=body.get("confirm", "") if not dry else "",
                                 asset=body.get("asset", "stock"))
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    if not dry:
        log_event(request, "web_run_bot", "chạy bot 1 vòng THẬT từ web", {"cmd": cmd})
    try:
        res = run_command(cmd, allow_real=not dry)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    return {"dry_run": dry, "returncode": res["returncode"], "timed_out": res["timed_out"],
            "stdout": res["stdout"][-OUTPUT_LIMIT:], "stderr": res["stderr"][-OUTPUT_LIMIT:]}
