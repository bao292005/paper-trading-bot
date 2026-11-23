"""Cấu hình tập trung cho bot (Configuration Pattern trong bài báo)."""

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

DEFAULT_SYMBOLS = ["AAPL", "GOOGL", "MSFT", "TSLA", "NVDA"]

# Tập mã cho backtest (xem docs/prereg-danh-gia.md). Có survivorship bias - đã ghi nhận.
UNIVERSE_50 = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "JPM", "JNJ", "V", "PG",
    "XOM", "UNH", "HD", "MA", "BAC", "KO", "PEP", "CVX", "ABBV", "MRK",
    "PFE", "WMT", "CSCO", "INTC", "ORCL", "ADBE", "CRM", "NFLX", "DIS", "T",
    "VZ", "CMCSA", "NKE", "MCD", "COST", "LLY", "TMO", "ABT", "DHR", "HON",
    "UNP", "CAT", "BA", "GS", "MS", "IBM", "QCOM", "TXN", "AMGN", "LOW",
]
BENCHMARK = "SPY"


@dataclass
class TradingConfig:
    api_key: str
    api_secret: str
    max_position_size: float = 1000.0  # tối đa bao nhiêu USD cho mỗi lệnh
    risk_per_trade: float = 0.02  # 2% buying power mỗi lệnh
    min_confidence: float = 0.5  # ngưỡng confidence trung bình để vào lệnh
    history_days: int = 100  # số ngày dữ liệu lịch sử
    symbols: List[str] = field(default_factory=lambda: list(DEFAULT_SYMBOLS))

    @classmethod
    def from_env(cls) -> "TradingConfig":
        """Đọc key từ biến môi trường (hoặc file .env)."""
        load_dotenv()
        key = os.getenv("ALPACA_API_KEY")
        secret = os.getenv("ALPACA_API_SECRET")
        if not key or not secret:
            raise SystemExit(
                "Thiếu ALPACA_API_KEY / ALPACA_API_SECRET. Copy .env.example thành .env rồi điền key."
            )
        return cls(api_key=key, api_secret=secret)
