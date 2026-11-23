"""P3 - Quản lý rủi ro: các hàm THUẦN (không gọi mạng) để backtest và bot thật dùng chung.

Bài gốc: cỡ lệnh = min(2% sức mua, 1000$) / giá -> "2%" thực ra là cỡ vị thế, không phải rủi ro.
Ở đây: rủi ro mỗi lệnh = % vốn bị mất NẾU chạm stop-loss (stop cách giá atr_stop_mult * ATR).
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from .contracts import Forecast, MarketState, OrderIntent, Signal

logger = logging.getLogger("paper_bot")


@dataclass
class RiskConfig:
    risk_per_trade: float = 0.01  # % vốn mất nếu chạm stop-loss
    atr_stop_mult: float = 2.0  # stop-loss = giá - 2 * ATR
    take_profit_mult: float = 3.0  # take-profit = giá + 3 * ATR
    max_position_pct: float = 0.10  # 1 vị thế tối đa 10% vốn
    max_positions: int = 10  # tối đa số mã nắm cùng lúc
    corr_window: int = 60  # số phiên để tính tương quan
    max_pair_corr: float = 0.8  # không mua mã tương quan > 0.8 với mã đang nắm
    max_daily_loss_pct: float = 0.03  # kill switch: lỗ trong ngày > 3%
    max_drawdown_pct: float = 0.15  # kill switch: sụt từ đỉnh > 15%
    atr_period: int = 14
    min_scale: float = 0.25  # hệ số nhỏ nhất của uncertainty_scale


def atr(bars: pd.DataFrame, n: int = 14) -> float:
    """ATR (biên độ thực trung bình n phiên) tại dòng CUỐI của bars. Không đủ dữ liệu -> NaN.
    Chỉ dùng dữ liệu tới dòng cuối nên không rò rỉ tương lai."""
    if len(bars) < n + 1:
        return float("nan")
    prev_close = bars["close"].shift(1)
    true_range = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(true_range.rolling(n).mean().iloc[-1])


def legacy_position_size(buying_power: float, price: float,
                         risk_per_trade: float = 0.02, max_position_size: float = 1000.0) -> int:
    """Công thức bài gốc (baseline B2): min(sức mua * 2%, 1000$) / giá, tối thiểu 1 cổ."""
    risk_amount = min(buying_power * risk_per_trade, max_position_size)
    return max(1, int(risk_amount / price))


def atr_position_size(equity: float, price: float, atr: float, cfg: RiskConfig, cash: float) -> int:
    """Số cổ = (vốn * risk_per_trade) / (atr_stop_mult * ATR), rồi chặn bởi:
    trần max_position_pct * vốn / giá, và tiền mặt / giá. Có thể ra 0 (không ép mua 1 cổ)."""
    if price <= 0 or atr is None or math.isnan(atr) or atr <= 0:
        return 0
    by_risk = equity * cfg.risk_per_trade / (cfg.atr_stop_mult * atr)
    by_cap = cfg.max_position_pct * equity / price
    by_cash = cash / price
    return max(0, int(min(by_risk, by_cap, by_cash)))


def uncertainty_scale(forecast: Optional[Forecast], ref_width: Optional[float],
                      min_scale: float = 0.25) -> float:
    """Hệ số trong [min_scale, 1]: forecast càng bất định (width lớn hơn ref_width) càng giảm cỡ.
    scale = ref_width / width. Không có forecast hoặc ref_width -> 1 (không giảm)."""
    if forecast is None or not ref_width or ref_width <= 0:
        return 1.0
    width = forecast.width
    if width <= ref_width:
        return 1.0
    return max(min_scale, ref_width / width)


def ref_width_from(forecasts: List[Optional[Forecast]]) -> Optional[float]:
    """Độ rộng tham chiếu = trung vị `width` của các forecast (bỏ None).
    Người gọi chỉ nên đưa forecast của giai đoạn <= asof. Không có forecast nào -> None."""
    widths = [f.width for f in forecasts if f is not None]
    return float(pd.Series(widths).median()) if widths else None


def bracket_levels(price: float, atr: float, cfg: RiskConfig) -> Tuple[float, float]:
    """(stop_loss, take_profit) cho lệnh MUA: stop = giá - k*ATR, take-profit = giá + m*ATR."""
    stop_loss = max(0.01, price - cfg.atr_stop_mult * atr)
    take_profit = price + cfg.take_profit_mult * atr
    return round(stop_loss, 2), round(take_profit, 2)


def correlation_ok(candidate: str, held: List[str], returns: pd.DataFrame,
                   cfg: RiskConfig) -> Tuple[bool, str]:
    """Đa dạng hoá: từ chối mã có tương quan lợi suất > max_pair_corr với mã đang nắm.
    `returns`: lợi suất ngày, mỗi cột một mã, chỉ gồm dữ liệu <= asof (người gọi đảm bảo).
    Thiếu dữ liệu của mã nào thì bỏ qua cặp đó."""
    if candidate not in returns.columns:
        return True, "không có lợi suất của mã ứng viên, bỏ qua kiểm tra tương quan"
    recent = returns.tail(cfg.corr_window)
    for other in held:
        if other == candidate or other not in recent.columns:
            continue
        corr = recent[candidate].corr(recent[other])
        if pd.notna(corr) and corr > cfg.max_pair_corr:
            return False, f"tương quan {candidate}-{other} = {corr:.2f} > {cfg.max_pair_corr}"
    return True, "ok"


def _skip(symbol: str, why: str) -> None:
    logger.info(f"{symbol}: bỏ qua lệnh - {why}")
    return None


def make_order_intent(signal: Signal, state: MarketState, equity: float, cash: float,
                      positions: Dict[str, float], open_orders: Set[str], cfg: RiskConfig,
                      returns: Optional[pd.DataFrame] = None,
                      ref_width: Optional[float] = None) -> Optional[OrderIntent]:
    """Gộp mọi luật rủi ro: Signal -> OrderIntent (hoặc None kèm lý do trong log).
    positions = {mã: số cổ đang nắm}, open_orders = các mã đang có lệnh chờ."""
    symbol = signal.symbol
    if signal.veto:
        return _skip(symbol, f"tín hiệu bị veto ({signal.veto_reason})")
    if symbol in open_orders:
        return _skip(symbol, "đã có lệnh chờ khớp")

    held_qty = positions.get(symbol, 0)
    if signal.action == "SELL":
        if held_qty <= 0:
            return _skip(symbol, "SELL nhưng chưa có vị thế (không bán khống)")
        return OrderIntent(symbol, "sell", int(held_qty), reason=signal.reason, asof=state.asof)

    if signal.action != "BUY":
        return _skip(symbol, f"action lạ: {signal.action}")
    if held_qty > 0:
        return _skip(symbol, "đã có vị thế (không mua trùng)")
    held = [s for s, q in positions.items() if q > 0]
    if len(held) >= cfg.max_positions:
        return _skip(symbol, f"đã đủ {cfg.max_positions} vị thế")
    if returns is not None:
        ok, why = correlation_ok(symbol, held, returns, cfg)
        if not ok:
            return _skip(symbol, why)

    price = float(state.bars["close"].iloc[-1])
    atr_value = atr(state.bars, cfg.atr_period)
    base_qty = atr_position_size(equity, price, atr_value, cfg, cash)
    scale = uncertainty_scale(state.forecast, ref_width, cfg.min_scale)
    qty = int(base_qty * scale)
    if qty <= 0:
        return _skip(symbol, f"cỡ lệnh = 0 (ATR={atr_value:.3g}, cash={cash:.0f}, scale={scale:.2f})")

    stop_loss, take_profit = bracket_levels(price, atr_value, cfg)
    reason = f"{signal.reason} | qty={qty} (ATR sizing x{scale:.2f})"
    return OrderIntent(symbol, "buy", qty, stop_loss=stop_loss, take_profit=take_profit,
                       reason=reason, asof=state.asof)
