# Demo tối ưu tham số — B2_goc, MA ngắn × MA dài (2026-11-26)

Lệnh: `uv run python scripts/tester.py optimize <cấu hình>` với
`grid = {"ma_short": [5, 10, 20], "ma_long": [30, 50, 100]}`, tiêu chí `sharpe`,
10 mã đầu UNIVERSE_50 (AAPL, MSFT, AMZN, GOOGL, META, NVDA, JPM, JNJ, V, PG), vốn 100k, phí 10 bps/chiều,
fill `daily_ohlc`, mode `single`. IS 2019-01-01 → 2022-12-31, OOS 2023-01-01 → 2024-12-31.
Không đụng dữ liệu ≥ 2025-01-01. 9 tổ hợp × 2 giai đoạn, 10.7 s.

> Chọn tham số trên dữ liệu này rồi đánh giá lại trên cùng dữ liệu là overfit; kết quả chỉ có giá trị
> khi kiểm tra trên holdout một lần.

| ma_short | ma_long | IS Sharpe | OOS Sharpe | IS rank | OOS rank | IS net $ | OOS net $ | IS trades | OOS trades |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 30 | 0.383 | 3.104 | 1 | 1 | 2,345 | 3,474 | 215 | 98 |
| 5 | 30 | 0.375 | 2.833 | 2 | 7 | 2,304 | 3,579 | 254 | 120 |
| 5 | 50 | 0.345 | 2.901 | 3 | 3 | 2,140 | 3,432 | 224 | 100 |
| 5 | 100 | 0.308 | 2.922 | 4 | 2 | 1,730 | 3,265 | 169 | 87 |
| 10 | 100 | 0.297 | 2.843 | 5 | 6 | 1,695 | 3,199 | 151 | 79 |
| 20 | 50 | 0.296 | 2.535 | 6 | 9 | 1,574 | 2,634 | 185 | 88 |
| 20 | 30 | 0.293 | 2.899 | 7 | 4 | 1,653 | 3,306 | 218 | 106 |
| 20 | 100 | 0.246 | 2.867 | 8 | 5 | 1,337 | 3,148 | 145 | 78 |
| 10 | 50 | 0.244 | 2.734 | 9 | 8 | 1,464 | 3,154 | 192 | 94 |

Tốt nhất IS: `ma_short=10, ma_long=30`, đứng hạng **1/9 ở OOS**.

Heatmap Sharpe (hàng = ma_long, cột = ma_short)

| IS | 5 | 10 | 20 | | OOS | 5 | 10 | 20 |
|---|---|---|---|---|---|---|---|---|
| 30 | 0.375 | 0.383 | 0.293 | | 30 | 2.833 | 3.104 | 2.899 |
| 50 | 0.345 | 0.244 | 0.296 | | 50 | 2.901 | 2.734 | 2.535 |
| 100 | 0.308 | 0.297 | 0.246 | | 100 | 2.922 | 2.843 | 2.867 |

## Kết luận
- **Kết quả không cho thấy tham số nào thật sự tốt hơn.** Ở IS, Sharpe của cả 9 tổ hợp chỉ nằm trong
  khoảng 0.24–0.38, chênh lệch nhỏ so với độ nhiễu của 4 năm dữ liệu. Tương quan hạng IS/OOS
  (Spearman) ≈ 0.52 với n = 9, không có ý nghĩa thống kê (p ≈ 0.15).
- Cặp tốt nhất IS (10/30) cũng đứng đầu OOS, nhưng đây không phải bằng chứng sạch. 10/30 chính là
  mặc định của B2 và đã được chọn từ trước trên chính giai đoạn phát triển này. Ngoài ra, tổ hợp
  xếp thứ 2 ở IS (5/30) rơi xuống hạng 7 ở OOS.
- **Sharpe OOS khoảng 3 là ảo.** Lợi nhuận tuyệt đối rất nhỏ: chỉ +2.6k đến +3.6k trên 100k vốn
  trong 2 năm, tức khoảng 1.5–1.8%/năm, trong khi nhóm megacap tăng rất mạnh giai đoạn 2023–2024.
  Sharpe cao chỉ vì B2 giới hạn mỗi vị thế ≤ 1000$, nên equity gần như toàn tiền mặt và biến động
  rất thấp (equity DD tối đa khoảng 0.3–0.5%). Mức OOS cao hơn hẳn IS là do thị trường, không do
  tham số.
- Không nên khoá tham số mới dựa trên bảng này; giữ mặc định 10/30. Nếu muốn so sánh thì dùng
  walk-forward (`mode: "walkforward"`) và chỉ chạy holdout một lần khi cấu hình đã khoá.
