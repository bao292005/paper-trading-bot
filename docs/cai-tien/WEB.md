# Nhật ký web app (agent j-web)

## Lệnh của tôi trên chart (agent j-fills, 2026-11-27)
- **API (chỉ đọc)** `GET /api/account/fills?symbol=&asset=&since=` (`web/api/account_fills.py`): `get_orders(status=closed, nested=True, limit=500)`,
  giữ lệnh `filled_qty > 0`, bung chân bracket/OCO; `since` = unix giây/ISO (mặc định 30 ngày; `after` gửi Alpaca lùi thêm 7 ngày cho lệnh GTC cũ).
  Mã crypto chuẩn hoá `ETHUSD` -> `ETH/USD`. Cache 10 giây theo (mã, asset, since). Thiếu key -> 503, lỗi Alpaca -> 502. Không gửi/huỷ lệnh.
- **Nguồn** (ưu tiên từ trên xuống): `bracket TP/SL`, `OCO TP/SL` (theo order_class + loại chân) > `server SL/TP` (event `sltp_hit` trùng order_id)
  > `web` (event `web_manual_order`/`web_position_order`/`web_close_position` cách giờ gửi lệnh <= 10s, cùng mã) > `bot` (order_id trong bảng `orders`, run `live-*`) > `khác (dashboard Alpaca)`.
- **Chart** (`fills_math.js`, `fills_lines.js` = hàm thuần; `fills.js` = nối DOM): mũi tên mua ▲ xanh / bán ▼ đỏ tại nến chứa giờ khớp (snap theo nến đã tải,
  đúng mọi khung/múi giờ), nhãn `B 0.02 @1722.8`, TP/SL ghi `TP`/`SL`. Tooltip: giờ (theo `CH.tz`), hướng, qty, giá, loại, nguồn, P/L FIFO của lệnh đóng.
  Gộp chung với marker của Bot thành 1 danh sách (chart chỉ cho 1 bộ marker/series). Không hiện khi Replay.
- **Đường giá**: vị thế = nét liền xanh `Vị thế 0.02 @1722.75`; lệnh chờ = nét đứt `BUY LMT 0.02`; cái đã có box Long/Short thì không vẽ trùng.
- **Tự cập nhật**: panel làm mới 10s (trước 15s) và gọi lại fills + orders; lệnh khớp mới -> marker ngay + toast 6s
  (`✅ Đã khớp MUA 0.02 ETH/USD @1722.75`, `🎯 TP khớp ...`, `🛑 SL khớp ...`), không báo lại id đã báo; lần tải đầu chỉ ghi nhớ.
- **Công tắc** "Lệnh của tôi" (mặc định bật, nhớ trong localStorage `ptb.myFills`). Kéo về quá khứ -> `since` lùi theo nến cũ nhất.
- Test: `tests/test_web_fills.py` (API), `tests/test_web_fills_js.py` (node). Hạn chế: lệnh đóng vị thế mở TRƯỚC khoảng đã tải không có lô để ghép P/L.

## SL/TP cho mọi vị thế (agent j-sltp, 2026-11-27)
Nút **SL/TP** trên từng dòng vị thế (thật + luyện tập) -> box vị thế hiện trên chart đúng mã, entry = giá vốn TB, SL/TP = mức đang có
(chưa có: ±2×ATR / 3×ATR). Kéo hoặc nhập số -> **Áp dụng** (thật: XAC NHAN) / **Gỡ SL/TP** (thật: XAC NHAN).
Long: SL < giá hiện tại < TP; short ngược lại. Sai thứ tự -> chặn; mức đã bị vượt -> hỏi thêm 1 lần (`allow_cross`), server cũng kiểm (409).
- **Bước 1 – live_state + monitor**: `live.py` thêm `claim/mark/release/remove_crypto_sltp`, cờ `triggering` (quá 120s = hết hạn) chống bán 2 lần;
  LiveBot bỏ qua entry đang triggering và claim trước khi bán. `web/sltp_monitor.py` (SLTPMonitor): chạy trong lifespan web (1 monitor,
  chỉ khi dùng client thật), mỗi `PAPER_SLTP_INTERVAL` giây (mặc định 3) lấy giá mid/trade; long chạm SL/TP -> market SELL đúng qty broker
  (kill switch không chặn lệnh bán). Event run_id `web-sltp`: `sltp_trigger` / `sltp_hit` / `sltp_failed` / `sltp_cleared` (vị thế về 0 -> xoá).
  Công tắc lưu ở `live_state["web-sltp"]` (mặc định BẬT). Lỗi mạng chỉ ghi `last_error`, không làm sập server.
