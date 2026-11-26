# Nhật ký UI Streamlit

## Bước 1 — 2026-11-26: `ui/core.py` (hàm thuần) + test

**File tạo:** `src/paper_bot/ui/__init__.py`, `src/paper_bot/ui/core.py`, `tests/test_ui_core.py`.
Chưa có trang nào (bước 2 trở đi). Không sửa file ngoài phạm vi.

**Làm gì** (core.py không import streamlit, không import `scripts/`):
- Dữ liệu/P1: `load_market_data`, `recent_dates`, `cut_upto`, `price_with_ma` (MA10/30/200), `get_forecasts`
  (bảng tính sẵn trước, thiếu thì hỏi forecaster được truyền vào), `fetch_news`, `build_state`,
  `state_summary`, `fan_chart_frame`, `ref_width_at`.
- P2: `get_calibrator`, `build_pipeline`, `decide` (ngưỡng 0,5 chỉ áp cho BUY), `signal_rows`, `scan_sessions`.
- P3: `plan_order` gọi `make_order_intent` rồi áp cờ legacy sizing/bracket/corr y như `LiveBot.decide_symbol`.
  Chỉ tính, không gửi lệnh. Lý do từ chối lấy bằng cách hứng log của `risk._skip`
  (không sửa risk.py); kiểm tra tương quan gọi riêng `correlation_ok` để hiện kết quả.
- Ghép lệnh CLI có whitelist: `validate_symbols` (chữ HOA A-Z, `fullmatch`), `validate_disable`
  (theo `config.SHORT_FLAG_NAMES`), `validate_interval`, `build_once_command`,
  `build_reset_kill_switch_command`, `is_real_order_command`, `loop_command_text` (chỉ để hiển thị).
  Chế độ thật / gỡ kill switch chỉ ra lệnh khi `ack is True` VÀ chữ đúng `XAC NHAN` (không strip, phân biệt hoa/thường).

**Kế thừa từ bài gốc / pipeline có sẵn:** `DecisionPipeline`, `build_market_state`, `make_order_intent`,
`load_or_fit_calibrator` (dùng lại nguyên, tái dùng logic demo_p1/demo_p2 chứ không import).

**Chống rò rỉ:** mọi hàm cắt nến <= asof; `ref_width_at` chỉ dùng forecast <= asof. Calibrator luôn fit tới
2024-12-31 (`get_calibrator` không cho đổi `calib_end`). Xem MarketState ngày >= 2025 thì được; `is_holdout` để UI cảnh báo.

**Test:** `uv run pytest tests/test_ui_core.py -q` → 38 passed (offline, dữ liệu tổng hợp + stub pipeline).
Smoke test đọc cache thật AAPL/NVDA/JPM quét 120 phiên tới 2024-06-28: ra bảng tín hiệu bình thường.

**Hạn chế:**
- `get_calibrator` nếu file cũ hơn 7 ngày sẽ fit lại và ghi `data/models/calibrator.pkl` (giống bot thật).
- Fan chart nội suy tuyến tính (Forecast chỉ có lợi suất tích luỹ ngày thứ 5) — chỉ minh hoạ.
- `fetch_news` cần key Alpaca; thiếu key thì trả tin rỗng kèm thông báo.
- Lý do từ chối P3 phụ thuộc chuỗi log "bỏ qua lệnh - ..." trong risk.py; đổi chuỗi đó thì phải sửa `_make_intent_with_reason`.

## Bước 2 — 2026-11-26: `app.py` + trang Pipeline + AppTest

**File tạo:** `ui/app.py`, `ui/pages/1_Pipeline.py`, `ui/charts.py`, `tests/test_ui_pages.py`.
**File sửa:** `ui/core.py` (thêm `FLAG_LABELS`, `load_forecast_table`).

- `app.py`: sơ đồ luồng P1→P2→P3→DB→P4 (dạng `st.code`), link tới trang. Trang 2–4 chưa có nên chỉ hiện "(sắp có)",
  tự thành link khi file tồn tại.
