"""Ghi (có kiểm soát): POST /api/orders, DELETE /api/orders/{id}, POST /api/bot/run.

Mọi endpoint ở đây: qua guard (Origin/Host localhost + X-Requested-With + JSON), phải có confirm == 'XAC NHAN',
chỉ dùng TradingClient paper, và ghi event vào DB (run_id 'web-manual') TRƯỚC khi gửi.
"""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ...ui.core import build_once_command, run_command
from .order_rules import (guard, require_confirm, stop_limit_price, validate_crypto_web_order, validate_levels,
                          validate_order, validate_order_id, validate_position)
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


# ---------- Box Long Position (chỉ long, GTC) ----------
def build_bracket_request(o: Dict[str, Any]):
    """Cổ phiếu: bracket GTC (vào market/limit/stop_limit + TP limit + SL stop)."""
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import (LimitOrderRequest, MarketOrderRequest, StopLimitOrderRequest, StopLossRequest,
                                         TakeProfitRequest)

    common = dict(symbol=o["symbol"], qty=o["qty"], side=OrderSide.BUY, time_in_force=TimeInForce.GTC,
                  order_class=OrderClass.BRACKET, take_profit=TakeProfitRequest(limit_price=o["tp"]),
                  stop_loss=StopLossRequest(stop_price=o["sl"]))
    if o["type"] == "limit":
        return LimitOrderRequest(limit_price=o["entry"], **common)
    if o["type"] == "stop":
        return StopLimitOrderRequest(stop_price=o["entry"], limit_price=stop_limit_price(o["entry"], o["tp"]), **common)
    return MarketOrderRequest(**common)


def _state_path(request: Request):
    from ...live import STATE_PATH
    return request.app.state.live_state_path or STATE_PATH


def _submit_position(request: Request, client, o: Dict[str, Any]) -> dict:
    """Gửi lệnh vào. Crypto: lệnh thường (không bracket) rồi cất SL/TP vào live_state (pending) cho bot tự quản."""
    if o["asset"] == "stock":
        res = call_alpaca(client.submit_order, order_data=build_bracket_request(o))
        return {"id": str(getattr(res, "id", "")), "status": enum_text(getattr(res, "status", None))}
    from ...live import set_crypto_sltp

    req = build_crypto_request({"symbol": o["symbol"], "qty": o["qty"], "side": "buy", "type": o["type"],
                                "limit_price": o["entry"] if o["type"] == "limit" else None})
    res = call_alpaca(client.submit_order, order_data=req)
    oid = str(getattr(res, "id", ""))
    set_crypto_sltp(o["symbol"], o["sl"], o["tp"], order_id=oid or "unknown", path=_state_path(request))
    return {"id": oid, "status": enum_text(getattr(res, "status", None)), "sltp": "live_state"}


def _position_order(request: Request, body: Dict[str, Any]):
    require_confirm(body)
    client = get_client(request)
    min_qty = _crypto_min_qty(client, body.get("symbol", "")) if body.get("asset") == "crypto" else 0.0
    return client, validate_position(body, min_qty)


@router.post("/api/position", dependencies=[Depends(guard)])
def place_position(request: Request, body: Dict[str, Any] = Body(...)):
    """Đặt lệnh LONG từ box: cổ phiếu bracket GTC; crypto market/limit GTC + SL/TP vào live_state."""
    client, o = _position_order(request, body)
    log_event(request, "web_position_order",
              f"long {o['qty']} {o['symbol']} {o['type']} @ {o['entry']} SL {o['sl']} TP {o['tp']}", o)
    return {"ok": True, "symbol": o["symbol"], **_submit_position(request, client, o)}


@router.post("/api/position/repost", dependencies=[Depends(guard)])
def repost_position(request: Request, body: Dict[str, Any] = Body(...)):
    """Sửa lệnh mua đang chờ (giá vào/SL/TP): Alpaca không sửa được chân đang 'held' -> huỷ lệnh cũ + đặt lại."""
    client, o = _position_order(request, body)
    old = validate_order_id(str(body.get("old_order_id", "")))
    log_event(request, "web_position_repost", f"huỷ {old} + đặt lại long {o['qty']} {o['symbol']} @ {o['entry']}",
              {**o, "old_order_id": old})
    call_alpaca(client.cancel_order_by_id, old)
    return {"ok": True, "symbol": o["symbol"], "old_id": old, **_submit_position(request, client, o)}


@router.post("/api/position/legs", dependencies=[Depends(guard)])
def edit_position_legs(request: Request, body: Dict[str, Any] = Body(...)):
    """Sửa SL/TP của vị thế: cổ phiếu = replace chân bracket (SL: stop_price, TP: limit_price);
    crypto = ghi live_state (sltp; còn chờ khớp thì pending)."""
    from alpaca.trading.requests import ReplaceOrderRequest

    require_confirm(body)
    crypto = body.get("asset", "stock") == "crypto"
    lv = validate_levels(body, crypto)
    sym = body.get("symbol")
    if crypto:
        from ...config import CRYPTO_UNIVERSE
        from ...live import get_crypto_sltp, set_crypto_sltp

        if sym not in CRYPTO_UNIVERSE:
            raise HTTPException(422, "mã crypto không nằm trong whitelist")
        path = _state_path(request)
        cur = get_crypto_sltp(sym, path)
        oid = (cur["pending"] or {}).get("order_id") if cur["pending"] and not cur["sltp"] else None
        log_event(request, "web_position_sltp", f"{sym}: SL {lv['sl']} TP {lv['tp']} (live_state)", {"symbol": sym, **lv})
        return {"ok": True, "symbol": sym, "section": set_crypto_sltp(sym, lv["sl"], lv["tp"], order_id=oid, path=path)}
    legs = {k: validate_order_id(str(body[k])) for k in ("sl_id", "tp_id") if body.get(k)}
    if not legs:
        raise HTTPException(422, "thiếu sl_id/tp_id (chân SL/TP cần sửa)")
    client = get_client(request)
    log_event(request, "web_position_legs", f"{sym}: sửa chân SL {lv['sl']} TP {lv['tp']}", {"symbol": sym, **lv, **legs})
    done = {}
    if "sl_id" in legs:
        done["sl"] = str(getattr(call_alpaca(client.replace_order_by_id, legs["sl_id"],
                                             ReplaceOrderRequest(stop_price=lv["sl"])), "id", ""))
    if "tp_id" in legs:
        done["tp"] = str(getattr(call_alpaca(client.replace_order_by_id, legs["tp_id"],
                                             ReplaceOrderRequest(limit_price=lv["tp"])), "id", ""))
    return {"ok": True, "symbol": sym, "replaced": done}


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
