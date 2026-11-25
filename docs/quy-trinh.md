# Quy trình bot: từ INPUT đến OUTPUT

Có hai engine:
- **full** (mặc định, `live.py`): xem **Phần A**.
- **legacy** (`--engine legacy`, bot gốc của bài): xem **Phần B** (mục 1–5).

# Phần A — Engine full (LiveBot)

Ví dụ lấy từ lần chạy thật `uv run paper-bot once --dry-run --symbols AAPL MSFT NVDA` ngày 2026-11-25.
Lúc chạy là 08:50 giờ New York, thị trường đóng.

## A1. Khởi tạo (một lần)
1. CLI → `TradingConfig`: engine, flags (`--disable`), dry_run, use_llm, calib_end.
   Có `--reset-kill-switch` thì gỡ ngắt trong `data/live_state.json` trước.
2. Client Alpaca (`paper=True`). Store SQLite `data/trading.db`, `run_id = "live-dry"` (dry-run) hoặc `"live-paper"`.
3. Calibrator (nếu bật):
   - Dùng lại `data/models/calibrator.pkl` khi file ≤ 7 ngày và cùng use_forecast/calib_end.
   - Nếu không: fit trên cache 3 năm tới `min(hôm qua, calib_end = 2024-12-31)` (50 mã), timeout 300s.
   - Fit lỗi hoặc quá giờ → chạy không calibrator, ghi WARNING.
   - Ví dụ: fit mới mất dưới 1 phút.
4. Chiến lược: `RegimeSwitch`, hoặc MA+RSI nếu tắt regime. Có `--llm` và có key thì thêm LLM.
5. Kill switch: khôi phục `halted` từ `data/live_state.json`.

## A2. Một vòng (`run_cycle`)
```
B1  Giờ thị trường          đóng → WARNING, vẫn chạy (lệnh xếp hàng). Lỗi mạng ở B1–B4 → event cycle_error, bỏ vòng
B2  Đối soát (vòng đầu)     executor.reconcile: broker vs DB (chỉ đọc). Dry-run bỏ qua
B3  Tài khoản               equity, cash, last_equity, vị thế, mã có lệnh chờ
B4  Nến ĐÃ ĐÓNG             cutoff = hôm nay nếu đã đóng cửa sau 16:00 NY, ngược lại hôm qua
                            tải symbols + SPY + mã đang nắm ngoài danh sách (chỉ để tính tương quan)
B5  Forecast Chronos        cache theo (ngày nến cuối, mã): loop không tính lại trong ngày
Lặp từng mã (lỗi 1 mã → event symbol_error, đi tiếp):
B6  MarketState             regime (từ SPY), forecast, feature
B7  DecisionPipeline        chiến lược → veto → calibrator → 1 Signal; ghi MỌI tín hiệu (kể cả bị veto) vào signals
B8  Ngưỡng                  BUY có score < min_confidence → bỏ; SELL luôn đi
B9  make_order_intent       sizing ATR (hoặc legacy), bracket SL/TP, giới hạn tương quan, không trùng lệnh chờ
B10 Gửi                     bán trước, mua sau. Thật: AlpacaExecutor.submit. Dry-run: orders status "dry_run"
                            (kill switch đang ngắt → MUA bị ghi "blocked")
Cuối vòng:
B11 Sync fill               lệnh đã khớp của bot (cả lệnh con SL/TP) → fills, không trùng order_id. Dry-run bỏ qua
B12 Equity                  → bảng equity
B13 Kill switch             lỗ ngày > 3% (so với last_equity) hoặc sụt > 15% từ đỉnh → ngắt
                            thật: huỷ lệnh MUA chờ; dry-run: KHÔNG huỷ gì. Lưu data/live_state.json
```
`loop`: mỗi `--interval` phút chạy một vòng khi thị trường mở. Mọi lỗi đều được bắt, bot chờ vòng sau. Dừng bằng Ctrl+C.

## A3. Output
| Output | Ở đâu |
|---|---|
| Lệnh (chỉ khi không dry-run) | Tài khoản paper Alpaca |
| signals / orders / fills / equity / events | `data/trading.db`, lọc theo `run_id` |
| Trạng thái kill switch | `data/live_state.json` |
| Báo cáo | `paper-bot report`: tài khoản + `report_from_store("live-paper")` |

## A4. Ví dụ thật (2026-11-25, dry-run)
- cutoff = 2026-11-24.
- Calibrator fit mới, train tới 2024-12-31. Regime và forecast chạy bình thường.
- AAPL, MSFT, NVDA: không chiến lược nào sinh tín hiệu → 0 signal, 0 lệnh.
- DB `live-dry`: 1 dòng equity (99.893$, exposure 1%), event `calibrator` + `start`.
- `live_state.json`: `halted = false`.

