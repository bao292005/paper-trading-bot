# Nhật ký Bar Replay + tài khoản luyện tập (agent j-replay)

## Bước 1 – Mô hình khớp lệnh `sim/` (2026-11-27)
- File: `src/paper_bot/sim/{__init__,matching,models,account,stats}.py`, test `tests/test_sim_{matching,account}.py` (24 pass).
- `matching.py` (thuần): `fill_price(type, side, price, o, h, l)` market = open; limit/stop chạm high/low, gap qua giá -> open;
  `exit_hit(sl, tp, …)` SL xét trước TP.
- `account.py` `PracticeAccount`: 1 mã, tiền mặt (mặc định 100.000$), phí từ `asset_profile` (stock 10 bps, crypto 25 bps),
  qty lẻ cho crypto (6 chữ số), KHÔNG bán khống. Lệnh mua có SL/TP -> khi khớp sinh 2 lệnh con bán (stop + limit, OCO),
  có hiệu lực từ nến 1m SAU nến khớp. `advance(bars, to)` bỏ mọi nến có `end > to` hoặc `start <` con trỏ cũ (chống nhìn trước);
  lệnh chỉ xét nến bắt đầu >= lúc đặt -> market khớp ở open nến kế tiếp. Không đủ tiền lúc khớp -> rejected.
- `stats.py`: `report()` gọi `mt5_report` (vị thế còn mở = trade "end" ở giá cuối, trừ phí bán ước tính); `save_session()` ghi
  fills + equity + event `practice_end` (tóm tắt, báo cáo, bảng lệnh, equity) với run_id `practice-<id>`.
- Kế thừa: quy tắc SL-trước-TP của backtest engine; trường `SimTrade` trùng `backtest.Trade` để dùng lại `mt5_report`.
- Hạn chế: không short; giá vốn theo trung bình (không FIFO); equity ghi theo từng bước replay nên Sharpe chỉ tham khảo.

## Bước 2 – API replay + practice (2026-11-27)
- `web/api/replay.py` `GET /api/replay/bars?symbol&asset&tf&cursor&after&before&steps`: con trỏ làm tròn xuống phút,
  chặn ở hiện tại; chỉ dùng nến 1m bắt đầu < con trỏ; khung lớn gộp từ đó (10:37 khung 1h -> nến 10:30 gồm 10:30..10:36);
  1D = ngày đã đóng (cache ngày) + ngày đang chạy gộp từ 1m (thiếu 1m -> `fallback: true`, chỉ hiện ngày đã đóng).
  `steps=k` tiến k nến khung tf (1D: tới 16:00 NY / 24:00 UTC); hết dữ liệu -> `end: true`. Trả kèm MA10/30/200 + RSI14.
- `web/api/practice.py` (phiên trong bộ nhớ `app.state.practice`, tối đa 20): sessions POST/GET, `{id}/advance {to, steps}`,
  `{id}/orders` POST, `orders/{oid}` DELETE/PATCH, `{id}/close`, `{id}/end` (lưu DB), `{id}/stats`, `history`, `history/{run_id}`.
  Ghi qua `guard` (DELETE cũng cần Content-Type JSON). Khớp bằng nến 1m trong [con trỏ, to); không có 1m -> nến ngày + ghi chú.
- Test: `tests/test_web_replay.py` (8), `tests/test_web_practice.py` (8, có test chống nhìn trước: loader không được hỏi phút >= to;
  trading client giả ném lỗi nếu bị gọi). Tổng sim+API: 40 pass.

## Bước 3 – Frontend replay (2026-11-27)
- `replay_math.js` (thuần, test node): `zonedToUnix` (ngày giờ theo CH.tz, chỉnh quanh đổi giờ mùa hè), `mergeByTime`, `nextSpeed`,
  `delayMs`, `ignoreKey`, `snapTime` (thời điểm -> nến chứa nó; 1D = 12:00 UTC), `inHoldout`.
- `replay_data.js`: khi `RP.on`, `loadAll/loadOlder` của data.js chuyển sang `/api/replay/bars`; step chỉ lấy đuôi (`after`) rồi
  `series.update` -> ở mép phải biểu đồ tự theo. `replay.js`: nút Replay/`R`, chọn nến (`subscribeClick`, đường dọc cam) hoặc ô ngày giờ,
  thanh điều khiển nổi, play bằng setTimeout theo tốc độ, `→`/Space/Esc. zoom.js nhường `→` (không Shift) khi replay -> `Shift+→` vẫn cuộn.
- live.js: `pauseLive/resumeLive`, bỏ qua nến realtime khi replay. Đổi mã/tài sản -> tự thoát replay. Chế độ thường không đổi.

## Bước 4 – Frontend tài khoản luyện tập (2026-11-27)
- `practice.js`: tạo phiên khi bắt đầu replay, `advance` sau mỗi step, render tiền/equity/P&L/vị thế/lệnh chờ, mũi tên khớp
  (`snapTime` theo khung đang xem), đường giá vốn/SL/TP/limit. `practice_orders.js`: form (SL/TP chỉ cho lệnh mua), `B`/`S`, huỷ lệnh, tab.
- `practice_drag.js`: bắt chuột ±5px quanh đường có `oid` (capture phase, không cho biểu đồ pan), kéo theo `coordinateToPrice`,
  thả -> PATCH; server từ chối -> vẽ lại chỗ cũ. CSS: `body.replay-on` ẩn `.real-only`, hiện `.practice-only` (viền cam).
- Ghi chú holdout đúng nguyên văn trên thanh replay, tô vàng khi con trỏ ≥ 2025.

## Bước 5 – Thống kê, lưu DB, phiên cũ (2026-11-27)
- `practice_stats.js`: tab Thống kê = chỉ số `mt5_report` (lãi ròng, PF, Sharpe, DD, thắng/thua, chuỗi…), equity nhỏ (AreaSeries),
  bảng lệnh; phiên đang chạy cập nhật khi tab mở; danh sách phiên cũ từ `/api/practice/history`, bấm để xem lại.
- Kết thúc (thoát/điểm mới/đổi mã) -> `POST end` (phiên không có lệnh khớp thì bỏ) -> run_id `practice-<id>` thêm vào ô "Bot".
- Test: `tests/test_web_replay_js.py` (13: hàm thuần qua node, ≤150 dòng + `node --check`, trang chủ nạp script + ghi chú holdout),
  `test_web_index.py` thêm id mới. Toàn bộ: 661 pass.
- Chưa làm: nút "So với bot gốc" (tuỳ chọn); test UI kéo-thả/biểu đồ thật trên trình duyệt (chỉ kiểm bằng hàm thuần + cú pháp).
