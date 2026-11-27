"""Quy tắc khớp lệnh giả lập trên MỘT nến OHLC (hàm thuần, không trạng thái).

Dùng cho tài khoản luyện tập (bar replay). Nến thường là nến 1 phút ("1 minute OHLC" như MT5).
  - market: khớp ở giá mở cửa (open) của nến.
  - limit/stop: khớp khi high/low chạm giá; nếu nến MỞ đã vượt qua giá (gap) thì khớp ở open.
  - SL/TP của vị thế mua: cùng quy tắc (SL = stop bán, TP = limit bán); vị thế short thì ngược lại
    (SL = stop mua ở TRÊN, TP = limit mua ở DƯỚI). Chạm cả hai trong cùng một nến -> tính SL trước
    (bi quan, giống backtest engine).
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


def exit_hit(sl: Optional[float], tp: Optional[float], o: float, h: float, l: float,
             side: str = "long") -> Optional[Tuple[str, float]]:
    """Vị thế có SL/TP: nến này chạm mức nào? -> ("sl"|"tp", giá khớp) hoặc None. SL xét trước.
    Long: SL = stop bán (low <= SL), TP = limit bán. Short: SL = stop MUA (high >= SL, nằm trên), TP = limit mua (low <= TP)."""
    close_side = "sell" if side == "long" else "buy"
    if sl is not None:
        p = fill_price("stop", close_side, sl, o, h, l)
        if p is not None:
            return "sl", p
    if tp is not None:
        p = fill_price("limit", close_side, tp, o, h, l)
        if p is not None:
            return "tp", p
    return None