- **Bước 2 – API** (`web/api/sltp.py`): `POST /api/sltp` {asset, symbol, sl, tp, confirm[, allow_cross]}: cổ phiếu = OCO Alpaca
  (limit TP + stop SL, qty = số đang nắm, chiều ngược, GTC); đã có OCO/bracket -> replace từng chân, lỗi -> huỷ hết lệnh đóng rồi đặt OCO mới
  (`mode` = replace / cancel_replace / oco_new); lệnh không phải đóng vị thế không bị đụng. Crypto = ghi live_state (`created_by="web"`), entry đang
  triggering -> 409. `DELETE /api/sltp`, `GET /api/sltp` (levels + nguồn `sàn` / `server` / `bot`), `GET /api/sltp/status`, `POST /api/sltp/monitor`
  {enabled, confirm}. Event run_id `web-manual`: `web_sltp_set` / `web_sltp_remove` / `web_sltp_monitor`. `GET /api/position/boxes` nhận OCO.
  Luyện tập: `PracticeAccount.set_position_sltp` + `POST /api/practice/{sid}/sltp` (null/null = gỡ). Test `test_web_sltp.py`, `test_sim_position_sltp.py`.
- **Bước 3 – UI**: `sltp_math.js` (SLT.check/levels/statusText, hàm thuần), `sltp.js` (mở box, ô nhập, Áp dụng/Gỡ), `sltp_panel.js`
  (cột SL/TP/Nguồn, dòng "Server canh SL/TP ● (kiểm cách đây Xs)" + nút Bật/Tắt). Box vị thế kéo tự do (SL qua được giá vốn, chỉ cần SL < TP).
  Test `test_web_sltp_js.py`.

## Short (agent j-short, 2026-11-27)
Lệnh SHORT: box Short Position + bán khống (luyện tập: stock + crypto; thật: chỉ cổ phiếu). Bot tự động vẫn chỉ long.
- **Bước 1 – sim** (`sim/*`, `report.py`): vị thế có dấu (qty < 0 = short); bán khi không có vị thế = mở short, mua khi short = đóng/giảm;
  lệnh đảo chiều -> 422 "đóng vị thế trước". SL short ở trên (khớp khi high ≥ SL), TP ở dưới; gap khớp open; SL trước TP.
  Tiền: mở short cash += tiền bán − phí; equity = cash + qty×giá; ký quỹ 1x (giá trị short sau lệnh ≤ equity);
  equity ≤ 0 -> tự đóng `margin_call` (lệnh mua stop tại cash/|qty|). Bỏ qua phí vay (ghi trong notes). `mt5_report`
  tách `short_trades/long_trades` + tỉ lệ thắng theo `trade.side`. Test `tests/test_sim_short.py`.
- **Bước 2 – box Short (JS)**: tham số `side` xuyên suốt `position_*.js` (không nhân đôi code): `PM.dir/lohi`, kéo giữ TP<entry<SL,
  R:R = (entry−TP)/(SL−entry), qty = rủi ro/(SL−entry+phí), gợi ý market/limit bán (trên giá)/stop bán (dưới giá);
  box đảo màu (`.pbox.short`), nút ⊟ Short / `Shift+P`; thật + crypto -> khoá nút kèm tooltip. Test `test_web_position_short_js.py`.
- **Bước 3 – Alpaca paper**: `POST /api/position` nhận `side` (mặc định long). Short cổ phiếu = bracket **SELL** GTC (market/limit/
  stop_limit, limit = entry−0,5% nhưng > TP); trước khi gửi: `get_asset` phải `shortable` + `easy_to_borrow`, không đang long mã đó,
  qty nguyên; crypto short -> 422 "Alpaca không cho bán khống crypto". repost/legs dùng cùng luật theo chiều.
  `GET /api/position/boxes` nhận bracket bán = box short, vị thế qty < 0 = short. Mới: `POST /api/position/close` (XAC NHAN,
  market ngược chiều đúng |qty|: short -> MUA). `/api/orders` (form cũ) giữ nguyên hành vi — short thật chỉ mở qua box.
  Test `tests/test_web_position_short.py` (bracket SELL, không shortable/crypto/đang long -> 422 không gửi, thiếu XAC NHAN -> 403,
  box short, đóng short = lệnh mua, short luyện tập qua `/api/practice`).
