"""Báo cáo kiểu MT5 Strategy Tester từ kết quả run_backtest / run_walkforward.

Quy ước (giống MT5, ghi rõ chỗ khác):
  - BALANCE chỉ đổi khi ĐÓNG lệnh (cộng P/L đã chốt); EQUITY = balance + lãi/lỗ chưa chốt
    (lấy từ cột equity của engine, đánh giá lại ở giá đóng cửa mỗi ngày).
  - P/L mỗi trade là SAU phí (Trade.pnl). Vị thế còn mở cuối kỳ (exit_reason "end") được coi như
    đóng ở close ngày cuối, trừ phí bán ước tính - như MT5 tự đóng lệnh khi hết test.
  - Engine chỉ mua (không bán khống) nên Short trades luôn = 0.
  - Sharpe: năm hoá từ lợi suất NGÀY của equity (metrics.sharpe, prereg mục 6), KHÔNG phải
    công thức Sharpe theo từng deal của MT5.
  - Balance chỉ ghi theo ngày: nhiều lệnh đóng cùng ngày được cộng gộp.
  - Không xác định được (vd profit factor khi không có lệnh lỗ) -> None (JSON null).
"""

from typing import Dict, List, Optional

import pandas as pd

from ..metrics import daily_returns, sharpe


def _sorted_trades(trades) -> list:
    """Trade theo thứ tự đóng lệnh (ổn định với các trade cùng ngày)."""
    return sorted(trades, key=lambda t: t.exit_date)


def balance_curve(trades, initial: float, days=None) -> pd.Series:
    """Balance theo ngày: initial + P/L đã chốt cộng dồn.

    days: danh sách ngày cần có trên đường cong (vd index của equity). Ngày không có lệnh
    đóng thì giữ nguyên balance hôm trước. None -> chỉ các ngày có lệnh đóng.
    """
    closed = [t for t in trades if t.exit_date is not None]
    pnl = pd.Series([t.pnl for t in closed], index=[pd.Timestamp(t.exit_date) for t in closed],
                    dtype=float)
    per_day = pnl.groupby(level=0).sum().sort_index()
    if days is not None:
        # union: không làm mất P/L nếu có lệnh đóng ngoài `days`
        per_day = per_day.reindex(pd.DatetimeIndex(days).union(per_day.index), fill_value=0.0)
    out = initial + per_day.cumsum()
    out.index.name = "date"
    return out.rename("balance")


def _ratio(a: float, b: float) -> Optional[float]:
    return float(a / b) if b else None


def drawdown_stats(values, initial: float) -> Dict[str, float]:
    """Drawdown kiểu MT5 cho một đường balance/equity (đỉnh tính cả `initial` ban đầu).

    absolute     = initial - đáy thấp nhất dưới initial (0 nếu chưa từng xuống dưới).
    maximal      = sụt lớn nhất tính bằng $ từ một đỉnh, kèm % so với đỉnh đó.
    relative     = sụt lớn nhất tính bằng %, kèm số $ tương ứng.
    """
    vals = pd.Series([initial] + [float(v) for v in values])
    peak = vals.cummax()
    dd = peak - vals
    pct = (dd / peak).where(peak > 0, 0.0)
    i_abs, i_pct = int(dd.idxmax()), int(pct.idxmax())
    return {
        "absolute": float(max(0.0, initial - vals.min())),
        "maximal": float(dd.iloc[i_abs]),
        "maximal_pct": float(pct.iloc[i_abs]),
        "relative_pct": float(pct.iloc[i_pct]),
        "relative": float(dd.iloc[i_pct]),
    }


