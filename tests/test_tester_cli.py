"""Test scripts/tester.py: markdown và ghi file (run_tester/run_optimize thay bằng bản offline)."""

import importlib.util
import json
from pathlib import Path

from paper_bot.backtest.tester import run_tester
from paper_bot.backtest.tester.optimize import run_optimize

from test_tester_run import CFG, SYMS, fake_bars

BARS = {s: fake_bars(i) for i, s in enumerate(SYMS)}
_spec = importlib.util.spec_from_file_location(
    "tester_cli", Path(__file__).resolve().parents[1] / "scripts" / "tester.py")
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def test_cli_run_ghi_json_md(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_tester", lambda c: run_tester(c, bars=BARS))
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps(CFG))
    assert cli.main(["run", str(cfg)]) == 0
    out = json.loads((tmp_path / "c-run.json").read_text())
    assert out["report"]["total_trades"] > 0
    assert "Total net profit" in (tmp_path / "c-run.md").read_text()


def test_cli_optimize_ghi_json_md(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_optimize",
                        lambda *a, **k: run_optimize(*a, **{**k, "n_jobs": 1, "bars": BARS}))
    spec = {"config": CFG, "grid": {"ma_short": [5, 10], "ma_long": [20, 40]},
            "is_period": ["2019-01-01", "2019-12-31"], "oos_period": ["2020-01-01", "2020-06-30"]}
    cfg = tmp_path / "o.json"
    cfg.write_text(json.dumps(spec))
    assert cli.main(["optimize", str(cfg), "--out", str(tmp_path / "kq")]) == 0
    out = json.loads((tmp_path / "kq.json").read_text())
    md = (tmp_path / "kq.md").read_text()
    assert out["n_combos"] == 4 and "overfit" in md and "Heatmap OOS" in md
    assert md.count("\n| ") >= 4 + 2 * 3  # 4 dòng tổ hợp + 2 heatmap (header + 2 hàng)
