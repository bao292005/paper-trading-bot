"""Baseline theo prereg mục 5. Mỗi hàm make_* trả về một policy cho engine.

B0: mua và giữ SPY.
B1: chia đều tiền vào các mã, cân bằng lại mỗi đầu tháng.
B2: bot gốc của bài báo (MA 10/30 + RSI 14, ngưỡng 0.5, sizing min(2% cash, 1000$), không stop).
"""

from collections import defaultdict
from typing import List

import numpy as np
import pandas as pd

from ..config import BENCHMARK, UNIVERSE_50
from ..contracts import OrderIntent
from ..strategies import MovingAverageCrossoverStrategy, RSIStrategy


def _has_bar_today(view, symbol, asof) -> bool:
    df = view.bars(symbol)
    return not df.empty and df.index[-1] == asof


def make_b0(symbol: str = BENCHMARK):
    """B0: ngày đầu tiên có dữ liệu thì mua hết tiền vào `symbol`, rồi giữ."""
    def policy(asof, view, pf) -> List[OrderIntent]:
        if symbol in pf.positions or not _has_bar_today(view, symbol, asof):
            return []
        price = float(view.bars(symbol)["close"].iloc[-1])
        # Engine tự giảm qty nếu open ngày sau cao hơn / không đủ tiền trả phí
        return [OrderIntent(symbol, "buy", int(pf.cash / price), reason="B0 buy&hold", asof=asof)]
    return policy


def make_b1(symbols=None, cash_buffer: float = 0.005):
    """B1: chia đều equity cho các mã có dữ liệu, cân bằng lại ở close phiên đầu mỗi tháng.
    Giữ lại `cash_buffer` (0.5%) tiền mặt để trả phí và chênh lệch giá open."""
    symbols = list(symbols or UNIVERSE_50)
    state = {"month": None}

    def policy(asof, view, pf) -> List[OrderIntent]:
        month = (asof.year, asof.month)
        if month == state["month"]:
            return []
        state["month"] = month
        live = [s for s in symbols if _has_bar_today(view, s, asof)]
        if not live:
            return []
        target_value = pf.equity * (1 - cash_buffer) / len(live)
        sells, buys = [], []
        for s in live:
            price = float(view.bars(s)["close"].iloc[-1])
            held = pf.positions[s].qty if s in pf.positions else 0
            diff = int(target_value / price) - held
            if diff < 0:
                sells.append(OrderIntent(s, "sell", -diff, reason="B1 rebalance", asof=asof))
            elif diff > 0:
                buys.append(OrderIntent(s, "buy", diff, reason="B1 rebalance", asof=asof))
        return sells + buys
    return policy


def _fast_ma(c: np.ndarray, short: int = 10, long: int = 30):
    """Như MovingAverageCrossoverStrategy nhưng bằng numpy, chỉ tính 2 dòng cuối."""
    if len(c) < long + 1:
        return None
    # Làm tròn 1e-8: hai MA bằng nhau thật (hiếm nhưng có, vd PFE 2023-08-04) không bị
    # sai số dấu phẩy động biến thành "cắt nhau" (pandas rolling cho đúng 0 ở trường hợp đó)
    s, lg = round(c[-short:].mean(), 8), round(c[-long:].mean(), 8)
    ps, pl = round(c[-short - 1:-1].mean(), 8), round(c[-long - 1:-1].mean(), 8)
    if s > lg and ps <= pl:
        return ("BUY", 0.7, f"MA crossover: {s:.2f} > {lg:.2f}")
    if s < lg and ps >= pl:
        return ("SELL", 0.7, f"MA crossunder: {s:.2f} < {lg:.2f}")
    return None


def _fast_rsi(c: np.ndarray, period: int = 14, oversold: float = 30, overbought: float = 70):
    """Như RSIStrategy (RSI trung bình đơn giản 14 phiên) nhưng bằng numpy."""
    if len(c) < period + 1:
        return None
    d = np.diff(c[-(period + 1):])
    gain, loss = d[d > 0].sum() / period, -d[d < 0].sum() / period
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = float(100 - 100 / (1 + np.float64(gain) / loss))
    if np.isnan(rsi):
        return None
    if rsi < oversold:
        return ("BUY", min(0.9, 0.6 + (oversold - rsi) / 100), f"RSI oversold: {rsi:.1f}")
    if rsi > overbought:
        return ("SELL", min(0.9, 0.6 + (rsi - overbought) / 100), f"RSI overbought: {rsi:.1f}")
    return None


def make_b2(symbols=None, history_days: int = 100, min_confidence: float = 0.5,
            risk_per_trade: float = 0.02, max_position_size: float = 1000.0, fast: bool = True):
    """B2: lặp lại TradingBot.run_trading_cycle + execute_signal_group của bài gốc trên View.

    fast=True: tính MA/RSI bằng numpy (nhanh ~20 lần, cùng công thức, chỉ dùng dữ liệu <= asof).
    fast=False: gọi đúng các lớp chiến lược gốc (chậm, dùng để đối chiếu trong test).
    """
    symbols = list(symbols or UNIVERSE_50)
    strategies = [MovingAverageCrossoverStrategy(), RSIStrategy()]

    def signals(data, s):
        """Danh sách (action, confidence, reason) từ 2 chiến lược."""
        if fast:
            c = data["close"].to_numpy(dtype=float)
            return [x for x in (_fast_ma(c), _fast_rsi(c)) if x is not None]
        return [(x.action, x.confidence, x.reason)
                for strat in strategies for x in strat.generate_signals(data, s)]

    def policy(asof, view, pf) -> List[OrderIntent]:
        orders = []
        cash = pf.cash  # trừ dần khi đặt mua (như buying power giảm khi có lệnh chờ)
        for s in symbols:
            bars = view.bars(s)
            if bars.empty or bars.index[-1] != asof:
                continue
            # Bài gốc lấy `history_days` ngày LỊCH gần nhất
            first = bars.index.searchsorted(asof - pd.Timedelta(days=history_days), side="right")
            data = bars.iloc[first:]

            grouped = defaultdict(list)
            for action, conf, reason in signals(data, s):
                grouped[action].append((conf, reason))
            if len(grouped) != 1:  # không có tín hiệu, hoặc mâu thuẫn -> đứng ngoài
                continue
            action, sigs = next(iter(grouped.items()))
            if sum(conf for conf, _ in sigs) / len(sigs) < min_confidence:
                continue
            reason = sigs[0][1]

            held = pf.positions[s].qty if s in pf.positions else 0
            price = float(data["close"].iloc[-1])
            if action == "BUY" and held <= 0:
                qty = max(1, int(min(cash * risk_per_trade, max_position_size) / price))
                cash -= qty * price
                orders.append(OrderIntent(s, "buy", qty, reason=reason, asof=asof))
            elif action == "SELL" and held > 0:
                orders.append(OrderIntent(s, "sell", held, reason=reason, asof=asof))
        return orders
    return policy
