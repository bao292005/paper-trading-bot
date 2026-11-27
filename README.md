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

## Crypto (`--asset crypto`)
```bash
uv run paper-bot once --asset crypto --dry-run            # 1 vòng crypto, KHÔNG gửi lệnh (nên thử trước)
uv run paper-bot loop --asset crypto --interval 60        # mặc định 60 phút/vòng, chạy cả cuối tuần
uv run paper-bot once --asset crypto --exchange-stop      # thêm stop-limit bán trên sàn (mặc định TẮT)
```
- Chỉ 5 mã `BTC/USD ETH/USD SOL/USD AVAX/USD LINK/USD`, chỉ engine `full` (`--engine legacy` bị từ chối). Số lượng lẻ, phí 25 bps,
  nến ngày đóng theo UTC, lệnh GTC. Tiêu chí đánh giá: [docs/prereg-crypto.md](docs/prereg-crypto.md).
- **SL/TP do bot tự quản**: Alpaca crypto không có bracket/OCO, nên lệnh mua gửi KHÔNG kèm SL/TP; mức SL/TP lưu trong
  `data/live_state.json` và được kiểm mỗi vòng (bán market toàn bộ qty thực khi chạm). Không kiểm realtime: độ trễ tối đa bằng `--interval`.
- Kill switch chặn MUA nhưng KHÔNG chặn bán SL/TP. Run_id riêng cho crypto (`report`, `--reset-kill-switch` đi theo `--asset`).
- Web (`/`): ô chọn Stock/Crypto, form lệnh crypto (qty lẻ, market/limit/stop-limit, không SL/TP, vẫn cần `XAC NHAN`).
  `/tester`: ô chọn loại tài sản (dev 2022–2024, tối đa 2024-12-31, phí 25 bps, IS 2022–2023 / OOS 2024). UI Streamlit trang "Điều khiển" có ô chọn tương tự.
- Cảnh báo: equity/cash lấy theo tài khoản chung (gồm cổ phiếu đang giữ); cấu hình crypto cũng chưa chạy holdout. Chi tiết: [docs/cai-tien/CRYPTO-LIVE.md](docs/cai-tien/CRYPTO-LIVE.md).

## Bar Replay + tài khoản luyện tập (web `/`)
Tua lại biểu đồ như TradingView Bar Replay và giao dịch giả lập trên dữ liệu quá khứ. **Lệnh luyện tập KHÔNG BAO GIỜ gửi lên Alpaca**
(khớp ở server bằng nến 1m, chỉ dùng dữ liệu trước con trỏ).
- Bấm **Replay** (hoặc `R`), rồi bấm vào một nến hoặc gõ ngày/giờ (theo múi giờ đang chọn, `YYYY-MM-DD` hoặc `YYYY-MM-DD HH:MM`) và bấm Bắt đầu.
- Thanh điều khiển: ⏮ chọn lại điểm bắt đầu (= phiên mới), ▶/⏸, ▶| tiến 1 nến, tốc độ 0.5x/1x/3x/10x (nến/giây), ⏭ thoát.
  Đổi khung 1m…1D vẫn cắt đúng con trỏ, nến khung lớn đang hình thành chỉ gộp từ nến 1m trước con trỏ. Realtime tạm dừng khi replay.
- Phím tắt: `R` chọn điểm bắt đầu · `→` tiến 1 nến · `Shift+→` cuộn biểu đồ · `Space` chạy/dừng · `Esc` huỷ chọn ·
  `B` mua market · `S` bán (đóng vị thế hoặc bán số ở ô Qty). Phím không chạy khi đang gõ trong ô nhập.
- Panel phải đổi thành **Tài khoản luyện tập** (100.000$, phí 10 bps cổ phiếu / 25 bps crypto, không bán khống): form market/limit/stop kèm SL/TP,
  kéo đường SL/TP/limit trên biểu đồ để sửa giá, mũi tên tại điểm khớp. Tab **Thống kê**: chỉ số kiểu MT5, equity, bảng lệnh, phiên cũ.
- Thoát / chọn điểm mới / đổi mã: phiên có lệnh khớp được lưu DB với run_id `practice-<id>` (xem lại ở tab Thống kê hoặc ô "Bot").
- Luyện tập trên dữ liệu ≥ 2025 sẽ "nhìn thấy" giai đoạn holdout: không ảnh hưởng bot, nhưng đừng dùng kết quả luyện tập để chỉnh bot.
  Chi tiết: [docs/cai-tien/REPLAY.md](docs/cai-tien/REPLAY.md).

## Giao diện (UI Streamlit)
```bash
uv run streamlit run src/paper_bot/ui/app.py
```
Giao diện tiếng Việt, nhiều trang (mỗi trang có dòng "Trang này cho biết gì"):
- **Trang chủ**: sơ đồ luồng P1→P2→P3→DB→P4.
- **1. Pipeline**: chọn mã + ngày, xem P1 (nến, MA, regime, fan chart forecast, features, tin), P2 (mọi tín hiệu, veto, Signal cuối), P3 (lệnh dự kiến, MÔ PHỎNG), hoặc quét N phiên. Bật/tắt 6 thành phần như `--disable`.
- **2. Đánh giá**: equity/drawdown các run `bt-*`, bảng ablation, calibration, forecast (đọc `docs/ket-qua/`). Chỉ là giai đoạn phát triển, holdout chưa chạy.
- **3. Giám sát**: tài khoản Alpaca paper (chỉ đọc), signals/orders/fills/events trong DB, trạng thái kill switch.
- **4. Điều khiển**: chạy thử `once --dry-run`; chạy THẬT `once` chỉ khi tick đồng ý **và** gõ đúng `XAC NHAN` (ghi event `ui_real_run` vào DB); gỡ kill switch cũng cần xác nhận. Có ô chọn Stock/Crypto (crypto: 5 mã whitelist, chỉ engine full). Không có nút loop (loop chạy ở terminal, UI chỉ hiện câu lệnh để chép).

An toàn: lệnh được ghép từ lựa chọn có whitelist (mã chữ HOA A-Z, cờ `--disable` theo danh sách), chạy bằng `subprocess` dạng list (không shell), timeout 10 phút; hàm chạy lệnh tự chặn lệnh thật nếu chưa xác nhận. Test (`tests/test_ui_*.py`) chạy offline, subprocess và TradingClient đều bị giả.

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
  ui/             giao diện Streamlit: app.py, pages/, core.py (hàm thuần, test được), charts.py
docs/quy-trinh.md Quy trình từ input đến output
```
