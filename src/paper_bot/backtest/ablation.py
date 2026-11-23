"""Bảng ablation (prereg mục 7-8): chạy nhiều cấu hình walk-forward, xuất bảng markdown.

Quy ước tên: "FULL" là hệ thống đầy đủ; "FULL bỏ X" là FULL bỏ đi thành phần X.
Thành phần X "có giá trị" nếu bỏ nó làm Sharpe giảm >= 0.1 so với FULL.
"""

from typing import Callable, Dict

import pandas as pd

from .metrics import compute_metrics
from .walkforward import run_walkforward

VALUE_THRESHOLD = 0.1


def run_ablation(factories: Dict[str, Callable], bars, **wf_kwargs) -> pd.DataFrame:
    """factories: tên -> policy_factory. wf_kwargs chuyển cho run_walkforward."""
    rows = {}
    for name, factory in factories.items():
        wf = run_walkforward(factory, bars, **wf_kwargs)
        rows[name] = compute_metrics(wf.result)
    table = pd.DataFrame.from_dict(rows, orient="index")
    table.index.name = "cấu hình"

    if "FULL" in table.index:
        table["Δsharpe_vs_FULL"] = table["sharpe"] - table.at["FULL", "sharpe"]
        table["kết luận"] = [
            ("có giá trị" if d <= -VALUE_THRESHOLD else "không chứng minh được giá trị")
            if name.startswith("FULL ") else ""
            for name, d in table["Δsharpe_vs_FULL"].items()
        ]
    return table


def _fmt(col: str, v) -> str:
    if isinstance(v, str):
        return v
    if col in ("cagr", "max_drawdown", "win_rate", "exposure"):
        return f"{v:.1%}"
    if col == "n_trades":
        return str(int(v))
    return f"{v:.2f}"


def to_markdown(table: pd.DataFrame) -> str:
    """Bảng markdown tự viết (pandas.to_markdown cần thư viện tabulate chưa cài)."""
    cols = [table.index.name or ""] + list(table.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for name, row in table.iterrows():
        cells = [str(name)] + [_fmt(c, row[c]) for c in table.columns]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
