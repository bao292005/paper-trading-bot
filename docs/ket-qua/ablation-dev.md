# Ablation trên giai đoạn phát triển (2019-01 → 2024-12) — KHÔNG phải kết quả chính thức

Sinh bởi `scripts/run_ablation.py` ngày 2026-11-24. Walk-forward 12 cửa sổ (train 3 năm / test 6 tháng), 50 mã + SPY (SPY chỉ để tính regime, FULL không giao dịch SPY), vốn 100,000$, khớp open phiên sau, phí 10 bps mỗi chiều. **Không dùng dữ liệu ≥ 2025-01-01.** Kết quả chính thức chỉ có trên HOLDOUT (prereg mục 4, 7). Ở đây được phép chỉnh tham số.

## Bảng metric (sau phí)

| cấu hình | sharpe | cagr | max_drawdown | n_trades | exposure | win_rate | turnover | Δsharpe_vs_FULL | kết luận | time_s |
|---|---|---|---|---|---|---|---|---|---|---|
| FULL | 0.12 | 0.6% | -16.3% | 590 | 36.2% | 42.7% | 8.90 | 0.00 |  | 1263 |
| FULL bỏ forecast | 0.39 | 2.9% | -19.0% | 681 | 47.4% | 44.2% | 11.43 | 0.27 | không chứng minh được giá trị | 978 |
| FULL bỏ regime switching | 0.46 | 3.5% | -13.0% | 1072 | 47.6% | 50.6% | 14.95 | 0.34 | không chứng minh được giá trị | 234 |
| FULL bỏ hiệu chỉnh xác suất | 0.25 | 1.5% | -17.5% | 702 | 43.9% | 43.4% | 10.58 | 0.13 | không chứng minh được giá trị | 59 |
| FULL bỏ sizing ATR | 0.26 | 0.2% | -2.0% | 590 | 3.7% | 42.7% | 0.90 | 0.14 | không chứng minh được giá trị | 86 |
| FULL bỏ bracket/stop | 0.72 | 6.4% | -16.5% | 339 | 64.2% | 46.9% | 5.15 | 0.60 | không chứng minh được giá trị | 86 |
| FULL bỏ giới hạn tương quan | 0.12 | 0.6% | -14.8% | 611 | 37.5% | 42.7% | 9.21 | 0.00 | không chứng minh được giá trị | 84 |
| B0 (SPY) | 0.92 | 17.1% | -33.6% | 1 | 99.4% | 100.0% | 0.05 | 0.80 |  | 0 |
| B1 (chia đều 50 mã) | 0.99 | 18.0% | -30.1% | 1285 | 97.2% | 92.5% | 0.34 | 0.87 |  | 6 |
| B2 (bot gốc) | 0.65 | 3.3% | -10.3% | 1610 | 15.9% | 63.9% | 2.21 | 0.53 |  | 13 |

Thành phần "có giá trị" = bỏ nó làm Sharpe giảm ≥ 0.1 so với FULL (prereg mục 7).

## So với mục tiêu prereg mục 7 (áp thử trên giai đoạn phát triển)
- (1) Sharpe(FULL) = 0.12 so với Sharpe(B2) + 0,2 = 0.85 → **KHÔNG đạt**.
- (2) MaxDD(FULL) = -16.3% so với MaxDD(B1) = -30.1% → **ĐẠT**.
- Nếu đây là holdout thì mức kết luận là: **Hoà**. (Chỉ là giai đoạn phát triển, KHÔNG phải kết quả chính thức.)

## Sharpe từng cửa sổ test

| test_start | FULL | FULL bỏ forecast | FULL bỏ regime switching | FULL bỏ hiệu chỉnh xác suất | FULL bỏ sizing ATR | FULL bỏ bracket/stop | FULL bỏ giới hạn tương quan | B0 (SPY) | B1 (chia đều 50 mã) | B2 (bot gốc) |
|---|---|---|---|---|---|---|---|---|---|---|
| 2019-01-01 | 1.53 | 2.49 | 2.51 | 0.81 | 1.53 | 2.33 | 1.53 | 2.98 | 3.04 | 2.66 |
| 2019-07-01 | 1.49 | 1.69 | 1.54 | 0.86 | 1.75 | 2.86 | 1.50 | 1.59 | 1.59 | 2.02 |
| 2020-01-01 | -1.83 | -1.79 | -1.93 | -1.78 | -1.59 | -1.03 | -1.61 | 0.02 | 0.20 | -0.06 |
| 2020-07-01 | 0.77 | 0.42 | 2.29 | 0.66 | 0.52 | 2.30 | 0.60 | 2.46 | 2.57 | 3.13 |
| 2021-01-01 | 0.43 | 2.00 | 2.10 | 2.91 | 0.54 | 0.59 | 0.39 | 2.46 | 2.57 | 2.92 |
| 2021-07-01 | 0.57 | 0.51 | 0.84 | 0.66 | 0.64 | 0.97 | 0.87 | 1.72 | 1.84 | 1.03 |
| 2022-01-01 | -4.27 | -3.91 | -1.31 | -4.29 | -3.95 | -3.91 | -4.26 | -1.72 | -1.65 | -0.54 |
| 2022-07-01 | -1.51 | -1.24 | -1.20 | -1.51 | -1.20 | -0.92 | -1.20 | 0.22 | 0.58 | 0.27 |
| 2023-01-01 | 1.52 | 1.64 | -0.00 | 1.51 | 2.37 | 1.69 | 1.68 | 2.32 | 2.05 | 1.62 |
| 2023-07-01 | 0.97 | 1.07 | 0.37 | 0.20 | 1.21 | 0.66 | 0.87 | 1.38 | 1.90 | 1.51 |
| 2024-01-01 | -0.11 | 0.75 | 0.14 | 0.21 | -0.16 | -0.29 | -0.45 | 2.88 | 2.42 | 1.67 |
| 2024-07-01 | -0.29 | -0.02 | -0.25 | -0.16 | -0.27 | 1.11 | -0.30 | 1.16 | 1.14 | 1.49 |

