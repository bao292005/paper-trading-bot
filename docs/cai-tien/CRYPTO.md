# Nhật ký: chế độ CRYPTO – lõi dữ liệu + backtest (agent j-cx-core)

Tiêu chí chốt trước: [prereg-crypto.md](../prereg-crypto.md). Cổ phiếu phải giữ nguyên hành vi.

## 2026-11-26 – Bước 1: config/profile + cache/fetch + intraday crypto

**File sửa/tạo**
- `src/paper_bot/config.py`: `ASSET_CLASSES`, `CRYPTO_UNIVERSE`, `CRYPTO_BENCHMARK`, `asset_profile(asset)`,
  `is_crypto_symbol(symbol)`, `TradingConfig.asset` (mặc định "stock") + `TradingConfig.profile()`.
  Mọi số lấy từ prereg (stock: 252 ngày, 10 bps, 0.8/10/10%; crypto: 365 ngày, 25 bps, 0.9/5/20%, 6 chữ số lẻ).
- `src/paper_bot/data/cache.py`: `normalize_bars(df, tz=...)` (crypto lấy ngày theo UTC), `symbol_filename`
  ("BTC/USD" → "BTC-USD"), tham số `asset=` cho `bars_path/save_bars/load_bars` (mặc định "stock"),
  `load_crypto_bars`, `missing_days`. Đường dẫn crypto: `data/bars_crypto/BTC-USD.parquet`.
- `src/paper_bot/data/sources.py`: `fetch_crypto_daily_bars(client, symbols, start, end)`.
- `src/paper_bot/data/intraday.py`: `load_intraday(..., asset=None)` – tự nhận crypto khi mã có "/".
  Crypto: index giờ UTC naive, không lọc phiên, resample mốc giờ tròn UTC, cache `data/bars_crypto_1m/<BTC-USD>/<YYYY-MM>.parquet`,
  client `CryptoHistoricalDataClient()` không cần key.
- `scripts/fetch_crypto_history.py` (mới): tải 2021-01-01 → hôm qua (UTC), ghi `data/bars_crypto_manifest.csv`.
- `tests/test_crypto_data.py` (mới, 13 test offline).

**Kế thừa:** định dạng cache P1 (5 cột float, index "date"), cơ chế cache tháng của intraday (agent web).

**Lưu ý thiết kế:** nến ngày crypto chốt 00:00 UTC; nếu dùng cách đổi sang giờ NY như cổ phiếu sẽ bị lùi 1 ngày → thêm tham số `tz`.

**Kết quả**
- Test: `test_crypto_data.py` + `test_data_intraday.py` 23 pass; toàn bộ 509 pass (test cổ phiếu không sửa).
- Dữ liệu thật (manifest):

| Mã | Ngày đầu | Ngày cuối | Số nến | Ngày thiếu |
|---|---|---|---|---|
| BTC/USD | 2021-01-01 | 2026-11-25 | 2155 | 0 |
| ETH/USD | 2021-01-01 | 2026-11-25 | 2155 | 0 |
| SOL/USD | 2021-01-01 | 2026-11-25 | 1737 | 418 |
| AVAX/USD | 2021-11-18 | 2026-11-25 | 1834 | 0 |
| LINK/USD | 2021-01-01 | 2026-11-25 | 2155 | 0 |

Khớp với kiểm tra trong prereg (SOL thiếu ~420 ngày, AVAX từ 2021-11-18).

**Hạn chế:** cache có cả dữ liệu ≥ 2025-01-01 (giống cổ phiếu); việc chặn HOLDOUT làm ở walkforward/engine (bước 2).
Không chạy được ruff (không có trong môi trường).
