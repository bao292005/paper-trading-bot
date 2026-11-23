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
| P1 | Cache lịch sử 10 năm (UNIVERSE_50 + SPY) | ✅ 51 mã × 2723 phiên, SIP | `data/cache.py`, `scripts/fetch_history.py` |
| P1 | Adapter: alpaca-py (giữ `get_historical_data`), stream, News | ✅ (News chỉ có từ 2026-04) | `data/sources.py` |
| P1 | Regime detector | ⏳ | |
| P1 | Forecast quantile: naive / Chronos-Bolt / Kronos | ⏳ | |
| P1 | `get_market_state()` → MarketState | ⏳ | |
| P2 | Regime switching | ⏳ | |
| P2 | Xác suất hiệu chỉnh + rule veto | ⏳ | |
| P2 | LLMAgentStrategy (multi-agent, OrcaRouter deepseek) | ⏳ | |
| P3 | Sizing theo ATR + độ bất định forecast, mức SL/TP, giới hạn tương quan | ✅ 37 test | `risk.py` |
| P3 | Bracket order, kill switch, đối soát | ⏳ đang làm | `execution.py` |
| P4 | Engine backtest (chống rò rỉ, phí, khớp open t+1, SL/TP) | ✅ 13 test | `backtest/engine.py` |
| P4 | Metrics, baseline B0/B1/B2, walk-forward | ⏳ đang làm | |
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

### 2026-11-23 — Giai đoạn 1, bước 1–2 (agent j-p1, j-p3, j-p4)
Chi tiết từng phần ở [cai-tien/P1.md](cai-tien/P1.md), [cai-tien/P3.md](cai-tien/P3.md), [cai-tien/P4.md](cai-tien/P4.md).
- **P1:**
  - Tải và cache nến ngày 2016-01-04 → 2026-10-30 cho 51 mã (feed SIP, điều chỉnh split + cổ tức).
  - `AlpacaDataManager` **kế thừa** `DataManager` của bài, giữ nguyên `get_historical_data`, thêm `get_bars` đọc cache trước.
  - Thêm `NewsSource` (chỉ trả tin ≤ thời điểm quyết định) và `StreamSource` (websocket).
  - Phát hiện: Alpaca News không có tin trước khoảng 2026-04. Đã ghi vào mục "Sửa đổi" của prereg: tin tức chỉ dùng khi chạy forward.
- **P3:**
  - Sửa đúng chỗ yếu của bài: "2%" giờ là **rủi ro** (khoảng cách tới stop = 2×ATR), không còn là cỡ vị thế.
  - Giữ `legacy_position_size` (công thức gốc) cho baseline B2.
  - Thêm: thu nhỏ vị thế khi dự báo bất định, mức SL/TP, chặn mã có tương quan > 0,8, tối đa 10 vị thế, `make_order_intent` gộp tất cả.
- **P4:**
  - Engine backtest: policy chỉ thấy dữ liệu ≤ asof, khớp ở open t+1, phí 10 bps.
  - SL/TP: nếu cả hai chạm cùng ngày thì tính stop-loss trước; giá mở cửa nhảy qua mức nào thì khớp ở open.
  - Không margin, không bán khống.
  - Quy ước SELL (đóng hết hay bán một phần) đã ghi vào `OrderIntent`.
- Tổng: 71 test pass.
