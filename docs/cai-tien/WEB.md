# Nhật ký web app (agent j-web)

## Bước 4 – Đặt/huỷ lệnh paper + nút chạy bot (2026-11-26)
- Backend mới: `web/api/order_rules.py` (guard + kiểm tra đầu vào, hàm thuần), `web/api/orders.py`
  (`POST /api/orders`, `DELETE /api/orders/{id}`, `POST /api/bot/run`), đăng ký trong `app.py`.
- Chốt chặn: mọi POST/DELETE qua `guard` (Origin/Host localhost, header `X-Requested-With: paper-web`, Content-Type JSON,
  thiếu thì 403/415); `confirm` phải đúng "XAC NHAN" (không strip/đổi hoa thường); mã A-Z tối đa 5; qty là số nguyên
  1..10000 (bool/float/chuỗi bị từ chối); giá hữu hạn > 0; mua SL<giá<TP, bán TP<giá<SL.
  Lệnh market có SL/TP phải kèm `ref_price` (UI lấy giá nến cuối) để so SL/TP. Có cả SL+TP -> bracket, chỉ 1 chân -> OTO. TIF = DAY.
- Chỉ dùng TradingClient paper (`get_client`). Ghi event `web_manual_order` / `web_cancel_order` / `web_run_bot`
  (run_id `web-manual`) TRƯỚC khi gửi; ghi lỗi thì HỦY gửi (500). Thiếu key -> 503, chưa ghi gì.
- Chạy bot: tái dùng `ui.core.build_once_command` + `run_command` (không viết lại chốt chặn); mặc định dry-run;
  `dry_run=false` cần confirm đúng, `allow_real=True` chỉ khi đó.
- Frontend: `js/modal.js` (hộp xác nhận, nút Gửi chỉ bật khi gõ đúng XAC NHAN), `js/orders.js` (form + huỷ lệnh),
  `js/botrun.js` (nút dry-run / chạy THẬT), `api.js` thêm `sendJSON`; panel lệnh chờ có nút Huỷ; form + modal trong index.html.
- Test: `tests/test_web_orders.py` (75 test: input sai/thiếu confirm/header lạ/Origin lạ đều 4xx và KHÔNG gọi submit_order;
  hợp lệ gọi đúng 1 lần và event được ghi trước khi gửi; huỷ lệnh; chạy bot với run_command giả). Tổng web+intraday: 112 pass.
- Hạn chế: chưa thử giao diện trên trình duyệt thật và KHÔNG đặt lệnh thật; DELETE kèm body JSON (fetch hỗ trợ,
  một số proxy thì không); ref_price do trình duyệt gửi chỉ dùng để kiểm tra hợp lý, Alpaca vẫn kiểm tra lại phía họ.

## Bước 3 – Frontend trang "/" (2026-11-26)
- File: `web/templates/index.html`, `static/style.css`, `static/js/{api,chart,legend,data,live,panel,main}.js` (mỗi file < 100 dòng);
  `app.py` thêm route `GET /` (FileResponse); `api/markers.py` thêm `GET /api/runs` (danh sách run_id cho ô chọn bot).
- Làm gì: candlestick + volume + MA10/30/200 (pane 0), RSI14 (pane 1, bật/tắt = tạo/xoá series), markers tín hiệu/fill
  kèm tooltip, price line (giá vào / SL / TP / lệnh chờ) từ panel, legend OHLC theo crosshair, kéo trái < 20 nến thì tải thêm
  (`before=`), giữ nguyên vị trí xem. Trục thời gian hiển thị giờ New York. WS tự nối lại backoff 1..30s, badge trạng thái.
- Panel phải: tài khoản, vị thế, lệnh chờ, làm mới 15s; bấm hàng để đổi mã.
- Test: `tests/test_web_index.py` (3 test: "/" 200 + id chính, mọi file tham chiếu tải được, /api/runs rỗng).
- Hạn chế: MA/RSI không cập nhật theo nến realtime (chỉ nến + volume); chưa thử trên trình duyệt thật (chỉ node --check + TestClient).

