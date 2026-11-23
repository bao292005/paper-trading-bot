"""Thước đo hiệu quả (prereg mục 6). Mọi số liệu tính SAU phí."""

import math

import pandas as pd

TRADING_DAYS = 252


def daily_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def sharpe(returns: pd.Series) -> float:
    """Sharpe năm hoá từ lợi suất ngày, rf = 0."""
    if len(returns) < 2:
        return 0.0
    std = returns.std(ddof=1)
    if std == 0 or math.isnan(std):
        return 0.0
    return float(returns.mean() / std * math.sqrt(TRADING_DAYS))


def years_between(equity: pd.Series) -> float:
    """Số năm lịch giữa ngày đầu và ngày cuối."""
    return (equity.index[-1] - equity.index[0]).days / 365.25


def cagr(equity: pd.Series) -> float:
    years = years_between(equity)
    if years <= 0 or equity.iloc[0] <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)


def max_drawdown(equity: pd.Series) -> float:
    """Mức sụt giảm lớn nhất từ đỉnh, số ÂM (vd -0.25 = -25%)."""
    return float((equity / equity.cummax() - 1).min())


def win_rate(trades) -> float:
    """Tỷ lệ trade có lãi sau phí. Không có trade -> 0."""
    if not trades:
        return 0.0
    return sum(t.pnl > 0 for t in trades) / len(trades)


def turnover(fills, equity: pd.Series) -> float:
    """Turnover năm hoá = (tổng giá trị mua+bán / 2) / equity trung bình / số năm.
    Ví dụ 1.0 = mỗi năm thay toàn bộ danh mục một lần."""
    years = years_between(equity)
    if years <= 0 or not fills:
        return 0.0
    traded = sum(f.qty * f.price for f in fills) / 2
    return float(traded / equity.mean() / years)


def compute_metrics(result) -> dict:
    """Gom mọi thước đo từ một BacktestResult."""
    eq = result.equity["equity"]
    return {
        "sharpe": sharpe(daily_returns(eq)),
        "cagr": cagr(eq),
        "max_drawdown": max_drawdown(eq),
        "n_trades": len(result.trades),
        "win_rate": win_rate(result.trades),
        "turnover": turnover(result.fills, eq),
        "exposure": float(result.equity["exposure"].mean()),
    }
