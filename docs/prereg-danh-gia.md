# Tiêu chí đánh giá — CHỐT TRƯỚC (pre-registration)

Chốt ngày 2026-11-23, TRƯỚC khi có bất kỳ kết quả backtest nào.
Không sửa file này sau khi đã xem kết quả holdout. Muốn đổi thì ghi thêm mục "Sửa đổi" kèm ngày và lý do.

## 1. Dữ liệu
- **Tập mã (universe):** 50 mã vốn hoá lớn của Mỹ, có lịch sử từ 2016 (danh sách `UNIVERSE_50` trong `config.py`), cộng SPY làm benchmark và để tính regime.
  - Hạn chế đã biết: mã được chọn theo hiện tại, nên có **survivorship bias** (thiên lệch kẻ sống sót). Báo cáo phải ghi rõ hạn chế này.
- **Nến ngày** 2016-01-01 → 2026-10-31, lấy từ Alpaca. Ưu tiên feed SIP cho dữ liệu lịch sử, không được thì dùng IEX. Điều chỉnh `ALL` (chia tách cổ phiếu + cổ tức) để backtest.
- Tin tức: Alpaca News API, chỉ dùng tin có `created_at <= asof`.

## 2. Thời điểm và cách khớp lệnh (chống rò rỉ dữ liệu)
- Quyết định ở giá **đóng cửa** ngày t, chỉ dùng dữ liệu tới hết ngày t.
- Khớp lệnh ở giá **mở cửa** ngày t+1.
- Stop-loss và take-profit kiểm tra trong ngày bằng high/low. Nếu cả hai cùng chạm trong một ngày thì **giả định stop-loss chạm trước** (giả định bi quan).

## 3. Chi phí
- Mỗi chiều mua/bán: **10 bps** (0,10%), gộp spread và slippage. Alpaca không thu hoa hồng.
- Kiểm tra độ nhạy ở 5 bps và 20 bps (chỉ báo cáo thêm, không dùng để chọn cấu hình).

## 4. Chia giai đoạn
| Giai đoạn | Thời gian | Dùng để |
|---|---|---|
| Khởi động | 2016-01 → 2018-12 | Chỉ để tính chỉ báo và fit ban đầu, không tính điểm |
| **Phát triển (walk-forward)** | 2019-01 → 2024-12 | Train 3 năm gần nhất, test 6 tháng, trượt mỗi 6 tháng. Được chỉnh tham số ở đây |
| **HOLDOUT** | 2025-01 → 2026-10 | **Chạy ĐÚNG 1 LẦN** khi mọi cấu hình đã khoá. Đây là kết quả chính thức |
| Forward (paper thật) | từ 2026-11-24 | Đánh giá LLMAgentStrategy (LLM không backtest được vì đã "biết" quá khứ) |

## 5. Baseline
- **B0:** Mua và giữ SPY.
- **B1:** Mua và giữ đều tiền vào 50 mã, cân bằng lại mỗi tháng.
- **B2:** Bot gốc của bài báo: MA(10/30) + RSI(14), confidence cứng, ngưỡng 0.5, cỡ vị thế min(2% sức mua, 1000$). Vốn 100k$, không có stop.

## 6. Thước đo
- **Chính:** Sharpe năm hoá sau phí (lợi suất ngày, rf = 0).
- **Phụ:** CAGR, max drawdown, số lệnh, tỷ lệ thắng, turnover, exposure trung bình.
- **Dự báo (P1):** pinball loss trung bình ở các quantile 0.1/0.5/0.9, so với dự báo naive (random walk, quantile lấy từ phân phối lợi suất 1 năm gần nhất).
- **Xác suất (P2):** Brier score và đường reliability, so với chiến lược luôn dự đoán bằng tỷ lệ gốc (base rate).

## 7. Tiêu chí THẮNG trên HOLDOUT (hệ thống đầy đủ = "FULL")
1. Sharpe(FULL) ≥ Sharpe(B2) + 0,2, **và**
2. MaxDD(FULL) không tệ hơn MaxDD(B1).

Kết luận có 3 mức:
- **Thắng:** đạt cả (1) và (2).
- **Hoà:** chỉ đạt một trong hai.
- **Thua:** không đạt cái nào.

Kết quả so với B0/B1 phải báo cáo trung thực, kể cả khi thua. Thua buy & hold là chuyện bình thường.

Từng thành phần được coi là **có giá trị** nếu bỏ nó đi (ablation) làm Sharpe trên giai đoạn phát triển giảm ≥ 0,1.
Thành phần không đạt mức này vẫn được giữ trong báo cáo, nhưng bị ghi là "không chứng minh được giá trị".

## Sửa đổi
- **2026-11-23 (trước khi có bất kỳ kết quả backtest nào):** Alpaca News chỉ có tin từ khoảng 2026-04 (P1 kiểm tra thực tế).
  Vì vậy tin tức **không** dùng trong backtest 2019–2024 hay holdout. Mọi thành phần dựa trên tin tức (LLMAgentStrategy)
  chỉ được đánh giá ở giai đoạn forward. Dữ liệu giá: 51 mã đủ 2016-01-04 → 2026-10-30, feed SIP, Adjustment.ALL.

## 8. Ablation (bảng bắt buộc)
FULL; FULL bỏ forecast; FULL bỏ regime switching; FULL bỏ hiệu chỉnh xác suất; FULL bỏ sizing ATR (dùng sizing gốc); FULL bỏ bracket/stop; FULL bỏ giới hạn tương quan; B2.
