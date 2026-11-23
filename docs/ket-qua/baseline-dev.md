# Baseline trên giai đoạn phát triển (2019-01 → 2024-12)

Sinh bởi `scripts/run_backtest.py` ngày 2026-11-24. Walk-forward 12 cửa sổ (train 3 năm / test 6 tháng), vốn 100,000$, khớp ở open phiên sau, phí mỗi chiều 10 bps (trừ khi ghi khác). **Không dùng dữ liệu holdout (>= 2025-01-01).**

## Tổng hợp (sau phí)

| cấu hình | sharpe | cagr | max_drawdown | n_trades | win_rate | turnover | exposure | final_equity | time_s |
|---|---|---|---|---|---|---|---|---|---|
| B0 (SPY) | 0.92 | 17.1% | -33.6% | 1 | 100.0% | 0.05 | 99.4% | 257,808$ | 0.1 |
| B1 (chia đều 50 mã) | 0.99 | 18.0% | -30.1% | 1285 | 92.5% | 0.34 | 97.2% | 270,086$ | 3.6 |
| B2 (bot gốc) | 0.65 | 3.3% | -10.3% | 1610 | 63.9% | 2.21 | 15.9% | 121,586$ | 10.4 |
| B2 @5bps | 0.69 | 3.5% | -10.3% | 1610 | 65.1% | 2.19 | 15.8% | 123,071$ | 7.7 |
| B2 @20bps | 0.56 | 2.9% | -10.4% | 1610 | 61.1% | 2.24 | 16.1% | 118,618$ | 8.1 |

Cột: sharpe năm hoá (rf=0); cagr; max_drawdown; n_trades = số lần đóng (gồm vị thế còn mở cuối kỳ); win_rate sau phí; turnover năm hoá; exposure = tỷ trọng cổ phiếu TB; time_s = thời gian chạy.

## Lợi suất từng năm

| năm | B0 (SPY) | B1 (chia đều 50 mã) | B2 (bot gốc) | B2 @5bps | B2 @20bps |
|---|---|---|---|---|---|
| 2019 | 31.7% | 33.0% | 5.9% | 6.2% | 5.4% |
| 2020 | 18.3% | 22.2% | 4.0% | 4.3% | 3.6% |
| 2021 | 28.4% | 27.2% | 4.4% | 4.6% | 4.0% |
| 2022 | -18.1% | -11.5% | -0.8% | -0.6% | -1.3% |
| 2023 | 26.0% | 25.2% | 3.8% | 4.0% | 3.4% |
| 2024 | 24.8% | 18.0% | 2.6% | 2.8% | 2.3% |

## Sharpe từng năm

| năm | B0 (SPY) | B1 (chia đều 50 mã) | B2 (bot gốc) | B2 @5bps | B2 @20bps |
|---|---|---|---|---|---|
| 2019 | 2.34 | 2.36 | 2.27 | 2.37 | 2.07 |
| 2020 | 0.69 | 0.78 | 0.43 | 0.45 | 0.39 |
| 2021 | 2.01 | 2.16 | 1.99 | 2.10 | 1.79 |
| 2022 | -0.71 | -0.46 | -0.11 | -0.07 | -0.18 |
| 2023 | 1.86 | 1.98 | 1.56 | 1.65 | 1.38 |
| 2024 | 1.83 | 1.67 | 1.54 | 1.66 | 1.31 |

## Max drawdown từng năm

| năm | B0 (SPY) | B1 (chia đều 50 mã) | B2 (bot gốc) | B2 @5bps | B2 @20bps |
|---|---|---|---|---|---|
| 2019 | -6.5% | -6.8% | -1.5% | -1.4% | -1.5% |
| 2020 | -33.6% | -30.1% | -10.3% | -10.3% | -10.4% |
| 2021 | -5.1% | -4.2% | -0.9% | -0.8% | -1.0% |
| 2022 | -24.4% | -22.7% | -4.6% | -4.6% | -4.8% |
| 2023 | -9.9% | -8.7% | -1.5% | -1.5% | -1.6% |
| 2024 | -8.4% | -5.9% | -1.1% | -1.1% | -1.2% |

## Ghi chú và hạn chế
- **Survivorship bias:** 50 mã được chọn theo vốn hoá hiện tại, nên B1/B2 được lợi nhờ biết trước các mã này sống sót và lớn mạnh.
- **B1:** mỗi lần bán bớt khi cân bằng lại được tính là 1 trade (bán một phần), nên n_trades/win_rate của B1 không có ý nghĩa như chiến lược giao dịch.
- **B2 dùng ít vốn:** mỗi lệnh tối đa 1000$ trên vốn 100k; vị thế tích luỹ dần nên exposure TB chỉ ~16%. Khi rf=0, Sharpe không đổi theo quy mô vị thế nên so Sharpe vẫn hợp lệ (prereg giữ nguyên), nhưng CAGR/max drawdown của B2 không so trực tiếp với B0/B1 được.
- "Sức mua" của B2 = tiền mặt (không có đòn bẩy 2x như tài khoản Alpaca).
- Baseline không học gì từ dữ liệu train; walk-forward ở đây chỉ để cùng khung với FULL. Danh mục được giữ liên tục qua các cửa sổ.
- Chỉ có lệnh market; SL/TP (không dùng ở baseline) kiểm tra theo nến ngày, SL trước nếu cả hai cùng chạm.
- Tổng thời gian chạy (gồm đọc dữ liệu): 30.3s.
