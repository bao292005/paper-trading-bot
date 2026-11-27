"""Kiểm tra đầu vào cho lệnh paper đặt từ web. Sai bất kỳ điều kiện nào -> HTTPException 4xx, KHÔNG gửi lệnh."""

import math
import re
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from ...ui.core import CONFIRM_WORD
from .security import origin_ok

ORDER_SYMBOL_RE = re.compile(r"[A-Z]{1,5}")  # fullmatch, tối đa 5 ký tự
MAX_QTY = 10000


def guard(request: Request) -> None:
    """Chống trang lạ gọi lệnh: Origin/Host phải là localhost, có X-Requested-With, nội dung JSON.
    (Header tuỳ biến buộc trình duyệt gửi preflight CORS, mà server không cho phép nên trang lạ không gọi được.)"""
    h = request.headers
    if not origin_ok(h.get("origin"), h.get("host")):
        raise HTTPException(403, "nguồn gọi không hợp lệ (chỉ nhận từ localhost)")
    if h.get("x-requested-with") != "paper-web":
        raise HTTPException(403, "thiếu header X-Requested-With: paper-web")
    if not h.get("content-type", "").lower().startswith("application/json"):
        raise HTTPException(415, "Content-Type phải là application/json")


def require_confirm(body: Dict[str, Any]) -> None:
    """Phải gõ đúng 'XAC NHAN' (không strip, không đổi hoa/thường)."""
    if body.get("confirm") != CONFIRM_WORD:
        raise HTTPException(403, f"cần xác nhận: gõ đúng '{CONFIRM_WORD}'")


def _price(body: Dict[str, Any], key: str, required: bool = False) -> Optional[float]:
    v = body.get(key)
    if v is None:
        if required:
            raise HTTPException(422, f"thiếu {key}")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise HTTPException(422, f"{key} phải là số > 0")
    return round(float(v), 2)


def validate_order(body: Dict[str, Any]) -> Dict[str, Any]:
    """Trả lệnh đã chuẩn hoá: symbol, side, type, qty, limit_price, stop_loss, take_profit, ref_price."""
    sym = body.get("symbol")
    if not isinstance(sym, str) or not ORDER_SYMBOL_RE.fullmatch(sym):
        raise HTTPException(422, "mã chỉ gồm chữ HOA A-Z, tối đa 5 ký tự")
    if body.get("side") not in ("buy", "sell"):
        raise HTTPException(422, "side phải là buy hoặc sell")
    if body.get("type") not in ("market", "limit"):
        raise HTTPException(422, "type phải là market hoặc limit")
    qty = body.get("qty")
    if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= MAX_QTY:
        raise HTTPException(422, f"qty phải là số nguyên 1..{MAX_QTY}")

    is_limit = body["type"] == "limit"
    limit_price = _price(body, "limit_price", required=is_limit)
    if not is_limit and body.get("limit_price") is not None:
        raise HTTPException(422, "lệnh market không có limit_price")
    stop_loss, take_profit = _price(body, "stop_loss"), _price(body, "take_profit")

    # Giá tham chiếu để so SL/TP: lệnh limit dùng limit_price; market dùng ref_price (giá gần nhất UI đang thấy)
    ref = limit_price if is_limit else _price(body, "ref_price", required=stop_loss is not None or take_profit is not None)
    buy = body["side"] == "buy"
    if stop_loss is not None and not (stop_loss < ref if buy else stop_loss > ref):
        raise HTTPException(422, "SL phải " + ("< giá" if buy else "> giá") + " vào lệnh")
    if take_profit is not None and not (take_profit > ref if buy else take_profit < ref):
        raise HTTPException(422, "TP phải " + ("> giá" if buy else "< giá") + " vào lệnh")
    return {"symbol": sym, "side": body["side"], "type": body["type"], "qty": qty, "limit_price": limit_price,
            "stop_loss": stop_loss, "take_profit": take_profit, "ref_price": ref}


def _positive_float(body: Dict[str, Any], key: str, required: bool = False) -> Optional[float]:
    v = body.get(key)
    if v is None:
        if required:
            raise HTTPException(422, f"thiếu {key}")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise HTTPException(422, f"{key} phải là số > 0")
    return float(v)


