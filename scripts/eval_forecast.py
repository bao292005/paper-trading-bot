"""So pinball loss (0.1/0.5/0.9) của Naive vs Chronos vs Kronos, horizon 5 ngày.

CHỈ dùng giai đoạn 2019-01-01 -> 2024-12-31 (nến chỉ nạp tới 2024-12-31, không đụng holdout).
  - Naive, Chronos: 50 mã UNIVERSE_50, mỗi 10 phiên.
  - Kronos (chậm): 10 mã đầu, mỗi 20 phiên (tập con cùng ngày/mã), n_samples=10, thử mps rồi cpu.
Kết quả thô lưu data/forecasts/eval_<model>.parquet (chạy lại được, bỏ qua ngày đã có),
báo cáo ghi docs/ket-qua/forecast-eval.md.

Chạy: uv run python scripts/eval_forecast.py [--models naive chronos kronos] [--report-only]
"""

import argparse
import json
import logging
import time

import numpy as np
import pandas as pd

from paper_bot.config import BENCHMARK, UNIVERSE_50
from paper_bot.data.cache import PROJECT_ROOT, load_bars
from paper_bot.data.forecast import FORECAST_DIR, ChronosForecaster, KronosForecaster, NaiveForecaster

START, END = "2019-01-01", "2024-12-31"
HORIZON = 5
TAUS = (0.1, 0.5, 0.9)
REPORT = PROJECT_ROOT / "docs" / "ket-qua" / "forecast-eval.md"
TIMES = FORECAST_DIR / "eval_times.json"  # tổng giây chạy của từng mô hình
logger = logging.getLogger("eval_forecast")


def eval_dates(bars, step):
    """Các ngày asof: mỗi `step` phiên trong [START, END], phải còn đủ HORIZON phiên sau đó để biết đáp án."""
    days = bars[BENCHMARK].loc[START:END].index
    return list(days[:-HORIZON][::step])


def collect(name, forecaster, bars, symbols, dates, flush=5):
    """Chạy dự báo cho từng ngày, lưu (date, symbol, q10, q50, q90, y). Bỏ qua ngày đã có trong file."""
    path = FORECAST_DIR / f"eval_{name}.parquet"
    done = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    done_days = set(pd.to_datetime(done["date"])) if len(done) else set()
    rows, t0 = [], time.time()
    todo = [d for d in dates if d not in done_days]
    logger.info(f"[{name}] {len(todo)}/{len(dates)} ngày cần chạy, {len(symbols)} mã")
    for i, day in enumerate(todo, 1):
        fc = forecaster.predict_many({s: bars[s] for s in symbols}, day, HORIZON)
        for sym, f in fc.items():
            close = bars[sym]["close"]
            pos = close.index.get_loc(day)
            y = float(np.log(close.iloc[pos + HORIZON] / close.iloc[pos]))  # đáp án thực tế
            rows.append({"date": day, "symbol": sym, "q10": f.quantiles[0.1],
                         "q50": f.quantiles[0.5], "q90": f.quantiles[0.9], "y": y})
        if i % flush == 0 or i == len(todo):
            done = pd.concat([done, pd.DataFrame(rows)], ignore_index=True)
            rows = []
            done.to_parquet(path, index=False)
            logger.info(f"[{name}] {i}/{len(todo)} ngày (tới {day.date()}), {time.time() - t0:.0f}s")
    times = json.loads(TIMES.read_text()) if TIMES.exists() else {}
    times[name] = times.get(name, 0.0) + (time.time() - t0)
    TIMES.write_text(json.dumps(times))
    return done


def make_kronos(samples):
    """Thử mps trước, lỗi thì cpu."""
    try:
        k = KronosForecaster(n_samples=samples, device="mps", max_batch=100)
        probe = load_bars("AAPL", end="2019-06-28")
        k.predict_many({"AAPL": probe}, "2019-06-28", HORIZON)  # chạy thử 1 lần
        logger.info("Kronos dùng mps")
        return k, "mps"
    except Exception as e:
        logger.warning(f"mps lỗi ({e}); dùng cpu")
        return KronosForecaster(n_samples=samples, device="cpu", max_batch=100), "cpu"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=["naive", "chronos", "kronos"])
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--kronos-symbols", type=int, default=10)
    parser.add_argument("--kronos-step", type=int, default=20)
    parser.add_argument("--kronos-samples", type=int, default=10)
    args = parser.parse_args()
    FORECAST_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(FORECAST_DIR / "eval.log"), logging.StreamHandler()])

    if not args.report_only:
        symbols = list(UNIVERSE_50)
        bars = {s: load_bars(s, end=END) for s in symbols + [BENCHMARK]}  # nến chỉ tới 2024-12-31
        dates10, dates20 = eval_dates(bars, 10), eval_dates(bars, args.kronos_step)
        if "naive" in args.models:
            collect("naive", NaiveForecaster(), bars, symbols, dates10)
        if "chronos" in args.models:
            collect("chronos", ChronosForecaster(batch_size=64), bars, symbols, dates10)
        if "kronos" in args.models:
            kronos, device = make_kronos(args.kronos_samples)
            collect("kronos", kronos, bars, symbols[:args.kronos_symbols], dates20, flush=1)
            (FORECAST_DIR / "eval_kronos_device.txt").write_text(device)
    write_report(args)


def pinball(y, q, tau):
    d = y - q
    return np.maximum(tau * d, (tau - 1) * d)


def load_eval(name):
    path = FORECAST_DIR / f"eval_{name}.parquet"
    return pd.read_parquet(path) if path.exists() else None


