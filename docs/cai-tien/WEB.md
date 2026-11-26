# Nhật ký web app (agent j-web)

## Chế độ crypto cho web + /tester (agent j-cx-live, 2026-11-26)
Chi tiết từng bước: [CRYPTO-LIVE.md](CRYPTO-LIVE.md). Cổ phiếu giữ nguyên hành vi (tin/phản hồi cổ phiếu không thêm khoá `asset`).
- Trang `/`: ô chọn Stock/Crypto; `/api/symbols|bars|indicators|markers|positions|orders` nhận `asset`; crypto 24/7, giờ UTC;
  `/ws/live?asset=crypto` dùng `CryptoDataStream` (tối đa 1 stream cổ phiếu + 1 stream crypto); form lệnh crypto: qty lẻ, market/limit/stop-limit,
  không SL/TP, GTC, vẫn cần `XAC NHAN` + Origin/header.
- Trang `/tester`: ô `s-asset`; `GET /api/tester/options?asset=` trả mã, `min_date`, mặc định ngày/phí, IS/OOS theo asset;
  body `asset` ("stock" mặc định | "crypto"). Crypto: mã chỉ trong 5 mã whitelist, từ 2021-01-01 (mặc định 2022-01-01), tới 2024-12-31 (holdout vẫn chặn),
  phí mặc định 25 bps, tối ưu IS 2022–2023 / OOS 2024 (server lấy từ profile, không nhận ngày). Config gửi `run_tester` chỉ có khoá `asset`
  khi là crypto. Visual/nến phút lấy `/api/bars?asset=`, nến phút crypto hiện giờ UTC. Lưu DB nhận qty lẻ.
- Test: `tests/test_web_crypto.py`, `tests/test_web_tester_crypto.py`, `tests/test_ui_crypto.py`.

## Trang /tester kiểu MT5 Strategy Tester (agent j-webt, 2026-11-26)

### Bước 1 – API + job runner
- File: `web/jobs.py` (JobManager: thread nền, tối đa 2 job chạy cùng lúc -> 429, job xong tự dọn sau 1 giờ,
  lỗi của tester được bắt thành chuỗi `error`), `web/api/tester.py`, `app.py` (thêm router, `state.jobs`, route `GET /tester`).
- Endpoint: `GET /api/tester/options`, `POST /api/tester/run`, `POST /api/tester/optimize`,
  `GET /api/tester/jobs/{id}` (status running|done|error, progress {done,total}, result), `POST /api/tester/save`.
- An toàn: chỉ đọc + tính toán, không đặt lệnh. POST qua `guard` (Origin/Host, X-Requested-With, JSON) dùng lại của orders.
  Kiểm tra input: mã thuộc UNIVERSE_50 + SPY (≤ 20), ngày 2019-01-01..2024-12-31 (>= 2025 -> 400 giải thích HOLDOUT),
  số trong khoảng hợp lý (bool/chuỗi bị từ chối), ma_short < ma_long, cờ chỉ thuộc FEATURE_FLAGS,
  lưới 1..3 tham số trong whitelist và ≤ 500 tổ hợp. B2_goc bỏ qua atr_stop_mult/take_profit_mult (chỉ FULL có).
- Không ghi DB trừ `/save`: tên `[a-z0-9-]{1,40}`, run_id `bt-tester-<tên>`, dựng lại equity + fills từ curve/replay của job rồi gọi `save_result`.
- Test: `tests/test_web_tester.py` (34: job -> poll, tối ưu có tiến độ, input sai 4xx, holdout 400, lưới > 500, Origin lạ 403,
  tối đa 2 job, TTL, lưu DB, không ghi DB khi không bấm lưu) + `tests/test_web_tester_real.py` (run_tester thật 2 mã, 6 tháng 2019, B2).

### Bước 2 – Trang /tester: cài đặt, báo cáo, đồ thị, lệnh
- File: `templates/tester.html`, `static/tester.css`, `static/js/tester_{form,job,report,chart,trades,main}.js` (mỗi file ≤ 150 dòng,
  có test kiểm tra), nút "Strategy Tester" ở thanh trên của "/" và link "← Biểu đồ" ở /tester.
- Form: chiến lược, mode, ngày (ghi chú vì sao tối đa 2024-12-31), fill model, deposit, phí, ≤ 20 mã, tham số, rủi ro, 6 cờ (FULL).
  B2_goc tự khoá ATR stop / take profit / cờ. Trình duyệt kiểm tra trước, server kiểm tra lại.
- Start -> POST job -> poll mỗi 0,6 s (thanh tiến độ). Báo cáo MT5 2 cột; đồ thị Balance (xanh dương) + Equity (xanh lá) + drawdown (pane dưới);
  bảng lệnh sắp xếp được, xuất CSV (chặn formula-injection), "Lưu vào DB" gọi `/api/tester/save`.
- Kế thừa: `/api/bars`, `chartTheme`, `api.js` (getJSON/sendJSON/fmt/esc), Lightweight Charts v5.3.0 có sẵn.

### Bước 3 – Visual mode
- File: `tester_replay.js` (phát lại: Play/Pause/Step, tốc độ 1x/5x/20x/max, thanh trượt), `tester_visual.js` (nến ngày + markers + đường SL/TP + equity nhỏ),
  `tester_minute.js` (bấm 1 lệnh trong bảng -> khung nến 5m ngày vào/ra, giờ New York, kèm giá vào/ra/SL/TP).
- Nến ngày lấy qua `/api/bars` (tf=1D) trong đúng khoảng from..to của run nên không đụng holdout. Dữ liệu phát lại lấy từ `result.replay`.
- Test bằng node + DOM giả (`tests/test_web_tester_js.py`): bước phát lại, markers, đường SL/TP.

### Bước 4 – Tab Optimization
- File: `tester_opt.js` (form + chạy job), `tester_opt_view.js` (hiển thị). Chọn 1–3 tham số (B2_goc ẩn ATR/TP), nhập danh sách `5,10,15`
  hoặc dãy `đầu:bước:cuối`, đếm tổ hợp trực tiếp, khoá nút Start nếu > 500. Tiêu chí: sharpe | net_profit | profit_factor | recovery_factor.
- Kết quả: bảng xếp hạng IS và OOS (hạng + chỉ số), dòng tốt nhất theo IS được tô và hiện hạng OOS của nó, heatmap IS | OOS (lưới div, 2 tham số đầu),
  khung cảnh báo overfit luôn hiện. Bấm 1 dòng -> chạy run_tester đầy đủ với tham số đó (ngày theo panel Cài đặt) và mở tab Báo cáo.
- Optimization luôn dùng IS 2019–2022 / OOS 2023–2024 cố định phía server, ngày trong Cài đặt không áp dụng (ghi rõ trong khung cảnh báo).
- Test: `tests/test_web_tester_js.py` (9: parseValues đúng/sai, render bảng + heatmap + best + note), `tests/test_web_tester_page.py` (id JS có trong HTML).
- Hạn chế: chưa thử trong trình duyệt thật (chỉ node + TestClient); nến phút cần cache nến phút; heatmap chỉ 2 tham số đầu; tối ưu nhiều mã/50 tổ hợp có thể chạy lâu.

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