- `1_Pipeline.py`: sidebar (mã, ngày asof, 6 cờ, vốn, vị thế giả định, chế độ), tab theo mã với P1/P2/P3,
  chế độ "Quét N phiên" (bấm nút mới chạy). Cache: `cache_data` (nến, bảng forecast, tin), `cache_resource` (Chronos, calibrator).
- `charts.py`: biểu đồ altair (nến/đường + MA, fan chart). Không có plotly nên dùng altair, không cài thêm.
- Chronos chỉ tải khi tick "Chạy Chronos thật" (mặc định tắt, dùng bảng forecast tính sẵn). Tin chỉ lấy khi tick (cần key).
- **Test:** `tests/test_ui_pages.py` 10 AppTest (trang chủ, pipeline mặc định, ra lệnh dự kiến, tắt hết cờ + vị thế giả định,
  bật tin, quét, không chọn mã, thiếu SPY, ngày holdout). Dữ liệu tổng hợp; subprocess và `TradingClient.__init__` bị chặn.
  Smoke test thủ công trên cache thật (calibrator giả): không exception.
- **Hạn chế:** calibrator thật lần đầu có thể fit tới 5 phút (có spinner) và ghi `data/models/calibrator.pkl`.
  Test chưa kiểm tra nội dung biểu đồ altair, chỉ kiểm tra không lỗi.

## Bước 3 — 2026-11-26: trang Đánh giá + trang Giám sát

**File tạo:** `ui/pages/2_Danh_gia.py`, `ui/pages/3_Giam_sat.py`. **File sửa:** `ui/core.py`, `tests/test_ui_core.py`, `tests/test_ui_pages.py`.
Không sửa `dashboard.py` (chỉ import lại `connect_readonly`, `list_runs`, `load_table`, `load_equity_many`, `summary_table`, `RESULTS_DIR`).

- `core.py` thêm (đều chỉ ĐỌC): `open_db` (DB mở `mode=ro`), `db_runs`, `backtest_runs`, `db_table`, `equity_curves`,
  `run_summary`, `filter_events`, `read_live_state` + `kill_switch_text` (đọc `data/live_state.json` qua `live.load_state`),
  `read_result_doc` (chặn `../`, chỉ `*.md`), `markdown_tables` (tách bảng markdown thành DataFrame, cột toàn số thì đổi sang số),
  `ablation_table`, `fetch_account` (TradingClient paper, lỗi/thiếu key -> thông báo), `account_snapshot(client)` (chỉ gọi
  get_account / get_all_positions / get_orders).
