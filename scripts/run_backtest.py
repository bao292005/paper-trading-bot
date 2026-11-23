"""Chạy baseline B0/B1/B2 bằng walk-forward trên giai đoạn PHÁT TRIỂN (2019-01 → 2024-12).

Cách chạy:  uv run python scripts/run_backtest.py
Kết quả:    docs/ket-qua/baseline-dev.md + data/trading.db (run_id bt-B0, bt-B1, bt-B2, ...)

KHÔNG chạy holdout: chỉ đọc dữ liệu tới 2024-12-31, và run_walkforward cũng tự chặn.
"""

import sys
import time
from pathlib import Path

import pandas as pd

from paper_bot.backtest.ablation import to_markdown
from paper_bot.backtest.baselines import make_b0, make_b1, make_b2
from paper_bot.backtest.metrics import compute_metrics, daily_returns, max_drawdown, sharpe
from paper_bot.backtest.report import save_result
from paper_bot.backtest.walkforward import DEV_END, DEV_START, run_walkforward
from paper_bot.config import BENCHMARK, UNIVERSE_50
from paper_bot.data.cache import load_bars
from paper_bot.storage import DEFAULT_DB, Store

OUT = Path(__file__).resolve().parents[1] / "docs" / "ket-qua" / "baseline-dev.md"
CASH = 100_000.0


def load_all():
    """Đọc cache, cắt tới hết giai đoạn phát triển. Thiếu mã -> hướng dẫn rồi thoát."""
    bars, missing = {}, []
    for s in UNIVERSE_50 + [BENCHMARK]:
        df = load_bars(s, end=DEV_END)
        if df.empty:
            missing.append(s)
        else:
            bars[s] = df
    if missing:
        print(f"Thiếu cache cho {len(missing)} mã: {', '.join(missing)}")
        print("Hãy tạo cache trước (phần P1) vào data/bars/<SYMBOL>.parquet, rồi chạy lại.")
        sys.exit(1)
    return bars


def yearly(equity: pd.Series) -> pd.DataFrame:
    """Lợi suất, Sharpe và max drawdown từng năm (từ lợi suất ngày)."""
    r = daily_returns(equity)
    rows = {}
    for year, ry in r.groupby(r.index.year):
        curve = (1 + ry).cumprod()
        rows[year] = {"return": curve.iloc[-1] - 1, "sharpe": sharpe(ry),
                      "max_drawdown": max_drawdown(pd.concat([pd.Series([1.0]), curve]))}
    return pd.DataFrame.from_dict(rows, orient="index")


def main():
    t0 = time.time()
    bars = load_all()
    stocks = {s: bars[s] for s in UNIVERSE_50}
    print(f"Đã đọc {len(bars)} mã trong {time.time() - t0:.1f}s")

    # (tên, run_id trong DB, factory, dữ liệu, cost_bps). Baseline không cần train -> bỏ train_bars.
    configs = [
        ("B0 (SPY)", "bt-B0", lambda tb: make_b0(), {BENCHMARK: bars[BENCHMARK]}, 10),
        ("B1 (chia đều 50 mã)", "bt-B1", lambda tb: make_b1(), stocks, 10),
        ("B2 (bot gốc)", "bt-B2", lambda tb: make_b2(), stocks, 10),
        ("B2 @5bps", "bt-B2-5bps", lambda tb: make_b2(), stocks, 5),
        ("B2 @20bps", "bt-B2-20bps", lambda tb: make_b2(), stocks, 20),
    ]
    totals, years, timing = {}, {}, {}
    for name, run_id, factory, data, bps in configs:
        t = time.time()
        wf = run_walkforward(factory, data, DEV_START, DEV_END, initial_cash=CASH, cost_bps=bps)
        timing[name] = time.time() - t
        metrics = compute_metrics(wf.result)
        # Ghi vào data/trading.db để dashboard xem được
        save_result(wf.result, Store(DEFAULT_DB, run_id=run_id), {**metrics, "cost_bps": bps})
        totals[name] = {**metrics, "final_equity": wf.result.equity["equity"].iloc[-1]}
        years[name] = yearly(wf.result.equity["equity"])
        m = totals[name]
        print(f"{name:22s} sharpe={m['sharpe']:.2f} cagr={m['cagr']:.1%} "
              f"maxDD={m['max_drawdown']:.1%} trades={m['n_trades']} ({timing[name]:.1f}s)")

    write_report(totals, years, timing, time.time() - t0)
    print(f"Đã ghi {OUT}")


