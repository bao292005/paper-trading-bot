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
uv run paper-bot report                        # xem tài khoản
uv run paper-bot once                          # chạy 1 vòng
uv run paper-bot once --symbols AAPL MSFT      # chọn mã
uv run paper-bot loop --interval 30            # chạy liên tục, 30 phút/vòng (Ctrl+C để dừng)
uv run pytest                                  # test offline, không cần key
```

## Cấu trúc
```
src/paper_bot/
  __init__.py     CLI (once / loop / report)
  config.py       TradingConfig: key, tham số rủi ro, danh sách mã
  strategies.py   TradingSignal + các chiến lược
  managers.py     DataManager, PortfolioManager, OrderManager
  bot.py          TradingBot: điều phối một vòng giao dịch
docs/quy-trinh.md Quy trình từ input đến output
```
