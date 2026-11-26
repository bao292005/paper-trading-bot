# Nhật ký: chế độ CRYPTO cho bot chạy thật + web + tester (agent j-cx-live)

Lõi dữ liệu/backtest: [CRYPTO.md](CRYPTO.md). Tiêu chí: [prereg-crypto.md](../prereg-crypto.md). Cổ phiếu giữ nguyên hành vi.

## 2026-11-26 – Bước 1: LiveBot crypto + executor + CLI

**File sửa/tạo**
- `src/paper_bot/execution.py`: `normalize_crypto_symbol`, `validate_crypto_order` (hàm thuần, bot và web dùng chung),
  `AlpacaExecutor(asset="crypto")`: lệnh market/limit, tif GTC, qty lẻ, KHÔNG bracket; mã lạ / có SL-TP / vượt 200k$
  bị chặn TRƯỚC khi gọi broker (log order "rejected" + event `order_rejected`); `submit_protective_stop` (stop_limit tuỳ chọn).
- `src/paper_bot/live.py`: `run_id_for`, `last_closed_day_crypto` (nến đóng = hôm qua UTC), `CryptoDataManager`
  (cache `bars_crypto` trước, thiếu thì API; `get_latest_price`), `fit_live_calibrator_crypto` (≤ calib_end 2024-12-31,
  lưu `data/models/calibrator_crypto.pkl`), `LiveBot` nhận `config.asset` + `exchange_stop`.
  Crypto: không hỏi giờ thị trường, whitelist `CRYPTO_UNIVERSE`, `RiskConfig.for_asset("crypto")`, qty làm tròn XUỐNG theo
  `min_trade_increment` (Assets API), loop chạy cả cuối tuần.
- `src/paper_bot/__init__.py`: `--asset stock|crypto`, `--exchange-stop`, `--interval` mặc định 30 (stock) / 60 (crypto);
  `--reset-kill-switch` và `report` theo run_id của asset. Crypto + `--engine legacy` bị từ chối.
- `tests/test_crypto_live.py` (mới, 20 test offline).

**SL/TP tự quản** (Alpaca crypto không có bracket/OCO)
- Lệnh mua gửi KHÔNG kèm SL/TP. SL/TP cất vào `data/live_state.json[run_id]["pending"][mã]`; khi broker có vị thế thì
  chuyển sang `["sltp"][mã]` (event `sltp_armed`); lệnh mua bị huỷ/từ chối mà không có vị thế thì bỏ (`sltp_dropped`).
- Mỗi vòng, TRƯỚC khi tải nến (lỗi dữ liệu không chặn cắt lỗ): giá mới nhất ≤ SL hoặc ≥ TP → bán market TOÀN BỘ qty THỰC
  của broker (phí crypto trừ bằng coin nên qty thực < qty đã mua), event `sl_hit` / `tp_hit`.
- `acted[mã] = ngày nến`: sau khi ra lệnh/cắt lỗ, không xét lại mã đó trong cùng nến ngày (tránh vào lại ngay).
- Tuỳ chọn `--exchange-stop` (mặc định TẮT): đặt thêm stop_limit bán trên sàn (limit thấp hơn stop 0.5%). Đánh đổi xem TICH-HOP.md.
- Kill switch chặn MUA nhưng KHÔNG chặn bán SL/TP. `check_kill_switch` gộp vào state cũ (không mất pending/sltp/acted).

**Phát hiện khi chạy thật (dry-run):** cùng tài khoản paper đang giữ cổ phiếu (NVDA) → bot crypto định tải nến NVDA từ API crypto
và báo lỗi. Đã sửa: `snapshot()` của crypto chỉ lấy vị thế/lệnh chờ thuộc `CRYPTO_UNIVERSE` (có test).

**Kế thừa:** `asset_profile`, `RiskConfig.for_asset`, `load_bars(asset="crypto")`, `fetch_crypto_daily_bars` của j-cx-core;
cơ chế state/kill switch/Store của LiveBot cổ phiếu.

**Kết quả**
- `tests/test_crypto_live.py` 20 pass; toàn bộ 538 pass (test cổ phiếu không sửa).
- Lệnh thật (chỉ đọc): `paper-bot --help` OK; `paper-bot report` OK; `paper-bot once --asset crypto --dry-run`: fit calibrator crypto
  (train tới 2024-12-31), 5 mã được xét, 0 lệnh, không gửi gì.

**Hạn chế**
- Equity/cash lấy theo tài khoản chung (gồm cả cổ phiếu đang giữ) nên cỡ lệnh crypto tính trên toàn tài khoản.
- Forecast (Chronos) vẫn chạy cho crypto nhưng không có `ref_width` (bảng forecast tính sẵn chỉ có cổ phiếu) → không giảm cỡ theo độ bất định.
- Giá SL/TP kiểm tra theo chu kỳ vòng (mặc định 60 phút), không phải realtime; muốn dày hơn thì giảm `--interval`.

## 2026-11-26 – Bước 2: Web app (cổ phiếu / crypto)

**File sửa/tạo**
- `web/api/timeutil.py`: `check_asset`, `check_symbol(symbol, asset)` (crypto chỉ nhận đúng 5 mã dạng "BTC/USD"), `local_tz`
  (stock = New York, crypto = UTC), `utc_to_unix`, `snap_to_bar(..., asset)` (crypto 24/7, mốc tròn UTC).
