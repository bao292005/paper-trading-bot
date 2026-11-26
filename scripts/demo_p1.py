"""Demo P1: chạy riêng phần Dữ liệu & dự báo, in ra MarketState của vài mã.

Chạy:  uv run python scripts/demo_p1.py [MÃ ...] [--asof YYYY-MM-DD]
Chỉ ĐỌC dữ liệu (cache + Alpaca News), không đặt lệnh.
"""

import argparse
from datetime import timedelta

import pandas as pd

from paper_bot.data.cache import load_bars
from paper_bot.data.forecast import ChronosForecaster
from paper_bot.data.market_state import build_market_state


def get_news(symbols, asof):
    """Tin 7 ngày trước asof từ Alpaca News (cần key trong .env). Lỗi thì trả rỗng."""
    try:
        from alpaca.data.historical import NewsClient

        from paper_bot.config import TradingConfig
        from paper_bot.data.sources import NewsSource

        cfg = TradingConfig.from_env()
        src = NewsSource(NewsClient(cfg.api_key, cfg.api_secret))
        end = pd.Timestamp(asof) + timedelta(hours=23)
        return src.get_news(symbols, end - timedelta(days=7), end.to_pydatetime())
    except Exception as e:  # demo: thiếu key / lỗi mạng thì bỏ qua tin
        print(f"(Không lấy được tin: {e})")
        return []


def main():
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="*", default=["AAPL", "NVDA", "JPM"])
    p.add_argument("--asof", help="ngày quyết định (mặc định: phiên cuối trong cache)")
    args = p.parse_args()

    # ---- INPUT ----
    spy = load_bars("SPY")
    bars = {s: load_bars(s) for s in args.symbols}
    asof = pd.Timestamp(args.asof) if args.asof else spy.index[-1]
    print(f"=== INPUT ===\nasof = {asof.date()} | mã = {args.symbols} | SPY {len(spy)} nến\n")

    # ---- XỬ LÝ ----
    cut = {s: b[b.index <= asof] for s, b in bars.items()}
    forecasts = ChronosForecaster().predict_many(cut, asof, horizon=5)
    news = get_news(args.symbols, asof)

    # ---- OUTPUT: MarketState từng mã ----
    print("=== OUTPUT: MarketState ===")
    for s in args.symbols:
        my_news = [n for n in news if s in n.symbols]
        st = build_market_state(s, asof, bars[s], spy, forecast=forecasts.get(s), news=my_news)
        last = st.bars.iloc[-1]
        print(f"\n--- {st.symbol} ---")
        print(f"asof      : {st.asof.date()}")
        print(f"bars      : {len(st.bars)} nến, từ {st.bars.index[0].date()} tới {st.bars.index[-1].date()}")
        print(f"nến cuối  : open {last.open:.2f}  high {last.high:.2f}  low {last.low:.2f}  "
              f"close {last.close:.2f}  vol {last.volume:,.0f}")
        print(f"regime    : {st.regime}   (tính từ SPY)")
        if st.forecast:
            q = st.forecast.quantiles
            print(f"forecast  : {st.forecast.model}, lợi suất {st.forecast.horizon_days} ngày tới: "
                  f"q10 {q[0.1]:+.2%} | q50 {q[0.5]:+.2%} | q90 {q[0.9]:+.2%}  (độ rộng {st.forecast.width:.2%})")
        print("features  : " + ", ".join(f"{k}={v:.4g}" for k, v in st.features.items()))
        print(f"news      : {len(st.news)} tin")
        for n in st.news[:3]:
            print(f"            [{n.created_at:%m-%d %H:%M}] {n.headline[:90]}")


if __name__ == "__main__":
    main()
