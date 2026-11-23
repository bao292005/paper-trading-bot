"""Các component riêng biệt (Component-Based Architecture):
DataManager (dữ liệu), PortfolioManager (vị thế + rủi ro), OrderManager (đặt lệnh).
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd
from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest, MarketOrderRequest

from .config import TradingConfig

logger = logging.getLogger("paper_bot")


class DataManager:
    def __init__(self, client: StockHistoricalDataClient):
        self.client = client

    def get_historical_data(self, symbol: str, days: int = 100) -> pd.DataFrame:
        """Nến ngày trong `days` ngày gần nhất. Lỗi API -> trả DataFrame rỗng."""
        end = datetime.now()
        start = end - timedelta(days=days)
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            adjustment=Adjustment.RAW,
            feed=DataFeed.IEX,  # tài khoản miễn phí chỉ dùng được feed IEX
        )
        try:
            df = self.client.get_stock_bars(request).df
        except APIError as e:
            logger.error(f"Lỗi lấy dữ liệu {symbol}: {e}")
            return pd.DataFrame()
        if df.empty:
            logger.warning(f"Không có dữ liệu cho {symbol}")
            return df
        # df có MultiIndex (symbol, timestamp) -> bỏ tầng symbol
        return df.xs(symbol, level="symbol")[["open", "high", "low", "close", "volume"]]


class PortfolioManager:
    def __init__(self, client: TradingClient, config: TradingConfig):
        self.client = client
        self.config = config

    def get_buying_power(self) -> float:
        return float(self.client.get_account().buying_power)

    def get_positions(self) -> Dict[str, float]:
        """{symbol: số lượng đang nắm}."""
        return {p.symbol: float(p.qty) for p in self.client.get_all_positions()}

    def get_open_order_symbols(self) -> set[str]:
        """Mã đang có lệnh chờ khớp (để không đặt trùng)."""
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        return {o.symbol for o in self.client.get_orders(filter=request)}

    def calculate_position_size(self, price: float, risk_amount: Optional[float] = None) -> int:
        """Số cổ phiếu = min(buying_power * risk%, max_position_size) / giá, tối thiểu 1."""
        if risk_amount is None:
            risk_amount = min(self.get_buying_power() * self.config.risk_per_trade,
                              self.config.max_position_size)
        return max(1, int(risk_amount / price))


class OrderManager:
    def __init__(self, client: TradingClient):
        self.client = client

    def _submit(self, request) -> bool:
        try:
            order = self.client.submit_order(request)
            logger.info(f"Đã đặt lệnh {order.side.value} {order.qty} {order.symbol} "
                        f"({order.type.value}), id={order.id}")
            return True
        except APIError as e:
            logger.error(f"Đặt lệnh thất bại {request.symbol}: {e}")
            return False

    def place_market_order(self, symbol: str, qty: int, side: str) -> bool:
        return self._submit(MarketOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide(side.lower()),
            time_in_force=TimeInForce.DAY,
        ))

    def place_limit_order(self, symbol: str, qty: int, side: str, limit_price: float) -> bool:
        return self._submit(LimitOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide(side.lower()),
            time_in_force=TimeInForce.DAY, limit_price=round(limit_price, 2),
        ))
