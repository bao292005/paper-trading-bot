"""Test offline: không gọi Alpaca, dùng dữ liệu giá tự tạo + manager giả."""

import logging
from datetime import datetime

import numpy as np
import pandas as pd

from paper_bot.bot import TradingBot
from paper_bot.config import TradingConfig
from paper_bot.strategies import (
    BollingerBandsStrategy,
    MovingAverageCrossoverStrategy,
    RSIStrategy,
    TradingSignal,
    add_macd_indicator,
)


def make_df(closes):
    return pd.DataFrame({"close": np.asarray(closes, dtype=float)})


# ---------- Chiến lược ----------

def test_ma_crossover_buy_at_cross():
    # Giảm 40 phiên rồi tăng mạnh -> phiên cuối MA ngắn vừa cắt lên MA dài
    closes = list(np.linspace(100, 80, 40))
    strat = MovingAverageCrossoverStrategy(short_window=5, long_window=20)
    # tăng dần cho tới phiên đầu tiên có tín hiệu
    price = 80
    for _ in range(30):
        price += 3
        closes.append(price)
        sigs = strat.generate_signals(make_df(closes), "AAA")
        if sigs:
            break
    assert sigs and sigs[0].action == "BUY" and sigs[0].confidence == 0.7
    # phiên kế tiếp vẫn tăng -> không còn "vừa cắt" nên không có tín hiệu
    closes.append(price + 3)
    assert strat.generate_signals(make_df(closes), "AAA") == []


def test_ma_not_enough_data():
    assert MovingAverageCrossoverStrategy().generate_signals(make_df([1, 2, 3]), "A") == []


def test_rsi_oversold_and_overbought():
    strat = RSIStrategy()
    down = make_df(np.linspace(100, 70, 30) + np.tile([0, 0.5], 15))  # giảm đều -> RSI thấp
    up = make_df(np.linspace(70, 100, 30) - np.tile([0, 0.5], 15))  # tăng đều -> RSI cao
    assert strat.generate_signals(down, "A")[0].action == "BUY"
    assert strat.generate_signals(up, "A")[0].action == "SELL"


def test_rsi_formula_known_value():
    # 14 phiên: 7 tăng 1, 7 giảm 1 -> gain = loss -> RSI = 50
    closes = [100]
    for i in range(14):
        closes.append(closes[-1] + (1 if i % 2 == 0 else -1))
    rsi = RSIStrategy._calculate_rsi(pd.Series(closes, dtype=float), 14)
    assert abs(rsi.iloc[-1] - 50) < 1e-9


def test_bollinger_breakdown_buy():
    closes = [100 + (i % 2) for i in range(25)] + [90]
    assert BollingerBandsStrategy().generate_signals(make_df(closes), "A")[0].action == "BUY"


def test_macd_columns():
    out = add_macd_indicator(make_df(np.arange(50)))
    assert {"MACD", "MACD_signal"} <= set(out.columns)


# ---------- Bot: lọc confidence + vị thế ----------

class FakePortfolio:
    def __init__(self, positions, open_orders=()):
        self.positions = positions
        self.open_orders = set(open_orders)

    def get_positions(self):
        return self.positions

    def get_open_order_symbols(self):
        return self.open_orders

    def calculate_position_size(self, price):
        return 5


class FakeOrders:
    def __init__(self):
        self.orders = []

    def place_market_order(self, symbol, qty, side):
        self.orders.append((symbol, qty, side))
        return True


def make_bot(positions, open_orders=()):
    bot = TradingBot.__new__(TradingBot)  # bỏ qua __init__ (không tạo client thật)
    bot.config = TradingConfig(api_key="x", api_secret="y")
    bot.logger = logging.getLogger("test")
    bot.portfolio_manager = FakePortfolio(positions, open_orders)
    bot.order_manager = FakeOrders()
    return bot


def sig(action, conf):
    return TradingSignal("AAA", action, 100.0, datetime.now(), "test", conf)


def test_buy_when_flat():
    bot = make_bot({})
    bot.execute_signal_group("AAA", "BUY", [sig("BUY", 0.7)])
    assert bot.order_manager.orders == [("AAA", 5, "buy")]


def test_skip_low_confidence():
    bot = make_bot({})
    bot.execute_signal_group("AAA", "BUY", [sig("BUY", 0.3), sig("BUY", 0.6)])  # avg 0.45
    assert bot.order_manager.orders == []


def test_no_double_buy_and_sell_only_held():
    bot = make_bot({"AAA": 3})
    bot.execute_signal_group("AAA", "BUY", [sig("BUY", 0.9)])
    assert bot.order_manager.orders == []  # đã có vị thế -> không mua thêm
    bot.execute_signal_group("AAA", "SELL", [sig("SELL", 0.9)])
    assert bot.order_manager.orders == [("AAA", 3, "sell")]


def test_no_duplicate_when_order_pending():
    # Lệnh mua trước chưa khớp (chưa thành vị thế) -> không được mua lần nữa
    bot = make_bot({}, open_orders={"AAA"})
    bot.execute_signal_group("AAA", "BUY", [sig("BUY", 0.9)])
    assert bot.order_manager.orders == []


def test_no_short_sell_when_flat():
    bot = make_bot({})
    bot.execute_signal_group("AAA", "SELL", [sig("SELL", 0.9)])
    assert bot.order_manager.orders == []