def write_report(totals, years, timing, total_time):
    table = pd.DataFrame.from_dict(totals, orient="index")
    table["final_equity"] = table["final_equity"].map(lambda v: f"{v:,.0f}$")
    table["time_s"] = [f"{timing[n]:.1f}" for n in table.index]
    table.index.name = "cấu hình"

    def by_year(col, fmt):
        df = pd.DataFrame({n: y[col] for n, y in years.items()})
        df.index.name = "năm"
        return to_markdown(df.apply(lambda c: c.map(fmt)))

    pct = "{:.1%}".format
    lines = [
        "# Baseline trên giai đoạn phát triển (2019-01 → 2024-12)",
        "",
        f"Sinh bởi `scripts/run_backtest.py` ngày {pd.Timestamp.now():%Y-%m-%d}. "
        f"Walk-forward 12 cửa sổ (train 3 năm / test 6 tháng), vốn {CASH:,.0f}$, "
        "khớp ở open phiên sau, phí mỗi chiều 10 bps (trừ khi ghi khác). "
        "**Không dùng dữ liệu holdout (>= 2025-01-01).**",
        "",
        "## Tổng hợp (sau phí)",
        "",
        to_markdown(table),
        "",
        "Cột: sharpe năm hoá (rf=0); cagr; max_drawdown; n_trades = số lần đóng (gồm vị thế còn mở "
        "cuối kỳ); win_rate sau phí; turnover năm hoá; exposure = tỷ trọng cổ phiếu TB; time_s = thời gian chạy.",
        "",
        "## Lợi suất từng năm", "", by_year("return", pct), "",
        "## Sharpe từng năm", "", by_year("sharpe", "{:.2f}".format), "",
        "## Max drawdown từng năm", "", by_year("max_drawdown", pct), "",
        "## Ghi chú và hạn chế",
        "- **Survivorship bias:** 50 mã được chọn theo vốn hoá hiện tại, nên B1/B2 được lợi nhờ "
        "biết trước các mã này sống sót và lớn mạnh.",
        "- **B1:** mỗi lần bán bớt khi cân bằng lại được tính là 1 trade (bán một phần), nên "
        "n_trades/win_rate của B1 không có ý nghĩa như chiến lược giao dịch.",
        f"- **B2 dùng ít vốn:** mỗi lệnh tối đa 1000$ trên vốn 100k; vị thế tích luỹ dần nên exposure "
        f"TB chỉ ~{totals['B2 (bot gốc)']['exposure']:.0%}. Khi rf=0, "
        "Sharpe không đổi theo quy mô vị thế nên so Sharpe vẫn hợp lệ (prereg giữ nguyên), "
        "nhưng CAGR/max drawdown của B2 không so trực tiếp với B0/B1 được.",
        "- \"Sức mua\" của B2 = tiền mặt (không có đòn bẩy 2x như tài khoản Alpaca).",
        "- Baseline không học gì từ dữ liệu train; walk-forward ở đây chỉ để cùng khung với FULL. "
        "Danh mục được giữ liên tục qua các cửa sổ.",
        "- Chỉ có lệnh market; SL/TP (không dùng ở baseline) kiểm tra theo nến ngày, SL trước nếu cả hai cùng chạm.",
        f"- Tổng thời gian chạy (gồm đọc dữ liệu): {total_time:.1f}s.",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
