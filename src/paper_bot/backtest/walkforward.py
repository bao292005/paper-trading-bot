"""Walk-forward theo prereg mục 4: train 3 năm, test 6 tháng, trượt 6 tháng, 2019-01 → 2024-12.

Cách chạy: mỗi cửa sổ gọi policy_factory(train_bars) -> policy (fit CHỈ trên dữ liệu train).
Sau đó chạy MỘT backtest liên tục từ đầu test đầu tiên tới cuối test cuối; tại mỗi ngày dùng
policy của cửa sổ chứa ngày đó. Danh mục được giữ nguyên khi chuyển cửa sổ (không bán hết
rồi mua lại), nên equity các đoạn test ghép liền nhau một cách tự nhiên.

CHỐT AN TOÀN: mọi ngày >= 2025-01-01 (HOLDOUT) bị cấm trừ khi truyền holdout=True.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import pandas as pd

from ..config import asset_profile
from .engine import BacktestResult, run_backtest
from .metrics import compute_metrics

HOLDOUT_START = pd.Timestamp("2025-01-01")
DEV_START, DEV_END = "2019-01-01", "2024-12-31"


class HoldoutError(RuntimeError):
    """Cố dùng dữ liệu HOLDOUT khi chưa cho phép."""


@dataclass
class Window:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def check_holdout(last_day, holdout: bool) -> None:
    if not holdout and pd.Timestamp(last_day) >= HOLDOUT_START:
        raise HoldoutError(f"Ngày {pd.Timestamp(last_day).date()} thuộc HOLDOUT (>= 2025-01-01). "
                           "Chỉ chạy holdout 1 lần khi cấu hình đã khoá: truyền holdout=True.")


def make_windows(start=DEV_START, end=DEV_END, train_years: Optional[int] = 3, test_months: int = 6,
                 step_months: int = 6, holdout: bool = False, train_start=None) -> List[Window]:
    """train_years=None -> train MỞ RỘNG: mọi cửa sổ train bắt đầu từ `train_start` (crypto)."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    check_holdout(end, holdout)
    if train_years is None and train_start is None:
        raise ValueError("train_years=None (train mở rộng) cần train_start")
    windows, test_start = [], start
    while test_start <= end:
        test_end = min(test_start + pd.DateOffset(months=test_months) - pd.Timedelta(days=1), end)
        first = (pd.Timestamp(train_start) if train_years is None
                 else test_start - pd.DateOffset(years=train_years))
        windows.append(Window(first, test_start - pd.Timedelta(days=1), test_start, test_end))
        test_start = test_start + pd.DateOffset(months=step_months)
    return windows


def slice_bars(bars: Dict[str, pd.DataFrame], start, end) -> Dict[str, pd.DataFrame]:
    return {s: df.loc[(df.index >= start) & (df.index <= end)] for s, df in bars.items()}


@dataclass
class WalkForwardResult:
    result: BacktestResult  # equity/trade/fill của toàn bộ các đoạn test ghép lại
    windows: List[Window]
    per_window: pd.DataFrame  # metric từng cửa sổ test


def run_walkforward(policy_factory: Callable, bars: Dict[str, pd.DataFrame],
                    start=None, end=None, holdout: bool = False, asset: str = "stock",
                    **backtest_kwargs) -> WalkForwardResult:
    """backtest_kwargs chuyển thẳng cho run_backtest (initial_cash, cost_bps, store).

    asset: "stock" (như cũ: train 3 năm trượt, Sharpe 252) | "crypto" (train mở rộng từ 2021-01,
      Sharpe 365, mua lẻ 6 chữ số). start/end None -> giai đoạn phát triển của asset."""
    prof = asset_profile(asset)
    start = prof["dev_start"] if start is None else start
    end = prof["dev_end"] if end is None else end
    if prof["fractional"]:
        backtest_kwargs.setdefault("qty_decimals", prof["qty_decimals"])
    windows = make_windows(start, end, train_years=prof["train_years"], test_months=prof["test_months"],
                           holdout=holdout, train_start=prof["warmup_start"])
    last_day = windows[-1].test_end
    check_holdout(last_day, holdout)
    # Phòng thủ 2 lớp: cắt bỏ hẳn dữ liệu sau ngày test cuối cùng
    bars = {s: df.loc[df.index <= last_day] for s, df in bars.items()}

    policies = [policy_factory(slice_bars(bars, w.train_start, w.train_end)) for w in windows]

    def dispatch(asof, view, pf):
        for w, pol in zip(windows, policies):
            if w.test_start <= asof <= w.test_end:
                return pol(asof, view, pf)
        return []

    res = run_backtest(bars, windows[0].test_start, last_day, dispatch, **backtest_kwargs)

    rows = []
    for w in windows:
        eq = res.equity.loc[w.test_start:w.test_end]
        if len(eq) < 2:
            continue
        part = BacktestResult(eq, [t for t in res.trades if w.test_start <= t.exit_date <= w.test_end],
                              [f for f in res.fills
                               if w.test_start <= pd.Timestamp(f.filled_at) <= w.test_end])
        rows.append({"test_start": w.test_start.date(), "test_end": w.test_end.date(),
                     **compute_metrics(part, prof["periods_per_year"])})
    return WalkForwardResult(res, windows, pd.DataFrame(rows))