- `web/api/market.py`, `markers.py`: tham số `asset` cho `/api/symbols|bars|indicators|markers`; `load_intraday(..., asset="crypto")`
  chỉ được truyền khi là crypto (hàm giả của test cũ không đổi).
- `web/hub.py`: hub cổ phiếu giữ thêm `crypto_hub` (cùng lớp, asset="crypto") → tối đa 1 stream cổ phiếu + 1 stream crypto;
  nến phút UTC, không kiểm tra giờ thị trường, trạng thái luôn "open"; `shutdown()` đóng cả hai.
- `web/api/live.py`: `AlpacaStreamAdapter(asset=...)` bọc `CryptoDataStream`; `/ws/live?asset=` và tin nhắn đổi mã có `asset`;
  mã crypto sai whitelist bị đóng kết nối (1008). `web/app.py`: nối `crypto_adapter_factory`, seed nến phút UTC.
- `web/api/order_rules.py`: `validate_crypto_web_order` (dùng lại `execution.validate_crypto_order` của bot): qty lẻ > 0, ≥ min_order_size
  (hỏi Assets API), ≤ 200k$, chỉ market/limit/stop_limit, KHÔNG SL/TP, mã đúng "XXX/USD". `orders.py`: `build_crypto_request` (GTC, không
  bracket), nút chạy bot truyền `asset`. XAC NHAN + Origin/header `X-Requested-With` giữ nguyên cho cả crypto.
- `web/api/trading.py`: `/api/positions|orders?asset=` lọc, mã "BTCUSD" chuẩn hoá về "BTC/USD", dòng crypto có thêm `asset`.
- `ui/core.py`: `validate_crypto_symbols`, `build_once_command(asset=...)` (crypto → `--asset crypto`, chỉ engine full, mã whitelist).
- Frontend: ô chọn Stock/Crypto ở thanh trên (mỗi loại một danh sách mã, crypto hiển thị giờ UTC), form lệnh crypto (qty lẻ, thêm
  stop-limit, ẩn SL/TP + ghi chú "SL/TP do bot tự quản"), panel hiển thị qty lẻ, nút bot gửi `--asset crypto`.
- `tests/test_web_crypto.py` (mới, 25 test), `tests/test_web_index.py` thêm id mới.

**Kế thừa:** `CRYPTO_UNIVERSE`, `load_intraday(asset=)` của j-cx-core; `normalize_crypto_symbol` / `validate_crypto_order` của Bước 1.

**Phát hiện:** tin `bar` của cổ phiếu phải giữ NGUYÊN khoá (test so khớp dict) → trường `asset` chỉ thêm cho crypto; tương tự `/api/positions`.

**Kết quả:** 564 test pass (gồm 25 test web crypto), test cổ phiếu cũ không sửa nội dung.

**Hạn chế:** CryptoDataStream chưa chạy thật (chỉ test bằng adapter giả; không kết nối Alpaca khi test). Giao diện chưa kiểm tra bằng trình duyệt thật (chỉ `node --check` cú pháp JS).

## 2026-11-26 – Bước 3: trang /tester + Streamlit "Điều khiển" + tài liệu

**File sửa/tạo**
- `web/api/tester.py`: `check_asset`, `asset_limits(asset)`, `check_symbols(v, asset)` (crypto: đúng 5 mã "XXX/USD"),
  `build_config` nhận `asset` (khoá `asset` chỉ vào config khi là crypto → config cổ phiếu y hệt cũ); crypto: từ 2021-01-01,
  mặc định 2022-01-01→2024-12-31, phí mặc định 25 bps (lấy từ `asset_profile`); `GET /api/tester/options?asset=` trả thêm
  `default_from/default_to/default_cost_bps/is_period/oos_period`. Tối ưu không nhận ngày: IS 2022–2023 / OOS 2024 do `run_optimize` lấy từ profile.
- `templates/tester.html` (`s-asset`, `o-periods`), `static/js/tester_form.js` (`applyAssetDefaults`, gán `onchange` để dựng lại form được),
  `tester_main.js` (`changeAsset`), `tester_visual.js` / `tester_minute.js` (`/api/bars?asset=`, nến phút crypto hiện giờ UTC).
- `ui/core.py` (`loop_command_text(asset=)`), `ui/pages/4_Dieu_khien.py` (ô chọn Stock/Crypto, crypto: 5 mã, chỉ engine full).
  Trang Pipeline KHÔNG thêm ô chọn (dùng loader cổ phiếu riêng, đổi không nhỏ).
- `tests/test_web_tester_crypto.py` (18 test), `tests/test_ui_crypto.py` (4 test). README (mục Crypto), TICH-HOP.md (đánh đổi `--exchange-stop`), WEB.md.

**Kế thừa:** `run_tester(config["asset"])`, `run_optimize` (IS/OOS theo profile), `asset_profile`, `CRYPTO_UNIVERSE` của j-cx-core; `build_once_command(asset=)` của Bước 2.

**Kết quả:** toàn bộ **586 test pass** (test cổ phiếu cũ không sửa). `node --check` các JS đã sửa OK.

**Hạn chế**
- Chưa chạy tester crypto thật qua web (test dùng run_tester giả; backend j-cx-core đã có test riêng); giao diện chưa mở bằng trình trình duyệt.
- `scripts/tester.py` (ngoài phạm vi) truyền sẵn IS/OOS cổ phiếu; web không dùng script này nên không ảnh hưởng.
- Streamlit: trang Pipeline chưa hỗ trợ crypto; gỡ kill switch crypto từ UI chưa có (dùng CLI `report --asset crypto --reset-kill-switch`).
