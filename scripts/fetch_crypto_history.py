"""Tải nến ngày crypto 2021-01-01 -> hôm qua (UTC) cho CRYPTO_UNIVERSE, ghi vào data/bars_crypto/.

Chạy: uv run python scripts/fetch_crypto_history.py
Không cần key (CryptoHistoricalDataClient). Nến hôm nay chưa chốt nên bị bỏ.
KHÔNG nội suy ngày thiếu; số ngày thiếu ghi vào data/bars_crypto_manifest.csv.
"""

import logging
from datetime import datetime

import pandas as pd
from alpaca.data.historical import CryptoHistoricalDataClient

from paper_bot.config import CRYPTO_UNIVERSE
from paper_bot.data.cache import CRYPTO_BARS_DIR, missing_days, save_bars
from paper_bot.data.sources import fetch_crypto_daily_bars

START = datetime(2021, 1, 1)

logger = logging.getLogger("fetch_crypto_history")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    last_day = today - pd.Timedelta(days=1)  # nến hôm nay chưa chốt
    client = CryptoHistoricalDataClient()  # dữ liệu crypto không cần key
    data = fetch_crypto_daily_bars(client, list(CRYPTO_UNIVERSE), START, today.to_pydatetime())

    rows = []
    for sym in CRYPTO_UNIVERSE:
        df = data[sym]
        df = df[df.index <= last_day]
        if df.empty:
            logger.error(f"{sym}: không có dữ liệu")
            continue
        save_bars(sym, df, asset="crypto")
        miss = missing_days(df)
        rows.append({"symbol": sym, "first_date": df.index[0].date(), "last_date": df.index[-1].date(),
                     "rows": len(df), "missing_days": miss})
        logger.info(f"{sym}: {len(df)} nến, {df.index[0].date()} -> {df.index[-1].date()}, thiếu {miss} ngày")

    manifest = pd.DataFrame(rows)
    manifest.to_csv(CRYPTO_BARS_DIR.parent / "bars_crypto_manifest.csv", index=False)
    print(manifest.to_string(index=False))


if __name__ == "__main__":
    main()
