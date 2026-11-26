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
| P1 | Regime detector + features (atr14, vol20, rsi14...) | ✅ | `data/regime.py`, `data/features.py` |
| P1 | Forecast quantile: naive / Chronos-Bolt / Kronos | ✅ (không mô hình nào hơn naive) | `data/forecast.py`, [ket-qua/forecast-eval.md](ket-qua/forecast-eval.md) |
| P1 | Tính sẵn forecast Chronos 2018-07→2024-12 (83k dòng) | ✅ | `scripts/precompute_forecasts.py` |
| P1 | `build_market_state()` → MarketState | ✅ | `data/market_state.py` |
| P2 | Bọc MA/RSI/Bollinger gốc thành StateStrategy | ✅ | `decision/base.py` |
| P2 | Regime switching | ✅ | `decision/regime_switch.py` |
| P2 | Gom tín hiệu mâu thuẫn + rule veto | ✅ | `decision/aggregate.py`, `decision/veto.py` |
| P2 | Xác suất hiệu chỉnh (walk-forward) | ✅ (không hơn base rate) | `decision/calibration.py`, [ket-qua/calibration.md](ket-qua/calibration.md) |
| P2 | LLMAgentStrategy (4 agent, chỉ forward, test bằng LLM giả) | ✅ code; chờ key | `decision/llm_agent.py` |
| P2 | DecisionPipeline: tín hiệu → veto → hiệu chỉnh → gộp → lọc | ✅ | `decision/pipeline.py` |
| P2 | Policy FULL có cờ tắt từng thành phần (cho ablation) | ✅ | `decision/full_policy.py` |
| P4/P2 | Bảng ablation giai đoạn phát triển | ✅ FULL thua B2 | `scripts/run_ablation.py`, [ket-qua/ablation-dev.md](ket-qua/ablation-dev.md) |
| P3 | Sizing theo ATR + độ bất định forecast, mức SL/TP, giới hạn tương quan | ✅ 37 test | `risk.py` |
| P3 | Bracket order, kill switch, đối soát | ✅ commit 99c7c31 | `execution.py` |
| P4 | Engine backtest (chống rò rỉ, phí, khớp open t+1, SL/TP) | ✅ 13 test | `backtest/engine.py` |
| P4 | Metrics, baseline B0/B1/B2, walk-forward 12 cửa sổ, ablation | ✅ | `backtest/` |
| P4 | Kết quả baseline 2019–2024 | ✅ | [ket-qua/baseline-dev.md](ket-qua/baseline-dev.md) |
| P4 | Bảng ablation + chạy HOLDOUT 1 lần | ⏳ | |
| P4 | Dashboard Streamlit (5 trang) + ghi kết quả backtest vào DB | ✅ | `dashboard.py`, `backtest/report.py` |
| P4 | Tăng tốc B2 (70–260 giây → 8–10 giây, kết quả giống hệt) | ✅ | `backtest/baselines.py` |
| — | Ghép tất cả vào `TradingBot` (giữ lệnh `once/loop/report` cũ, thêm `--engine full\|legacy`, `--dry-run`, `--disable`) | ✅ (chưa có tín hiệu thật nào để thử đặt lệnh) | `live.py`, `bot.py`, [cai-tien/TICH-HOP.md](cai-tien/TICH-HOP.md) |
| — | UI Streamlit 4 trang (Pipeline, Đánh giá, Giám sát, Điều khiển) | ✅ | `ui/`, [cai-tien/UI.md](cai-tien/UI.md) |
| — | Chạy HOLDOUT 2025-01 → 2026-10 (1 lần) | ⏳ chờ chốt cấu hình | |
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

### 2026-11-23 — P3 hoàn thành (agent j-p3, commit 99c7c31)
- Thêm `execution.py`:
  - **Bracket order:** lệnh mua kèm sẵn stop-loss và take-profit, hiệu lực GTC.
  - **Kill switch:** tự ngắt khi lỗ trong ngày > 3% hoặc sụt từ đỉnh > 15%.
    - Khi ngắt: **chỉ huỷ lệnh MUA đang chờ**, giữ nguyên stop/TP đang bảo vệ vị thế, không tự bán (quyết định của Jarvis).
    - Sau đó chặn mọi lệnh mua mới; lệnh bán vẫn được phép.
  - **Đối soát:** so lệnh và vị thế trên broker với DB, phát hiện lệnh lạ / lệnh thiếu / vị thế lệch.
- Bảng "bài gốc khuyến nghị → hàm hiện thực" nằm trong [cai-tien/P3.md](cai-tien/P3.md).
- Tổng: 122 test pass.

