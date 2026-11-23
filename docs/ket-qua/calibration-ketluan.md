# Kết luận hiệu chỉnh xác suất (P2) — viết tay

Số liệu ở [calibration.md](calibration.md) (file sinh tự động, chạy lại sẽ ghi đè; file này thì không).

## Lần chạy 2026-11-23 (50 mã, 31.5k tín hiệu OOS 2019–2024, không forecast, isotonic)
- **Không chứng minh được hiệu chỉnh tốt hơn base rate:** Brier mô hình 0.2491 và base rate 0.2489 coi như bằng nhau (skill −0.001).
  Mô hình thắng base rate ở 8/12 cửa sổ, nhưng thua nặng ở 2020-01 (COVID: 0.283 so với 0.249) và 2022-01 (thị trường giảm).
- **So với bài gốc thì tốt hơn rõ:** nếu coi confidence gán cứng là xác suất, Brier là 0.291, tệ hơn cả base rate.
  Confidence thô 0.6–0.83 nhưng tỷ lệ thắng thật chỉ 44–48%: **bài gốc tự tin quá mức**.
- **Có xếp hạng được một phần:** p ∈ [0.3, 0.4) thắng 41%, p ∈ [0.5, 0.6) thắng 53%. Lọc p ≥ 0.5 còn 31% số tín hiệu, tỷ lệ thắng 52.4%
  so với 46.3% khi lấy tất cả. Nghi phần lớn khác biệt chỉ do BUY thắng nhiều hơn SELL (BUY ~54%, SELL ~41%) → xem mục dưới.
- Các bin ở hai đầu (p < 0.3 hoặc p > 0.6) có ít mẫu và lệch nhiều. Isotonic tạo ra một số xác suất cực trị không đáng tin.
- **Lưu ý thống kê:** nhãn 5 ngày chồng lên nhau, 50 mã lại tương quan với nhau, nên số mẫu độc lập thực tế ít hơn nhiều so với 31.5k.
  Chênh lệch Brier cỡ 0.001 nằm trong mức nhiễu.
- **Forecast chưa dùng:** file Chronos lúc chạy mới phủ 2018-07 → 2020-03. Khi P1 tính xong, chạy lại với `--forecast`.

## So với base rate theo hướng (BUY/SELL riêng) — lần chạy 2026-11-24
Chạy lại ra đúng các số cũ (mô hình 0.2491, base rate 0.2489), nên các kết luận ở trên vẫn giữ nguyên.
- **Mô hình THUA baseline theo hướng:** Brier 0.2491 so với 0.2454, skill −0.015. Mức chênh này lớn hơn hẳn mức nhiễu ~0.001 nói ở trên.
- Mô hình chỉ thắng 1/12 cửa sổ (2023-07: 0.2431 so với 0.2439). Trong cả 6 nhóm chiến lược × hướng, mô hình đều thua baseline này.
- **Kết luận:** phần "xếp hạng được" của mô hình (lọc p ≥ 0.5 cho tỷ lệ thắng 52.4%) gần như chỉ đến từ hướng của lệnh.
  Biết lệnh là BUY (thắng ~54%) hay SELL (~41%) đã đủ giải thích. Các feature còn lại (RSI, MA, lợi suất, vol, regime) không thêm
  thông tin dự báo, mà còn làm Brier tệ đi vì overfit, nhất là isotonic tạo ra các xác suất cực trị.
- **Hệ quả cho pipeline:** dùng calibrator hiện tại không tốt hơn một luật đơn giản là "SELL bị coi là xác suất thấp".
  Trong ablation (prereg mục 8), dòng `use_calibration` có thể không tạo khác biệt dương. Hướng thử tiếp:
  dùng sigmoid thay isotonic, dùng ít feature hơn, hoặc dùng thẳng base rate theo hướng làm prob.