def _streaks(pnls: List[float], sign: int) -> List[List[float]]:
    """Các chuỗi liên tiếp cùng dấu (sign=1: thắng >0, sign=-1: thua <0). Trade P/L = 0 ngắt chuỗi."""
    out, cur = [], []
    for p in pnls:
        if p * sign > 0:
            cur.append(p)
        else:
            if cur:
                out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def streak_stats(pnls: List[float], sign: int) -> Dict[str, Optional[float]]:
    """max_count/max_count_money: chuỗi DÀI nhất (nếu bằng nhau lấy chuỗi đầu tiên);
    max_money/max_money_count: chuỗi có tổng $ lớn nhất (theo trị tuyệt đối);
    avg_count: độ dài trung bình các chuỗi."""
    s = _streaks(pnls, sign)
    if not s:
        return {"max_count": 0, "max_count_money": 0.0, "max_money": 0.0,
                "max_money_count": 0, "avg_count": 0.0}
    longest = max(s, key=len)
    biggest = max(s, key=lambda x: abs(sum(x)))
    return {"max_count": len(longest), "max_count_money": float(sum(longest)),
            "max_money": float(sum(biggest)), "max_money_count": len(biggest),
            "avg_count": float(sum(len(x) for x in s) / len(s))}


def mt5_report(result, initial_cash: float) -> dict:
    """Báo cáo kiểu MT5. `result`: BacktestResult hoặc WalkForwardResult (lấy .result)."""
    res = getattr(result, "result", result)
    trades = _sorted_trades(res.trades)
    pnls = [float(t.pnl) for t in trades]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    gross_profit, gross_loss = float(sum(wins)), float(sum(losses))
    net = gross_profit + gross_loss
    n = len(trades)

    eq = res.equity["equity"] if len(res.equity) else pd.Series(dtype=float)
    bal = balance_curve(trades, initial_cash, eq.index if len(eq) else None)
    bal_dd = drawdown_stats(bal.to_numpy(), initial_cash)
    eq_dd = drawdown_stats(eq.to_numpy(), initial_cash)
    hold_days = [(pd.Timestamp(t.exit_date) - pd.Timestamp(t.entry_date)).days for t in trades]
    win_streak, loss_streak = streak_stats(pnls, 1), streak_stats(pnls, -1)

    return {
        "period_start": eq.index[0].date().isoformat() if len(eq) else None,
        "period_end": eq.index[-1].date().isoformat() if len(eq) else None,
        "bars": int(len(eq)),
        "initial_deposit": float(initial_cash),
        "final_balance": float(initial_cash + net),
        "final_equity": float(eq.iloc[-1]) if len(eq) else float(initial_cash),
        "total_net_profit": net,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": _ratio(gross_profit, -gross_loss),
        "expected_payoff": _ratio(net, n),
        "recovery_factor": _ratio(net, eq_dd["maximal"]),
        "sharpe_ratio": sharpe(daily_returns(eq)) if len(eq) > 1 else 0.0,
        "balance_drawdown_absolute": bal_dd["absolute"],
        "balance_drawdown_maximal": bal_dd["maximal"],
        "balance_drawdown_maximal_pct": bal_dd["maximal_pct"],
        "balance_drawdown_relative_pct": bal_dd["relative_pct"],
        "balance_drawdown_relative": bal_dd["relative"],
        "equity_drawdown_absolute": eq_dd["absolute"],
        "equity_drawdown_maximal": eq_dd["maximal"],
        "equity_drawdown_maximal_pct": eq_dd["maximal_pct"],
        "equity_drawdown_relative_pct": eq_dd["relative_pct"],
        "equity_drawdown_relative": eq_dd["relative"],
        "total_trades": n,
        "short_trades": 0,
        "short_won_pct": None,
        "long_trades": n,
        "long_won_pct": _ratio(len(wins), n),
        "profit_trades": len(wins),
        "profit_trades_pct": _ratio(len(wins), n),
        "loss_trades": len(losses),
        "loss_trades_pct": _ratio(len(losses), n),
        "largest_profit_trade": max(wins) if wins else 0.0,
        "largest_loss_trade": min(losses) if losses else 0.0,
        "average_profit_trade": _ratio(gross_profit, len(wins)) or 0.0,
        "average_loss_trade": _ratio(gross_loss, len(losses)) or 0.0,
        "max_consecutive_wins": win_streak["max_count"],
        "max_consecutive_wins_money": win_streak["max_count_money"],
        "max_consecutive_losses": loss_streak["max_count"],
        "max_consecutive_losses_money": loss_streak["max_count_money"],
        "maximal_consecutive_profit": win_streak["max_money"],
        "maximal_consecutive_profit_count": win_streak["max_money_count"],
        "maximal_consecutive_loss": loss_streak["max_money"],
        "maximal_consecutive_loss_count": loss_streak["max_money_count"],
        "average_consecutive_wins": win_streak["avg_count"],
        "average_consecutive_losses": loss_streak["avg_count"],
        "average_holding_days": _ratio(sum(hold_days), n),
        "total_fees": float(sum(t.fee for t in trades)),
        "open_at_end": sum(t.exit_reason == "end" for t in trades),
    }


