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
    sl: Optional[float] = None      # SL/TP gắn kèm lệnh MUA (bracket giả lập)
    tp: Optional[float] = None
    created: int = 0                # con trỏ (unix giây) lúc đặt: chỉ xét nến bắt đầu >= created
    role: str = "entry"             # entry | sl | tp (sl/tp = lệnh con bán, sinh ra khi lệnh mua khớp)
    parent: Optional[str] = None    # lệnh con: id lệnh mua gốc (cặp SL/TP là OCO)
    status: str = "pending"         # pending | filled | cancelled | rejected
    note: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class SimTrade:
    """Một lần đóng (toàn bộ hoặc một phần) vị thế mua. Cùng tên trường với backtest.Trade để dùng mt5_report."""
    symbol: str
    qty: float
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    exit_reason: str    # manual | stop_loss | take_profit | end
    fee: float = 0.0    # phí mua (phần tương ứng) + phí bán

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.qty - self.fee

    def to_dict(self) -> dict:
        return {"qty": self.qty, "entry_time": int(self.entry_date.timestamp()), "entry_price": self.entry_price,
                "exit_time": int(self.exit_date.timestamp()), "exit_price": self.exit_price,
                "exit_reason": self.exit_reason, "fee": self.fee, "pnl": self.pnl}


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


def check_bracket(sl: Optional[float], tp: Optional[float], ref: Optional[float]) -> None:
    """Lệnh mua: SL < giá tham chiếu < TP."""
    if ref is None:
        return
    if sl is not None and not sl < ref:
        raise ValueError(f"SL ({sl:g}) phải < giá vào ({ref:g})")
    if tp is not None and not tp > ref:
        raise ValueError(f"TP ({tp:g}) phải > giá vào ({ref:g})")
