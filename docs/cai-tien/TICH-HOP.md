# Nhật ký ghép bot (TradingBot dùng pipeline mới)

## 2026-11-25 — Bước 1: config + CLI, giữ legacy
**File:** `config.py`, `__init__.py`, `bot.py` (sửa); `tests/test_live_config.py` (mới).
- `TradingConfig` thêm: `engine` ("full" mặc định | "legacy"), `flags` (6 cờ trùng tên `full_policy.DEFAULT_FLAGS`), `dry_run`, `use_llm`; hàm `disable(names)` nhận tên ngắn (`bracket`) hoặc đầy đủ (`use_bracket`).
  `FEATURE_FLAGS` khai báo lại trong config (không import full_policy vì full_policy import config → vòng); test đảm bảo hai danh sách khớp.
- CLI: thêm `--engine full|legacy`, `--disable <thành phần...>`, `--dry-run`, `--llm`. Lệnh cũ `once | loop --interval N | report`, `--symbols` giữ nguyên. Tách `build_parser()` / `apply_args()` để test được offline.
- Kế thừa bài gốc: `TradingBot` giữ nguyên làm engine legacy (MA+RSI qua managers.py). Chỉ thêm: khi `dry_run` thì thay `OrderManager` bằng `DryRunOrderManager` (chỉ ghi log, không gửi lệnh).
- Tạm thời: `once/loop` với `--engine full` thoát với thông báo "chưa ghép xong" (bước 2 làm). `report` chạy như cũ với mọi engine.
- Test: `test_bot.py` + `test_live_config.py` 19 passed; toàn bộ `pytest -q` 193 passed. `uv run paper-bot --help` OK.
- Hạn chế: legacy dry-run chưa ghi DB (chỉ log). Ruff không có trong môi trường nên chưa lint.

## 2026-11-25 — Bước 2: LiveBot (engine full)
**File:** `live.py` (mới), `__init__.py` (main chuyển sang LiveBot khi `--engine full` và once/loop); `tests/test_live_cycle.py` (mới).
- Vòng `run_cycle`: giờ thị trường (đóng thì WARNING như bài gốc, vẫn chạy) → tài khoản/vị thế/lệnh chờ → nến ≤ hôm nay (symbols + SPY, `AlpacaDataManager.get_bars`: cache trước, thiếu thì API) → forecast Chronos (nếu bật; tải lỗi → WARNING, chạy không forecast) → `build_market_state` → `DecisionPipeline.decide` (ghi MỌI tín hiệu, kể cả veto, vào `signals`) → ngưỡng `min_confidence` chỉ chặn BUY → `make_order_intent` → bán trước, mua sau → `AlpacaExecutor.submit` hoặc `log_order(status="dry_run")` → `log_equity`.
- Flag dùng y như `full_policy._make_policy` (atr_sizing → `legacy_position_size`, bracket → bỏ SL/TP, corr_limit → truyền/không truyền `returns`, regime → RegimeSwitch hay MA+RSI). `ref_width` = median độ rộng bảng forecast tính sẵn.
- Bắt lỗi: lỗi 1 mã (tải nến/quyết định/gửi) → log ERROR + event `symbol_error`, đi tiếp; lỗi đầu vòng → event `cycle_error`, bỏ vòng; `run_continuous` bắt mọi lỗi, chờ vòng sau.
- Run_id: `live-paper` / `live-dry`. `--llm` thiếu key → WARNING, chạy không LLM.
- Test: `test_live_cycle.py` 9 test (dry-run với client strict ném lỗi nếu submit/cancel; bracket ở chế độ thật; tắt bracket/atr_sizing; tương quan; lỗi 1 mã; lỗi mạng; loop không sập; ngưỡng chỉ chặn BUY; LLM thiếu key). Toàn bộ `pytest -q` 202 passed.
- Hạn chế: calibrator = None (bước 3). Chưa có kill switch, reconcile, ghi fill, live_state.json (bước 3). Trong giờ mở cửa, nến ngày hôm nay từ API là nến CHƯA đóng (quyết định trên giá tạm thời, khác backtest dùng giá đóng cửa). Kiểm tra tương quan chỉ dùng nến của các mã trong `--symbols`; mã đang nắm ngoài danh sách bị bỏ qua.