## Lý do thoát lệnh (số trade)

| cấu hình | stop_loss | take_profit | signal | end |
|---|---|---|---|---|
| FULL | 245 | 224 | 118 | 3 |
| FULL bỏ forecast | 275 | 265 | 137 | 4 |
| FULL bỏ regime switching | 374 | 219 | 469 | 10 |
| FULL bỏ hiệu chỉnh xác suất | 295 | 272 | 131 | 4 |
| FULL bỏ sizing ATR | 245 | 224 | 118 | 3 |
| FULL bỏ bracket/stop | 0 | 0 | 331 | 8 |
| FULL bỏ giới hạn tương quan | 251 | 233 | 124 | 3 |
| B0 (SPY) | 0 | 0 | 0 | 1 |
| B1 (chia đều 50 mã) | 0 | 0 | 1235 | 50 |
| B2 (bot gốc) | 0 | 0 | 1578 | 32 |

Tổng thời gian chạy: 2810s (46.8 phút).

## Kết luận (viết tay, P2, 2026-11-24 — chạy lại script sẽ GHI ĐÈ mục này, cần chép lại)
- **FULL thua rõ trên giai đoạn phát triển.** Sharpe chỉ 0.12, thấp hơn B2 (0.65), B0 (0.92), B1 (0.99) và còn xa mục tiêu 0.85.
  Mức "Hoà" ở trên chỉ nhờ MaxDD: FULL nắm cổ phiếu trung bình 36% vốn, còn B1 nắm ~97%. Ít tiếp xúc thì sụt ít, KHÔNG phải nhờ quản lý rủi ro tốt.
  Với CAGR 0.6%, FULL tệ hơn cả việc để tiền mặt sau khi tính rủi ro.
- **Không thành phần nào "có giá trị" theo prereg.** Bỏ bất kỳ thành phần nào, Sharpe đều không giảm; hầu hết còn TĂNG:
  - bỏ bracket/stop +0.60. SL 2×ATR và TP 3×ATR cắt lệnh quá sớm: 469/590 trade thoát bằng SL/TP.
  - bỏ regime switching +0.34. Bản bỏ regime chỉ thua FULL ở 3/12 cửa sổ (2020-01, 2023-01, 2023-07).
  - bỏ forecast +0.27. Veto BUY khi median < −0.002 và giảm cỡ theo độ rộng dự báo đều không giúp.
  - bỏ sizing ATR +0.14, bỏ calibration +0.13. Riêng bỏ giới hạn tương quan thì không đổi (0.00).
- Bỏ sizing ATR cho ra đúng 590 trade như FULL, vì sizing không đổi quyết định vào/ra. Nhưng sizing gốc (≤1000$/lệnh) chỉ dùng 3.7% vốn.
  Vì vậy MaxDD −2% của dòng này không có ý nghĩa so sánh.
- Calibration: khớp với kết luận ở calibration-ketluan.md (thua base rate theo hướng). Với phương án (a), bỏ calibration vẫn tăng Sharpe 0.13.
- 2022-01 (thị trường giảm) là cửa sổ tệ nhất của mọi biến thể FULL (−3.9 đến −4.3), trừ bản bỏ regime (−1.31).
  Giả thuyết (CHƯA kiểm chứng): trong bear_calm, regime switch chỉ dùng RSI, nên các lệnh MUA bắt đáy khi thị trường đang giảm vẫn lọt qua.
- **Lưu ý thống kê:** chỉ có một đường giá 6 năm. Sai số chuẩn của Sharpe vào khoảng ±0.4, nên các Δ 0.1–0.3 có thể là nhiễu.
  Chỉ riêng mức +0.60 của bracket và việc FULL thua B2 là tương đối chắc. Các biến thể dùng chung tín hiệu nên chênh lệch giữa chúng ít nhiễu hơn so với từng con số riêng.
- **Hệ quả:** nếu khoá cấu hình theo kết quả này (bỏ bracket, regime, forecast...) thì đó là chọn cấu hình trên dữ liệu dev. Prereg cho phép làm vậy,
  nhưng phải khoá trước khi chạy holdout và ghi rõ trong báo cáo. Không được chạy holdout cho tới khi cấu hình đã khoá.