# Phần B — Engine legacy (bot gốc)

Ví dụ minh hoạ lấy từ lần chạy thật `uv run paper-bot once` ngày 2026-11-23 (mã NVDA).
Lệnh tương ứng hiện nay là `--engine legacy`.

## 1. INPUT (bot nhận gì)

| # | Input | Nguồn | Ví dụ |
|---|---|---|---|
| I1 | Chế độ chạy + tham số | Dòng lệnh | `once`, `--symbols AAPL MSFT`, `--interval 30` |
| I2 | API key + secret | File `.env` | `ALPACA_API_KEY=PK...` |
| I3 | Tham số rủi ro | `config.py` (mặc định) | 1000$/lệnh, 2%/lệnh, ngưỡng confidence 0.5, 100 ngày, 5 mã |
| I4 | Nến ngày của từng mã | Alpaca Data API (feed IEX) | ~68 dòng `open, high, low, close, volume` |
| I5 | Trạng thái tài khoản | Alpaca Trading API | sức mua 399.996$, vị thế `{}`, lệnh chờ `{}`, thị trường đóng |

## 2. XỬ LÝ (từng bước)

```
[I1, I2, I3] ─► B1. Khởi tạo
                  └─ TradingConfig, log, client Alpaca (paper=True), 3 manager, 2 chiến lược

[I5: đồng hồ] ─► B2. Kiểm tra giờ thị trường  → đóng thì ghi cảnh báo, vẫn chạy tiếp

Lặp từng mã:
[I4] ─────────► B3. Tải dữ liệu       DataManager.get_historical_data
                                       lỗi/rỗng → bỏ qua mã này
                B4. Sinh tín hiệu     Mỗi chiến lược → 0 hoặc 1 TradingSignal
                                       (symbol, action, price, reason, confidence)
                B5. Gom theo hướng    {"BUY": [...], "SELL": [...]}
                                       có cả 2 hướng → bỏ qua mã này
                B6. Lọc confidence    trung bình < 0.5 → bỏ
[I5: lệnh chờ] ► B7. Chống trùng       mã đang có lệnh chờ khớp → bỏ
[I5: vị thế]  ─► B8. Quyết định        BUY  + chưa nắm  → mua
                                       SELL + đang nắm  → bán hết
                                       còn lại          → không làm gì
[I5: sức mua] ─► B9. Tính khối lượng   qty = max(1, int(min(sức mua × 2%, 1000) / giá))
                B10. Đặt lệnh          Lệnh market, hiệu lực trong ngày (DAY)
```

## 3. OUTPUT (bot tạo ra gì)

| # | Output | Ở đâu |
|---|---|---|
| O1 | Lệnh mua/bán | Tài khoản Paper trên Alpaca (xem tab Orders) |
| O2 | Log mọi bước | Màn hình + file `trading_bot.log` |
| O3 | Báo cáo tài khoản (chỉ chế độ `report`) | Màn hình: equity, sức mua, số vị thế, lãi/lỗ trong ngày |

## 4. Lần theo một ví dụ thật: NVDA

| Bước | Dữ liệu vào | Kết quả |
|---|---|---|
| B3 | 100 ngày lịch | ~68 nến, giá đóng cửa gần nhất 125,65 |
| B4 – MA | MA10 vs MA30 hôm nay và hôm qua | Không có crossover → không có tín hiệu |
| B4 – RSI | RSI(14) = 13,8 < 30 | BUY, conf = min(0.9, 0.6 + (30 − 13,8)/100) = **0,76** |
| B5 | `{BUY: [RSI]}` | Chỉ 1 hướng → đi tiếp |
| B6 | Trung bình = 0,76 ≥ 0,5 | Qua |
| B7 | Lệnh chờ = {} | Qua |
| B8 | BUY, vị thế = 0 | → mua |
| B9 | min(399.996 × 2% = 7.999,9; 1000) = 1000 → 1000 / 125,65 = 7,96 | **qty = 7** |
| B10 | `MarketOrderRequest(NVDA, 7, buy, DAY)` | Lệnh xếp hàng (thị trường đóng), khớp lúc mở cửa |
| O2 | | `MUA 7 NVDA @~125.65 (conf=0.76)` |

4 mã còn lại (AAPL, GOOGL, MSFT, TSLA) dừng ở B4 vì không chiến lược nào báo tín hiệu.

## 5. Chế độ `loop`

Lặp `B2 → B10` mỗi `--interval` phút. Nếu thị trường đóng thì bỏ qua B3–B10, chỉ ghi "đang đóng cửa".
Dừng bằng Ctrl+C.
