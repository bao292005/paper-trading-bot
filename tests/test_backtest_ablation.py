"""Test bảng ablation."""

import numpy as np
import pandas as pd

from paper_bot.backtest.ablation import run_ablation, to_markdown
from paper_bot.backtest.baselines import make_b0


def bars(start="2016-01-01", end="2024-12-31"):
    idx = pd.bdate_range(start, end, name="date")
    rng = np.random.default_rng(0)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, len(idx))))
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": 1e6}, index=idx)


def test_ablation_table_and_markdown():
    data = {"AAA": bars()}
    factories = {
        "FULL": lambda tb: make_b0("AAA"),          # đầu tư hết
        "FULL bỏ X": lambda tb: (lambda a, v, p: []),  # không giao dịch -> sharpe 0
    }
    table = run_ablation(factories, data, start="2023-01-01", end="2024-12-31")
    assert list(table.index) == ["FULL", "FULL bỏ X"]
    assert table.at["FULL bỏ X", "sharpe"] == 0.0
    assert table.at["FULL", "Δsharpe_vs_FULL"] == 0.0
    assert table.at["FULL", "kết luận"] == ""
    expected = ("có giá trị" if table.at["FULL", "sharpe"] >= 0.1
                else "không chứng minh được giá trị")
    assert table.at["FULL bỏ X", "kết luận"] == expected

    md = to_markdown(table)
    lines = md.splitlines()
    assert lines[0].startswith("| cấu hình | sharpe | cagr")
    assert len(lines) == 4
    assert "%" in lines[2]
