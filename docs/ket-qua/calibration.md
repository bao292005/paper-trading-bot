# Kết quả hiệu chỉnh xác suất (P2)

Sinh tự động bởi `uv run python -m paper_bot.decision.calib_history` ngày 2026-11-24.
File này bị GHI ĐÈ mỗi lần chạy. Kết luận viết tay ở [calibration-ketluan.md](calibration-ketluan.md).

## Thiết lập
- Tín hiệu: MA_Crossover(10/30), RSI(14), BollingerBands(20,2) của bài gốc, chạy mỗi ngày trên 50 mã.
- Nhãn: y=1 nếu lợi suất log close(t)→close(t+5) theo hướng tín hiệu > 0.002 (phí 2 chiều).
- Feature: confidence thô, chiến lược (one-hot), BUY/SELL, regime (one-hot), RSI, khoảng cách MA50/MA200, lợi suất 5/20 ngày, vol20
  (các feature giá nhân với hướng tín hiệu). Forecast: **KHÔNG** (bật bằng `--forecast`).
- Mô hình: StandardScaler + LogisticRegression, hiệu chỉnh `isotonic` (CalibratedClassifierCV, cv=3) CHỈ trên train của từng cửa sổ.
- Walk-forward: train 3 năm trước, test 6 tháng, trượt 6 tháng, test 2019-01 → 2024-12. Train chỉ gồm tín hiệu có ngày nhãn (t+5) < ngày bắt đầu test.
- Dữ liệu chỉ tới 2024-12-31 (không chạm HOLDOUT). Bảng tín hiệu từ 2016 (2016–2018 chỉ làm train cho cửa sổ đầu).
- Base rate: tỷ lệ y=1 trên TRAIN của cùng cửa sổ (không nhìn test).
- Base rate theo hướng: tỷ lệ y=1 của BUY và của SELL riêng, cũng lấy từ TRAIN.

## Tổng hợp (out-of-sample, 31529 tín hiệu)
| Dự đoán | Brier |
|---|---|
| Mô hình hiệu chỉnh | 0.2491 |
| Base rate (train) | 0.2489 |
| Base rate theo hướng BUY/SELL (train) | 0.2454 |
| Confidence thô của bài gốc (coi như xác suất) | 0.2912 |

Tỷ lệ thắng thực tế (test): 0.463. Brier skill score (> 0 là tốt hơn):
so với base rate -0.0010; so với base rate theo hướng -0.0151.
- **Mô hình KHÔNG tốt hơn base rate (Brier cao hơn hoặc bằng).**
- **Mô hình KHÔNG tốt hơn base rate theo hướng (Brier cao hơn hoặc bằng).**

## Theo cửa sổ walk-forward
| window | n_train | n_test | y_rate | brier_model | brier_base | brier_base_dir |
|---|---|---|---|---|---|---|
| 2019-01 | 14989 | 2693 | 0.4274 | 0.2377 | 0.2452 | 0.2348 |
| 2019-07 | 16359 | 2592 | 0.4444 | 0.2412 | 0.2469 | 0.2399 |
| 2020-01 | 16165 | 2387 | 0.4650 | 0.2833 | 0.2492 | 0.2618 |
| 2020-07 | 15656 | 2463 | 0.4982 | 0.2463 | 0.2520 | 0.2445 |
| 2021-01 | 15181 | 2456 | 0.4650 | 0.2446 | 0.2488 | 0.2418 |
| 2021-07 | 15003 | 2678 | 0.4302 | 0.2433 | 0.2468 | 0.2421 |
| 2022-01 | 15199 | 2604 | 0.5342 | 0.2609 | 0.2552 | 0.2523 |
| 2022-07 | 15123 | 2548 | 0.4568 | 0.2496 | 0.2484 | 0.2477 |
| 2023-01 | 15091 | 2760 | 0.4467 | 0.2488 | 0.2479 | 0.2483 |
| 2023-07 | 15438 | 3037 | 0.4445 | 0.2431 | 0.2476 | 0.2439 |
| 2024-01 | 15952 | 2607 | 0.4595 | 0.2445 | 0.2484 | 0.2408 |
| 2024-07 | 16162 | 2704 | 0.4859 | 0.2501 | 0.2504 | 0.2488 |

## Theo chiến lược / hướng
| strategy | action | n | conf_tb | y_rate | p_model_tb | brier_model | brier_base | brier_base_dir |
|---|---|---|---|---|---|---|---|---|
| BollingerBands | BUY | 3448 | 0.6000 | 0.5435 | 0.5561 | 0.2563 | 0.2558 | 0.2485 |
| BollingerBands | SELL | 4569 | 0.6000 | 0.4373 | 0.4273 | 0.2496 | 0.2469 | 0.2472 |
| MA_Crossover | BUY | 1441 | 0.7000 | 0.5461 | 0.5362 | 0.2524 | 0.2552 | 0.2481 |
| MA_Crossover | SELL | 1421 | 0.7000 | 0.4363 | 0.4038 | 0.2488 | 0.2461 | 0.2462 |
| RSI | BUY | 6994 | 0.6685 | 0.5372 | 0.5360 | 0.2561 | 0.2547 | 0.2495 |
| RSI | SELL | 13656 | 0.6787 | 0.4063 | 0.4088 | 0.2433 | 0.2444 | 0.2416 |

## Reliability – mô hình hiệu chỉnh (10 bin)
| bin | n | p_mean | y_rate |
|---|---|---|---|
| 0.0-0.1 | 16 | 0.0008 | 0.6875 |
| 0.1-0.2 | 8 | 0.1423 | 0.3750 |
| 0.2-0.3 | 52 | 0.2471 | 0.5000 |
| 0.3-0.4 | 8133 | 0.3708 | 0.4109 |
| 0.4-0.5 | 13563 | 0.4456 | 0.4487 |
| 0.5-0.6 | 8725 | 0.5452 | 0.5303 |
| 0.6-0.7 | 614 | 0.6258 | 0.5065 |
| 0.7-0.8 | 264 | 0.7543 | 0.4318 |
| 0.8-0.9 | 106 | 0.8513 | 0.4811 |
| 0.9-1.0 | 48 | 0.9341 | 0.2708 |

## Reliability – confidence thô của bài gốc
| bin | n | p_mean | y_rate |
|---|---|---|---|
| 0.0-0.1 | 0 | — | — |
| 0.1-0.2 | 0 | — | — |
| 0.2-0.3 | 0 | — | — |
| 0.3-0.4 | 0 | — | — |
| 0.4-0.5 | 0 | — | — |
| 0.5-0.6 | 8017 | 0.6000 | 0.4830 |
| 0.6-0.7 | 17454 | 0.6531 | 0.4596 |
| 0.7-0.8 | 5248 | 0.7386 | 0.4428 |
| 0.8-0.9 | 810 | 0.8291 | 0.4519 |
| 0.9-1.0 | 0 | — | — |

## Ngưỡng 0.5
| tập | n | tỷ lệ thắng |
|---|---|---|
| mọi tín hiệu (bài gốc: conf ≥ 0.5 gần như luôn đạt) | 31529 | 0.4626 |
| p_model ≥ 0.5 | 9757 | 0.5243 |
