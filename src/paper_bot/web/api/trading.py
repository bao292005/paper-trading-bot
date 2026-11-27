"""API tài khoản paper (CHỈ ĐỌC): /api/account, /api/positions, /api/orders."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from ...config import CRYPTO_UNIVERSE
from ...execution import normalize_crypto_symbol
from .timeutil import check_asset

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


def _sym_asset(symbol: str):
    """Mã của Alpaca ("BTCUSD" hoặc "BTC/USD") -> (mã chuẩn, "crypto"/"stock")."""
    s = normalize_crypto_symbol(symbol)
    return s, ("crypto" if s in CRYPTO_UNIVERSE else "stock")


def _keep(asset: Optional[str], row_asset: str) -> bool:
    return asset is None or asset == row_asset  # không truyền asset = trả tất cả (như trước)


@router.get("/api/positions")
def positions(request: Request, asset: Optional[str] = None):
    """Vị thế (qty lẻ với crypto). ?asset=stock|crypto để lọc; mã crypto luôn chuẩn hoá về "BTC/USD"."""
    if asset is not None:
        check_asset(asset)
    rows = call_alpaca(get_client(request).get_all_positions)
    out = []
    for p in rows:
        sym, kind = _sym_asset(p.symbol)
        if _keep(asset, kind):
            row = {
                "symbol": sym, "qty": float(p.qty), "side": enum_text(getattr(p, "side", None)),
                "avg_entry_price": float(p.avg_entry_price), "current_price": num(getattr(p, "current_price", None)),
                "market_value": num(p.market_value), "unrealized_pl": num(p.unrealized_pl),
                "unrealized_plpc": num(getattr(p, "unrealized_plpc", None))}
            out.append({**row, "asset": "crypto"} if kind == "crypto" else row)  # dòng cổ phiếu giữ nguyên như cũ
    return {"positions": out}


def _unix(dt) -> Optional[int]:
    try:
        return int(dt.timestamp()) if dt is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _entry_price(o) -> Optional[float]:
    """Giá vào của lệnh mua chờ: limit -> limit_price; stop/stop_limit -> stop_price; market -> None."""
    t = enum_text(o.order_type)
    return num(getattr(o, "limit_price", None)) if t == "limit" else num(getattr(o, "stop_price", None)) if "stop" in t else None


def _exit_legs(orders_, close_side: str = "sell") -> dict:
    """Lệnh đóng (long: bán, short: mua): chân stop (SL) và chân limit (TP) đầu tiên -> {"sl": (giá, id, t), "tp": (...)}."""
    out = {}
    for o in orders_:
        if enum_text(o.side) != close_side:
            continue
        t = enum_text(o.order_type)
        if "stop" in t and "sl" not in out:
            out["sl"] = (num(o.stop_price), str(o.id), _unix(getattr(o, "submitted_at", None)))
        elif t == "limit" and "tp" not in out:
            out["tp"] = (num(o.limit_price), str(o.id), _unix(getattr(o, "submitted_at", None)))
    return out


def _crypto_inc(client, symbol: str) -> dict:
    inc = {"price": 0.01, "qty": 0.0001}
    try:
        a = client.get_asset(symbol)
        inc = {"price": float(getattr(a, "price_increment", 0) or 0.01), "qty": float(getattr(a, "min_trade_increment", 0) or 0.0001)}
    except Exception:
        pass
    return inc


@router.get("/api/position/boxes")
def position_boxes(request: Request, symbol: str, asset: str = "stock"):
    """Box Long/Short Position của 1 mã (CHỈ ĐỌC): lệnh vào chờ có SL/TP (pending) + vị thế có SL/TP (position).
    Cổ phiếu: SL/TP = chân bracket trên sàn (bracket mua = long, bracket bán = short; vị thế qty < 0 = short).
    Crypto (chỉ long): SL/TP lấy từ data/live_state.json (bot tự quản)."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    check_asset(asset)
    client = get_client(request)
    req = GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True, limit=100)
    rows = [o for o in call_alpaca(client.get_orders, filter=req) if _sym_asset(o.symbol)[0] == symbol]
    pos = next((p for p in call_alpaca(client.get_all_positions)
                if _sym_asset(p.symbol)[0] == symbol and float(p.qty) != 0), None)
    boxes, crypto = [], asset == "crypto"
    saved = {}
    if crypto:
        from ...live import STATE_PATH, get_crypto_sltp
        saved = get_crypto_sltp(symbol, request.app.state.live_state_path or STATE_PATH)
    exits = []  # lệnh đang mở không phải lệnh vào chờ (= SL/TP của vị thế)
    for o in rows:
        legs, side = list(getattr(o, "legs", None) or []), enum_text(o.side)
        if enum_text(getattr(o, "order_class", None)) == "oco":  # OCO gắn cho vị thế (/api/sltp): mẹ = TP, chân = SL
            exits.extend([o] + legs)
            continue
        if enum_text(getattr(o, "status", None)) == "filled":  # mẹ đã khớp mà vẫn trả kèm chân đang mở
            exits.extend(legs)
            continue
        # lệnh vào: cổ phiếu = mẹ bracket (có chân); crypto = lệnh mua (SL/TP trong live_state)
        if (crypto and side != "buy") or (not crypto and not legs):
            exits.append(o)
            continue
        short = side == "sell"
        entry, lv = _entry_price(o), _exit_legs(legs, "buy" if short else "sell")
        sl, tp = (lv["sl"][0], lv["tp"][0]) if "sl" in lv and "tp" in lv else (None, None)
        p = saved.get("pending") or {}
        if crypto and p.get("order_id") == str(o.id):
            sl, tp = p.get("stop_loss"), p.get("take_profit")
        if entry is None or sl is None or tp is None:
            continue
        boxes.append({"key": "rl-" + str(o.id), "kind": "pending", "side": "short" if short else "long",
                      "entry": entry, "sl": sl, "tp": tp,
                      "qty": num(o.qty), "type": "limit" if enum_text(o.order_type) == "limit" else "stop",
                      "t0": _unix(getattr(o, "submitted_at", None)), "ids": {"entry": str(o.id)}})
    if pos is not None:
        pshort = float(pos.qty) < 0
        lv, s = _exit_legs(exits, "buy" if pshort else "sell"), saved.get("sltp") or {}
        if crypto and s.get("stop_loss") and s.get("take_profit"):
            sl, tp, ids, t0 = s["stop_loss"], s["take_profit"], {}, None
        elif not crypto and "sl" in lv and "tp" in lv:
            sl, tp, t0 = lv["sl"][0], lv["tp"][0], lv["sl"][2]
            ids = {"sl": lv["sl"][1], "tp": lv["tp"][1]}
        else:
            sl = None
        if sl is not None:
            boxes.append({"key": "rp-" + symbol, "kind": "position", "side": "short" if pshort else "long",
                          "entry": float(pos.avg_entry_price), "sl": sl,
                          "tp": tp, "qty": float(pos.qty), "type": "vị thế", "t0": t0, "ids": ids})
    return {"symbol": symbol, "asset": asset, "boxes": boxes, "inc": _crypto_inc(client, symbol) if crypto else None}


@router.get("/api/orders")
def orders(request: Request, asset: Optional[str] = None):
    """Lệnh đang mở (kể cả chân SL/TP của bracket, trả phẳng không lồng). ?asset= để lọc."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    if asset is not None:
        check_asset(asset)
    req = GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=False, limit=100)
    rows = [o for o in call_alpaca(get_client(request).get_orders, filter=req)
            if _keep(asset, _sym_asset(o.symbol)[1])]
    return {"orders": [{
        "id": str(o.id), "symbol": _sym_asset(o.symbol)[0], "side": enum_text(o.side), "qty": num(o.qty),
        "type": enum_text(o.order_type), "status": enum_text(o.status),
        "limit_price": num(getattr(o, "limit_price", None)), "stop_price": num(getattr(o, "stop_price", None)),
        "order_class": enum_text(getattr(o, "order_class", None)),
        "submitted_at": str(o.submitted_at) if getattr(o, "submitted_at", None) else None} for o in rows]}
