"""Backtest engine vòng lặp theo ngày (đơn giản, dễ đọc).

Quy tắc (docs/prereg-danh-gia.md, mục 2-3):
  - policy ra quyết định ở giá ĐÓNG CỬA ngày t, chỉ thấy dữ liệu <= t (qua lớp View).
  - Lệnh khớp ở giá MỞ CỬA ngày t+1, phí `cost_bps` mỗi chiều.
  - Không margin, không bán khống: mua vượt tiền mặt thì giảm qty.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pandas as pd

from ..contracts import Fill, OrderIntent


class View:
    """Cửa sổ dữ liệu cho policy: CHỈ trả nến có ngày <= asof (chốt chống rò rỉ)."""

    def __init__(self, bars: Dict[str, pd.DataFrame], asof: pd.Timestamp):
        self._bars = bars
        self.asof = asof

    def symbols(self) -> List[str]:
        return list(self._bars)

    def bars(self, symbol: str) -> pd.DataFrame:
        df = self._bars.get(symbol)
        if df is None:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        n = df.index.searchsorted(self.asof, side="right")  # số dòng có ngày <= asof
        return df.iloc[:n]


@dataclass
class Position:
    qty: int
    entry_price: float
    entry_date: pd.Timestamp
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


@dataclass
class Portfolio:
    cash: float
    positions: Dict[str, Position] = field(default_factory=dict)
    equity: float = 0.0  # giá trị tài khoản tại close gần nhất


@dataclass
class Trade:
    """Một lần vào - ra (round trip). exit_reason: signal | stop_loss | take_profit | end."""
    symbol: str
    qty: int
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    fee: float = 0.0  # tổng phí mua + bán của phần này (trade "end": phí bán ước tính)

    @property
    def pnl(self) -> float:
        """Lãi/lỗ SAU phí."""
        if self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) * self.qty - self.fee


@dataclass
class BacktestResult:
    equity: pd.DataFrame  # index ngày, cột equity, cash, exposure
    trades: List[Trade]
    fills: List[Fill]


Policy = Callable[[pd.Timestamp, View, Portfolio], List[OrderIntent]]


class _Engine:
    def __init__(self, bars, cost_bps, initial_cash, store):
        self.bars = bars
        self.cost = cost_bps / 10_000
        self.pf = Portfolio(cash=initial_cash, equity=initial_cash)
        self.store = store
        self.trades: List[Trade] = []
        self.open_trades: Dict[str, Trade] = {}
        self.fills: List[Fill] = []
        self.last_close: Dict[str, float] = {}

    def _record_fill(self, symbol, side, qty, price, day):
        fee = qty * price * self.cost
        fill = Fill(f"bt-{len(self.fills) + 1}", symbol, side, qty, price, day.to_pydatetime(), fee)
        self.fills.append(fill)
        if self.store is not None:
            self.store.log_fill(fill)
        return fee

    def buy(self, intent: OrderIntent, price: float, day):
        # Không margin: giảm qty cho vừa tiền mặt (tính cả phí)
        qty = min(int(intent.qty), int(self.pf.cash / (price * (1 + self.cost))))
        if qty <= 0:
            return
        fee = self._record_fill(intent.symbol, "buy", qty, price, day)
        self.pf.cash -= qty * price + fee
        pos = self.pf.positions.get(intent.symbol)
        if pos is None:
            self.pf.positions[intent.symbol] = Position(qty, price, day, intent.stop_loss,
                                                        intent.take_profit)
            self.open_trades[intent.symbol] = Trade(intent.symbol, qty, day, price, fee=fee)
        else:  # mua thêm: giá vốn bình quân
            pos.entry_price = (pos.entry_price * pos.qty + price * qty) / (pos.qty + qty)
            pos.qty += qty
            trade = self.open_trades[intent.symbol]
            trade.entry_price, trade.qty = pos.entry_price, pos.qty
            trade.fee += fee

    def sell(self, symbol: str, qty: int, price: float, day, reason: str):
        """Bán `qty` (<= 0 hoặc >= số đang nắm thì đóng toàn bộ). Không bán khống."""
        pos = self.pf.positions.get(symbol)
        if pos is None:
            return
        qty = pos.qty if qty <= 0 or qty >= pos.qty else int(qty)
        fee = self._record_fill(symbol, "sell", qty, price, day)
        self.pf.cash += qty * price - fee
        trade = self.open_trades[symbol]
        entry_fee = trade.fee * qty / pos.qty  # chia phí mua theo tỷ lệ phần bán
        trade.fee -= entry_fee
        self.trades.append(Trade(symbol, qty, trade.entry_date, pos.entry_price, day, price,
                                 reason, entry_fee + fee))
        pos.qty -= qty
        trade.qty = pos.qty
        if pos.qty == 0:
            del self.pf.positions[symbol]
            del self.open_trades[symbol]

    def execute_pending(self, pending: List[OrderIntent], day):
        """Khớp lệnh hôm qua ở giá open hôm nay. Bán trước để giải phóng tiền."""
        ordered = [o for o in pending if o.side == "sell"] + [o for o in pending if o.side == "buy"]
        for o in ordered:
            df = self.bars.get(o.symbol)
            if df is None or day not in df.index:
                continue  # mã không có phiên hôm nay -> bỏ lệnh
            price = float(df.at[day, "open"])
            if o.side == "buy":
                self.buy(o, price, day)
            else:
                self.sell(o.symbol, int(o.qty), price, day, "signal")

    def check_stops(self, day):
        """Kiểm tra stop-loss / take-profit trong ngày bằng high/low.

        - Chỉ từ ngày SAU ngày vào lệnh.
        - Cả SL và TP cùng chạm trong ngày -> giả định SL chạm trước (bi quan, theo prereg).
        - Giá mở cửa đã vượt mức (gap) -> khớp ở giá open.
        """
        for sym in list(self.pf.positions):
            pos = self.pf.positions[sym]
            df = self.bars[sym]
            if pos.entry_date >= day or day not in df.index:
                continue
            o, h, low = (float(df.at[day, c]) for c in ("open", "high", "low"))
            sl, tp = pos.stop_loss, pos.take_profit
            if sl is not None and low <= sl:
                self.sell(sym, 0, min(o, sl), day, "stop_loss")
            elif tp is not None and h >= tp:
                self.sell(sym, 0, max(o, tp), day, "take_profit")

    def mark_to_market(self, day) -> dict:
        for sym, df in self.bars.items():
            if day in df.index:
                self.last_close[sym] = float(df.at[day, "close"])
        pos_value = sum(p.qty * self.last_close[s] for s, p in self.pf.positions.items())
        self.pf.equity = self.pf.cash + pos_value
        exposure = pos_value / self.pf.equity if self.pf.equity > 0 else 0.0
        return {"equity": self.pf.equity, "cash": self.pf.cash, "exposure": exposure}


def run_backtest(bars: Dict[str, pd.DataFrame], start, end, policy: Policy,
                 initial_cash: float = 100_000.0, cost_bps: float = 10.0,
                 store=None) -> BacktestResult:
    """Chạy backtest từ `start` tới `end` (gồm cả 2 đầu).

    bars: dict symbol -> DataFrame nến (định dạng cache chung). Dữ liệu trước `start`
    vẫn được policy thấy qua View (để tính chỉ báo), nhưng không giao dịch.
    store: storage.Store tuỳ chọn để ghi fill + equity (run_id đặt sẵn trong Store).
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    all_days = sorted(set().union(*(df.index for df in bars.values())))
    days = [d for d in all_days if start <= d <= end]

    eng = _Engine(bars, cost_bps, initial_cash, store)
    rows, pending = [], []
    for day in days:
        eng.execute_pending(pending, day)
        eng.check_stops(day)
        row = eng.mark_to_market(day)
        rows.append({"date": day, **row})
        if store is not None:
            store.log_equity(day.to_pydatetime(), row["equity"], row["cash"], row["exposure"])
        pending = list(policy(day, View(bars, day), eng.pf) or [])

    equity = pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame(
        columns=["equity", "cash", "exposure"])
    # Vị thế còn mở cuối kỳ: ghi trade lý do "end" ở close cuối, trừ phí bán ước tính
    # (chỉ cho thống kê trade; equity không đổi vì không thực sự bán)
    trades = list(eng.trades)
    for sym, t in eng.open_trades.items():
        px = eng.last_close[sym]
        trades.append(Trade(sym, t.qty, t.entry_date, t.entry_price, days[-1], px, "end",
                            t.fee + t.qty * px * eng.cost))
    return BacktestResult(equity=equity, trades=trades, fills=eng.fills)
