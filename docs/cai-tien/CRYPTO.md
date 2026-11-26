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

## 2026-11-26 – Bước 2: engine/risk/metrics/walk-forward/baseline/policy/tester theo asset

**File sửa** (mọi tham số mới có mặc định = hành vi cổ phiếu cũ)
- `backtest/metrics.py`: `sharpe(r, periods_per_year=252)`, `compute_metrics(res, periods_per_year=252)`.
- `risk.py`: `floor_qty(x, decimals=None)`, `RiskConfig.qty_decimals`, `RiskConfig.for_asset(asset)`
  (`for_asset("stock") == RiskConfig()`), `legacy_position_size(..., qty_decimals=None)` (crypto: lẻ, không ép 1 coin).
- `backtest/engine.py`: `run_backtest(..., qty_decimals=None)`; crypto mua lẻ, phí mua trừ vào coin nhận
  (trả qty·giá USD, nhận qty·(1−phí) coin), phí bán trừ vào USD.
- `backtest/walkforward.py`: `make_windows(train_years=None, train_start=...)` = train mở rộng;
  `run_walkforward(..., asset="stock")`: crypto 6 cửa sổ 2022H1→2024H2, train từ 2021-01-01, Sharpe 365, HoldoutError giữ nguyên.
- `data/regime.py`: `regime_series(bars, vol_lookback=252)`, `detect_regime(..., vol_lookback)`.
- `backtest/baselines.py`: `make_b0/b1/b2(..., qty_decimals=None)` → C0/C1/C2. C1 crypto chia equity/5 cố định (mã thiếu nến giữ phần của nó).
- `decision/full_policy.py`: `make_full_policy_factory(..., asset="stock")`: crypto dùng BTC/USD làm benchmark VÀ giao dịch BTC,
  median vol 365, `RiskConfig.for_asset`, forecast `chronos_h5_crypto.parquet`, cache `bars_crypto`.
- `backtest/tester/{run,strategies,optimize,report}.py`: config `"asset"` (mặc định "stock"); crypto mặc định from/to 2022-01-01→2024-12-31,
  phí 25 bps, IS 2022–2023 / OOS 2024, Sharpe 365, mua lẻ. Holdout vẫn bị chặn.
- `tests/test_crypto_backtest.py` (mới, 10 test). Không sửa test cũ.

**Kết quả:** 519 test pass. Chạy lại `scripts/run_backtest.py` (cổ phiếu): B0 0.92 / B1 0.99 / **B2 0.65** / B2@5 0.69 / B2@20 0.56 – y hệt trước.

**Còn lại:** `scripts/tester.py` (ngoài phạm vi được sửa) truyền sẵn IS/OOS cổ phiếu → với crypto cần ghi `is_period/oos_period` trong spec
hoặc để web gọi `run_optimize` không truyền kỳ. Bộ chặn holdout nến phút trong engine vẫn là 2025-01-01 (trùng với crypto nên đúng).

## 2026-11-26 – Bước 3: forecast + baseline + ablation crypto trên dev (KHÔNG chạy holdout)

- Forecast: `precompute_forecasts.py --asset crypto` → `data/forecasts/chronos_h5_crypto.parquet` (6201 dòng, 2021-07→2024-12-31, 72s).
- **Sửa lỗi + chạy lại:** lần chạy ablation đầu crash `ValueError: Input X contains NaN` khi fit calibrator (forecast crypto có dòng
  median/width NaN do context ngắn). Sửa `decision/calibration.py::signal_features`: forecast không hữu hạn → coi như không có forecast
  (has_fc=0, fc_*=0). Với giá trị hữu hạn không đổi gì → cổ phiếu y hệt. Sau khi sửa đã **chạy lại cả baseline lẫn ablation crypto**;
  `docs/ket-qua/crypto-*.md` chỉ chứa kết quả lần chạy sau. 513 test (trừ test_crypto_live/test_data_forecast) pass; cổ phiếu chạy lại B2 = **0.65**.
- Baseline dev (`crypto-baseline-dev.md`, 25 bps): C0 0.68 (CAGR 25%, DD -67%) / C1 0.51 (12%, -80%) / C2 0.54 (2.4%, -3.2%); C2@15 0.55, C2@40 0.51.
- Ablation (`crypto-ablation-dev.md`): FULL 0.24; bỏ forecast 0.41; bỏ regime -0.38; bỏ calib 0.14; bỏ ATR 0.11; bỏ bracket 0.90; bỏ corr 0.24 (không đổi).
- **FINAL theo mục 8:** bỏ `use_bracket` (+0.66) và `use_forecast` (+0.16) → FINAL dev Sharpe 0.92, CAGR 23.6%, DD -18.1%, 37 trade.
- Kết luận trung thực: FINAL thắng chủ yếu nhờ BỎ stop/TP, tức giữ lệnh theo xu hướng trong chu kỳ tăng 2023-24 (CAGR gần C0)
  – lựa chọn này làm trên chính dev nên có rủi ro quá khớp; forecast Chronos không giúp gì cho crypto; giới hạn tương quan
  không bao giờ kích hoạt (5 mã, ≤5 vị thế). Regime + calibration + ATR có giá trị trên dev.
- Hạn chế: 5 mã tương quan cao, dev 3 năm/6 cửa sổ (nhiễu lớn), SOL thiếu ~418 ngày, AVAX từ 2021-11, survivorship, không mô phỏng spread.
- Chưa làm: khoá cấu hình FINAL (hash commit) rồi chạy holdout 2025-01→2026-10 đúng 1 lần.
