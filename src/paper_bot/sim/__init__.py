"""Mô phỏng khớp lệnh cho chế độ luyện tập (bar replay trên web). KHÔNG bao giờ gửi lệnh lên Alpaca."""

from .account import PracticeAccount
from .matching import exit_hit, fill_price

__all__ = ["PracticeAccount", "exit_hit", "fill_price"]
