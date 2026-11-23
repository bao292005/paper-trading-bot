"""Bảng ablation (prereg mục 7-8) trên giai đoạn PHÁT TRIỂN 2019-01 → 2024-12, phí 10 bps.

Cách chạy:  uv run python scripts/run_ablation.py
Kết quả:    docs/ket-qua/ablation-dev.md + data/trading.db (run_id bt-abl-<tên>)

KHÔNG chạy holdout: chỉ đọc dữ liệu tới 2024-12-31, không truyền holdout=True.
Calibrator giống hệt nhau giữa các dòng (chỉ phụ thuộc cửa sổ train + có forecast hay không)
được fit 1 lần rồi dùng chung qua calib_cache.
"""

import logging
import time
from pathlib import Path

import pandas as pd

from paper_bot.backtest.ablation import VALUE_THRESHOLD, to_markdown
from paper_bot.backtest.baselines import make_b0, make_b1, make_b2
from paper_bot.backtest.metrics import compute_metrics
from paper_bot.backtest.report import save_result
from paper_bot.backtest.walkforward import DEV_END, DEV_START, run_walkforward
from paper_bot.config import BENCHMARK, UNIVERSE_50
from paper_bot.data.cache import load_bars
from paper_bot.data.forecast import load_forecasts
from paper_bot.decision.full_policy import make_full_policy_factory
from paper_bot.storage import DEFAULT_DB, Store

OUT = Path(__file__).resolve().parents[1] / "docs" / "ket-qua" / "ablation-dev.md"
CASH, BPS = 100_000.0, 10

# (tên trong bảng, đuôi run_id, flag tắt)
FULL_ROWS = [
    ("FULL", "FULL", {}),
    ("FULL bỏ forecast", "no-forecast", {"use_forecast": False}),
    ("FULL bỏ regime switching", "no-regime", {"use_regime": False}),
    ("FULL bỏ hiệu chỉnh xác suất", "no-calibration", {"use_calibration": False}),
    ("FULL bỏ sizing ATR", "no-atr-sizing", {"use_atr_sizing": False}),
    ("FULL bỏ bracket/stop", "no-bracket", {"use_bracket": False}),
    ("FULL bỏ giới hạn tương quan", "no-corr-limit", {"use_corr_limit": False}),
]


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def timed_factory(name, factory):
    """Bọc factory để in tiến độ fit từng cửa sổ."""
    def wrapped(train_bars):
        t = time.time()
        pol = factory(train_bars)
        end = max(df.index[-1] for df in train_bars.values() if len(df))
        log(f"  [{name}] fit cửa sổ train tới {end.date()} mất {time.time() - t:.1f}s")
        return pol
    return wrapped


def main():
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    t0 = time.time()
    bars = {s: load_bars(s, end=DEV_END) for s in UNIVERSE_50 + [BENCHMARK]}
    assert all(len(df) for df in bars.values()), "thiếu cache"
    stocks = {s: bars[s] for s in UNIVERSE_50}
    fc = load_forecasts()
    log(f"Đọc {len(bars)} mã + {len(fc)} dòng forecast trong {time.time() - t0:.1f}s")

    cache = {}
    configs = [(name, slug, make_full_policy_factory(flags, forecasts=fc, calib_cache=cache), bars)
               for name, slug, flags in FULL_ROWS]
    configs += [("B0 (SPY)", "B0", lambda tb: make_b0(), {BENCHMARK: bars[BENCHMARK]}),
                ("B1 (chia đều 50 mã)", "B1", lambda tb: make_b1(), stocks),
                ("B2 (bot gốc)", "B2", lambda tb: make_b2(), stocks)]

    rows, per_window, timing, exits = {}, {}, {}, {}
    for name, slug, factory, data in configs:
        t = time.time()
        log(f"Bắt đầu {name}")
        wf = run_walkforward(timed_factory(name, factory), data, DEV_START, DEV_END,
                             initial_cash=CASH, cost_bps=BPS)
        timing[name] = time.time() - t
        m = compute_metrics(wf.result)
        save_result(wf.result, Store(DEFAULT_DB, run_id=f"bt-abl-{slug}"), {**m, "cost_bps": BPS})
        rows[name] = m
        per_window[name] = wf.per_window.set_index("test_start")["sharpe"]
        exits[name] = pd.Series([tr.exit_reason for tr in wf.result.trades]).value_counts().to_dict()
        log(f"Xong {name}: sharpe={m['sharpe']:.2f} cagr={m['cagr']:.1%} "
            f"maxDD={m['max_drawdown']:.1%} trades={m['n_trades']} ({timing[name]:.0f}s)")

    write_report(rows, per_window, timing, exits, time.time() - t0)
    log(f"Đã ghi {OUT}. Tổng {time.time() - t0:.0f}s")


