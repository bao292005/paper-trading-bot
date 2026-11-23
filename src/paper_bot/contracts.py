"""HỢP ĐỒNG CHUNG giữa 4 phần (giai đoạn 0). Sửa file này = phải báo cả nhóm.

Luồng dữ liệu:
  P1 DataManager  --MarketState-->  P2 Strategy  --Signal-->  P3 Risk/Order  --OrderIntent/Fill-->  DB  -->  P4 Report

QUY TẮC CHỐNG RÒ RỈ (leakage) - mọi phần phải tuân theo:
  - MarketState có `asof` = thời điểm ra quyết định = giá ĐÓNG CỬA của ngày `asof`.
  - Mọi thứ trong MarketState chỉ được dùng dữ liệu có timestamp <= asof
    (nến tới hết ngày asof, tin tức có created_at <= asof, forecast chỉ fit trên dữ liệu <= asof).
  - Lệnh sinh ra tại asof được khớp ở giá MỞ CỬA của phiên kế tiếp (trong backtest).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from .strategies import TradingSignal

# Các bối cảnh thị trường (regime) hợp lệ. P1 sinh ra, P2 dùng để chọn chiến lược.
REGIMES = ("bull_calm", "bull_volatile", "bear_calm", "bear_volatile", "unknown")


@dataclass
class NewsItem:
    headline: str
    created_at: datetime
    source: str = ""
    summary: str = ""
    symbols: List[str] = field(default_factory=list)


@dataclass
class Forecast:
    """Dự báo LỢI SUẤT (không phải giá) cho `horizon_days` phiên tới.
    quantiles: {0.1: -0.03, 0.5: 0.002, 0.9: 0.035} nghĩa là lợi suất log tích luỹ."""
    model: str  # "naive" | "chronos-bolt-small" | "kronos-small" ...
    horizon_days: int
    quantiles: Dict[float, float]

    @property
    def median(self) -> float:
        return self.quantiles[0.5]

    @property
    def width(self) -> float:
        """Độ bất định = q90 - q10. P3 dùng để giảm cỡ vị thế khi bất định cao."""
        return self.quantiles[0.9] - self.quantiles[0.1]


@dataclass
class MarketState:
    """Đầu ra của P1. Mọi thứ chiến lược cần biết về 1 mã tại 1 thời điểm."""
    symbol: str
    asof: datetime
    bars: pd.DataFrame  # cột open, high, low, close, volume; index = ngày, dòng cuối = asof
    regime: str = "unknown"  # một giá trị trong REGIMES (regime của THỊ TRƯỜNG, tính từ SPY)
    forecast: Optional[Forecast] = None
    news: List[NewsItem] = field(default_factory=list)
    features: Dict[str, float] = field(default_factory=dict)  # vd {"atr14": 3.2, "vol20": 0.018}


@dataclass
class Signal(TradingSignal):
    """Đầu ra của P2. Mở rộng TradingSignal của bài gốc (vẫn có reason, confidence).

    confidence: điểm thô của chiến lược (như bài gốc).
    prob: xác suất ĐÃ HIỆU CHỈNH rằng lợi suất `horizon_days` phiên tới (sau phí) > 0
          theo hướng action. None = chưa hiệu chỉnh.
    veto: True = luật rule-based chặn tín hiệu này (vẫn ghi log/DB để giải thích).
    """
    strategy: str = ""
    prob: Optional[float] = None
    horizon_days: int = 5
    veto: bool = False
    veto_reason: str = ""
    meta: Dict[str, object] = field(default_factory=dict)

    @property
    def score(self) -> float:
        """Giá trị dùng cho ngưỡng lọc: ưu tiên prob, không có thì dùng confidence."""
        return self.prob if self.prob is not None else self.confidence


@dataclass
class OrderIntent:
    """Đầu ra của P3 trước khi gửi broker (hoặc backtest engine).

    Quy ước SELL: qty <= 0 hoặc qty >= số đang nắm -> đóng TOÀN BỘ vị thế;
    0 < qty < số đang nắm -> bán một phần (dùng khi cân bằng lại danh mục)."""
    symbol: str
    side: str  # "buy" | "sell"
    qty: int
    order_type: str = "market"  # "market" | "limit"
    limit_price: Optional[float] = None
    stop_loss: Optional[float] = None  # có cả 2 -> bracket order
    take_profit: Optional[float] = None
    reason: str = ""
    asof: Optional[datetime] = None


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    filled_at: datetime
    fee: float = 0.0
