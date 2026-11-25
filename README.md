# paper-trading-bot

Bot **paper trading** (tiền ảo) cho chứng khoán Mỹ qua [Alpaca](https://alpaca.markets), dựng lại từ bài
*"Build a Python Paper Trading Bot with Alpaca API"* (Anil Gaddam, Medium, 11/2025).

> Dự án học tập. Không phải tư vấn đầu tư. Bot gắn cứng `paper=True`, không giao dịch tiền thật.

## Tính năng
- Chiến lược: **MA Crossover** (10/30) và **RSI** (14, ngưỡng 30/70); có sẵn Bollinger Bands, MACD để mở rộng
- Quản lý rủi ro: mỗi lệnh tối đa `min(2% sức mua, 1000$)`, lọc theo confidence, không bán khống, không đặt trùng khi còn lệnh chờ
- Dữ liệu nến ngày từ Alpaca (feed IEX, dùng được với tài khoản miễn phí)
- Log ra màn hình và `trading_bot.log`

## Cài đặt
Cần [uv](https://docs.astral.sh/uv/) và Python 3.12+.
```bash
git clone <repo-url> && cd paper-trading-bot
uv sync
cp .env.example .env   # điền key PAPER trading của Alpaca
```

## Chạy
```bash
uv run paper-bot report                                  # tài khoản + hiệu quả LiveBot (run "live-paper") từ DB
uv run paper-bot once --dry-run --symbols AAPL MSFT      # 1 vòng, ghi DB nhưng KHÔNG gửi lệnh (nên thử trước)
uv run paper-bot once                                    # 1 vòng thật trên tài khoản paper
uv run paper-bot loop --interval 30                      # chạy liên tục, 30 phút/vòng (Ctrl+C để dừng)
uv run paper-bot once --engine legacy                    # bot gốc của bài (MA + RSI), code không đổi
uv run paper-bot once --disable bracket regime           # tắt thành phần của engine full
uv run paper-bot once --llm                              # thêm LLMAgentStrategy (cần LLM_API_KEY; thiếu -> chạy không LLM)
uv run paper-bot report --reset-kill-switch              # gỡ kill switch (thêm --dry-run để gỡ cho run "live-dry")
uv run pytest                                            # test offline, không cần key
```

**Engine.** `--engine full` (mặc định) chạy pipeline mới: nến ngày đã đóng → regime (SPY) + forecast Chronos
→ chiến lược + veto + calibrator → sizing ATR, bracket SL/TP, giới hạn tương quan → Alpaca → SQLite `data/trading.db`.
`--engine legacy` là bot gốc. Thành phần tắt được: `regime calibration forecast atr_sizing bracket corr_limit`.
Chi tiết luồng: [docs/quy-trinh.md](docs/quy-trinh.md).

**Cảnh báo — đọc trước khi chạy thật:**
- **Cấu hình mặc định CHƯA được chứng minh.** Ablation trên giai đoạn phát triển 2019–2024
  ([docs/ket-qua/ablation-dev.md](docs/ket-qua/ablation-dev.md)): FULL có Sharpe 0.12, thua bot gốc (0.65) và SPY (0.92).
  Bỏ bracket (+0.60), regime (+0.34) hoặc forecast (+0.27) đều làm Sharpe TĂNG, tức là các thành phần này đang kéo Sharpe xuống.
  Cấu hình chính thức sẽ được khoá trước khi chạy HOLDOUT (≥ 2025-01-01), và chỉ kết quả holdout mới có giá trị kết luận.
- **Calibrator** fit trên cache tới `TradingConfig.calib_end = "2024-12-31"` để không đụng HOLDOUT. Nó được lưu ở
  `data/models/calibrator.pkl` và fit lại sau 7 ngày. **Sau khi chạy holdout xong** thì đổi `calib_end` sang `"yesterday"`.
- Bot chỉ dùng **nến đã đóng phiên**. Chạy trong giờ giao dịch thì tín hiệu dựa trên phiên trước, còn lệnh market khớp ở giá hiện tại.
- **Kill switch** ngắt khi lỗ trong ngày > 3% hoặc sụt từ đỉnh > 15%. Khi ngắt, bot huỷ các lệnh MUA chờ và chặn mua mới,
  nhưng giữ SL/TP và không tự bán. Trạng thái nằm ở `data/live_state.json` và vẫn giữ sau khi khởi động lại.
  Chỉ gỡ ngắt bằng `--reset-kill-switch` sau khi đã xem lại.
- `--dry-run` không gửi hay huỷ lệnh nào. Lệnh dự kiến được ghi vào DB với `run_id="live-dry"`, `status="dry_run"`.

## Cấu trúc
```
src/paper_bot/
  __init__.py     CLI (once / loop / report, --engine, --disable, --dry-run, --llm, --reset-kill-switch)
  config.py       TradingConfig: key, tham số rủi ro, danh sách mã, engine, cờ thành phần, calib_end
  live.py         LiveBot: engine full (pipeline mới, calibrator, fill, kill switch)
  strategies.py   TradingSignal + các chiến lược
  managers.py     DataManager, PortfolioManager, OrderManager (engine legacy)
  bot.py          TradingBot: engine legacy (bot gốc)
  data/ decision/ risk.py execution.py storage.py backtest/   các thành phần của engine full
docs/quy-trinh.md Quy trình từ input đến output
```
