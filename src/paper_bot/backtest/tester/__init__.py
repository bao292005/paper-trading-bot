"""Strategy Tester kiểu MetaTrader 5: báo cáo, một lần chạy, so sánh fill model, tối ưu tham số.

Mọi hàm public trả về dict/list chỉ gồm kiểu JSON (str, int, float, bool, None)."""

from .fillcompare import compare_fill_models
from .optimize import run_optimize
from .report import balance_curve, mt5_report, report_markdown
from .run import run_tester

__all__ = ["balance_curve", "compare_fill_models", "mt5_report", "report_markdown",
           "run_optimize", "run_tester"]