## Bước 2 – Backend FastAPI + WS realtime (2026-11-26)
- File mới: `web/__init__.py`, `web/__main__.py`, `web/app.py` (create_app, nhận phụ thuộc giả để test),
  `web/hub.py` (LiveHub), `web/api/{timeutil,security,market,trading,markers,live}.py`;
  test: `tests/test_web_{market,trading,live}.py` (22 test offline, pass cùng 12 test intraday).
- Endpoint: /api/symbols, /api/bars, /api/indicators, /api/markers, /api/account, /api/positions, /api/orders (chỉ đọc), WS /ws/live.
- Quy ước thời gian: `time` = unix giây UTC; nến ngày = 12:00 UTC của ngày đó. `before=<unix>` chỉ lấy nến time < before (kéo về quá khứ).
- Realtime: LiveHub giữ 1 stream Alpaca (adapter bọc StockDataStream, feed IEX; subscribe qua thread vì alpaca-py
  dùng run_coroutine_threadsafe().result()). Nạp seed nến phút hôm nay qua REST, gộp lên tf từng client, đẩy nến đang chạy.
  Lỗi stream -> tạo stream mới, backoff 1,2,4..60s (reset nếu chạy ổn >60s). Hết người xem thì đóng stream.
  Heartbeat 30s gửi trạng thái market open/closed + stream. WS kiểm tra Origin/Host localhost, mã, tf.
- Kế thừa: `ui.core` chưa dùng ở bước này (dành cho bước 4); `dashboard.connect_readonly` đọc DB; `features.rsi_series` cho RSI14.
- Hạn chế: ngày lễ chưa tính vào "market closed"; volume stream (IEX) khác volume REST (SIP) nên nến khung lớn đang chạy có thể lệch volume;
  ruff chưa cài trong môi trường nên chưa lint; chưa thử WS với Alpaca thật (không tự mở server/stream dài hạn).

## Bước 1 – Dữ liệu nến phút (2026-11-26)
- File: `src/paper_bot/data/intraday.py` (mới), `tests/test_data_intraday.py` (mới, 12 test).
- Làm gì: `load_intraday(symbol, tf, start, end)` với tf ∈ 1m/5m/15m/1h/1D; `resample_bars(df_1m, tf)`;
  `regular_session(df)`; `fetch_minute_bars(client, symbol, start, end)` (SIP trước, lỗi thì IEX, adjustment ALL).
- Đầu ra: cột open/high/low/close/volume, index giờ New York không timezone, chỉ 09:30–15:59 (nến phút).
  1D đọc từ cache ngày (`data/bars`, index là ngày).
- Cache phút: `data/bars_1m/<SYMBOL>/<YYYY-MM>.parquet`. Tháng đã qua có file thì không tải lại;
  tháng hiện tại tải phần thiếu (từ nến cuối đã cache + 1 phút). Đóng cửa/cuối tuần mà cache đã đủ thì không gọi API.
  Cả hai feed lỗi -> không ghi cache, trả phần đã có.
- Resample: mốc mỗi ngày bắt đầu 09:30 (1h = 09:30, 10:30, … 15:30; nến 15:30 chỉ 30 phút).
- Kế thừa: `load_bars`/`PROJECT_ROOT` từ `data/cache.py`; cách thử SIP→IEX từ `AlpacaDataManager._fetch_api`.
- Test: 12 test offline (client giả) pass. Tải thật AAPL 2026-10: 8580 nến phút = 22 ngày x 390, 154 nến 1h.
- Hạn chế: nến phút cache theo giá điều chỉnh tại thời điểm tải (split/cổ tức sau đó không cập nhật);
  ngày lễ ở tháng hiện tại bị hỏi lại API mỗi lần; alpaca-py đổi start/end thành UTC naive (fake client phải tính tới).