### 2026-11-23 — P1 bước 3–4 (agent j-p1)
- **Regime:** dựa trên SPY. Giá trên/dưới MA200 cho ra bull/bear; vol20 so với median 1 năm trước cho ra volatile/calm.
  - Có kiểm tra chống nhìn tương lai.
  - Ví dụ thật: 2020-03-20 ra bear_volatile, 2021-06 ra bull_calm.
  - Bối cảnh `bear_calm` rất hiếm: chỉ 40 ngày trong 10 năm.
- **Forecast:** 3 mô hình dùng chung một interface, cho ra quantile 10/50/90 của lợi suất 5 ngày tới.
- **Tốc độ trên CPU:**
  - Chronos: 0,07 giây cho 10 mã.
  - Kronos: khoảng 10 giây mỗi mã.
- **Quyết định (Jarvis):** hệ thống FULL dùng **Chronos**, vì Kronos quá chậm cho backtest hằng ngày. Kronos chỉ được đánh giá riêng (10 mã, mỗi 20 phiên, 10 mẫu, thử GPU MPS).
- Lỗi phát hiện và đã sửa: RSI ra NaN khi giá chỉ tăng; nay ra 100.
- Tổng: 129 test pass.

### 2026-11-23 — P2 bước 1–3 (agent j-p2)
- `wrap_legacy` bọc MA, RSI, Bollinger của bài gốc thành chiến lược đọc `MarketState`. **Không sửa** `strategies.py`; `generate_signals` cũ vẫn chạy.
- **Regime switching** (chọn chiến lược theo bối cảnh thị trường):

  | Bối cảnh | Chiến lược dùng |
  |---|---|
  | Tăng (bull) | MA (theo xu hướng) |
  | Giảm êm (bear_calm) | RSI (đảo chiều) |
  | Giảm mạnh (bear_volatile) | Chỉ cho phép bán |
  | Không rõ (unknown) | MA + RSI như bài gốc |

- **Gom tín hiệu:** sửa hạn chế "bài không xử lý mâu thuẫn".
  - BUY và SELL ngược nhau: bên nào có tổng điểm lớn hơn ít nhất 0,2 thì theo bên đó, không thì đứng ngoài.
  - Lý do quyết định được ghi lại.
- **Veto rule-based** (3 luật):
  - Forecast dự báo giảm.
  - Giá nằm dưới MA200 trong bối cảnh giảm mạnh.
  - Thiếu dữ liệu (dưới 60 nến).

  Tín hiệu bị chặn không bị xoá: vẫn được lưu kèm lý do, để giải thích được về sau.
- Tổng: 146 test pass.

### 2026-11-24 — P4: kết quả baseline (agent j-p4)
Walk-forward 2019-01 → 2024-12, 51 mã, phí 10 bps. Chưa đụng tới holdout.

| | Sharpe | CAGR | MaxDD |
|---|---|---|---|
| B0 mua giữ SPY | 0,92 | 17,1% | −33,6% |
| B1 chia đều 50 mã | 0,99 | 18,0% | −30,1% |
| **B2 bot gốc của bài** | **0,65** | 3,3% | −10,3% |

- **Bot gốc thua buy & hold** cả về Sharpe lẫn lợi nhuận. Nó lỗ ít năm 2022 (−0,8% so với SPY −18,1%), nhưng chủ yếu vì phần lớn vốn nằm im ở tiền mặt (exposure ~16%).
- Độ nhạy theo phí: Sharpe B2 = 0,69 ở 5 bps, 0,56 ở 20 bps.
- **Mức FULL phải vượt** (theo prereg): Sharpe ≥ **0,85** và MaxDD không tệ hơn **−30,1%**.

### 2026-11-24 — P2 bước 4–6 (agent j-p2)
- **Hiệu chỉnh xác suất:** walk-forward trên 31,5 nghìn tín hiệu kiểm tra ngoài mẫu, 2019–2024.
  - Brier (càng thấp càng tốt): mô hình **0,2491**, tỷ lệ gốc **0,2489** → **không chứng minh được giá trị**.
  - Confidence gán cứng của bài gốc: Brier **0,291**, tệ hơn cả tỷ lệ gốc. Bài báo 0,6–0,83, thắng thật chỉ 44–48%, tức **tự tin quá mức**.
  - Kết luận: tín hiệu MA/RSI gần như không dự báo được giá 5 ngày tới. Thay bằng xác suất trung thực vẫn là cải tiến thật, vì nó không thổi phồng.