## 2026-11-25 — Bước 3: trạng thái, calibrator, fill, kill switch
**File:** `live.py`, `__init__.py` (report); `tests/test_live_state.py`, `tests/test_live_step3.py` (mới), `tests/test_live_cycle.py` (make_bot: calibrator=None, state_path tạm).
- **Quyết định (Jarvis) — chỉ nến ĐÃ ĐÓNG:** `last_closed_day(is_open, now_NY)` = hôm nay chỉ khi thị trường đóng VÀ ≥16:00 NY, ngược lại hôm qua. Nến > cutoff bị bỏ; log INFO ghi rõ cutoff mỗi vòng. Nhất quán với backtest (quyết định trên giá đóng cửa). Hệ quả: chạy trong phiên thì tín hiệu dựa trên phiên trước, lệnh market khớp giá hiện tại.
- **Quyết định — tương quan:** mã đang nắm ngoài `--symbols` được tải nến thêm (chỉ khi bật corr_limit), CHỈ dùng tính tương quan, không ra quyết định/không ghi signal.
- **Quyết định — forecast cache:** bộ nhớ, khoá (ngày nến cuối, mã); loop trong ngày không tính lại.
- **Calibrator:** `data/models/calibrator.pkl` {fit_date, train_end, use_forecast, symbols, calibrator}. Dùng lại nếu ≤7 ngày và cùng use_forecast; không thì fit lại (`_fit_on_train` giống full_policy, isotonic, cache 3 năm, symbols ∪ UNIVERSE_50) trong thread daemon, timeout 300s. Lỗi/timeout/không đủ tín hiệu → None + WARNING + event `calibrator`.
  **HOLDOUT:** "fit trên cache ≤ hôm qua" bị chặn thêm: `train_end = min(hôm qua, 2024-12-31)` → hiện tại luôn train tới 2024-12-31 (cache có tới 2026-10-30 nhưng không đụng ≥2025).
- **Đối soát:** `executor.reconcile()` ở vòng đầu (chỉ đọc). Lỗi mạng → vòng sau thử lại. Dry-run bỏ qua (run live-dry không có lệnh thật, mọi vị thế sẽ "lệch" vô nghĩa).
- **Sync fill:** cuối vòng, lệnh CLOSED (nested) từ broker → `log_fill`, chỉ lệnh có id trong bảng orders của run + lệnh con stop/TP; bỏ order_id đã có, bỏ filled_qty=0. Dry-run bỏ qua.
- **Kill switch:** sau log_equity. day_start = `account.last_equity` (thiếu → equity), peak = max(file, MAX equity trong DB, hiện tại). Thật: `executor.check_kill_switch` (huỷ lệnh MUA chờ). Dry-run: chỉ `KillSwitch.check` + halted + event CRITICAL, KHÔNG huỷ gì; MUA sau đó ghi orders status `blocked`. Trạng thái {halted, day, day_start_equity, peak_equity} lưu `data/live_state.json` theo run_id, khôi phục khi khởi động. Gỡ ngắt: sửa `halted` trong file (chưa có lệnh CLI).
- **Report:** `paper-bot report` in thêm `report_from_store(…, "live-paper")` (hoặc "chưa có dữ liệu"); phần legacy giữ định dạng cũ.
- execution.py không sửa.
- Test: test_live_state 7 + test_live_step3 7 (nến đã đóng, mã ngoài danh sách, forecast cache, reconcile 1 lần/không ở dry-run, fill không trùng + bỏ lệnh lạ, dry-run kill switch với client strict + khôi phục, chế độ thật gọi executor). Toàn bộ `pytest -q` 216 passed.
- Hạn chế: peak chỉ tính từ lúc bot bắt đầu ghi equity (không biết đỉnh trước đó của tài khoản). Không có lệnh `resume` qua CLI. Ruff vẫn chưa có nên chưa lint.

