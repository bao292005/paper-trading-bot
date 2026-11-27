"""Quy tắc khớp lệnh giả lập trên MỘT nến OHLC (hàm thuần, không trạng thái).

Dùng cho tài khoản luyện tập (bar replay). Nến thường là nến 1 phút ("1 minute OHLC" như MT5).
  - market: khớp ở giá mở cửa (open) của nến.
  - limit/stop: khớp khi high/low chạm giá; nếu nến MỞ đã vượt qua giá (gap) thì khớp ở open.
  - SL/TP của vị thế mua: cùng quy tắc (SL = stop bán, TP = limit bán); chạm cả hai trong
    cùng một nến -> tính SL trước (bi quan, giống backtest engine).
"""

from typing import Optional, Tuple


def fill_price(otype: str, side: str, price: Optional[float], o: float, h: float, l: float) -> Optional[float]:
    """Giá khớp của lệnh trong nến (o, h, l) hoặc None nếu chưa khớp."""
    if otype == "market":
        return float(o)
    if otype == "limit":
        if side == "buy":  # mua limit: giá xuống tới price
            return float(o) if o <= price else (float(price) if l <= price else None)
        return float(o) if o >= price else (float(price) if h >= price else None)
    if otype == "stop":
        if side == "buy":  # mua stop: giá lên tới price
            return float(o) if o >= price else (float(price) if h >= price else None)
        return float(o) if o <= price else (float(price) if l <= price else None)
    raise ValueError(f"loại lệnh không hỗ trợ: {otype}")


def exit_hit(sl: Optional[float], tp: Optional[float], o: float, h: float, l: float) -> Optional[Tuple[str, float]]:
    """Vị thế mua có SL/TP: nến này chạm mức nào? -> ("sl"|"tp", giá khớp) hoặc None. SL xét trước."""
    if sl is not None:
        p = fill_price("stop", "sell", sl, o, h, l)
        if p is not None:
            return "sl", p
    if tp is not None:
        p = fill_price("limit", "sell", tp, o, h, l)
        if p is not None:
            return "tp", p
    return None