def write_report(rows, per_window, timing, exits, total):
    table = pd.DataFrame.from_dict(rows, orient="index")
    table.index.name = "cấu hình"
    full = table.at["FULL", "sharpe"]
    table["Δsharpe_vs_FULL"] = table["sharpe"] - full
    table["kết luận"] = [("có giá trị" if d <= -VALUE_THRESHOLD else "không chứng minh được giá trị")
                         if n.startswith("FULL ") else "" for n, d in table["Δsharpe_vs_FULL"].items()]
    table["time_s"] = [f"{timing[n]:.0f}" for n in table.index]
    cols = ["sharpe", "cagr", "max_drawdown", "n_trades", "exposure", "win_rate", "turnover",
            "Δsharpe_vs_FULL", "kết luận", "time_s"]

    b2, b1_dd, f_dd = table.at["B2 (bot gốc)", "sharpe"], table.at["B1 (chia đều 50 mã)", "max_drawdown"], \
        table.at["FULL", "max_drawdown"]
    c1, c2 = full >= b2 + 0.2, f_dd >= b1_dd  # drawdown âm: lớn hơn = ít tệ hơn
    verdict = "Thắng" if c1 and c2 else ("Hoà" if c1 or c2 else "Thua")
    pw = pd.DataFrame(per_window)
    pw.index.name = "test_start"
    ex = pd.DataFrame(exits).fillna(0).astype(int).T
    ex.index.name = "cấu hình"

    lines = [
        "# Ablation trên giai đoạn phát triển (2019-01 → 2024-12) — KHÔNG phải kết quả chính thức",
        "",
        f"Sinh bởi `scripts/run_ablation.py` ngày {pd.Timestamp.now():%Y-%m-%d}. Walk-forward 12 cửa sổ "
        f"(train 3 năm / test 6 tháng), 50 mã + SPY (SPY chỉ để tính regime, FULL không giao dịch SPY), "
        f"vốn {CASH:,.0f}$, khớp open phiên sau, phí {BPS} bps mỗi chiều. **Không dùng dữ liệu ≥ 2025-01-01.** "
        "Kết quả chính thức chỉ có trên HOLDOUT (prereg mục 4, 7). Ở đây được phép chỉnh tham số.",
        "",
        "## Bảng metric (sau phí)", "", to_markdown(table[cols]), "",
        f"Thành phần \"có giá trị\" = bỏ nó làm Sharpe giảm ≥ {VALUE_THRESHOLD} so với FULL (prereg mục 7).",
        "",
        "## So với mục tiêu prereg mục 7 (áp thử trên giai đoạn phát triển)",
        f"- (1) Sharpe(FULL) = {full:.2f} so với Sharpe(B2) + 0,2 = {b2 + 0.2:.2f} → **{'ĐẠT' if c1 else 'KHÔNG đạt'}**.",
        f"- (2) MaxDD(FULL) = {f_dd:.1%} so với MaxDD(B1) = {b1_dd:.1%} → **{'ĐẠT' if c2 else 'KHÔNG đạt'}**.",
        f"- Nếu đây là holdout thì mức kết luận là: **{verdict}**. (Chỉ là giai đoạn phát triển, KHÔNG phải kết quả chính thức.)",
        "",
        "## Sharpe từng cửa sổ test", "", to_markdown(pw), "",
        "## Lý do thoát lệnh (số trade)", "", to_markdown(ex.astype(str)), "",
        f"Tổng thời gian chạy: {total:.0f}s ({total / 60:.1f} phút).", "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
