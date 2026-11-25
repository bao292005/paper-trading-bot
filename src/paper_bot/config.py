"""Cấu hình tập trung cho bot (Configuration Pattern trong bài báo)."""

import os
from dataclasses import dataclass, field
from typing import Dict, List

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

ENGINES = ("full", "legacy")
# Cờ bật/tắt từng thành phần, trùng tên với decision/full_policy.DEFAULT_FLAGS.
# (Không import full_policy ở đây để tránh import vòng: full_policy import config.)
FEATURE_FLAGS = ("use_regime", "use_calibration", "use_forecast",
                 "use_atr_sizing", "use_bracket", "use_corr_limit")
# Tên ngắn dùng cho CLI: --disable bracket regime ...
SHORT_FLAG_NAMES = tuple(f.removeprefix("use_") for f in FEATURE_FLAGS)


def default_flags() -> Dict[str, bool]:
    return {f: True for f in FEATURE_FLAGS}


@dataclass
class TradingConfig:
    api_key: str
    api_secret: str
    max_position_size: float = 1000.0  # tối đa bao nhiêu USD cho mỗi lệnh
    risk_per_trade: float = 0.02  # 2% buying power mỗi lệnh
    min_confidence: float = 0.5  # ngưỡng confidence trung bình để vào lệnh
    history_days: int = 100  # số ngày dữ liệu lịch sử
    symbols: List[str] = field(default_factory=lambda: list(DEFAULT_SYMBOLS))
    # --- phần ghép bot ---
    engine: str = "full"  # "full" = pipeline mới | "legacy" = bot gốc của bài
    flags: Dict[str, bool] = field(default_factory=default_flags)
    dry_run: bool = False  # True = làm mọi bước nhưng KHÔNG gửi lệnh lên broker
    use_llm: bool = False  # bật LLMAgentStrategy (cần LLM_API_KEY)
    # Mốc cuối dữ liệu fit calibrator: giữ "2024-12-31" (trước HOLDOUT) tới khi chạy holdout xong;
    # sau đó đổi sang "yesterday" (= hôm qua theo giờ New York).
    calib_end: str = "2024-12-31"

    def disable(self, names) -> None:
        """Tắt các thành phần theo tên ngắn ("bracket") hoặc tên đầy đủ ("use_bracket")."""
        for name in names:
            flag = name if name.startswith("use_") else f"use_{name}"
            if flag not in FEATURE_FLAGS:
                raise ValueError(f"thành phần lạ: {name} (hợp lệ: {', '.join(SHORT_FLAG_NAMES)})")
            self.flags[flag] = False

    def disabled(self) -> List[str]:
        return [f for f, on in self.flags.items() if not on]

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
