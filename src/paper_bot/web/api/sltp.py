"""SL/TP cho MỌI vị thế đang mở (kể cả vị thế mở từ form thủ công, chưa có SL/TP).

- Cổ phiếu: SL/TP nằm TRÊN SÀN = lệnh OCO của Alpaca (limit chốt lời + stop cắt lỗ, GTC, qty = số đang nắm,
  chiều ngược vị thế). Sửa = replace từng chân; không replace được -> huỷ cũ rồi đặt lại. Vị thế đã có bracket
  (mở bằng box) thì sửa chân bracket. Chạy được cả khi server tắt.
- Crypto (Alpaca không có OCO/bracket): ghi data/live_state.json; SLTPMonitor trong web server tự canh.
POST/DELETE: guard + 'XAC NHAN' + ghi event (run_id web-manual) TRƯỚC khi gửi.
"""

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ...config import CRYPTO_UNIVERSE
from .order_rules import ORDER_SYMBOL_RE, _positive_float, _price, guard, require_confirm
from .orders import _state_path, log_event
from .trading import _sym_asset, call_alpaca, enum_text, get_client, num

router = APIRouter()
WAIT_TRIES, WAIT_SLEEP = 10, 0.3  # chờ sàn huỷ xong lệnh cũ (nhả qty) trước khi đặt OCO mới


# ---------- kiểm tra ----------
def check_sltp_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """{asset, symbol, sl, tp} -> đã chuẩn hoá (cổ phiếu làm tròn 2 số lẻ). Sai -> 422."""
    asset, sym = body.get("asset", "stock"), body.get("symbol")
    if asset == "crypto":
        if sym not in CRYPTO_UNIVERSE:
            raise HTTPException(422, "mã crypto không nằm trong whitelist")
        get = _positive_float
    elif asset == "stock":
        if not isinstance(sym, str) or not ORDER_SYMBOL_RE.fullmatch(sym):
            raise HTTPException(422, "mã chỉ gồm chữ HOA A-Z, tối đa 5 ký tự")
        get = _price
    else:
        raise HTTPException(422, "asset phải là stock hoặc crypto")
    return {"asset": asset, "symbol": sym, "sl": get(body, "sl", required=True), "tp": get(body, "tp", required=True)}


def check_sides(side: str, sl: float, tp: float, price: Optional[float], allow_cross: bool) -> None:
    """Long: SL < TP (và SL < giá hiện tại < TP); short ngược lại. Sai thứ tự -> 422.
    Mức đã bị giá vượt qua (đặt là khớp ngay) -> 409, trừ khi người dùng xác nhận thêm (allow_cross)."""
    long_ = side == "long"
    if not (sl < tp if long_ else tp < sl):
        raise HTTPException(422, "long: cần SL < TP" if long_ else "short: cần TP < SL")
    if price is None or allow_cross is True:
        return
    if not (sl < price < tp if long_ else tp < price < sl):
        need = "SL < giá hiện tại < TP" if long_ else "TP < giá hiện tại < SL"
        raise HTTPException(409, f"giá hiện tại {price:g}: mức SL/TP đã bị vượt qua (cần {need}), đặt là khớp NGAY. "
                                 "Muốn đặt thật thì xác nhận thêm (allow_cross)")


def find_position(client, symbol: str):
    pos = next((p for p in call_alpaca(client.get_all_positions)
                if _sym_asset(p.symbol)[0] == symbol and float(p.qty) != 0), None)
    if pos is None:
        raise HTTPException(404, f"không có vị thế {symbol}")
    return pos


# ---------- chân SL/TP cổ phiếu đang có trên sàn ----------
EXIT_CLASSES = ("bracket", "oco", "oto")


def stock_exits(rows: List, symbol: str, close_side: str) -> dict:
    """Từ lệnh đang mở (nested): chân SL (stop) / TP (limit) đóng vị thế của mã.
    Trả {"kind": "oco"|"bracket"|None, "sl": (giá, id)|None, "tp": (giá, id)|None, "cancel": [id cần huỷ khi gỡ]}."""
    out = {"kind": None, "sl": None, "tp": None, "cancel": []}
    for o in rows:
        if _sym_asset(o.symbol)[0] != symbol:
            continue
        legs, cls = list(getattr(o, "legs", None) or []), enum_text(getattr(o, "order_class", None)) or ""
        if cls == "oco":  # OCO: mẹ = limit TP, chân = stop SL; huỷ mẹ là huỷ cả cặp
            cands, kind = [o] + legs, "oco"
        elif legs and enum_text(getattr(o, "status", None)) == "filled":  # bracket đã vào lệnh: chân còn mở
            cands, kind = legs, "bracket"
        elif not legs and cls in EXIT_CLASSES:  # chân bracket trả phẳng
            cands, kind = [o], cls
        else:
            continue  # lệnh vào đang chờ / lệnh thường của người dùng: không phải SL/TP
        hit = []
        for x in cands:
            if enum_text(x.side) != close_side:
                continue
            t = enum_text(x.order_type) or ""
            if "stop" in t and out["sl"] is None:
                out["sl"] = (num(x.stop_price), str(x.id))
                hit.append(str(x.id))
            elif t == "limit" and out["tp"] is None:
                out["tp"] = (num(x.limit_price), str(x.id))
                hit.append(str(x.id))
        if hit:
            out["kind"] = out["kind"] or kind
            out["cancel"] += [str(o.id)] if kind == "oco" else hit
    return out


