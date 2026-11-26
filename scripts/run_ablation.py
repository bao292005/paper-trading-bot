"""Bảng ablation (prereg mục 7-8) trên giai đoạn PHÁT TRIỂN 2019-01 → 2024-12, phí 10 bps.

Cách chạy:  uv run python scripts/run_ablation.py [--asset crypto]
Kết quả:    docs/ket-qua/ablation-dev.md + data/trading.db (run_id bt-abl-<tên>)
            crypto: docs/ket-qua/crypto-ablation-dev.md (dev 2022-01 → 2024-12, 25 bps, run_id bt-cabl-<tên>)
                    + mục "FINAL theo luật mục 8" của prereg-crypto

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


CRYPTO_OUT = OUT.parent / "crypto-ablation-dev.md"
FINAL_GAIN = 0.1  # prereg-crypto mục 8: bỏ thành phần nếu bỏ nó làm Sharpe dev TĂNG >= 0,1


def run_rows(configs, asset, bps, ppy):
    """Chạy walk-forward từng dòng; trả (metric, sharpe từng cửa sổ, thời gian, lý do thoát)."""
    rows, per_window, timing, exits = {}, {}, {}, {}
    for name, slug, factory, data in configs:
        t = time.time()
        log(f"Bắt đầu {name}")
        wf = run_walkforward(timed_factory(name, factory), data, asset=asset, initial_cash=CASH,
                             cost_bps=bps)
        timing[name] = time.time() - t
        m = compute_metrics(wf.result, ppy)
        save_result(wf.result, Store(DEFAULT_DB, run_id=f"bt-cabl-{slug}"), {**m, "cost_bps": bps})
        rows[name] = m
        per_window[name] = wf.per_window.set_index("test_start")["sharpe"]
        exits[name] = pd.Series([tr.exit_reason for tr in wf.result.trades]).value_counts().to_dict()
        log(f"Xong {name}: sharpe={m['sharpe']:.2f} cagr={m['cagr']:.1%} "
            f"maxDD={m['max_drawdown']:.1%} trades={m['n_trades']} ({timing[name]:.0f}s)")
    return rows, per_window, timing, exits


def run_crypto():
    """Ablation crypto (prereg-crypto mục 8) trên dev 2022-01 → 2024-12, phí 25 bps, rồi FINAL 1 lần."""
    from paper_bot.config import CRYPTO_BENCHMARK, CRYPTO_UNIVERSE, asset_profile
    from paper_bot.data.forecast import CRYPTO_FORECAST_PATH
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    p = asset_profile("crypto")
    bps, ppy, d = p["cost_bps"], p["periods_per_year"], p["qty_decimals"]
    t0 = time.time()
    bars = {s: load_bars(s, end=p["dev_end"], asset="crypto") for s in CRYPTO_UNIVERSE}
    fc = load_forecasts(CRYPTO_FORECAST_PATH)
    fc = fc[fc.index.get_level_values("date") <= pd.Timestamp(p["dev_end"])]
    log(f"Đọc {len(bars)} mã crypto + {len(fc)} dòng forecast")

    cache = {}
    full = lambda flags: make_full_policy_factory(flags, forecasts=fc, calib_cache=cache,  # noqa: E731
                                                  asset="crypto")
    configs = [(name, slug, full(flags), bars) for name, slug, flags in FULL_ROWS]
    configs += [("C0 (BTC buy&hold)", "C0", lambda tb: make_b0(CRYPTO_BENCHMARK, qty_decimals=d),
                 {CRYPTO_BENCHMARK: bars[CRYPTO_BENCHMARK]}),
                ("C1 (chia đều 5 mã)", "C1", lambda tb: make_b1(CRYPTO_UNIVERSE, qty_decimals=d), bars),
                ("C2 (bot gốc)", "C2", lambda tb: make_b2(CRYPTO_UNIVERSE, qty_decimals=d), bars)]
    res = run_rows(configs, "crypto", bps, ppy)

    # Luật mục 8: bỏ mọi thành phần mà bỏ nó làm Sharpe TĂNG >= 0,1 so với FULL
    full_sharpe = res[0]["FULL"]["sharpe"]
    removed = {flag: False for name, _, flags in FULL_ROWS[1:] for flag in flags
               if res[0][name]["sharpe"] - full_sharpe >= FINAL_GAIN}
    final = None
    if removed:
        log(f"FINAL bỏ {sorted(removed)} -> chạy FINAL trên dev 1 lần")
        f_res = run_rows([("FINAL", "FINAL", full(removed), bars)], "crypto", bps, ppy)
        final = f_res[0]["FINAL"]
        for part, extra in zip(res, f_res):
            part.update(extra)
    write_crypto_report(*res, removed, final, bps, time.time() - t0)
    log(f"Đã ghi {CRYPTO_OUT}. Tổng {time.time() - t0:.0f}s")


def main():
    import sys
    if "--asset" in sys.argv and sys.argv[sys.argv.index("--asset") + 1] == "crypto":
        return run_crypto()
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


def write_crypto_report(rows, per_window, timing, exits, removed, final, bps, total):
    table = pd.DataFrame.from_dict(rows, orient="index")
    table.index.name = "cấu hình"
    full = table.at["FULL", "sharpe"]
    table["Δsharpe_vs_FULL"] = table["sharpe"] - full
    table["kết luận"] = [("BỎ trong FINAL (Δ ≥ +0,1)" if d >= FINAL_GAIN else
                          "có giá trị" if d <= -VALUE_THRESHOLD else "giữ (không chứng minh được giá trị)")
                         if n.startswith("FULL ") else "" for n, d in table["Δsharpe_vs_FULL"].items()]
    table["time_s"] = [f"{timing[n]:.0f}" for n in table.index]
    cols = ["sharpe", "cagr", "max_drawdown", "n_trades", "exposure", "win_rate", "turnover",
            "Δsharpe_vs_FULL", "kết luận", "time_s"]
    fin = "FINAL" if final is not None else "FULL"
    f_sh, f_dd = table.at[fin, "sharpe"], table.at[fin, "max_drawdown"]
    c2, c1_dd = table.at["C2 (bot gốc)", "sharpe"], table.at["C1 (chia đều 5 mã)", "max_drawdown"]
    ok1, ok2 = f_sh >= c2 + 0.2, f_dd >= c1_dd
    verdict = "Thắng" if ok1 and ok2 else ("Hoà" if ok1 or ok2 else "Thua")
    names = {k: n for n, _, fl in FULL_ROWS[1:] for k in fl}
    rm = ", ".join(f"`{k}` ({names[k]})" for k in sorted(removed)) or "không bỏ thành phần nào"
    pw = pd.DataFrame(per_window)
    pw.index.name = "test_start"
    ex = pd.DataFrame(exits).fillna(0).astype(int).T
    ex.index.name = "cấu hình"
    lines = [
        "# Ablation CRYPTO trên giai đoạn phát triển (2022-01 → 2024-12) — KHÔNG phải kết quả chính thức", "",
        f"Sinh bởi `scripts/run_ablation.py --asset crypto` ngày {pd.Timestamp.now():%Y-%m-%d}. "
        "Theo [prereg-crypto.md](../prereg-crypto.md): 5 mã BTC/ETH/SOL/AVAX/LINK (BTC/USD vừa là benchmark "
        "regime vừa được giao dịch), walk-forward 6 cửa sổ test 6 tháng, train mở rộng từ 2021-01, "
        f"vốn {CASH:,.0f}$, khớp open nến ngày sau, phí {bps} bps mỗi chiều (mua trừ vào coin), Sharpe 365 ngày, "
        "corr ≤ 0,9, tối đa 5 vị thế × 20% vốn, forecast Chronos-Bolt-small h=5 tính sẵn (≤ 2024-12-31). "
        "**Không dùng dữ liệu ≥ 2025-01-01, KHÔNG chạy holdout.**", "",
        "## Bảng metric (sau phí)", "", to_markdown(table[cols]), "",
        "## FINAL theo luật mục 8", "",
        f"- Luật: FINAL = FULL bỏ mọi thành phần mà khi bỏ, Sharpe dev TĂNG ≥ {FINAL_GAIN} (Sharpe FULL = {full:.2f}).",
        f"- Thành phần bị bỏ: {rm}.",
        f"- Cờ FINAL: `{ {**{k: True for k in names}, **removed} }`.",
        (f"- FINAL chạy trên dev 1 lần (không chỉnh thêm): Sharpe {f_sh:.2f}, MaxDD {f_dd:.1%}."
         if final is not None else "- Không bỏ gì → FINAL = FULL (không cần chạy thêm)."),
        "- Bước tiếp theo theo prereg: khoá cấu hình (ghi hash commit) rồi mới chạy holdout ĐÚNG 1 lần (chưa làm).", "",
        "## So với mục tiêu prereg mục 7 (áp thử trên dev, KHÔNG phải kết quả chính thức)",
        f"- (1) Sharpe({fin}) = {f_sh:.2f} so với Sharpe(C2) + 0,2 = {c2 + 0.2:.2f} → **{'ĐẠT' if ok1 else 'KHÔNG đạt'}**.",
        f"- (2) MaxDD({fin}) = {f_dd:.1%} so với MaxDD(C1) = {c1_dd:.1%} → **{'ĐẠT' if ok2 else 'KHÔNG đạt'}**.",
        f"- Nếu là holdout thì mức: **{verdict}**. So với C0 (Sharpe {table.at['C0 (BTC buy&hold)', 'sharpe']:.2f}) "
        "báo cáo trung thực ở bảng trên.", "",
        "## Sharpe từng cửa sổ test", "", to_markdown(pw), "",
        "## Lý do thoát lệnh (số trade)", "", to_markdown(ex.astype(str)), "",
        "## Hạn chế",
        "- Chỉ 5 mã, tương quan rất cao (cùng theo BTC) → giới hạn tương quan 0,9 và đa dạng hoá ít tác dụng.",
        "- Dev chỉ 3 năm (1 chu kỳ sập 2022 + hồi 2023-2024), 6 cửa sổ: Sharpe từng dòng nhiễu lớn, "
        "chênh 0,1–0,2 giữa các dòng chưa chắc có ý nghĩa thống kê.",
        "- SOL/USD thiếu ~418 ngày, AVAX/USD chỉ từ 2021-11-18 (cửa sổ train đầu ngắn); không nội suy.",
        "- Calibrator fit trên train ngắn (từ 2021-01, cửa sổ đầu chỉ 1 năm) và ít mã → dễ quá khớp.",
        "- Survivorship bias (chọn mã theo hiện tại); giá Alpaca, không mô phỏng spread/trượt giá.",
        f"Tổng thời gian chạy: {total:.0f}s ({total / 60:.1f} phút).", "",
    ]
    CRYPTO_OUT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
