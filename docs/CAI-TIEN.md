# Theo dõi cải tiến (so với bài báo gốc)

File chính để theo dõi. Mỗi thay đổi ghi: **ngày, ai/agent, file, làm gì, kế thừa gì từ bài, kết quả test**.
Tiêu chí đánh giá đã chốt trước: [prereg-danh-gia.md](prereg-danh-gia.md). Luồng dữ liệu chung: `src/paper_bot/contracts.py`.

```
P1 Tiên (DataManager) --MarketState--> P2 Lanh (Strategy) --Signal--> P3 Mfan (Risk/Order) --Order/Fill--> SQLite --> P4 Bảo (Backtest/Report)
```

## Bảng trạng thái

| Phần | Hạng mục | Trạng thái | File |
|---|---|---|---|
| 0 | Hợp đồng chung (MarketState, Signal, OrderIntent, Fill) | ✅ xong | `contracts.py` |
| 0 | DB SQLite (signals, orders, fills, equity, events) | ✅ xong | `storage.py` |
| 0 | Tiêu chí đánh giá chốt trước + UNIVERSE_50 | ✅ xong | `docs/prereg-danh-gia.md`, `config.py` |
| 0 | Thư viện: sklearn, streamlit, chronos, torch, openai, einops; Kronos (MIT) chép vào `vendor/kronos` | ✅ xong | `pyproject.toml`, `vendor/` |
| P1 | Cache lịch sử 10 năm (UNIVERSE_50 + SPY) | ⏳ | |
| P1 | Adapter: alpaca-py (giữ `get_historical_data`), stream, News | ⏳ | |
| P1 | Regime detector | ⏳ | |
| P1 | Forecast quantile: naive / Chronos-Bolt / Kronos | ⏳ | |
| P1 | `get_market_state()` → MarketState | ⏳ | |
| P2 | Regime switching | ⏳ | |
| P2 | Xác suất hiệu chỉnh + rule veto | ⏳ | |
| P2 | LLMAgentStrategy (multi-agent, OrcaRouter deepseek) | ⏳ | |
| P3 | Sizing theo ATR + độ bất định forecast | ⏳ | |
| P3 | Bracket order, giới hạn tương quan, kill switch, đối soát | ⏳ | |
| P4 | Backtest walk-forward có phí/slippage | ⏳ | |
| P4 | Bảng ablation + chạy HOLDOUT 1 lần | ⏳ | |
| P4 | Dashboard Streamlit + log vào DB | ⏳ | |
| — | Ghép tất cả vào `TradingBot` (giữ lệnh `once/loop/report` cũ) | ⏳ | |
| — | Alpaca MCP | ❌ bỏ (theo quyết định 2026-11-23) | |

## Nhật ký thay đổi

### 2026-11-23 — Giai đoạn 0 (Jarvis)
- Thêm `contracts.py`: định nghĩa dữ liệu trao đổi giữa 4 phần.
  - `Signal` **kế thừa** `TradingSignal` của bài gốc, thêm `prob`, `veto`, `strategy`.
  - Có quy tắc chống rò rỉ dữ liệu: quyết định ở giá đóng cửa ngày t, khớp lệnh ở giá mở cửa ngày t+1.
- Thêm `storage.py`: SQLite thay cho log text. Cột `run_id` dùng để tách bot thật và từng lần backtest.
- Thêm `docs/prereg-danh-gia.md`: baseline B0/B1/B2, chi phí 10 bps, holdout 2025-01 → 2026-10 chỉ chạy 1 lần, tiêu chí thắng.
- `config.py`: thêm `UNIVERSE_50` và `BENCHMARK = "SPY"`.