def open_rows(client) -> List:
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    return list(call_alpaca(client.get_orders, filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True,
                                                                     limit=500)))


def build_oco_request(symbol: str, qty: int, side: str, sl: float, tp: float):
    """OCO của Alpaca: LimitOrderRequest(order_class=OCO) gồm chân TP (limit) + chân SL (stop), GTC.
    alpaca-py cho phép bỏ limit_price ở lệnh mẹ khi order_class=OCO (giá TP nằm trong take_profit)."""
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, TakeProfitRequest

    return LimitOrderRequest(symbol=symbol, qty=qty, side=OrderSide(side), time_in_force=TimeInForce.GTC,
                             order_class=OrderClass.OCO, take_profit=TakeProfitRequest(limit_price=tp),
                             stop_loss=StopLossRequest(stop_price=sl))


def _cancel_all(client, ids: List[str]) -> None:
    """Huỷ từng lệnh; huỷ 1 chân có thể kéo chân kia huỷ theo (lỗi 'không huỷ được' thì bỏ qua)."""
    errors = []
    for oid in ids:
        try:
            client.cancel_order_by_id(oid)
        except Exception as e:
            errors.append(str(e))
    if ids and len(errors) == len(ids):
        raise HTTPException(502, f"Alpaca lỗi khi huỷ SL/TP cũ: {errors[0]}")


def _wait_released(client, symbol: str, close_side: str) -> None:
    """Sàn huỷ lệnh không tức thì: chờ tới khi không còn chân SL/TP cũ (nhả qty) mới đặt OCO mới."""
    for _ in range(WAIT_TRIES):
        if stock_exits(open_rows(client), symbol, close_side)["kind"] is None:
            return
        time.sleep(WAIT_SLEEP)


def _set_stock(request: Request, client, o: dict, pos, allow_cross: bool) -> dict:
    from alpaca.trading.requests import ReplaceOrderRequest

    qty = float(pos.qty)
    side, n = ("short" if qty < 0 else "long"), abs(qty)
    if n != int(n):
        raise HTTPException(422, "vị thế cổ phiếu lẻ: Alpaca không nhận OCO cho qty lẻ")
    check_sides(side, o["sl"], o["tp"], num(getattr(pos, "current_price", None)), allow_cross)
    close_side = "buy" if side == "short" else "sell"
    ex = stock_exits(open_rows(client), o["symbol"], close_side)
    log_event(request, "web_sltp_set", f"{o['symbol']} ({side} {n:g}): SL {o['sl']} TP {o['tp']} (sàn)",
              {**o, "side": side, "qty": n, "existing": ex["kind"]})
    if ex["sl"] and ex["tp"]:  # đã có cặp SL/TP (OCO hoặc bracket): replace từng chân
        try:
            sl_id = str(getattr(call_alpaca(client.replace_order_by_id, ex["sl"][1],
                                            ReplaceOrderRequest(stop_price=o["sl"])), "id", ""))
            tp_id = str(getattr(call_alpaca(client.replace_order_by_id, ex["tp"][1],
                                            ReplaceOrderRequest(limit_price=o["tp"])), "id", ""))
            return {"mode": "replace", "kind": ex["kind"], "ids": {"sl": sl_id, "tp": tp_id}}
        except HTTPException:
            ex = stock_exits(open_rows(client), o["symbol"], close_side)  # replace dở: id có thể đã đổi
    mode = "cancel_replace" if ex["cancel"] else "oco_new"
    _cancel_all(client, ex["cancel"])
    if ex["cancel"]:
        _wait_released(client, o["symbol"], close_side)
    res = call_alpaca(client.submit_order, order_data=build_oco_request(o["symbol"], int(n), close_side, o["sl"], o["tp"]))
    return {"mode": mode, "kind": "oco", "id": str(getattr(res, "id", "")), "status": enum_text(getattr(res, "status", None))}


def _set_crypto(request: Request, o: dict, pos, allow_cross: bool) -> dict:
    from ...live import crypto_sltp_entries, set_crypto_sltp, trigger_active

    if float(pos.qty) <= 0:
        raise HTTPException(422, "Alpaca không cho bán khống crypto")
    check_sides("long", o["sl"], o["tp"], num(getattr(pos, "current_price", None)), allow_cross)
    path = _state_path(request)
    if trigger_active(crypto_sltp_entries(path).get(o["symbol"])):
        raise HTTPException(409, f"{o['symbol']}: SL/TP đang kích hoạt (đang bán), chờ xong rồi thử lại")
    log_event(request, "web_sltp_set", f"{o['symbol']}: SL {o['sl']} TP {o['tp']} (server canh)", o)
    set_crypto_sltp(o["symbol"], o["sl"], o["tp"], path=path, created_by="web")
    return {"mode": "live_state", "kind": "server"}