## 2026-11-25 — Bước 3b + Bước 4: calib_end, reset kill switch, chạy thật chỉ đọc, tài liệu
**File:** `config.py`, `live.py`, `__init__.py`; `tests/test_live_state.py`, `tests/test_live_step3.py`; `README.md`, `docs/quy-trinh.md`.
- `TradingConfig.calib_end = "2024-12-31"` (Jarvis xác nhận giữ tới khi chạy holdout xong; sau đó đổi `"yesterday"`).
  Mốc fit = min(hôm qua, calib_end). Đổi calib_end → fit lại (pkl lưu `calib_end`).
- `--reset-kill-switch`: `reset_kill_switch(run_id)` đặt halted=False trong live_state.json, ghi event `kill_switch`, in thông báo.
  Chạy trước khi tạo LiveBot. Run_id là live-paper, hoặc live-dry nếu kèm `--dry-run`. Run_id khác không bị đụng.
- Sửa nhỏ: peak = max(file, DB, equity, **last_equity**), vì last_equity là mức đã đạt thật.
  Lần chạy thật đầu cho thấy peak < day_start là vô lý.
- **Chạy thật (chỉ đọc), 2026-11-25 ~08:50 NY, thị trường đóng:**
  - `--help` OK (có cờ mới).
  - `report`: legacy equity 99.892$, 1 vị thế; LiveBot "chưa có dữ liệu".
  - `once --dry-run --symbols AAPL MSFT NVDA`:
    - fit calibrator mới (train tới 2024-12-31) → `data/models/calibrator.pkl`; RegimeSwitch;
    - cutoff 2026-11-24; Chronos tải OK;
    - cả 3 mã không có tín hiệu → 0 lệnh.
  - DB `live-dry` (đọc `mode=ro`): signals 0, orders 0, equity 1 dòng (99.893$, exposure 1%), events `calibrator` + `start`.
  - `live_state.json` có live-dry, `halted = false`.
- Lần chạy thật chưa sinh orders(dry_run), vì hôm nay không có tín hiệu. Đường dry-run → orders "dry_run" mới chỉ được kiểm bằng test offline (client strict).
- README: cách chạy mới + cảnh báo (ablation dev: bỏ bracket/regime/forecast làm Sharpe tăng; cấu hình chính thức chờ holdout; calib_end; nến đã đóng; kill switch).
  quy-trinh.md: thêm Phần A (luồng full), phần cũ thành Phần B (legacy).
- Test: thêm 3 test (calib_end đổi → fit lại; reset kill switch theo run_id + event; cờ CLI). `pytest -q` cuối: 219 passed.

## 2026-11-26 — Crypto: `--asset crypto` và đánh đổi `--exchange-stop` (agent j-cx-live)
Chi tiết kỹ thuật: [CRYPTO-LIVE.md](CRYPTO-LIVE.md). Cổ phiếu giữ nguyên bracket SL/TP của Alpaca.
- Alpaca crypto không có bracket/OCO → bot **tự quản SL/TP**: kiểm giá mỗi vòng (trước khi tải nến) rồi bán market toàn bộ qty thực.
- `--exchange-stop` (mặc định **TẮT**) đặt thêm stop_limit bán trên sàn (limit thấp hơn stop 0.5%). Đánh đổi:
  - Được: có bảo vệ giữa hai vòng khi bot không chạy / mất mạng.
  - Mất: stop_limit có thể KHÔNG khớp khi giá gap qua limit (vẫn giữ vị thế, tệ hơn market); khoá một phần coin nên bot bán SL/TP
    tự quản có thể bị sàn từ chối vì thiếu số dư khả dụng; phải huỷ stop khi bot tự bán/chốt lời; thêm lệnh chờ cần theo dõi.
  - Vì vậy mặc định tắt; chỉ bật khi chấp nhận rủi ro trên, và nên để `--interval` ngắn hơn thay vì dựa vào stop trên sàn.
- Kill switch chặn mua, KHÔNG chặn bán SL/TP. Equity/cash lấy theo tài khoản chung (cả cổ phiếu đang giữ).
