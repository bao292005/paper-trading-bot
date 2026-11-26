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

# --- Chế độ crypto (xem docs/prereg-crypto.md) ---
ASSET_CLASSES = ("stock", "crypto")
CRYPTO_UNIVERSE = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD"]
CRYPTO_BENCHMARK = "BTC/USD"

# Mọi con số lấy từ prereg-danh-gia.md (stock) và prereg-crypto.md (crypto). KHÔNG tối ưu.
_PROFILES = {
    "stock": {
        "universe": UNIVERSE_50, "benchmark": BENCHMARK,
        "periods_per_year": 252, "cost_bps": 10.0, "cost_sensitivity_bps": (5.0, 20.0),
        "fractional": False, "qty_decimals": 0, "market_hours": True,
        "warmup_start": "2016-01-01", "dev_start": "2019-01-01", "dev_end": "2024-12-31",
        "holdout_start": "2025-01-01", "holdout_end": "2026-10-31",
        "train_years": 3,  # walk-forward: train 3 năm gần nhất (cửa sổ trượt)
        "is_period": None, "oos_period": None,  # tester tự chọn như cũ
        "max_pair_corr": 0.8, "max_positions": 10, "max_position_pct": 0.10,
    },
    "crypto": {
        "universe": CRYPTO_UNIVERSE, "benchmark": CRYPTO_BENCHMARK,
        "periods_per_year": 365, "cost_bps": 25.0, "cost_sensitivity_bps": (15.0, 40.0),
        "fractional": True, "qty_decimals": 6, "market_hours": False,
        "warmup_start": "2021-01-01", "dev_start": "2022-01-01", "dev_end": "2024-12-31",
        "holdout_start": "2025-01-01", "holdout_end": "2026-10-31",
        "train_years": None,  # walk-forward: train MỞ RỘNG từ warmup_start
        "is_period": ("2022-01-01", "2023-12-31"), "oos_period": ("2024-01-01", "2024-12-31"),
        "max_pair_corr": 0.9, "max_positions": 5, "max_position_pct": 0.20,
    },
}
# Các tham số chung cả hai chế độ (prereg-crypto mục 8.4)
_COMMON = {"test_months": 6, "risk_per_trade": 0.01, "atr_stop_mult": 2.0, "take_profit_mult": 3.0}


def asset_profile(asset: str = "stock") -> dict:
    """Thông số của một loại tài sản ("stock" | "crypto"). Trả bản sao, sửa không ảnh hưởng gốc."""
    if asset not in _PROFILES:
        raise ValueError(f"asset phải thuộc {ASSET_CLASSES}, nhận: {asset}")
    prof = {**_COMMON, **_PROFILES[asset], "asset": asset}
    prof["universe"] = list(prof["universe"])
    return prof


def is_crypto_symbol(symbol: str) -> bool:
    """Mã crypto có dạng "BTC/USD"."""
    return "/" in symbol


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
    asset: str = "stock"  # "stock" | "crypto" (xem asset_profile)

    def profile(self) -> dict:
        return asset_profile(self.asset)

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
