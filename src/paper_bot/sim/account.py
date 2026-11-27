"""Tài khoản luyện tập giả lập cho bar replay: tiền mặt, 1 mã, long HOẶC short, lệnh market/limit/stop + SL/TP.

Vị thế có dấu: qty > 0 = long, qty < 0 = short. Lệnh ngược chiều vị thế đang có = lệnh ĐÓNG/giảm (reduce);
KHÔNG đảo chiều trong 1 lệnh (đang long 10 mà bán 15 -> từ chối, "đóng vị thế trước").
Short: nhận tiền bán ngay (cash += tiền bán − phí); equity = cash + qty × giá. Ký quỹ đơn giản 1x: giá trị short
sau lệnh không vượt equity; equity chạm 0 -> tự đóng ("margin call"). KHÔNG tính phí vay cổ phiếu (ghi chú).

Nến đưa vào advance() là dict {start, end, open, high, low, close} (start/end = unix giây, end = lúc nến đóng).
CHỐNG NHÌN TRƯỚC: advance(bars, to) bỏ qua mọi nến có end > to hoặc start < con trỏ cũ; lệnh chỉ xét nến bắt đầu
>= lúc đặt lệnh (market = open nến kế tiếp); lệnh con SL/TP chỉ xét từ nến SAU nến lệnh mở khớp.
"""

from typing import Dict, List, Optional

from ..config import asset_profile
from .matching import exit_hit, fill_price
from .models import ORDER_TYPES, Order, SimTrade, check_bracket, clean_price, clean_qty, to_ts

EPS = 1e-9
REASONS = {"entry": "manual", "sl": "stop_loss", "tp": "take_profit", "margin": "margin_call"}
SHORT_NOTE = "Short giả lập: không tính phí vay cổ phiếu; ký quỹ 1x (giá trị short ≤ equity)"
MARGIN_NOTE = "Margin call: equity về 0 nên vị thế short bị tự đóng"


