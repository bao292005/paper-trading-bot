# Baseline CRYPTO trên giai đoạn phát triển (2022-01 → 2024-12)

Sinh bởi `scripts/run_backtest.py --asset crypto` ngày 2026-11-26. Theo [prereg-crypto.md](../prereg-crypto.md): 5 mã BTC/ETH/SOL/AVAX/LINK (USD), walk-forward 6 cửa sổ test 6 tháng (train mở rộng từ 2021-01), vốn 100,000$, khớp open nến ngày sau (UTC), phí taker 25 bps mỗi chiều (mua: trừ vào coin nhận; bán: trừ vào USD), mua lẻ 6 chữ số, Sharpe năm hoá 365 ngày. **Không dùng dữ liệu holdout (>= 2025-01-01).**

## Tổng hợp (sau phí)

| cấu hình | sharpe | cagr | max_drawdown | n_trades | win_rate | turnover | exposure | final_equity |
|---|---|---|---|---|---|---|---|---|
| C0 (BTC buy&hold) | 0.68 | 25.0% | -67.1% | 1 | 100.0% | 0.19 | 99.9% | 195,355$ |
| C1 (chia đều 5 mã) | 0.51 | 12.1% | -80.4% | 78 | 33.3% | 0.96 | 97.2% | 140,827$ |
| C2 (bot gốc) | 0.54 | 2.4% | -3.2% | 117 | 51.3% | 0.39 | 2.7% | 107,300$ |
| C2 @15bps | 0.55 | 2.5% | -3.2% | 117 | 53.0% | 0.39 | 2.7% | 107,544$ |
| C2 @40bps | 0.51 | 2.3% | -3.2% | 117 | 50.4% | 0.39 | 2.7% | 106,933$ |

## Lợi suất từng năm

| năm | C0 (BTC buy&hold) | C1 (chia đều 5 mã) | C2 (bot gốc) | C2 @15bps | C2 @40bps |
|---|---|---|---|---|---|
| 2022 | -65.5% | -80.2% | -1.7% | -1.6% | -1.8% |
| 2023 | 155.8% | 174.4% | 1.5% | 1.6% | 1.4% |
| 2024 | 121.1% | 159.4% | 7.5% | 7.6% | 7.4% |

## Sharpe từng năm

| năm | C0 (BTC buy&hold) | C1 (chia đều 5 mã) | C2 (bot gốc) | C2 @15bps | C2 @40bps |
|---|---|---|---|---|---|
| 2022 | -1.34 | -1.42 | -0.69 | -0.65 | -0.74 |
| 2023 | 2.34 | 2.18 | 1.00 | 1.04 | 0.94 |
| 2024 | 1.75 | 1.37 | 1.02 | 1.03 | 1.00 |

## Max drawdown từng năm

| năm | C0 (BTC buy&hold) | C1 (chia đều 5 mã) | C2 (bot gốc) | C2 @15bps | C2 @40bps |
|---|---|---|---|---|---|
| 2022 | -67.1% | -80.4% | -3.2% | -3.2% | -3.2% |
| 2023 | -20.1% | -33.6% | -1.1% | -1.0% | -1.1% |
| 2024 | -26.2% | -38.9% | -1.9% | -1.9% | -1.9% |

## Ghi chú và hạn chế
- **Chỉ 5 mã, tương quan cao** (đều theo BTC): đa dạng hoá gần như không có; kết quả phụ thuộc mạnh vào chu kỳ 2022 (sập) → 2023-2024 (hồi).
- **Survivorship bias:** 5 mã chọn theo hiện tại (năm 2026), các mã đã chết (LUNA, FTT...) không có mặt.
- **Thiếu dữ liệu:** SOL/USD thiếu ~418 ngày, AVAX/USD chỉ có từ 2021-11-18; ngày thiếu không nội suy, mã không có nến thì không quyết định/không khớp. C1 chia equity/5 cố định: phần của mã thiếu nến ở ngày cân bằng giữ nguyên (tiền mặt hoặc vị thế cũ).
- **C2 dùng ít vốn:** mỗi lệnh tối đa 1000$ / 100k, exposure TB ~3%; Sharpe (rf=0) không đổi theo quy mô nhưng CAGR/MaxDD của C2 không so trực tiếp với C0/C1.
- Nến ngày Alpaca crypto (chốt 00:00 UTC), giá tổng hợp sàn của Alpaca, không mô phỏng spread/trượt giá riêng.
- Tổng thời gian chạy: 2.3s.
