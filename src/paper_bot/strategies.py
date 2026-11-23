"""Các chiến lược giao dịch (Strategy Pattern).

Mỗi chiến lược nhận DataFrame nến ngày (cột: open, high, low, close, volume)
và trả về danh sách TradingSignal. Chiến lược KHÔNG tự tính khối lượng lệnh:
việc đó thuộc về PortfolioManager (quản lý rủi ro).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List

import pandas as pd


@dataclass
class TradingSignal:
    symbol: str
    action: str  # "BUY" hoặc "SELL"
    price: float
    timestamp: datetime
    reason: str
    confidence: float  # 0..1


class TradingStrategy(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame, symbol: str) -> List[TradingSignal]:
        pass


class MovingAverageCrossoverStrategy(TradingStrategy):
    """Mua khi MA ngắn cắt LÊN MA dài, bán khi cắt XUỐNG."""

    def __init__(self, short_window: int = 10, long_window: int = 30):
        super().__init__("MA_Crossover")
        self.short_window = short_window
        self.long_window = long_window

    def generate_signals(self, data: pd.DataFrame, symbol: str) -> List[TradingSignal]:
        if len(data) < self.long_window + 1:
            return []  # chưa đủ dữ liệu

        df = data.copy()
        df["MA_short"] = df["close"].rolling(self.short_window).mean()
        df["MA_long"] = df["close"].rolling(self.long_window).mean()
        latest, previous = df.iloc[-1], df.iloc[-2]
        price = float(latest["close"])
        s, l = latest["MA_short"], latest["MA_long"]

        signals = []
        if s > l and previous["MA_short"] <= previous["MA_long"]:
            signals.append(TradingSignal(symbol, "BUY", price, datetime.now(),
                                         f"MA crossover: {s:.2f} > {l:.2f}", 0.7))
        elif s < l and previous["MA_short"] >= previous["MA_long"]:
            signals.append(TradingSignal(symbol, "SELL", price, datetime.now(),
                                         f"MA crossunder: {s:.2f} < {l:.2f}", 0.7))
        return signals


class RSIStrategy(TradingStrategy):
    """Mua khi RSI < 30 (quá bán), bán khi RSI > 70 (quá mua)."""

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        super().__init__("RSI")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    @staticmethod
    def _calculate_rsi(prices: pd.Series, period: int) -> pd.Series:
        delta = prices.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def generate_signals(self, data: pd.DataFrame, symbol: str) -> List[TradingSignal]:
        if len(data) < self.period + 1:
            return []

        rsi = float(self._calculate_rsi(data["close"], self.period).iloc[-1])
        if pd.isna(rsi):
            return []
        price = float(data["close"].iloc[-1])

        # Càng xa ngưỡng thì confidence càng cao (tối đa 0.9)
        if rsi < self.oversold:
            conf = min(0.9, 0.6 + (self.oversold - rsi) / 100)
            return [TradingSignal(symbol, "BUY", price, datetime.now(),
                                  f"RSI oversold: {rsi:.1f}", conf)]
        if rsi > self.overbought:
            conf = min(0.9, 0.6 + (rsi - self.overbought) / 100)
            return [TradingSignal(symbol, "SELL", price, datetime.now(),
                                  f"RSI overbought: {rsi:.1f}", conf)]
        return []


class BollingerBandsStrategy(TradingStrategy):
    """Phần "Extending the Bot": mua khi giá thủng dải dưới, bán khi vượt dải trên."""

    def __init__(self, period: int = 20, std_dev: float = 2):
        super().__init__("BollingerBands")
        self.period = period
        self.std_dev = std_dev

    def generate_signals(self, data: pd.DataFrame, symbol: str) -> List[TradingSignal]:
        if len(data) < self.period:
            return []

        close = data["close"]
        mid = close.rolling(self.period).mean().iloc[-1]
        std = close.rolling(self.period).std().iloc[-1]
        upper, lower = mid + self.std_dev * std, mid - self.std_dev * std
        price = float(close.iloc[-1])

        if price < lower:
            return [TradingSignal(symbol, "BUY", price, datetime.now(),
                                  f"Price {price:.2f} < lower band {lower:.2f}", 0.6)]
        if price > upper:
            return [TradingSignal(symbol, "SELL", price, datetime.now(),
                                  f"Price {price:.2f} > upper band {upper:.2f}", 0.6)]
        return []


def add_macd_indicator(data: pd.DataFrame) -> pd.DataFrame:
    """Chỉ báo MACD (phần "Adding Technical Indicators")."""
    data = data.copy()
    exp1 = data["close"].ewm(span=12).mean()
    exp2 = data["close"].ewm(span=26).mean()
    data["MACD"] = exp1 - exp2
    data["MACD_signal"] = data["MACD"].ewm(span=9).mean()
    return data