def summarize(frames, title):
    """frames: {tên mô hình: df}, đã cùng tập (date, symbol). Trả (markdown, danh sách kết luận)."""
    per_model = {}
    for name, df in frames.items():
        loss = pd.DataFrame({f"l{int(t * 100)}": pinball(df["y"], df[f"q{int(t * 100)}"], t) for t in TAUS})
        loss["mean"] = loss.mean(axis=1)
        loss["date"] = df["date"].values
        per_model[name] = (df, loss)
    base_daily = per_model["naive"][1].groupby("date")["mean"].mean()
    n_obs, n_days = len(frames["naive"]), frames["naive"]["date"].nunique()

    lines = [f"### {title}", f"{n_obs} quan sát ({n_days} ngày x mã), horizon {HORIZON} phiên. "
             "Loss càng thấp càng tốt. `t` = kiểm định cặp theo ngày so với naive (t > 2: tốt hơn naive "
             "có ý nghĩa; t < -2: tệ hơn). Phủ = tỷ lệ y nhỏ hơn quantile (lý tưởng 10% / 50% / 90%).", "",
             "| Mô hình | q0.1 | q0.5 | q0.9 | Trung bình | Tỷ lệ vs naive | t | Phủ 10% | Phủ 50% | Phủ 90% | Độ rộng q90-q10 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    verdicts = []
    for name, (df, loss) in per_model.items():
        ratio = loss["mean"].mean() / per_model["naive"][1]["mean"].mean()
        diff = base_daily - loss.groupby("date")["mean"].mean()  # dương = mô hình tốt hơn naive
        t = diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff))) if name != "naive" and len(diff) > 2 else float("nan")
        cov = [(df["y"] < df[c]).mean() for c in ("q10", "q50", "q90")]
        lines.append(f"| {name} | {loss['l10'].mean():.5f} | {loss['l50'].mean():.5f} | {loss['l90'].mean():.5f} | "
                     f"{loss['mean'].mean():.5f} | {ratio:.3f} | {t:.2f} | {cov[0]:.1%} | {cov[1]:.1%} | {cov[2]:.1%} | "
                     f"{(df['q90'] - df['q10']).mean():.4f} |")
        if name != "naive":
            if t > 2:
                v = f"{name} TỐT HƠN naive có ý nghĩa (loss thấp hơn {1 - ratio:.1%}, t={t:.2f})"
            elif t < -2:
                v = f"{name} TỆ HƠN naive có ý nghĩa (loss cao hơn {ratio - 1:.1%}, t={t:.2f})"
            else:
                v = f"{name} KHÔNG khác naive một cách có ý nghĩa (tỷ lệ loss {ratio:.3f}, t={t:.2f})"
            verdicts.append(f"{title}: {v}.")
    return "\n".join(lines), verdicts


def write_report(args):
    naive, chronos, kronos = load_eval("naive"), load_eval("chronos"), load_eval("kronos")
    parts, verdicts = [], []
    if naive is not None and chronos is not None:
        m = naive.merge(chronos, on=["date", "symbol", "y"], suffixes=("_n", "_c"))
        frames = {"naive": m.rename(columns={"q10_n": "q10", "q50_n": "q50", "q90_n": "q90"})[["date", "symbol", "y", "q10", "q50", "q90"]],
                  "chronos": m.rename(columns={"q10_c": "q10", "q50_c": "q50", "q90_c": "q90"})[["date", "symbol", "y", "q10", "q50", "q90"]]}
        text, v = summarize(frames, "A. Naive vs Chronos, 50 mã, mỗi 10 phiên")
        parts.append(text)
        verdicts += v
    if naive is not None and chronos is not None and kronos is not None:
        keys = kronos[["date", "symbol"]]
        frames = {n: d.merge(keys, on=["date", "symbol"]) for n, d in
                  (("naive", naive), ("chronos", chronos), ("kronos", kronos))}
        text, v = summarize(frames, "B. Ba mô hình trên tập con của Kronos (cùng ngày, cùng mã)")
        parts.append(text)
        verdicts += v
    times = json.loads(TIMES.read_text()) if TIMES.exists() else {}
    dev = (FORECAST_DIR / "eval_kronos_device.txt")
    time_lines = ["### Thời gian chạy (tổng giây, máy hiện tại)", "", "| Mô hình | Giây | Ghi chú |", "|---|---|---|"]
    for name, sec in times.items():
        note = f"thiết bị: {dev.read_text()}, n_samples={args.kronos_samples}" if name == "kronos" and dev.exists() else ""
        time_lines.append(f"| {name} | {sec:.0f} | {note} |")
    header = ["# Đánh giá dự báo quantile: Naive vs Chronos vs Kronos", "",
              f"Sinh tự động bởi `scripts/eval_forecast.py`. Giai đoạn {START} → {END} (không đụng holdout 2025+). "
              "Đáp án y = lợi suất log tích luỹ 5 phiên từ đóng cửa ngày asof; mô hình chỉ thấy nến <= asof. "
              "Pinball loss trung bình của q0.1/0.5/0.9. Hạn chế: survivorship bias (50 mã còn sống, vốn hoá lớn); "
              "các mã trong cùng ngày tương quan nên t tính theo ngày, không theo từng quan sát.", ""]
    footer = ["", "## Kết luận (tự sinh theo luật t > 2 / t < -2)", ""] + [f"- {v}" for v in verdicts]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n\n".join(header + parts + ["\n".join(time_lines)]) + "\n" + "\n".join(footer) + "\n")
    logger.info(f"Đã ghi {REPORT}")


if __name__ == "__main__":
    main()