- **Bước 4 – UI**: modal XAC NHAN khi short thật thêm dòng cảnh báo rủi ro lỗ không giới hạn/phí vay/ký quỹ; panel vị thế có nhãn
  LONG/SHORT + nút Đóng; form luyện tập: S/bán khi không có vị thế = mở short (không SL/TP — short kèm SL/TP dùng box), phím B/S đối xứng
  (ngược chiều = đóng/giảm).

## Long Position box (agent j-pos, 2026-11-27)
Box TP/SL kéo thả kiểu TradingView trên trang `/`, chỉ long, cho cả tài khoản luyện tập (replay) và Alpaca paper.
- **JS** (mỗi file ≤150 dòng): `position_math.js` (PM: snap, ATR14, mức mặc định, phí, qty theo rủi ro, R:R, gợi ý loại lệnh,
  ràng buộc kéo SL<entry<TP, P/L chưa chốt) · `position_box.js` (vẽ overlay HTML, vẽ lại khi zoom/cuộn/Log/%/resize, nhãn, nút
  Áp dụng/Huỷ thay đổi) · `position_drag.js` (công cụ ⊞ Long/P, bắt chuột pha capture, tắt scroll/scale chart khi kéo, Alt = dời box)
  · `position_orders.js` (panel rủi ro/qty/loại lệnh, đặt lệnh) · `position_sync.js` (box lệnh chờ/vị thế, sửa bằng kéo).
- **Thống nhất với `practice_drag.js`**: đường SL/TP/limit của lệnh đã có box (`pbOwnsOid`) chỉ kéo được qua box, tránh 2 hệ kéo trùng.
- **Luyện tập**: POST `/api/practice/{sid}/orders` (không XAC NHAN); kéo box -> PATCH lệnh mua chờ (price/sl/tp) hoặc lệnh con SL/TP (price).
- **Thật** (guard + XAC NHAN + log_event trước khi gửi, run_id `web-manual`; API cũ `/api/orders` giữ nguyên):
  - `POST /api/position` – cổ phiếu bracket GTC (market / limit / stop -> stop_limit, limit = entry+0,5% nhưng < TP);
    crypto market/limit GTC không bracket + `set_crypto_sltp` ghi `pending[sym]` (kèm order_id) vào `data/live_state.json`.
  - `POST /api/position/repost` – sửa lệnh chờ: huỷ `old_order_id` rồi đặt lại (Alpaca không sửa được chân bracket đang held).
  - `POST /api/position/legs` – sửa SL/TP vị thế: cổ phiếu `replace_order_by_id` (SL stop_price, TP limit_price); crypto ghi live_state.
  - `GET /api/position/boxes?symbol&asset` (chỉ đọc) – box lệnh chờ + vị thế (crypto lấy SL/TP từ live_state) + bước giá/qty crypto.
- **live.py**: `save_state`/`set_crypto_sltp` ghi nguyên tử (file tạm + `os.replace`) có khoá `flock`; bot crypto gọi
  `reload_sltp()` đầu mỗi vòng để nhận SL/TP web vừa đặt/sửa (không web sửa thì y hệt bộ nhớ -> hành vi bot không đổi).
- Test: `tests/test_web_position.py` (client giả: payload bracket, replace chân, thiếu XAC NHAN -> 4xx không gửi, SL/TP sai -> 422,
  crypto ghi live_state không bracket, boxes, reload_sltp, PATCH luyện tập), `tests/test_web_position_js.py` (toán + đồng bộ, node).