- Trang Đánh giá: hộp cảnh báo giai đoạn phát triển/holdout chưa chạy; 4 tab: Equity & drawdown (chọn nhiều run bt-*, mặc định
  FULL/B0/B1/B2), Ablation, Calibration, Forecast (bảng đọc từ docs/ket-qua/*.md, kèm nguyên văn trong expander).
- Trang Giám sát: chọn run_id (mặc định run nào đã có dữ liệu trong DB), kill switch, tài khoản Alpaca (cache 30s + nút Làm mới),
  equity theo thời gian, tab signals/orders/fills/events (events lọc theo level/kind). Không có nút nào gửi lệnh.
- **Test:** `test_ui_core.py` 47 passed (thêm 9: DB tạm bằng Store, live_state tạm, bảng markdown, client giả có `submit_order` nổ nếu bị gọi);
  `test_ui_pages.py` 18 passed (thêm 8 AppTest trang 2–3: có/không DB, bỏ chọn run, lọc event, thiếu key, live-paper rỗng, kill switch ngắt).
  Toàn bộ `pytest tests` (trừ 2 file UI) vẫn 219 passed. Smoke test trên DB + docs thật: không exception.
- **Hạn chế:** cột có `%` trong bảng markdown giữ dạng chữ (không vẽ biểu đồ từ đó). Chưa chạy `ruff` (chưa cài trong venv).
  Trang Giám sát không tự refresh; nhấn "Làm mới". (Nhãn tiếng Việt cho run bt-abl-* đã làm ở bước 4.)

## Bước 4 — 2026-11-26: trang Điều khiển + chốt chặn an toàn + README

**File tạo:** `ui/pages/4_Dieu_khien.py`. **File sửa:** `ui/core.py`, `ui/pages/2_Danh_gia.py` (nhãn run), `tests/test_ui_core.py`,
`tests/test_ui_pages.py`, `README.md` (mục "Giao diện" + cấu trúc thư mục), file nhật ký này.

- `core.py` thêm: `run_command(cmd, allow_real=False, timeout=600)` là CHỐT CHẶN cuối trước `subprocess.run` (list args, `shell=False`):
  lệnh phải bắt đầu bằng `uv run paper-bot`, subcommand chỉ `once`/`report` (loop bị chặn); lệnh `once` không `--dry-run`
  (`is_real_order_command`) chỉ chạy khi `allow_real is True` (chuỗi/số truthy cũng bị từ chối). Timeout -> trả `timed_out=True`.
  `log_ui_event` (ghi event WARNING vào DB), `real_run_id`, `run_label` + `RUN_LABELS` (nhãn tiếng Việt cho `bt-*`, giá trị chọn vẫn là run_id).
- Trang Điều khiển: form (mã từ danh sách + ô "mã khác" qua whitelist, engine, `--disable`, `--llm`, interval), 4 khối:
  1) dry-run (hiện câu lệnh, nút chạy, stdout/stderr sau khi xong); 2) chạy THẬT: nút `disabled` cho tới khi tick + gõ đúng `XAC NHAN`
  (không strip, phân biệt hoa/thường); khi bấm, ghi event `ui_real_run` (run_id live-paper, kèm câu lệnh) TRƯỚC khi chạy;
  3) gỡ kill switch (chọn run, cũng cần tick + `XAC NHAN`, lệnh `report --reset-kill-switch [--dry-run]`);
  4) loop: chỉ hiện câu lệnh để chép vào terminal, không có nút. Tham số sai -> báo lỗi và dừng, không có nút nào.
- Phòng thủ nhiều lớp: (a) nút disabled; (b) `build_once_command` ném PermissionError nếu thiếu xác nhận; (c) `run_command` chặn lại lần nữa.
- **Test:** `test_ui_core.py` 60 passed, `test_ui_pages.py` 28 passed (thêm 10 AppTest trang 4 + nhãn run). Mọi test dùng `subprocess.run` giả
  (ghi lại lệnh), `log_ui_event` bị chặn mặc định (không ghi DB thật), `TradingClient.__init__` nổ nếu bị gọi. Kiểm tra: mở trang không chạy gì;
  dry-run đúng lệnh; 7 tổ hợp thiếu/sai xác nhận (kể cả thừa/thiếu khoảng trắng, sai hoa/thường) đều không chạy; đủ xác nhận thì
  chạy đúng 1 lệnh không `--dry-run` và event ghi trước khi chạy; input lạ (`aapl`, `AAPL;`, `$(rm)`, `../x`, mã quá dài, không chọn mã) bị chặn;
  legacy bỏ `--disable`; lỗi/timeout hiện thông báo; gỡ kill switch cần xác nhận; `loop`, lệnh lạ, `once` có phần tử không phải chuỗi bị `run_command` từ chối.
- **Hạn chế:** AppTest không bấm được nút disabled nên lớp (b)/(c) được kiểm tra ở test core chứ không qua bấm nút trên trang.
  stdout/stderr hiện sau khi lệnh xong (không stream). Chưa chạy `ruff` (chưa cài). Chưa chạy Streamlit server thật; chưa bấm bot thật.
