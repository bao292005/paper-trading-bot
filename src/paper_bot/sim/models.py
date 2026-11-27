"""Kiểu dữ liệu + kiểm tra đầu vào cho tài khoản luyện tập (bar replay)."""

import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd

ORDER_TYPES = ("market", "limit", "stop")
MAX_STOCK_QTY = 100000


@dataclass
class Order:
    id: str
    side: str            # buy | sell
    type: str            # market | limit | stop
    qty: float
    price: Optional[float] = None   # giá limit/stop (market: None)
    sl: Optional[float] = None      # SL/TP gắn kèm lệnh MỞ vị thế (bracket giả lập)
    tp: Optional[float] = None
    created: int = 0                # con trỏ (unix giây) lúc đặt: chỉ xét nến bắt đầu >= created
    role: str = "entry"             # entry | sl | tp | margin (sl/tp = lệnh con đóng, sinh ra khi lệnh mở khớp)
    parent: Optional[str] = None    # lệnh con: id lệnh mở gốc (cặp SL/TP là OCO)
    status: str = "pending"         # pending | filled | cancelled | rejected
    note: str = ""
    reduce: bool = False            # True = lệnh ĐÓNG/giảm vị thế (không bao giờ mở chiều ngược lại)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class SimTrade:
    """Một lần đóng (toàn bộ hoặc một phần) vị thế. Cùng tên trường với backtest.Trade để dùng mt5_report.
    qty luôn dương; side = "long" | "short" (short lời khi giá giảm)."""
    symbol: str
    qty: float
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    exit_reason: str    # manual | stop_loss | take_profit | margin_call | end
    fee: float = 0.0    # phí lệnh mở (phần tương ứng) + phí lệnh đóng
    side: str = "long"

    @property
    def pnl(self) -> float:
        move = self.exit_price - self.entry_price
        return (move if self.side == "long" else -move) * self.qty - self.fee

    def to_dict(self) -> dict:
        return {"qty": self.qty, "side": self.side, "entry_time": int(self.entry_date.timestamp()),
                "entry_price": self.entry_price, "exit_time": int(self.exit_date.timestamp()),
                "exit_price": self.exit_price, "exit_reason": self.exit_reason, "fee": self.fee, "pnl": self.pnl}


def to_ts(unix: int) -> pd.Timestamp:
    return pd.Timestamp(int(unix), unit="s", tz="UTC")


def clean_qty(v, fractional: bool) -> float:
    """Cổ phiếu: số nguyên 1..100000. Crypto: số lẻ > 0 (làm tròn 6 chữ số)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise ValueError("qty phải là số > 0")
    if fractional:
        q = round(float(v), 6)
        if q <= 0:
            raise ValueError("qty quá nhỏ (tối thiểu 0.000001)")
        return q
    if float(v) != int(v) or not 1 <= v <= MAX_STOCK_QTY:
        raise ValueError(f"cổ phiếu: qty phải là số nguyên 1..{MAX_STOCK_QTY}")
    return float(int(v))


def clean_price(v, name: str, required: bool = False) -> Optional[float]:
    if v is None:
        if required:
            raise ValueError(f"thiếu {name}")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise ValueError(f"{name} phải là số > 0")
    return float(v)


def check_bracket(sl: Optional[float], tp: Optional[float], ref: Optional[float], side: str = "long") -> None:
    """Long: SL < giá tham chiếu < TP. Short: TP < giá tham chiếu < SL."""
    if ref is None:
        return
    long_ = side == "long"
    if sl is not None and not (sl < ref if long_ else sl > ref):
        raise ValueError(f"SL ({sl:g}) phải {'<' if long_ else '>'} giá vào ({ref:g})")
    if tp is not None and not (tp > ref if long_ else tp < ref):
        raise ValueError(f"TP ({tp:g}) phải {'>' if long_ else '<'} giá vào ({ref:g})")