## Realtime v2 (agent j-rt, 2026-11-27)
Bối cảnh: log server có 78 lần "connection limit exceeded"; nến chỉ nhảy theo phút (chỉ có bars).
### Bước 1-2 – Hub bền + trades + throttle
- `web/hub.py`: 1 stream sống lâu/loại tài sản. Hết người xem -> chờ `grace` (server: `GRACE_SECONDS`=300s
  trong `app.py`; lớp mặc định 0 để test cũ giữ hành vi đóng ngay). Lỗi -> `_dispose()` (chờ `adapter.stop()`
  đóng socket) RỒI mới tạo stream mới. "connection limit exceeded" -> trạng thái `limit`, chờ 30→60→…→300s,
  status có `retry_in` + câu "Alpaca đang giữ kết nối cũ, thử lại sau Xs". Shutdown: huỷ task trước, đóng sau.
- Trades: `_on_trade` dựng phút đang chạy (h/l/c, vol += size), bar chính thức (`final`) thắng trade.
  `candle()` cache phần gộp các phút trước phút cuối -> mỗi tick O(1), không sort lại dict. Trade chỉ
  đánh dấu `_dirty`, `_flush_loop` đẩy mỗi 0.25s (≤4 tin/s/client); bar đẩy ngay. Tin "bar" thêm `ts`
  (unix giây trade cuối) để client đo độ trễ. Stock ngoài phiên chính: bỏ trade như bỏ bar.
- `api/live.py`: adapter KHÔNG dùng `_run_forever()` (hàm đó tự nuốt lỗi limit và tự thử lại 1-30s, chính
  là nguồn 78 lần lỗi) mà chạy 1 phiên `_start_ws/_send_subscribe_msg/_consume`, lỗi thì raise; `stop()`
  gọi `stream.stop()` + `await stream.close()` + chờ phiên kết thúc. Subscribe thêm trades.
- `_send` lỗi không còn xoá client khỏi `clients` (trước đây làm `refs` lệch -> mã không bao giờ unsubscribe).
- Test: `tests/test_web_rt.py` 9 test (grace, hết grace, limit backoff + dọn stream, trade/bar, ngoài giờ,
  throttle, flush loop, shutdown, adapter thật không mạng). test_web_live/crypto cũ giữ nguyên, pass.
### Bước 3 – Frontend
- File mới `static/js/indicators_live.js` (`IL`): MA10/30/200 + RSI14 (kiểu SMA như `rsi_series`) của NẾN CUỐI
  từ ~201 giá đóng cửa cuối -> mỗi tick O(200). `upsertLast` thay điểm cùng time / thêm time mới.
- `live.js` viết lại: mỗi tin "bar" -> `series.update()` nến + volume + MA/RSI điểm cuối + `refreshLegend()`.
  Badge: "Realtime ● (độ trễ Xs)" xanh (độ trễ = giờ máy − `ts` trade cuối); vàng khi stream `limit`/`reconnecting`
  (kèm lý do + đếm ngược), đóng cửa, đang poll; đỏ "mất kết nối, thử lại sau Xs". WS im lặng > 20s khi thị trường
  mở (crypto luôn mở) -> poll `/api/bars` (3 nến cuối) mỗi 10s, có tin WS lại thì ngừng; hổng lớn -> `loadAll`.
  Nối lại 1→2→…→15s. Tab hiện lại: nối ngay nếu WS đã đóng + tải bù. Replay: bỏ tin realtime, dừng poll.
- Test: `tests/test_web_rt_js.py` 5 test node (chỉ báo khớp pandas ở mọi vị trí, nến cùng time/mới/cũ/mã khác/
  replay, poll dự phòng bật/tắt, badge vàng/đỏ). `node --check` OK; 255 test web pass.
### Bước 4 – REST gần hiện tại + chạy thật + README
- `data/sources.py`: `feed_order(end, now)`: end trong 15 phút gần nhất (hoặc tương lai) -> chỉ IEX (SIP free chắc
  chắn lỗi), ngược lại SIP rồi IEX. Dùng ở `fetch_minute_bars` (nhận thêm `now`, `_ensure_month` truyền vào) và
  `AlpacaDataManager._fetch_api`. Cache lịch sử không đổi. Test: 2 test mới trong `test_data_intraday.py`.
- Chạy thật adapter `AlpacaStreamAdapter` crypto (không đụng server 8600, không bị limit): subscribe OK
  (trades+bars+updatedBars). BTC/USD 20s: 0 trade; 4 mã 75s: 3 trade, bar phút trade_count 0 -> sàn crypto Alpaca rất thưa.
  Độ trễ trade -> máy: ~0.06s (âm nhẹ do lệch đồng hồ, coi như ≈ 0s).
