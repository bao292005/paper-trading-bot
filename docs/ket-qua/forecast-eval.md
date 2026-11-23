# Đánh giá dự báo quantile: Naive vs Chronos vs Kronos



Sinh tự động bởi `scripts/eval_forecast.py`. Giai đoạn 2019-01-01 → 2024-12-31 (không đụng holdout 2025+). Đáp án y = lợi suất log tích luỹ 5 phiên từ đóng cửa ngày asof; mô hình chỉ thấy nến <= asof. Pinball loss trung bình của q0.1/0.5/0.9. Hạn chế: survivorship bias (50 mã còn sống, vốn hoá lớn); các mã trong cùng ngày tương quan nên t tính theo ngày, không theo từng quan sát.



### A. Naive vs Chronos, 50 mã, mỗi 10 phiên
7550 quan sát (151 ngày x mã), horizon 5 phiên. Loss càng thấp càng tốt. `t` = kiểm định cặp theo ngày so với naive (t > 2: tốt hơn naive có ý nghĩa; t < -2: tệ hơn). Phủ = tỷ lệ y nhỏ hơn quantile (lý tưởng 10% / 50% / 90%).

| Mô hình | q0.1 | q0.5 | q0.9 | Trung bình | Tỷ lệ vs naive | t | Phủ 10% | Phủ 50% | Phủ 90% | Độ rộng q90-q10 |
|---|---|---|---|---|---|---|---|---|---|---|
| naive | 0.00715 | 0.01386 | 0.00642 | 0.00914 | 1.000 | nan | 9.4% | 50.5% | 90.4% | 0.0900 |
| chronos | 0.00711 | 0.01443 | 0.00646 | 0.00933 | 1.021 | -2.89 | 10.9% | 53.5% | 90.4% | 0.0897 |

### B. Ba mô hình trên tập con của Kronos (cùng ngày, cùng mã)
760 quan sát (76 ngày x mã), horizon 5 phiên. Loss càng thấp càng tốt. `t` = kiểm định cặp theo ngày so với naive (t > 2: tốt hơn naive có ý nghĩa; t < -2: tệ hơn). Phủ = tỷ lệ y nhỏ hơn quantile (lý tưởng 10% / 50% / 90%).

| Mô hình | q0.1 | q0.5 | q0.9 | Trung bình | Tỷ lệ vs naive | t | Phủ 10% | Phủ 50% | Phủ 90% | Độ rộng q90-q10 |
|---|---|---|---|---|---|---|---|---|---|---|
| naive | 0.00753 | 0.01430 | 0.00658 | 0.00947 | 1.000 | nan | 10.9% | 51.1% | 90.4% | 0.0940 |
| chronos | 0.00755 | 0.01477 | 0.00644 | 0.00959 | 1.012 | -0.68 | 10.1% | 54.2% | 91.4% | 0.0951 |
| kronos | 0.01400 | 0.03026 | 0.02032 | 0.02153 | 2.273 | -10.82 | 19.5% | 38.2% | 66.1% | 0.0997 |

### Thời gian chạy (tổng giây, máy hiện tại)

| Mô hình | Giây | Ghi chú |
|---|---|---|
| naive | 5 |  |
| chronos | 33 |  |
| kronos | 1811 | thiết bị: mps, n_samples=10 |

## Kết luận (tự sinh theo luật t > 2 / t < -2)

- A. Naive vs Chronos, 50 mã, mỗi 10 phiên: chronos TỆ HƠN naive có ý nghĩa (loss cao hơn 2.1%, t=-2.89).
- B. Ba mô hình trên tập con của Kronos (cùng ngày, cùng mã): chronos KHÔNG khác naive một cách có ý nghĩa (tỷ lệ loss 1.012, t=-0.68).
- B. Ba mô hình trên tập con của Kronos (cùng ngày, cùng mã): kronos TỆ HƠN naive có ý nghĩa (loss cao hơn 127.3%, t=-10.82).

## Kết luận viết tay (P1, thêm sau khi chạy xong; nếu chạy lại script thì phần này bị ghi đè, cần chép lại)

1. **Chronos không tốt hơn naive.** Trên 50 mã, Chronos tệ hơn naive 2,1% (t=-2,89); trên tập con 10 mã thì không khác (t=-0,68). Chronos chỉ nhỉnh hơn ở đuôi q0.1 (0,00711 so với 0,00715), còn q0.5 tệ hơn (0,01443 so với 0,01386). Median của Chronos lệch dương nhẹ (phủ 50% là 53,5%). Dự báo của Chronos không mang thêm thông tin so với phân phối lịch sử 252 ngày.
2. **Kronos tệ hơn naive rõ rệt** (tỷ lệ loss 2,27; t=-10,8) trong cấu hình đã chạy (small, 10 mẫu, mps, context 512). Độ phủ lệch nhiều: 19,5% / 38,2% / 66,1% so với 10/50/90. Kiểm tra thêm trên 760 quan sát:
   - median Kronos lệch âm hệ thống: trung bình q50 = -2,06%/5 phiên, thực tế y = +0,33%.
   - Độ lệch phụ thuộc mã: NVDA -10,6%, GOOGL -2,7%, V -2,0%, MSFT -2,0%, AAPL -1,9%, trong khi JPM và PG gần 0 hoặc dương. Phụ thuộc năm: 2020 -4,9%, 2024 -5,1%, 2022 +0,4%.
   - Tương quan giữa q50 và y chỉ 0,087, tức gần như không dự đoán được hướng.
   - Trừ đi độ lệch trung bình thì loss vẫn 0,0213, gấp hơn 2 lần naive. Vậy lỗi không chỉ là lệch trung bình mà còn do phân tán các mẫu quá lớn.
   - **Chưa kiểm chứng được** đây là giới hạn của mô hình hay do cách dùng của tôi (nhân bản chuỗi, `sample_count=1`, tham số lấy mẫu mặc định của vendor, lịch ngày tương lai dùng `bdate_range`). Muốn kết luận chắc về Kronos thì cần thử thêm (ví dụ nhiệt độ thấp hơn, nhiều mẫu hơn), nhưng đó là việc ngoài phạm vi.
3. **Hệ quả cho hệ thống FULL:** dùng Chronos (đã tính sẵn), vì Kronos chậm (mps ~2,7 s/mã/ngày, tổng 1811 s cho chỉ 760 quan sát; Chronos 33 s cho 7550 quan sát) và kém hơn. Tuy nhiên vì Chronos cũng không tốt hơn naive, P2/P4 **không nên mặc định coi dự báo là có tín hiệu**. Nên kiểm tra riêng bằng backtest xem tín hiệu nào từ dự báo (ví dụ độ rộng q90-q10 dùng cho định cỡ vị thế) có giá trị, và nếu không thì ghi rõ là không.
4. **Hạn chế:** survivorship bias (50 mã còn sống, vốn hoá lớn); mẫu Kronos nhỏ (10 mã × 76 ngày, các mã tương quan nhau nên số quan sát độc lập thực tế ít hơn 760); t tính theo ngày nhưng các ngày cách 10-20 phiên có thể vẫn chồng lấn regime; chỉ có horizon 5 và chỉ giai đoạn 2019-2024 (chưa chạm holdout).
