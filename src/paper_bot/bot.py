"""TradingBot: điều phối toàn bộ component."""

import logging
import time
from collections import defaultdict
from typing import List

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient

from .config import TradingConfig
from .managers import DataManager, OrderManager, PortfolioManager
from .strategies import (
    MovingAverageCrossoverStrategy,
    RSIStrategy,
    TradingSignal,
)


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler("trading_bot.log"), logging.StreamHandler()],
    )
    return logging.getLogger("paper_bot")


class TradingBot:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.logger = setup_logging()
        # paper=True được gắn cứng: bot này KHÔNG BAO GIỜ giao dịch tiền thật
        self.trading_client = TradingClient(config.api_key, config.api_secret, paper=True)
        data_client = StockHistoricalDataClient(config.api_key, config.api_secret)

        self.data_manager = DataManager(data_client)
        self.portfolio_manager = PortfolioManager(self.trading_client, config)
        self.order_manager = OrderManager(self.trading_client)
        self.strategies = [MovingAverageCrossoverStrategy(), RSIStrategy()]
        self.logger.info(f"Bot khởi tạo (PAPER), chiến lược: {[s.name for s in self.strategies]}")

    def is_market_open(self) -> bool:
        return self.trading_client.get_clock().is_open

    def run_trading_cycle(self) -> None:
        """Một vòng: lấy dữ liệu -> sinh tín hiệu -> gom theo hướng -> lọc confidence -> đặt lệnh."""
        self.logger.info("=== Bắt đầu vòng giao dịch ===")
        if not self.is_market_open():
            self.logger.warning("Thị trường đang ĐÓNG: lệnh market sẽ xếp hàng, khớp ở giá mở cửa phiên sau")
        for symbol in self.config.symbols:
            data = self.data_manager.get_historical_data(symbol, self.config.history_days)
            if data.empty:
                continue

            grouped: dict[str, List[TradingSignal]] = defaultdict(list)
            for strategy in self.strategies:
                for sig in strategy.generate_signals(data, symbol):
                    self.logger.info(f"[{strategy.name}] {symbol} {sig.action}: {sig.reason} "
                                     f"(conf={sig.confidence:.2f})")
                    grouped[sig.action].append(sig)

            # Nếu các chiến lược mâu thuẫn (vừa BUY vừa SELL) thì đứng ngoài
            if len(grouped) > 1:
                self.logger.warning(f"{symbol}: tín hiệu mâu thuẫn, bỏ qua")
                continue
            for action, signals in grouped.items():
                self.execute_signal_group(symbol, action, signals)
        self.logger.info("=== Kết thúc vòng giao dịch ===")

    def execute_signal_group(self, symbol: str, action: str, signals: List[TradingSignal]) -> None:
        avg_confidence = sum(s.confidence for s in signals) / len(signals)
        if avg_confidence < self.config.min_confidence:
            self.logger.info(f"Skipping {action} for {symbol} due to low confidence")
            return

        if symbol in self.portfolio_manager.get_open_order_symbols():
            self.logger.info(f"{symbol}: đã có lệnh chờ khớp, không đặt thêm")
            return

        current_qty = self.portfolio_manager.get_positions().get(symbol, 0)
        price = signals[-1].price

        if action == "BUY" and current_qty <= 0:
            qty = self.portfolio_manager.calculate_position_size(price)
            self.logger.info(f"MUA {qty} {symbol} @~{price:.2f} (conf={avg_confidence:.2f})")
            self.order_manager.place_market_order(symbol, qty, "buy")
        elif action == "SELL" and current_qty > 0:
            # Chỉ bán phần đang nắm, không bán khống
            self.logger.info(f"BÁN {current_qty:g} {symbol} @~{price:.2f} (conf={avg_confidence:.2f})")
            self.order_manager.place_market_order(symbol, int(current_qty), "sell")
        else:
            self.logger.info(f"{symbol}: {action} nhưng vị thế hiện tại={current_qty:g}, không làm gì")

    def run_continuous(self, interval_minutes: int = 30) -> None:
        """Chạy mãi, mỗi `interval_minutes` phút một vòng; thị trường đóng thì chờ. Ctrl+C để dừng."""
        try:
            while True:
                if self.is_market_open():
                    self.run_trading_cycle()
                else:
                    self.logger.info("Thị trường đang đóng cửa, chờ...")
                time.sleep(interval_minutes * 60)
        except KeyboardInterrupt:
            self.logger.info("Đã dừng bot.")

    def generate_performance_report(self) -> dict:
        account = self.trading_client.get_account()
        equity, last_equity = float(account.equity), float(account.last_equity)
        return {
            "total_equity": equity,
            "buying_power": float(account.buying_power),
            "positions": len(self.portfolio_manager.get_positions()),
            "day_gain": equity - last_equity,  # alpaca-py không có unrealized_pl ở account
        }
