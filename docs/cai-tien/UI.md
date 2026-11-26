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