def _money(v) -> str:
    return "—" if v is None else f"{v:,.2f}"


def _pct(v) -> str:
    return "—" if v is None else f"{v:.2%}"


def _num(v) -> str:
    return "—" if v is None else f"{v:.2f}"


def report_markdown(rep: dict) -> str:
    """Bảng markdown theo bố cục báo cáo MT5 (2 cột: chỉ số | giá trị)."""
    r = rep
    rows = [
        ("Period", f"{r['period_start']} → {r['period_end']} ({r['bars']} ngày)"),
        ("Initial deposit", _money(r["initial_deposit"])),
        ("Total net profit", _money(r["total_net_profit"])),
        ("Gross profit", _money(r["gross_profit"])),
        ("Gross loss", _money(r["gross_loss"])),
        ("Profit factor", _num(r["profit_factor"])),
        ("Expected payoff", _money(r["expected_payoff"])),
        ("Recovery factor", _num(r["recovery_factor"])),
        ("Sharpe ratio (năm hoá, theo ngày)", _num(r["sharpe_ratio"])),
        ("Balance drawdown absolute", _money(r["balance_drawdown_absolute"])),
        ("Balance drawdown maximal", f"{_money(r['balance_drawdown_maximal'])} "
                                     f"({_pct(r['balance_drawdown_maximal_pct'])})"),
        ("Balance drawdown relative", f"{_pct(r['balance_drawdown_relative_pct'])} "
                                      f"({_money(r['balance_drawdown_relative'])})"),
        ("Equity drawdown absolute", _money(r["equity_drawdown_absolute"])),
        ("Equity drawdown maximal", f"{_money(r['equity_drawdown_maximal'])} "
                                    f"({_pct(r['equity_drawdown_maximal_pct'])})"),
        ("Equity drawdown relative", f"{_pct(r['equity_drawdown_relative_pct'])} "
                                     f"({_money(r['equity_drawdown_relative'])})"),
        ("Total trades", str(r["total_trades"])),
        ("Short trades (won %)", f"{r['short_trades']} ({_pct(r['short_won_pct'])})"),
        ("Long trades (won %)", f"{r['long_trades']} ({_pct(r['long_won_pct'])})"),
        ("Profit trades (% of total)", f"{r['profit_trades']} ({_pct(r['profit_trades_pct'])})"),
        ("Loss trades (% of total)", f"{r['loss_trades']} ({_pct(r['loss_trades_pct'])})"),
        ("Largest profit trade", _money(r["largest_profit_trade"])),
        ("Largest loss trade", _money(r["largest_loss_trade"])),
        ("Average profit trade", _money(r["average_profit_trade"])),
        ("Average loss trade", _money(r["average_loss_trade"])),
        ("Maximum consecutive wins ($)", f"{r['max_consecutive_wins']} "
                                         f"({_money(r['max_consecutive_wins_money'])})"),
        ("Maximum consecutive losses ($)", f"{r['max_consecutive_losses']} "
                                           f"({_money(r['max_consecutive_losses_money'])})"),
        ("Maximal consecutive profit (count)", f"{_money(r['maximal_consecutive_profit'])} "
                                               f"({r['maximal_consecutive_profit_count']})"),
        ("Maximal consecutive loss (count)", f"{_money(r['maximal_consecutive_loss'])} "
                                             f"({r['maximal_consecutive_loss_count']})"),
        ("Average consecutive wins / losses", f"{_num(r['average_consecutive_wins'])} / "
                                              f"{_num(r['average_consecutive_losses'])}"),
        ("Average holding time (ngày lịch)", _num(r["average_holding_days"])),
        ("Commission / phí tổng", _money(r["total_fees"])),
        ("Lệnh còn mở cuối kỳ (tính như đóng)", str(r["open_at_end"])),
    ]
    lines = ["| Chỉ số | Giá trị |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    return "\n".join(lines)
