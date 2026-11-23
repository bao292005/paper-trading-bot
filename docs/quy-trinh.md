# Quy trình bot: từ INPUT đến OUTPUT

Ví dụ minh hoạ lấy từ lần chạy thật `uv run paper-bot once` ngày 2026-11-23 (mã NVDA).

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