class PracticeAccount:
    def __init__(self, symbol: str, asset: str = "stock", cash: float = 100000.0, cursor: int = 0,
                 last_price: Optional[float] = None, fee_bps: Optional[float] = None):
        prof = asset_profile(asset)
        self.symbol, self.asset = symbol, asset
        self.fee_bps = float(prof["cost_bps"] if fee_bps is None else fee_bps)
        self.fractional = bool(prof["fractional"])
        self.initial = self.cash = float(cash)
        self.qty = self.avg = self.fee_pool = 0.0  # vị thế: số lượng CÓ DẤU, giá vào TB, phí lệnh mở chưa phân bổ
        self.opened: Optional[int] = None
        self.orders: Dict[str, Order] = {}
        self.fills: List[dict] = []
        self.trades: List[SimTrade] = []
        self.equity: List[tuple] = []  # (unix, equity, cash, exposure)
        self.start = self.cursor = int(cursor)
        self.last_price = last_price
        self.notes: set = set()
        self.bars_seen = 0
        self._seq = 0

    @property
    def side(self) -> Optional[str]:
        """Chiều vị thế: "long" | "short" | None (không có vị thế)."""
        return "long" if self.qty > EPS else "short" if self.qty < -EPS else None

    def _funds_error(self, side: str, qty: float, p: float) -> Optional[str]:
        """Lệnh MỞ: mua cần đủ tiền mặt; bán khống cần giá trị short sau lệnh ≤ equity (ký quỹ 1x)."""
        if side == "buy":
            return "không đủ tiền mặt" if qty * p * (1 + self.fee_bps / 1e4) > self.cash + EPS else None
        if (abs(self.qty) + qty) * p > self.cash + self.qty * p + EPS:
            return "vượt ký quỹ 1x (giá trị short sau lệnh > equity)"
        return None

    # ---------- đặt / sửa / huỷ lệnh ----------
    def place_order(self, side, otype, qty, price=None, sl=None, tp=None) -> Order:
        if side not in ("buy", "sell"):
            raise ValueError("side phải là buy hoặc sell")
        if otype not in ORDER_TYPES:
            raise ValueError(f"type phải thuộc {ORDER_TYPES}")
        qty = clean_qty(qty, self.fractional)
        price = clean_price(price, "price", required=otype != "market")
        if otype == "market" and price is not None:
            raise ValueError("lệnh market không có price")
        sl, tp = clean_price(sl, "sl"), clean_price(tp, "tp")
        sign = 1 if side == "buy" else -1
        reduce = self.qty * sign < -EPS  # ngược chiều vị thế đang có = đóng/giảm
        ref = price if price is not None else self.last_price
        if reduce:
            if sl is not None or tp is not None:
                raise ValueError("SL/TP chỉ gắn với lệnh mở vị thế (lệnh này đang đóng/giảm vị thế)")
            if qty > abs(self.qty) + EPS:
                raise ValueError(f"không đảo chiều trong 1 lệnh: đang {self.side} {abs(self.qty):g}, "
                                 "hãy đóng vị thế trước")
        else:
            check_bracket(sl, tp, ref, "long" if side == "buy" else "short")
            err = self._funds_error(side, qty, ref) if ref is not None else None
            if err:
                raise ValueError(err + " cho lệnh này")
        self._seq += 1
        o = Order(f"o{self._seq}", side, otype, qty, price, sl, tp, created=self.cursor, reduce=reduce)
        self.orders[o.id] = o
        return o

    def _pending(self, oid: str) -> Order:
        o = self.orders.get(oid)
        if o is None or o.status != "pending":
            raise KeyError(oid)
        return o

    def cancel_order(self, oid: str) -> Order:
        o = self._pending(oid)
        o.status = "cancelled"
        return o

    def modify_order(self, oid: str, changes: dict) -> Order:
        """Sửa giá lệnh chờ (kéo đường giá trên chart). changes: price / sl / tp (None = xoá SL/TP của lệnh mở)."""
        o = self._pending(oid)
        price = clean_price(changes.get("price", o.price), "price", required=o.type != "market")
        if o.role == "entry":
            sl = clean_price(changes.get("sl", o.sl), "sl")
            tp = clean_price(changes.get("tp", o.tp), "tp")
            if not o.reduce:
                check_bracket(sl, tp, price if price is not None else self.last_price,
                              "long" if o.side == "buy" else "short")
            o.sl, o.tp = (None, None) if o.reduce else (sl, tp)
        elif self.last_price is not None:  # lệnh con: long SL dưới/TP trên giá hiện tại; short ngược lại
            check_bracket(price if o.role == "sl" else None, price if o.role == "tp" else None, self.last_price,
                          "long" if o.side == "sell" else "short")
        o.price = price if o.type != "market" else None
        return o

    def close_position(self) -> Optional[Order]:
        """Đóng vị thế = lệnh market ngược chiều toàn bộ (khớp ở open nến 1m kế tiếp)."""
        if abs(self.qty) <= EPS:
            raise ValueError("không có vị thế để đóng")
        o = self.place_order("sell" if self.qty > 0 else "buy", "market", abs(self.qty))
        o.note = "đóng vị thế"
        return o

    # ---------- tiến con trỏ ----------
    def advance(self, bars, to: int, steps: int = 1) -> List[dict]:
        """Khớp lệnh trên các nến nằm trong [con trỏ cũ, to]. Trả các fill mới."""
        to = int(to)
        if to < self.cursor:
            raise ValueError("không lùi con trỏ được (chọn lại điểm bắt đầu = phiên mới)")
        n0 = len(self.fills)
        for b in sorted(bars, key=lambda x: x["start"]):
            if b["end"] > to or b["start"] < self.cursor:
                continue  # chống nhìn trước / nến đã xét
            self._process_bar(b)
            self.cursor = int(b["start"])  # nến sau không được bắt đầu trước nến này
        self.cursor = to
        self.bars_seen += max(int(steps), 0)
        self._record_equity(to)
        return self.fills[n0:]

    def _process_bar(self, b: dict) -> None:
        o_, h, l_ = b["open"], b["high"], b["low"]
        for o in [x for x in self.orders.values() if x.status == "pending" and x.role == "entry"]:
            if o.created <= b["start"]:
                p = fill_price(o.type, o.side, o.price, o_, h, l_)
                if p is not None:
                    self._fill(o, p, b)
        for parent in {x.parent for x in self.orders.values() if x.status == "pending" and x.role in ("sl", "tp")}:
            kids = {x.role: x for x in self.orders.values()
                    if x.parent == parent and x.status == "pending" and x.created <= b["start"]}
            if not kids:
                continue
            side = "long" if next(iter(kids.values())).side == "sell" else "short"  # lệnh con bán = vị thế long
            hit = exit_hit(kids["sl"].price if "sl" in kids else None,
                           kids["tp"].price if "tp" in kids else None, o_, h, l_, side)
            if hit:
                self._fill(kids[hit[0]], hit[1], b)
                for k in kids.values():
                    if k.status == "pending":
                        k.status, k.note = "cancelled", "OCO: chân kia đã khớp"
        self._margin_check(b)
        self.last_price = float(b["close"])

    def _margin_check(self, b: dict) -> None:
        """Short: giá lên tới mức equity = 0 (cash / |qty|) -> tự mua đóng toàn bộ (gap thì khớp ở open)."""
        if self.qty >= -EPS:
            return
        p = fill_price("stop", "buy", max(self.cash / abs(self.qty), EPS), b["open"], b["high"], b["low"])
        if p is None:
            return
        self._seq += 1
        mo = Order(f"o{self._seq}", "buy", "market", abs(self.qty), created=int(b["start"]), role="margin",
                   reduce=True, note="margin call: equity <= 0")
        self.orders[mo.id] = mo
        self._close(mo, abs(self.qty), p, b)
        self.notes.add(MARGIN_NOTE)

    def _fill(self, o: Order, p: float, b: dict) -> None:
        sign = 1 if o.side == "buy" else -1
        if o.reduce:
            if self.qty * sign >= -EPS:
                o.status, o.note = "cancelled", "vị thế đã đóng"
                return
            self._close(o, min(o.qty, abs(self.qty)), p, b)
            return
        if self.qty * sign < -EPS:
            o.status, o.note = "rejected", "đang có vị thế ngược chiều: đóng vị thế trước"
            return
        err = self._funds_error(o.side, o.qty, p)
        if err:
            o.status, o.note = "rejected", err + " lúc khớp"
            return
        self._open(o, sign, p, b)

    def _open(self, o: Order, sign: int, p: float, b: dict) -> None:
        """Mở/thêm vị thế. Mua: trả tiền; bán khống: nhận tiền bán. Phí lệnh mở cất vào fee_pool."""
        notional = o.qty * p
        fee = notional * self.fee_bps / 1e4
        self.cash += -sign * notional - fee
        self.avg = (self.avg * abs(self.qty) + notional) / (abs(self.qty) + o.qty)
        self.qty += sign * o.qty
        self.fee_pool += fee
        self.opened = self.opened if self.opened is not None else int(b["start"])
        if sign < 0:
            self.notes.add(SHORT_NOTE)
        self._log_fill(o, o.qty, p, fee, b)
        exit_side = "sell" if sign > 0 else "buy"
        for role, lvl, typ in (("sl", o.sl, "stop"), ("tp", o.tp, "limit")):
            if lvl is not None:  # lệnh con chỉ có hiệu lực từ nến sau
                self._seq += 1
                kid = Order(f"o{self._seq}", exit_side, typ, o.qty, lvl, created=int(b["end"]), role=role,
                            parent=o.id, reduce=True)
                self.orders[kid.id] = kid

    def _close(self, o: Order, q: float, p: float, b: dict) -> None:
        """Đóng q đơn vị: long = bán (nhận tiền), short = mua lại (trả tiền). Ghi 1 SimTrade."""
        long_ = self.qty > 0
        fee = q * p * self.fee_bps / 1e4
        share = self.fee_pool * q / abs(self.qty)
        self.cash += (q * p if long_ else -q * p) - fee
        self.trades.append(SimTrade(self.symbol, q, to_ts(self.opened), self.avg, to_ts(b["start"]), p,
                                    REASONS[o.role], share + fee, "long" if long_ else "short"))
        self.fee_pool -= share
        self.qty += -q if long_ else q
        self._log_fill(o, q, p, fee, b)
        if abs(self.qty) <= EPS:
            self.qty, self.avg, self.fee_pool, self.opened = 0.0, 0.0, 0.0, None
        for k in self.orders.values():  # lệnh đóng còn chờ không được vượt vị thế còn lại
            if k.status == "pending" and k.reduce:
                if abs(self.qty) <= EPS:
                    k.status, k.note = "cancelled", "vị thế đã đóng"
                else:
                    k.qty = min(k.qty, abs(self.qty))

    def _log_fill(self, o: Order, q: float, p: float, fee: float, b: dict) -> None:
        o.status = "filled"
        self.fills.append({"order_id": o.id, "side": o.side, "qty": q, "price": p, "fee": fee,
                           "time": int(b["start"]), "role": o.role, "reason": REASONS[o.role] if o.reduce else "entry"})

    # ---------- trạng thái ----------
    def equity_now(self) -> float:
        return self.cash + self.qty * (self.last_price if self.last_price is not None else self.avg)

    def _record_equity(self, t: int) -> None:
        exposure = abs(self.qty) * (self.last_price or self.avg)
        point = (int(t), self.equity_now(), self.cash, exposure)
        if self.equity and self.equity[-1][0] == point[0]:
            self.equity[-1] = point
        else:
            self.equity.append(point)

    def snapshot(self) -> dict:
        unreal = self.qty * ((self.last_price or self.avg) - self.avg)  # short: (giá vào − giá hiện tại) × |qty|
        pos = {"qty": self.qty, "side": self.side, "avg_price": self.avg, "unrealized": unreal}
        return {"symbol": self.symbol, "asset": self.asset, "cursor": self.cursor, "start": self.start,
                "initial": self.initial, "cash": self.cash, "equity": self.equity_now(),
                "pl": self.equity_now() - self.initial, "last_price": self.last_price, "fee_bps": self.fee_bps,
                "position": pos if self.side else None,
                "orders": [o.to_dict() for o in self.orders.values() if o.status == "pending"],
                "fills": self.fills, "trades": [t.to_dict() for t in self.trades],
                "bars_seen": self.bars_seen, "notes": sorted(self.notes)}
