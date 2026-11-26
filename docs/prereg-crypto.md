# Tiêu chí đánh giá CRYPTO — CHỐT TRƯỚC (pre-registration)

Chốt ngày 2026-11-26, TRƯỚC khi có bất kỳ kết quả backtest crypto nào.
Thực hiện chung với prereg cổ phiếu ([prereg-danh-gia.md](prereg-danh-gia.md)); đây là một thí nghiệm riêng, kết quả không gộp chung.
Không sửa sau khi đã xem kết quả holdout. Muốn đổi thì ghi mục "Sửa đổi" kèm ngày và lý do.

## 1. Dữ liệu
- **Tập mã:** BTC/USD, ETH/USD, SOL/USD, AVAX/USD, LINK/USD (`CRYPTO_UNIVERSE`). Regime tính theo BTC/USD.
- **Nguồn:** nến ngày Alpaca Crypto (`CryptoHistoricalDataClient`), nến chốt 00:00 UTC.
- **Đã kiểm tra 2026-11-26:**
  - BTC, ETH, LINK có đủ từ 2021-01-01.
  - SOL thiếu ~420 ngày.
  - AVAX chỉ có từ 2021-11-18.
- **Cách xử lý ngày thiếu:**
  - KHÔNG nội suy giá.
  - Ngày thiếu nến thì mã đó không được ra quyết định và không khớp lệnh. Vị thế đang mở thì giữ nguyên.
  - Phải báo cáo số ngày thiếu của từng mã.
- **Hạn chế đã biết:**
  - Volume là của riêng sàn Alpaca, rất nhỏ, nên không dùng volume làm tín hiệu.
  - Chỉ có 5 mã và tương quan cao → **sức mạnh thống kê thấp**.
  - Mã được chọn theo hiện tại → survivorship bias.

## 2. Thời điểm & khớp lệnh
- Quyết định ở giá close ngày t (00:00 UTC ngày t+1), chỉ dùng dữ liệu ≤ t.
- Khớp ở giá open ngày t+1.
- SL/TP: kiểm tra trong ngày bằng high/low; nếu cả hai cùng chạm thì tính SL trước (có thể dùng fill model nến phút).
- Giao dịch 24/7, nên mọi ngày đều là ngày giao dịch. Sharpe năm hoá theo **365** ngày.
- Được mua lẻ (fractional), làm tròn 6 chữ số thập phân. Không bán khống, không margin.

## 3. Chi phí
- **25 bps mỗi chiều**, tương đương phí taker của Alpaca crypto cộng spread/slippage.
- Kiểm tra độ nhạy ở 15 và 40 bps (chỉ báo cáo, không dùng để chọn cấu hình).

## 4. Chia giai đoạn
| Giai đoạn | Thời gian | Dùng để |
|---|---|---|
| Khởi động | 2021-01-01 → 2021-12-31 | Chỉ để tính chỉ báo (MA200) và fit ban đầu, không chấm điểm |
| **Phát triển (walk-forward)** | 2022-01-01 → 2024-12-31 | Train **mở rộng dần** từ 2021-01 tới đầu cửa sổ; test 6 tháng, trượt 6 tháng (6 cửa sổ). Được chỉnh tham số ở đây |
| **HOLDOUT** | 2025-01-01 → 2026-10-31 | Chạy **ĐÚNG 1 LẦN** sau khi đã khoá cấu hình |
| Forward (paper thật) | từ khi LiveBot crypto chạy | Dùng cho LLMAgentStrategy và để theo dõi |

Mọi code của chế độ crypto phải chặn dữ liệu ≥ 2025-01-01, giống như với cổ phiếu.

## 5. Baseline
- **C0:** mua và giữ BTC/USD.
- **C1:** chia đều 5 mã, cân bằng lại đầu mỗi tháng. Mã chưa có dữ liệu thì phần vốn đó giữ tiền mặt cho tới khi mã có dữ liệu.
- **C2:** bot gốc của bài báo trên 5 mã: MA(10/30) + RSI(14), ngưỡng 0,5, cỡ vị thế min(2% tiền mặt, 1000$) / giá, không stop. Vốn 100k$.

## 6. Thước đo
Giống prereg cổ phiếu, nhưng năm hoá theo 365 ngày:
- **Chính:** Sharpe sau phí.
- **Phụ:** CAGR, MaxDD, số lệnh, tỷ lệ thắng, turnover, exposure.

## 7. Tiêu chí THẮNG trên HOLDOUT (cấu hình đã khoá = "CRYPTO-FINAL")
1. Sharpe(FINAL) ≥ Sharpe(C2) + 0,2, **và**
2. MaxDD(FINAL) không tệ hơn MaxDD(C1).

Kết luận có 3 mức: **Thắng** nếu đạt cả hai; **Hoà** nếu đạt một trong hai; **Thua** nếu không đạt cái nào. So với C0/C1 thì báo cáo trung thực, kể cả khi thua.

## 8. Luật chọn cấu hình FINAL (định trước, để tránh "chọn sau khi nhìn")
1. Chạy ablation trên giai đoạn phát triển. Các dòng: FULL; FULL bỏ forecast; FULL bỏ regime; FULL bỏ hiệu chỉnh xác suất; FULL bỏ sizing ATR; FULL bỏ bracket/stop; FULL bỏ giới hạn tương quan; cùng C0, C1, C2.
2. **FINAL = FULL bỏ đi mọi thành phần mà khi bỏ, Sharpe dev TĂNG ≥ 0,1.** Các thành phần còn lại giữ nguyên.
3. Chạy FINAL trên dev **1 lần** để ghi nhận, KHÔNG chỉnh thêm. Khoá cấu hình (ghi hash commit). Sau đó chạy holdout đúng 1 lần.
4. Tham số mặc định giữ như phần cổ phiếu: MA 10/30, RSI 14, SL 2×ATR, TP 3×ATR, rủi ro 1%/lệnh.
   - Riêng **giới hạn tương quan** của crypto đặt là **0,9** thay vì 0,8, vì 5 mã vốn tương quan cao, chặn ở 0,8 thì gần như không vào được lệnh nào.
   - Tối đa 5 vị thế; mỗi vị thế tối đa 20% vốn.
   - Mọi con số trên được chốt trước khi chạy và không được tối ưu.

## Sửa đổi
(chưa có)