- Hệ quả và sửa: heartbeat server 30s -> 10s (`HEARTBEAT_SECONDS` trong `app.py`); client coi tin status "connected"
  là WS còn sống (không bật poll vô ích); độ trễ đo lúc nhận trade mới, không tăng dần khi không có trade. +1 test node.
- README: mục "Realtime trên web" (1 stream/loại/key: không chạy 2 server, không chạy script khác dùng stream cùng key).
- Còn lại: `stop()` làm `_consume` raise ConnectionClosedOK (vô hại, hub đã huỷ task trước khi dispose); chưa thử trên
  trình duyệt thật / giờ mở cửa cổ phiếu.

## Zoom biểu đồ (agent j-zoom, 2026-11-27)
- File mới (`static/js/`, mỗi file ≤ 150 dòng): `zoom_math.js` (hàm thuần: zoom factor, cuộn, toạ độ chọn → range, giữ range khi prepend,
  đổi khung theo thời gian), `pricescale.js` (Thường/Log/% + Auto + giá thủ công, nhớ chế độ ở localStorage `paperbot.priceMode`),
  `zoombox.js` (Shift+kéo), `zoom.js` (`attachZoom(chart, series, el, {bar})` trả `reset/zoomIn/zoomOut/toLatest/fitAll/setMode/setAuto/saveRange/restoreRange`).
- Gắn vào chart chính "/" (`#zoombar`, id `z-*`) và chart Visual của /tester (`#vz-bar`, id `vz-*`). Không đổi backend, không đổi `static/vendor`.
- Nút (góc dưới phải chart, tooltip ghi phím): `−` (-) · `+` (+ hoặc =) · `⟲` reset ~150 nến cuối + auto giá (0) · `→|` về hiện tại (End) ·
  `⤢` fit all · ô chọn trục giá Thường/Log/% (L bật/tắt Log) · nút Auto/Tay (A). Mỗi lần zoom 22%, quanh nến cuối nếu đang thấy, không thì quanh tâm.
- Phím tắt (bỏ qua khi focus ở input/textarea/select, hoặc có Ctrl/Cmd/Alt): `+`/`=` vào, `-` ra, `←`/`→` cuộn 10% vùng nhìn, `Home` nến đầu đã tải,
  `End` hiện tại, `0` reset, `L` Log, `A` Auto, `Esc` huỷ vùng đang chọn. Trên /tester chỉ hoạt động khi tab Visual đang hiện.
- Box zoom: giữ **Shift + kéo chuột trái** → div mờ; thả chuột: thời gian bằng `coordinateToLogical`, giá bằng `coordinateToPrice` + `priceScale.setVisibleRange`
  (tự tắt auto). Trong lúc kéo tắt `handleScroll/handleScale`; vùng < 5 px bị bỏ. Double-click trục giá hoặc ⟲ → về auto-scale.
- Lần đầu / đổi mã: ~150 nến cuối (không còn `fitContent`). Đổi khung: `saveRange` (khoảng thời gian) → `restoreRange` sau khi nạp; nếu khung mới chưa phủ
  tới thời điểm cũ hoặc có < 10 nến thì lùi về ~150 nến. Đang ở mép phải thì tiếp tục theo nến mới.
- Realtime: `shiftVisibleRangeOnNewBar: true` — đang xem quá khứ thì chart KHÔNG tự cuộn; ở mép phải thì theo nến mới (`live.js` không đổi).
- Lazy load: `loadOlder` vẫn cộng offset nhờ `ZM.shiftRange` (vùng nhìn không nhảy), có test.
- Hạn chế: ở chế độ Log/% thư viện dùng đơn vị giá nội bộ nên Box zoom chỉ phóng theo thời gian (giá vẫn auto); chưa thử trên trình duyệt thật
  (chỉ node + chart giả + TestClient); thanh zoom che một phần chart/RSI ở góc phải dưới (mờ 80%); trên /tester kéo thanh trượt phát lại vẫn đặt lại vùng ~120 nến.
- Test: `tests/test_web_zoom.py` (12: hàm thuần, lazy load, đổi khung, attachZoom với chart giả), `tests/test_web_zoom_page.py` (id nút, thứ tự script, ≤ 150 dòng, `node --check`).

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
