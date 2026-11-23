"""Paper trading bot dựng lại từ bài "Build a Python Paper Trading Bot with Alpaca API"."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="paper-bot", description="Paper trading bot (Alpaca)")
    parser.add_argument("mode", choices=["once", "loop", "report"],
                        help="once: chạy 1 vòng | loop: chạy liên tục | report: xem tài khoản")
    parser.add_argument("--interval", type=int, default=30, help="số phút giữa các vòng (loop)")
    parser.add_argument("--symbols", nargs="+", help="ví dụ: --symbols AAPL MSFT")
    args = parser.parse_args()

    # import muộn để `--help` chạy được kể cả khi chưa có key
    from .bot import TradingBot
    from .config import TradingConfig

    config = TradingConfig.from_env()
    if args.symbols:
        config.symbols = [s.upper() for s in args.symbols]
    bot = TradingBot(config)

    if args.mode == "once":
        bot.run_trading_cycle()
    elif args.mode == "loop":
        bot.run_continuous(interval_minutes=args.interval)
    else:
        for k, v in bot.generate_performance_report().items():
            print(f"{k:>14}: {v:,.2f}" if isinstance(v, float) else f"{k:>14}: {v}")
