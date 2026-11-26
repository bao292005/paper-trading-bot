# Ablation CRYPTO trên giai đoạn phát triển (2022-01 → 2024-12) — KHÔNG phải kết quả chính thức

Sinh bởi `scripts/run_ablation.py --asset crypto` ngày 2026-11-26. Theo [prereg-crypto.md](../prereg-crypto.md): 5 mã BTC/ETH/SOL/AVAX/LINK (BTC/USD vừa là benchmark regime vừa được giao dịch), walk-forward 6 cửa sổ test 6 tháng, train mở rộng từ 2021-01, vốn 100,000$, khớp open nến ngày sau, phí 25.0 bps mỗi chiều (mua trừ vào coin), Sharpe 365 ngày, corr ≤ 0,9, tối đa 5 vị thế × 20% vốn, forecast Chronos-Bolt-small h=5 tính sẵn (≤ 2024-12-31). **Không dùng dữ liệu ≥ 2025-01-01, KHÔNG chạy holdout.**

## Bảng metric (sau phí)

| cấu hình | sharpe | cagr | max_drawdown | n_trades | exposure | win_rate | turnover | Δsharpe_vs_FULL | kết luận | time_s |
|---|---|---|---|---|---|---|---|---|---|---|
| FULL | 0.24 | 0.8% | -6.2% | 30 | 4.8% | 43.3% | 0.90 | 0.00 |  | 34 |
| FULL bỏ forecast | 0.41 | 2.3% | -10.2% | 50 | 6.8% | 52.0% | 1.50 | 0.16 | BỎ trong FINAL (Δ ≥ +0,1) | 30 |
| FULL bỏ regime switching | -0.38 | -1.3% | -5.5% | 36 | 5.2% | 47.2% | 0.89 | -0.62 | có giá trị | 11 |
| FULL bỏ hiệu chỉnh xác suất | 0.14 | 0.6% | -11.9% | 70 | 9.5% | 44.3% | 2.08 | -0.11 | có giá trị | 5 |
| FULL bỏ sizing ATR | 0.11 | 0.1% | -1.2% | 30 | 0.7% | 43.3% | 0.10 | -0.14 | có giá trị | 7 |
| FULL bỏ bracket/stop | 0.90 | 22.9% | -13.3% | 28 | 12.5% | 53.6% | 1.09 | 0.66 | BỎ trong FINAL (Δ ≥ +0,1) | 6 |
| FULL bỏ giới hạn tương quan | 0.24 | 0.8% | -6.2% | 30 | 4.8% | 43.3% | 0.90 | 0.00 | giữ (không chứng minh được giá trị) | 6 |
| C0 (BTC buy&hold) | 0.68 | 25.0% | -67.1% | 1 | 99.9% | 100.0% | 0.19 | 0.44 |  | 0 |
| C1 (chia đều 5 mã) | 0.51 | 12.1% | -80.4% | 78 | 97.2% | 33.3% | 0.96 | 0.27 |  | 0 |
| C2 (bot gốc) | 0.54 | 2.4% | -3.2% | 117 | 2.7% | 51.3% | 0.39 | 0.30 |  | 1 |
| FINAL | 0.92 | 23.6% | -18.1% | 37 | 14.9% | 51.4% | 1.31 | 0.68 |  | 7 |

## FINAL theo luật mục 8

- Luật: FINAL = FULL bỏ mọi thành phần mà khi bỏ, Sharpe dev TĂNG ≥ 0.1 (Sharpe FULL = 0.24).
- Thành phần bị bỏ: `use_bracket` (FULL bỏ bracket/stop), `use_forecast` (FULL bỏ forecast).
- Cờ FINAL: `{'use_forecast': False, 'use_regime': True, 'use_calibration': True, 'use_atr_sizing': True, 'use_bracket': False, 'use_corr_limit': True}`.
- FINAL chạy trên dev 1 lần (không chỉnh thêm): Sharpe 0.92, MaxDD -18.1%.
- Bước tiếp theo theo prereg: khoá cấu hình (ghi hash commit) rồi mới chạy holdout ĐÚNG 1 lần (chưa làm).