- **LLMAgentStrategy** gồm 4 vai: phân tích kỹ thuật, tin tức, phản biện rủi ro, người quyết định trả JSON.
  - Lỗi bất kỳ thì trả HOLD. Thiếu key thì không chạy. Dữ liệu cũ hơn 7 ngày thì không chạy (chỉ dùng forward).
  - Toàn bộ lập luận được lưu để giải thích.
- **DecisionPipeline:** sinh tín hiệu → veto → hiệu chỉnh → gộp → lọc ngưỡng.
- Tổng: 177 test pass.

### 2026-11-24 — P1 đánh giá dự báo + P4 dashboard (commit 9c0afd8)
- **Dự báo 5 ngày** trên 2019–2024, đo bằng pinball loss (càng thấp càng tốt), so với naive:
  - **Chronos:** tệ hơn naive 2,1% trên 50 mã (có ý nghĩa thống kê, t = −2,89).
  - **Kronos:** loss gấp **2,27 lần** naive. Median lệch âm có hệ thống: dự báo −2%, thực tế +0,3%.
  - Kết luận trung thực: **dự báo giá cổ phiếu theo ngày bằng foundation model không hơn đoán ngây thơ**. Hệ thống FULL vẫn dùng Chronos, chủ yếu để lấy độ rộng q90−q10 làm thước đo bất định khi định cỡ vị thế. Ablation sẽ cho biết phần này có giúp gì không.
- **MarketState:** có test chống rò rỉ; tin đăng sau 16:00 giờ New York bị loại.
- **Dashboard:** chạy bằng `uv run streamlit run src/paper_bot/dashboard.py`. Gồm 5 trang:
  - Equity & drawdown (so nhiều run)
  - Lệnh / fill
  - Tín hiệu (kèm lý do, xác suất, veto)
  - Sự kiện (kill switch, đối soát)
  - Báo cáo
- **Tăng tốc B2:** đối chiếu với bản cũ, 3188/3188 fill khớp. Lúc đối chiếu còn bắt được một lỗi so sánh số thực (MA10 = MA30 đúng bằng nhau), đã sửa.
- Sửa `.gitignore`: dòng `data/` từng chặn nhầm cả code `src/paper_bot/data/`.
- Tổng: **178 test pass** (Jarvis tự chạy lại để kiểm chứng).

### 2026-11-24 — P2: so với tỷ lệ gốc theo hướng + policy FULL
- So thêm với cách đoán "tỷ lệ thắng riêng cho BUY và SELL": Brier mô hình **0,2491**, cách đoán này **0,2454**, nên **mô hình thua**.
  - Lợi thế duy nhất của bộ hiệu chỉnh đến từ việc biết lệnh là BUY hay SELL. Các feature khác (RSI, MA, regime...) không thêm thông tin.
  - Kết luận viết tay: [ket-qua/calibration-ketluan.md](ket-qua/calibration-ketluan.md).
- `decision/full_policy.py`: policy FULL có **6 cờ**, mỗi cờ tắt một thành phần để chạy ablation.
  - Có test chống rò rỉ: sửa dữ liệu tương lai không làm đổi lệnh quá khứ.
- **Phát hiện khi chạy thử:** bật hiệu chỉnh với ngưỡng 0,5 thì bot gần như không giao dịch. Xác suất của SELL chỉ ~0,3–0,45 nên lệnh thoát vị thế bị lọc hết.
  - **Quyết định (Jarvis): ngưỡng chỉ áp cho lệnh VÀO (BUY).** Tín hiệu bán khi đang nắm vị thế luôn được thực hiện, vì thoát vị thế là giảm rủi ro.
  - Quyết định này đưa ra trên giai đoạn phát triển, trước khi có bất kỳ kết quả ablation nào.
- Tổng: 183 test pass.

### 2026-11-24 — Bảng ablation, giai đoạn phát triển 2019–2024 (agent j-p2)
Chạy 47 phút. Chưa phải kết quả chính thức: holdout chưa chạy.

| Cấu hình | Sharpe | CAGR | MaxDD | Bỏ đi thì Sharpe đổi |
|---|---|---|---|---|
| **FULL** | **0,12** | 0,6% | −16,3% | — |
| bỏ bracket/stop | 0,72 | 6,4% | −16,5% | **+0,60** |
| bỏ regime switching | 0,46 | 3,5% | −13,0% | +0,34 |
| bỏ forecast | 0,39 | 2,9% | −19,0% | +0,27 |
| bỏ sizing ATR | 0,26 | 0,2% | −2,0% | +0,14 |
| bỏ hiệu chỉnh xác suất | 0,25 | 1,5% | −17,5% | +0,13 |
| bỏ giới hạn tương quan | 0,12 | 0,6% | −14,8% | 0,00 |
| B0 SPY / B1 chia đều / B2 bot gốc | 0,92 / 0,99 / 0,65 | | | |

