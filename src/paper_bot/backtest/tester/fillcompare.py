"""So sánh fill_model "daily_ohlc" và "minute" trên cùng một cấu hình (nhật ký bước 3)."""

from .run import run_tester

KEYS = ("total_net_profit", "sharpe_ratio", "profit_trades", "loss_trades",
        "equity_drawdown_maximal_pct")


def compare_fill_models(config: dict, **kw) -> dict:
    """Chạy 2 lần, trả về fill_stats, chỉ số chính của mỗi lần và các trade bị đổi kết cục."""
    daily = run_tester({**config, "fill_model": "daily_ohlc"}, **kw)
    minute = run_tester({**config, "fill_model": "minute"}, **kw)
    key = lambda t: (t["symbol"], t["entry_date"])  # noqa: E731
    before = {key(t): t for t in daily["trades"]}
    changed = [{"symbol": t["symbol"], "entry_date": t["entry_date"], "exit_date": t["exit_date"],
                "daily": before[key(t)]["exit_reason"], "minute": t["exit_reason"],
                "pnl_daily": before[key(t)]["pnl"], "pnl_minute": t["pnl"]}
               for t in minute["trades"]
               if key(t) in before and (before[key(t)]["exit_reason"], before[key(t)]["exit_date"])
               != (t["exit_reason"], t["exit_date"])]
    n_days = len(daily["curve"]) * len(daily["config"]["symbols"])
    return {
        "fill_stats": minute["fill_stats"],
        "ambiguous_pct_of_symbol_days": minute["fill_stats"]["ambiguous"] / n_days if n_days else 0.0,
        "ambiguous_pct_of_sl_tp_exits": _ratio(minute["fill_stats"]["ambiguous"], sum(
            t["exit_reason"] in ("stop_loss", "take_profit") for t in daily["trades"])),
        "daily": {k: daily["report"][k] for k in KEYS},
        "minute": {k: minute["report"][k] for k in KEYS},
        "changed_trades": changed,
        "timing_s": {"daily": daily["timing"]["total_s"], "minute": minute["timing"]["total_s"]},
    }


def _ratio(a, b):
    return a / b if b else None