## So với mục tiêu prereg mục 7 (áp thử trên dev, KHÔNG phải kết quả chính thức)
- (1) Sharpe(FINAL) = 0.92 so với Sharpe(C2) + 0,2 = 0.74 → **ĐẠT**.
- (2) MaxDD(FINAL) = -18.1% so với MaxDD(C1) = -80.4% → **ĐẠT**.
- Nếu là holdout thì mức: **Thắng**. So với C0 (Sharpe 0.68) báo cáo trung thực ở bảng trên.

## Sharpe từng cửa sổ test

| test_start | FULL | FULL bỏ forecast | FULL bỏ regime switching | FULL bỏ hiệu chỉnh xác suất | FULL bỏ sizing ATR | FULL bỏ bracket/stop | FULL bỏ giới hạn tương quan | C0 (BTC buy&hold) | C1 (chia đều 5 mã) | C2 (bot gốc) | FINAL |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2022-01-01 | -2.68 | -1.46 | -2.68 | -2.48 | -2.05 | -2.61 | -2.68 | -2.14 | -2.35 | -2.04 | -2.42 |
| 2022-07-01 | -0.20 | -0.61 | 0.49 | 0.01 | -0.03 | 0.58 | -0.20 | -0.26 | -0.29 | 1.01 | 0.03 |
| 2023-01-01 | 0.00 | 3.07 | -0.65 | -0.19 | 0.00 | 2.16 | 0.00 | 2.72 | 1.75 | 1.39 | 3.07 |
| 2023-07-01 | 2.81 | 2.49 | 1.32 | 1.03 | 2.92 | 3.45 | 2.81 | 1.87 | 2.96 | 0.36 | 2.98 |
| 2024-01-01 | 1.84 | 1.92 | -1.42 | 0.25 | 1.09 | 1.69 | 1.84 | 1.56 | 0.65 | -0.22 | 1.64 |
| 2024-07-01 | 0.07 | 1.91 | 0.17 | 1.67 | 0.11 | 1.59 | 0.07 | 1.77 | 1.76 | 1.48 | 1.66 |

## Lý do thoát lệnh (số trade)

| cấu hình | stop_loss | take_profit | signal | end |
|---|---|---|---|---|
| FULL | 13 | 10 | 7 | 0 |
| FULL bỏ forecast | 22 | 16 | 12 | 0 |
| FULL bỏ regime switching | 15 | 4 | 16 | 1 |
| FULL bỏ hiệu chỉnh xác suất | 27 | 18 | 25 | 0 |
| FULL bỏ sizing ATR | 13 | 10 | 7 | 0 |
| FULL bỏ bracket/stop | 0 | 0 | 28 | 0 |
| FULL bỏ giới hạn tương quan | 13 | 10 | 7 | 0 |
| C0 (BTC buy&hold) | 0 | 0 | 0 | 1 |
| C1 (chia đều 5 mã) | 0 | 0 | 73 | 5 |
| C2 (bot gốc) | 0 | 0 | 112 | 5 |
| FINAL | 0 | 0 | 37 | 0 |

## Hạn chế
- Chỉ 5 mã, tương quan rất cao (cùng theo BTC) → giới hạn tương quan 0,9 và đa dạng hoá ít tác dụng.
- Dev chỉ 3 năm (1 chu kỳ sập 2022 + hồi 2023-2024), 6 cửa sổ: Sharpe từng dòng nhiễu lớn, chênh 0,1–0,2 giữa các dòng chưa chắc có ý nghĩa thống kê.
- SOL/USD thiếu ~418 ngày, AVAX/USD chỉ từ 2021-11-18 (cửa sổ train đầu ngắn); không nội suy.
- Calibrator fit trên train ngắn (từ 2021-01, cửa sổ đầu chỉ 1 năm) và ít mã → dễ quá khớp.
- Survivorship bias (chọn mã theo hiện tại); giá Alpaca, không mô phỏng spread/trượt giá.
Tổng thời gian chạy: 108s (1.8 phút).