- **FULL thua bot gốc B2** và còn xa mục tiêu 0,85.
- Bỏ thành phần nào cũng làm Sharpe **tăng** hoặc giữ nguyên. Theo prereg, không thành phần nào "có giá trị".
- Sai số Sharpe trên 6 năm vào khoảng ±0,4. Vì vậy chỉ hai kết luận tương đối chắc:
  - bracket/stop gây hại rõ (+0,60 khi bỏ);
  - FULL thua B2.

  Các chênh lệch 0,1–0,3 còn lại có thể chỉ là nhiễu.
- Bài học: thêm nhiều thành phần "thông minh" không tự động làm bot tốt hơn. Mỗi thành phần phải được đo riêng, và bảng ablation là công cụ làm việc đó.
- Tổng: 185 test pass (Jarvis tự chạy lại).

### 2026-11-25 — Ghép vào TradingBot (agent j-int)
- `--engine full` (mặc định) chạy `LiveBot` trong `live.py`, nối toàn bộ P1 → P2 → P3 → DB. `--engine legacy` là bot gốc của bài, code không đổi.
  - Các lệnh cũ `once`, `loop`, `report`, `--symbols` vẫn giữ nguyên.
- Lệnh mới:
  - `--dry-run`: chạy đủ các bước, ghi DB, **không gửi hay huỷ lệnh nào**; có test chứng minh.
  - `--disable <thành phần>`
  - `--llm`
  - `--reset-kill-switch`
- **Nhất quán với backtest:** chỉ dùng nến ngày đã đóng; bộ hiệu chỉnh chỉ học tới `calib_end = 2024-12-31` cho tới khi chạy holdout.
- **Vận hành thật:**
  - kill switch có trạng thái lưu trong `data/live_state.json`;
  - đối soát lệnh với broker khi khởi động;
  - đồng bộ lệnh đã khớp vào DB;
  - lỗi một mã hoặc lỗi mạng không làm sập `loop`;
  - mọi tín hiệu, lệnh, equity, sự kiện ghi vào DB (`live-paper` / `live-dry`) nên dashboard xem được.
- **Chạy thật dry-run** (2026-11-25, AAPL/MSFT/NVDA): fit bộ hiệu chỉnh, tải Chronos, dùng nến tới 2026-11-24. Không mã nào có tín hiệu nên 0 lệnh. DB ghi equity và sự kiện đúng.
- Luồng 13 bước của engine full: [quy-trinh.md](quy-trinh.md), phần A.
- Tổng: **219 test pass** (Jarvis tự chạy lại).

### 2026-11-26 — UI Streamlit (agent j-ui, j-ui2)
Chạy: `uv run streamlit run src/paper_bot/ui/app.py`
- **Trang chủ:** sơ đồ luồng P1 → P2 → P3 → DB → P4.
- **Pipeline:**
  - Chọn mã, ngày, bật/tắt 6 thành phần, vốn và vị thế giả định.
  - P1: biểu đồ giá kèm MA, regime, dải dự báo, chỉ báo, tin tức.
  - P2: bảng tín hiệu kèm lý do, xác suất, veto.
  - P3: lệnh mô phỏng (số cổ, SL/TP, % vốn, tương quan).
  - Có chế độ quét N phiên.
- **Đánh giá:** equity và drawdown các lần backtest; bảng ablation, calibration, forecast. Có cảnh báo "holdout chưa chạy".
- **Giám sát:** tài khoản Alpaca (chỉ đọc), kill switch, equity, tín hiệu, lệnh, fill, sự kiện trong DB.
- **Điều khiển:**
  - Chạy thử (dry-run).
  - **Chạy thật:** chỉ mở khi tick xác nhận **và** gõ đúng `XAC NHAN`. Sự kiện `ui_real_run` được ghi vào DB trước khi chạy.
  - Gỡ kill switch, cũng phải xác nhận.
  - Không có nút `loop`.
- **An toàn:**
  - Lệnh dạng list, `shell=False`. Mã chỉ nhận chữ A–Z; tên thành phần theo danh sách cho phép.
  - Chốt chặn cuối ngay trước subprocess.
  - Có test cho 7 cách xác nhận sai và các input độc (`AAPL;`, `$(rm)`...).
- Tổng: **307 test pass** (Jarvis tự chạy lại). Server đã trả HTTP 200 cho cả 5 trang. Nút chạy thật chưa được bấm thử.