def validate_crypto_web_order(body: Dict[str, Any], min_qty: float = 0.0) -> Dict[str, Any]:
    """Lệnh crypto từ web: qty lẻ > 0, market/limit (stop_limit tuỳ chọn), KHÔNG SL/TP, mã đúng dạng "XXX/USD" trong whitelist.
    Luật cốt lõi dùng lại execution.validate_crypto_order (bot cũng dùng). Trả cùng khung dict như validate_order."""
    from ...execution import validate_crypto_order

    if body.get("side") not in ("buy", "sell"):
        raise HTTPException(422, "side phải là buy hoặc sell")
    otype = body.get("type")
    limit_price = _positive_float(body, "limit_price", required=otype in ("limit", "stop_limit"))
    stop_price = _positive_float(body, "stop_price", required=otype == "stop_limit")
    if otype == "market" and (body.get("limit_price") is not None or body.get("stop_price") is not None):
        raise HTTPException(422, "lệnh market không có limit_price/stop_price")
    ref = _positive_float(body, "ref_price")
    try:
        validate_crypto_order(body.get("symbol"), body.get("qty"), otype, limit_price, stop_price,
                              body.get("stop_loss"), body.get("take_profit"), ref, min_qty)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return {"asset": "crypto", "symbol": body["symbol"], "side": body["side"], "type": otype,
            "qty": float(body["qty"]), "limit_price": limit_price, "stop_price": stop_price,
            "stop_loss": None, "take_profit": None, "ref_price": ref}


POSITION_TYPES = ("market", "limit", "stop")


def validate_levels(body: Dict[str, Any], crypto: bool) -> Dict[str, float]:
    """entry/sl/tp > 0 và CHỈ LONG: SL < entry < TP (sai -> 422). Cổ phiếu làm tròn 2 số lẻ trước khi so."""
    get = _positive_float if crypto else _price
    lv = {k: get(body, k, required=True) for k in ("entry", "sl", "tp")}
    if not lv["sl"] < lv["entry"] < lv["tp"]:
        raise HTTPException(422, "chỉ long: cần SL < giá vào < TP")
    return lv


def stop_limit_price(entry: float, tp: float) -> float:
    """Giá limit cho lệnh vào stop_limit: entry + 0.5% (dễ khớp khi giá vọt), nhưng luôn < TP."""
    return round(max(entry, min(entry * 1.005, tp - 0.01)), 2)


def validate_position(body: Dict[str, Any], min_qty: float = 0.0) -> Dict[str, Any]:
    """Lệnh vào từ box Long Position: {asset, symbol, type, qty, entry, sl, tp}.
    Cổ phiếu: qty nguyên 1..MAX_QTY, market/limit/stop (stop -> stop_limit trên sàn).
    Crypto: chỉ market/limit, qty lẻ, luật chung execution.validate_crypto_order (SL/TP KHÔNG gắn vào lệnh)."""
    from ...execution import validate_crypto_order

    asset, otype = body.get("asset", "stock"), body.get("type")
    if asset not in ("stock", "crypto"):
        raise HTTPException(422, "asset phải là stock hoặc crypto")
    if otype not in POSITION_TYPES:
        raise HTTPException(422, f"type phải là một trong {POSITION_TYPES}")
    crypto = asset == "crypto"
    lv = validate_levels(body, crypto)
    sym, qty = body.get("symbol"), body.get("qty")
    if crypto:
        if otype == "stop":
            raise HTTPException(422, "crypto: chỉ market hoặc limit")
        try:
            validate_crypto_order(sym, qty, otype, lv["entry"] if otype == "limit" else None, None, None, None,
                                  lv["entry"], min_qty)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        qty = float(qty)
    else:
        if not isinstance(sym, str) or not ORDER_SYMBOL_RE.fullmatch(sym):
            raise HTTPException(422, "mã chỉ gồm chữ HOA A-Z, tối đa 5 ký tự")
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= MAX_QTY:
            raise HTTPException(422, f"qty phải là số nguyên 1..{MAX_QTY}")
    return {"asset": asset, "symbol": sym, "side": "buy", "type": otype, "qty": qty, **lv}


def validate_order_id(order_id: str) -> str:
    try:
        return str(uuid.UUID(order_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(422, "order id không hợp lệ") from None