@router.post("/api/sltp", dependencies=[Depends(guard)])
def set_sltp(request: Request, body: Dict[str, Any] = Body(...)):
    """Gắn hoặc sửa SL/TP cho vị thế đang mở: cổ phiếu -> OCO/replace trên sàn; crypto -> live_state."""
    require_confirm(body)
    o = check_sltp_body(body)
    client = get_client(request)
    pos = find_position(client, o["symbol"])
    allow = body.get("allow_cross") is True
    done = _set_crypto(request, o, pos, allow) if o["asset"] == "crypto" else _set_stock(request, client, o, pos, allow)
    return {"ok": True, "symbol": o["symbol"], "sl": o["sl"], "tp": o["tp"], **done}


@router.delete("/api/sltp", dependencies=[Depends(guard)])
def remove_sltp(request: Request, body: Dict[str, Any] = Body(...)):
    """Gỡ SL/TP: cổ phiếu huỷ OCO (hoặc chân bracket); crypto xoá entry trong live_state."""
    require_confirm(body)
    asset, sym = body.get("asset", "stock"), body.get("symbol")
    if asset == "crypto":
        from ...live import crypto_sltp_entries, remove_crypto_sltp, trigger_active

        if sym not in CRYPTO_UNIVERSE:
            raise HTTPException(422, "mã crypto không nằm trong whitelist")
        path = _state_path(request)
        cur = crypto_sltp_entries(path).get(sym)
        if cur is None:
            raise HTTPException(404, f"{sym} không có SL/TP")
        if trigger_active(cur):
            raise HTTPException(409, f"{sym}: SL/TP đang kích hoạt (đang bán)")
        log_event(request, "web_sltp_remove", f"gỡ SL/TP {sym} (server canh)", {"asset": asset, "symbol": sym})
        remove_crypto_sltp(sym, path)
        return {"ok": True, "symbol": sym, "removed": "live_state"}
    if asset != "stock" or not isinstance(sym, str) or not ORDER_SYMBOL_RE.fullmatch(sym):
        raise HTTPException(422, "mã/asset không hợp lệ")
    client = get_client(request)
    pos = find_position(client, sym)
    ex = stock_exits(open_rows(client), sym, "buy" if float(pos.qty) < 0 else "sell")
    if not ex["cancel"]:
        raise HTTPException(404, f"{sym} không có SL/TP trên sàn")
    log_event(request, "web_sltp_remove", f"gỡ SL/TP {sym}: huỷ {ex['cancel']}", {"symbol": sym, **ex})
    _cancel_all(client, ex["cancel"])
    return {"ok": True, "symbol": sym, "removed": ex["cancel"]}


def _source(entry: dict) -> str:
    return "server" if entry.get("created_by") == "web" or entry.get("source") == "web" else "bot"


@router.get("/api/sltp")
def sltp_levels(request: Request):
    """SL/TP của mọi vị thế đang mở (CHỈ ĐỌC), cho cột SL / TP / nguồn ở panel Vị thế:
    "sàn" = OCO/bracket của Alpaca, "server" = web server tự canh, "bot" = LiveBot tự quản."""
    from ...live import crypto_sltp_entries

    client = get_client(request)
    positions = [p for p in call_alpaca(client.get_all_positions) if float(p.qty) != 0]
    rows = open_rows(client) if any(_sym_asset(p.symbol)[1] == "stock" for p in positions) else []
    entries = crypto_sltp_entries(_state_path(request))
    out = {}
    for p in positions:
        sym, kind = _sym_asset(p.symbol)
        if kind == "stock":
            ex = stock_exits(rows, sym, "buy" if float(p.qty) < 0 else "sell")
            if ex["kind"]:
                out[sym] = {"sl": ex["sl"] and ex["sl"][0], "tp": ex["tp"] and ex["tp"][0], "source": "sàn",
                            "kind": ex["kind"], "ids": {k: ex[k][1] for k in ("sl", "tp") if ex[k]}}
        elif sym in entries:
            e = entries[sym]
            out[sym] = {"sl": e.get("stop_loss"), "tp": e.get("take_profit"), "source": _source(e),
                        "triggering": bool(e.get("triggering"))}
    return {"levels": out}


@router.get("/api/sltp/status")
def sltp_status(request: Request):
    """Trạng thái SLTPMonitor: entry đang canh, giá gần nhất, lần kiểm cuối, lỗi gần nhất."""
    return {**request.app.state.sltp_monitor.status(), "now": time.time()}


@router.post("/api/sltp/monitor", dependencies=[Depends(guard)])
def sltp_monitor_switch(request: Request, body: Dict[str, Any] = Body(...)):
    """Bật/tắt server canh SL/TP crypto (lưu trong live_state.json, khởi động lại vẫn giữ)."""
    require_confirm(body)
    on = body.get("enabled")
    if not isinstance(on, bool):
        raise HTTPException(422, "enabled phải là true/false")
    log_event(request, "web_sltp_monitor", f"server canh SL/TP crypto: {'BẬT' if on else 'TẮT'}", {"enabled": on})
    request.app.state.sltp_monitor.set_enabled(on)
    return {"ok": True, "enabled": on}
