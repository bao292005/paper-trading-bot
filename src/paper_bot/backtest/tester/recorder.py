"""Ghi lại những gì xảy ra mỗi ngày để dựng danh sách trade chi tiết + replay payload (visual mode).

Recorder.wrap(policy) bọc policy: engine gọi policy ở close mỗi ngày SAU khi đã khớp lệnh, chạy
stop và đánh giá lại danh mục -> lúc đó chụp lại vị thế đang mở (kèm SL/TP) và các lệnh mới.
Không thay đổi quyết định của policy (chỉ quan sát).
"""

import bisect
from collections import defaultdict

import pandas as pd

from .report import balance_curve


def _d(ts) -> str:
    return pd.Timestamp(ts).date().isoformat()


def _f(x):
    return None if x is None else float(x)


class Recorder:
    def __init__(self):
        self.orders = defaultdict(list)  # ngày quyết định -> lệnh mới (khớp ở open phiên sau)
        self.levels = {}  # ngày -> vị thế đang mở cuối ngày
        self.buy_reasons = defaultdict(list)  # mã -> [(asof, reason)] lệnh mua
        self.sell_reasons = defaultdict(list)

    def wrap(self, policy):
        def recorded(asof, view, pf):
            out = list(policy(asof, view, pf) or [])
            day = _d(asof)
            self.levels[day] = [
                {"symbol": s, "qty": int(p.qty), "entry_price": float(p.entry_price),
                 "entry_date": _d(p.entry_date), "stop_loss": _f(p.stop_loss),
                 "take_profit": _f(p.take_profit)} for s, p in pf.positions.items()]
            for o in out:
                self.orders[day].append({"symbol": o.symbol, "side": o.side, "qty": int(o.qty),
                                         "stop_loss": _f(o.stop_loss), "take_profit": _f(o.take_profit),
                                         "reason": o.reason})
                book = self.buy_reasons if o.side == "buy" else self.sell_reasons
                book[o.symbol].append((pd.Timestamp(asof), o.reason))
            return out
        return recorded

    @staticmethod
    def _last_before(book, symbol, day):
        """Lý do của lệnh gần nhất quyết định TRƯỚC `day` (lệnh khớp ở open `day`)."""
        items = book.get(symbol, [])
        i = bisect.bisect_left([a for a, _ in items], pd.Timestamp(day))
        return items[i - 1][1] if i else ""

    def _level_at(self, symbol, day):
        for lv in self.levels.get(_d(day), []):
            if lv["symbol"] == symbol:
                return lv
        return {}

    def trades(self, res) -> list:
        out = []
        for i, t in enumerate(sorted(res.trades, key=lambda t: t.exit_date)):
            lv = self._level_at(t.symbol, t.entry_date)  # SL/TP đặt lúc vào lệnh
            out.append({
                "id": i + 1, "symbol": t.symbol, "side": "long", "qty": int(t.qty),
                "entry_date": _d(t.entry_date), "entry_price": float(t.entry_price),
                "exit_date": _d(t.exit_date), "exit_price": float(t.exit_price),
                "exit_reason": t.exit_reason, "pnl": float(t.pnl), "fee": float(t.fee),
                "stop_loss": lv.get("stop_loss"), "take_profit": lv.get("take_profit"),
                "entry_note": self._last_before(self.buy_reasons, t.symbol, t.entry_date),
                "exit_note": (self._last_before(self.sell_reasons, t.symbol, t.exit_date)
                              if t.exit_reason == "signal" else ""),
            })
        return out

    def payload(self, res, deposit: float) -> dict:
        """curve + trades + replay, tất cả kiểu JSON."""
        eq = res.equity
        bal = balance_curve(res.trades, deposit, eq.index)
        # Lý do của fill bán: lấy từ trade cùng mã, cùng ngày, cùng giá
        exit_reason = {(t.symbol, _d(t.exit_date), float(t.exit_price)): t.exit_reason
                       for t in res.trades if t.exit_reason != "end"}
        fills = defaultdict(list)
        for f in res.fills:
            day = _d(f.filled_at)
            reason = "entry" if f.side == "buy" else exit_reason.get((f.symbol, day, float(f.price)), "")
            fills[day].append({"symbol": f.symbol, "side": f.side, "qty": int(f.qty),
                               "price": float(f.price), "fee": float(f.fee), "reason": reason})
        curve, replay = [], []
        for ts, row in eq.iterrows():
            day = _d(ts)
            point = {"date": day, "equity": float(row["equity"]), "balance": float(bal.get(ts, deposit)),
                     "cash": float(row["cash"]), "exposure": float(row["exposure"])}
            curve.append(point)
            replay.append({"date": day, "equity": point["equity"], "balance": point["balance"],
                           "fills": fills.get(day, []), "levels": self.levels.get(day, []),
                           "orders": self.orders.get(day, [])})
        return {"curve": curve, "trades": self.trades(res), "replay": replay}
