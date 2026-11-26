"""Test optimize (tester/optimize.py) trên lưới nhỏ, dữ liệu tổng hợp."""

import json

import pytest

from paper_bot.backtest.tester.optimize import (NOTE, apply_params, expand_grid, run_optimize)
from paper_bot.backtest.tester.run import run_tester
from paper_bot.backtest.walkforward import HoldoutError

from test_tester_run import CFG, SYMS, fake_bars

BARS = {s: fake_bars(i) for i, s in enumerate(SYMS)}
IS, OOS = ("2019-01-01", "2019-12-31"), ("2020-01-01", "2020-06-30")
GRID = {"ma_short": [5, 10], "ma_long": [20, 30], "risk.risk_per_trade": [0.02]}


def test_expand_grid_va_gioi_han():
    assert len(expand_grid(GRID)) == 4
    with pytest.raises(ValueError):
        expand_grid({"a": list(range(30)), "b": list(range(20))})  # 600 > 500
    with pytest.raises(ValueError):
        expand_grid({"x.y": [1]})
    cfg = apply_params(CFG, {"ma_short": 5, "risk.risk_per_trade": 0.01})
    assert cfg["strategy_params"] == {"ma_short": 5} and cfg["risk"] == {"risk_per_trade": 0.01}
    assert "strategy_params" not in CFG  # không sửa config gốc


def test_optimize_tuan_tu_dung_va_json():
    seen = []
    out = run_optimize(CFG, GRID, "sharpe", IS, OOS, n_jobs=1, bars=BARS,
                       progress=lambda d, t: seen.append((d, t)))
    json.dumps(out)
    assert seen[-1] == (4, 4) and out["n_combos"] == 4 and out["note"] == NOTE
    # số liệu khớp với chạy tay 1 tổ hợp
    r = next(r for r in out["rows"] if r["params"] == {"ma_short": 10, "ma_long": 30,
                                                        "risk.risk_per_trade": 0.02})
    cfg = {**CFG, "from": OOS[0], "to": OOS[1], "strategy_params": {"ma_short": 10, "ma_long": 30},
           "risk": {"risk_per_trade": 0.02}}
    assert r["oos"]["sharpe_ratio"] == run_tester(cfg, bars=BARS)["report"]["sharpe_ratio"]
    # rows sắp theo IS rank, best_is là dòng đầu, có OOS rank
    assert [r["is_rank"] for r in out["rows"]] == [1, 2, 3, 4]
    assert out["best_is"]["params"] == out["rows"][0]["params"]
    assert out["best_is"]["oos_rank"] == out["rows"][0]["oos_rank"]
    hm = out["heatmap"]
    assert (hm["x"], hm["y"]) == ("ma_short", "ma_long")
    assert len(hm["is"]) == 2 and len(hm["oos"][0]) == 2 and hm["is"][1][1] == r["is"]["sharpe_ratio"]


def test_optimize_song_song_giong_tuan_tu():
    a = run_optimize(CFG, GRID, "net_profit", IS, OOS, n_jobs=1, bars=BARS)
    b = run_optimize(CFG, GRID, "net_profit", IS, OOS, n_jobs=2, bars=BARS)
    assert a["rows"] == b["rows"]


def test_to_hop_loi_va_holdout():
    out = run_optimize(CFG, {"ma_short": [10, 40], "ma_long": [30]}, "sharpe", IS, OOS,
                       n_jobs=1, bars=BARS)
    bad = [r for r in out["rows"] if "error" in r]
    assert len(bad) == 1 and bad[0]["is_rank"] == 2 and out["best_is"]["params"]["ma_short"] == 10
    with pytest.raises(HoldoutError):
        run_optimize(CFG, GRID, oos_period=("2024-01-01", "2025-03-01"), n_jobs=1, bars=BARS)
    with pytest.raises(ValueError):
        run_optimize(CFG, GRID, criterion="winrate", n_jobs=1, bars=BARS)
