"""Dựng policy_factory cho Strategy Tester từ cấu hình: "B2_goc" hoặc "FULL".

B2 có tham số: baselines.make_b2 (chỉ đọc) cố định MA 10/30, RSI 14, nên ở đây viết lại đúng
logic đó nhưng nhận tham số. Với tham số mặc định, lệnh giống hệt make_b2(fast=True) (có test).
"""

import math
from collections import defaultdict
from typing import List

import pandas as pd

from ...contracts import OrderIntent
from ...decision.full_policy import DEFAULT_STRATEGY_PARAMS, make_full_policy_factory
from ...risk import RiskConfig
from ..baselines import _fast_ma, _fast_rsi

STRATEGIES = ("B2_goc", "FULL")
B2_RISK_DEFAULTS = {"risk_per_trade": 0.02, "max_position_size": 1000.0}
B2_EXTRA_PARAMS = {"min_score": 0.5, "history_days": None}  # None -> tự tính theo ma_long


def _check_keys(given: dict, allowed, what: str) -> None:
    unknown = set(given or {}) - set(allowed)
    if unknown:
        raise ValueError(f"{what} lạ: {sorted(unknown)} (hợp lệ: {sorted(allowed)})")


def make_b2_params(symbols, strategy_params=None, risk=None):
    """Policy B2 như make_b2 nhưng chỉnh được MA/RSI, ngưỡng, sizing.

    history_days mặc định = max(100, 1.5*ma_long + 20) ngày LỊCH: bài gốc lấy 100 ngày lịch
    (~69 phiên), không đủ cho MA dài 100 -> tự nới ra. Với ma_long=30 vẫn đúng 100 như bài gốc.
    """
    _check_keys(strategy_params, {**DEFAULT_STRATEGY_PARAMS, **B2_EXTRA_PARAMS}, "strategy_params")
    _check_keys(risk, B2_RISK_DEFAULTS, "risk (B2_goc)")
    p = {**DEFAULT_STRATEGY_PARAMS, **B2_EXTRA_PARAMS, **(strategy_params or {})}
    r = {**B2_RISK_DEFAULTS, **(risk or {})}
    short, long_ = int(p["ma_short"]), int(p["ma_long"])
    if not 0 < short < long_:
        raise ValueError(f"cần 0 < ma_short < ma_long, nhận {short}/{long_}")
    history_days = p["history_days"] or max(100, math.ceil(1.5 * long_ + 20))
    symbols = list(symbols)

    def signals(c):
        return [x for x in (_fast_ma(c, short, long_),
                            _fast_rsi(c, int(p["rsi_period"]), p["rsi_oversold"], p["rsi_overbought"]))
                if x is not None]

    def policy(asof, view, pf) -> List[OrderIntent]:
        orders = []
        cash = pf.cash
        for s in symbols:
            bars = view.bars(s)
            if bars.empty or bars.index[-1] != asof:
                continue
            first = bars.index.searchsorted(asof - pd.Timedelta(days=history_days), side="right")
            data = bars.iloc[first:]
            grouped = defaultdict(list)
            for action, conf, reason in signals(data["close"].to_numpy(dtype=float)):
                grouped[action].append((conf, reason))
            if len(grouped) != 1:
                continue
            action, sigs = next(iter(grouped.items()))
            if sum(conf for conf, _ in sigs) / len(sigs) < p["min_score"]:
                continue
            held = pf.positions[s].qty if s in pf.positions else 0
            price = float(data["close"].iloc[-1])
            if action == "BUY" and held <= 0:
                qty = max(1, int(min(cash * r["risk_per_trade"], r["max_position_size"]) / price))
                cash -= qty * price
                orders.append(OrderIntent(s, "buy", qty, reason=sigs[0][1], asof=asof))
            elif action == "SELL" and held > 0:
                orders.append(OrderIntent(s, "sell", held, reason=sigs[0][1], asof=asof))
        return orders
    return policy


def make_factory(strategy: str, symbols, flags=None, strategy_params=None, risk=None,
                 spy_bars=None, forecasts=None):
    """policy_factory(train_bars) -> policy, dùng được cho cả single lẫn walk-forward."""
    if strategy == "B2_goc":
        if flags:
            raise ValueError("B2_goc không có flags")
        policy = make_b2_params(symbols, strategy_params, risk)  # kiểm tra tham số ngay
        return lambda train_bars: policy
    if strategy == "FULL":
        sp = dict(strategy_params or {})
        min_score = sp.pop("min_score", 0.5)
        try:
            cfg = RiskConfig(**(risk or {}))
        except TypeError as e:
            raise ValueError(f"risk (FULL) lạ: {e}") from None
        return make_full_policy_factory(flags, risk_cfg=cfg, forecasts=forecasts, spy_bars=spy_bars,
                                        symbols=list(symbols), min_score=min_score,
                                        strategy_params=sp or None)
    raise ValueError(f"strategy phải thuộc {STRATEGIES}, nhận {strategy!r}")
