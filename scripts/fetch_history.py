"""Tải nến ngày 2016-01-01 -> 2026-10-31 cho UNIVERSE_50 + SPY, ghi vào data/bars/.

Chạy: uv run python scripts/fetch_history.py [--batch 5]
Thử feed SIP trước, lỗi thì dùng IEX. Ghi bảng tóm tắt vào data/bars_manifest.csv.
"""

import argparse
import logging
from datetime import datetime

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient

from paper_bot.config import BENCHMARK, UNIVERSE_50, TradingConfig
from paper_bot.data.cache import BARS_DIR, save_bars
from paper_bot.data.sources import fetch_daily_bars

START = datetime(2016, 1, 1)
END = datetime(2026, 11, 1)  # lấy dư 1 ngày rồi cắt về <= 2026-10-31
LAST_DAY = pd.Timestamp("2026-10-31")

logger = logging.getLogger("fetch_history")


def fetch_batch(client, symbols):
    """Thử SIP, lỗi thì IEX. Trả (dữ liệu, tên feed đã dùng)."""
    for feed in (DataFeed.SIP, DataFeed.IEX):
        try:
            return fetch_daily_bars(client, symbols, START, END, feed=feed), feed.value
        except Exception as e:  # APIError hoặc lỗi mạng
            logger.warning(f"Feed {feed.value} lỗi cho {symbols}: {e}")
    raise RuntimeError(f"Không tải được {symbols} bằng cả SIP lẫn IEX")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=5, help="số mã mỗi lần gọi API")
    args = parser.parse_args()

    config = TradingConfig.from_env()
    client = StockHistoricalDataClient(config.api_key, config.api_secret)
    symbols = list(UNIVERSE_50) + [BENCHMARK]

    rows = []
    for i in range(0, len(symbols), args.batch):
        batch = symbols[i:i + args.batch]
        data, feed = fetch_batch(client, batch)
        for sym, df in data.items():
            df = df[df.index <= LAST_DAY]
            if df.empty:
                logger.error(f"{sym}: không có dữ liệu")
                continue
            save_bars(sym, df)
            rows.append({"symbol": sym, "feed": feed, "first_date": df.index[0].date(),
                         "last_date": df.index[-1].date(), "rows": len(df)})
            logger.info(f"{sym}: {len(df)} dòng, {df.index[0].date()} -> {df.index[-1].date()}, feed={feed}")

    manifest = pd.DataFrame(rows)
    manifest.to_csv(BARS_DIR.parent / "bars_manifest.csv", index=False)
    print(manifest.to_string(index=False))


if __name__ == "__main__":
    main()
