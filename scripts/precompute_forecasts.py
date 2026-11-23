"""Tính sẵn dự báo Chronos-Bolt horizon 5 cho MỌI phiên giao dịch của UNIVERSE_50 + SPY.

Mặc định: 2018-07-01 -> 2024-12-31 (KHÔNG đụng holdout), ghi data/forecasts/chronos_h5.parquet.
Cờ --holdout (chỉ Jarvis dùng, 1 lần): 2025-01-01 -> 2026-10-31, ghi chronos_h5_holdout.parquet.

Ghi dần theo lô (mỗi --flush ngày) và chạy lại được: ngày nào đã có trong file thì bỏ qua.
Dự báo tại ngày t chỉ dùng nến <= t (ChronosForecaster tự cắt).
Chạy: uv run python scripts/precompute_forecasts.py
"""

import argparse
import logging
import time

import pandas as pd

from paper_bot.config import BENCHMARK, UNIVERSE_50
from paper_bot.data.cache import load_bars
from paper_bot.data.forecast import FORECAST_DIR, ChronosForecaster

DEV_START, DEV_END = "2018-07-01", "2024-12-31"
HOLDOUT_START, HOLDOUT_END = "2025-01-01", "2026-10-31"
logger = logging.getLogger("precompute")


def read_done(path):
    """Đọc file đã có (nếu có). Trả DataFrame cột date, symbol, q10, q50, q90."""
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=["date", "symbol", "q10", "q50", "q90"])


def save(df, path):
    """Ghi qua file tạm cùng thư mục rồi đổi tên, để dừng giữa chừng không làm hỏng file."""
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", action="store_true", help="chạy giai đoạn holdout (chỉ khi được phép)")
    parser.add_argument("--flush", type=int, default=20, help="ghi file sau mỗi N ngày")
    args = parser.parse_args()

    FORECAST_DIR.mkdir(parents=True, exist_ok=True)
    start, end = (HOLDOUT_START, HOLDOUT_END) if args.holdout else (DEV_START, DEV_END)
    path = FORECAST_DIR / ("chronos_h5_holdout.parquet" if args.holdout else "chronos_h5.parquet")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(FORECAST_DIR / "precompute.log"), logging.StreamHandler()])

    symbols = list(UNIVERSE_50) + [BENCHMARK]
    # Chỉ nạp nến tới `end` => không thể nhìn thấy dữ liệu sau giai đoạn đang chạy
    bars = {s: load_bars(s, end=end) for s in symbols}
    days = bars[BENCHMARK].loc[start:end].index
    assert args.holdout or days[-1] <= pd.Timestamp(DEV_END), "không được tính vượt 2024-12-31"

    done = read_done(path)
    done_days = set(pd.to_datetime(done["date"]).unique())
    todo = [d for d in days if d not in done_days]
    logger.info(f"{len(days)} phiên, đã có {len(days) - len(todo)}, còn {len(todo)}. Tải model...")

    model = ChronosForecaster(batch_size=64)
    rows, t0 = [], time.time()
    for i, day in enumerate(todo, 1):
        fc = model.predict_many(bars, day, horizon=5)
        for sym, f in fc.items():
            rows.append({"date": day, "symbol": sym, "q10": f.quantiles[0.1],
                         "q50": f.quantiles[0.5], "q90": f.quantiles[0.9]})
        if i % args.flush == 0 or i == len(todo):
            done = pd.concat([done, pd.DataFrame(rows)], ignore_index=True)
            rows = []
            save(done, path)
            speed = (time.time() - t0) / i
            logger.info(f"{i}/{len(todo)} phiên (tới {day.date()}), {speed:.2f}s/phiên, "
                        f"còn ~{speed * (len(todo) - i) / 60:.1f} phút, tổng {len(done)} dòng")
    logger.info(f"XONG. {path}: {len(done)} dòng, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
