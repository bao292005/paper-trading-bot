"""Paper trading bot dựng lại từ bài "Build a Python Paper Trading Bot with Alpaca API"."""

import argparse

# Tên ngắn của các thành phần có thể tắt (khớp config.SHORT_FLAG_NAMES).
# Ghi lại ở đây để `--help` không phải import config (config cần dotenv...).
_SHORT_FLAGS = ["regime", "calibration", "forecast", "atr_sizing", "bracket", "corr_limit"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-bot", description="Paper trading bot (Alpaca)")
    parser.add_argument("mode", choices=["once", "loop", "report"],
                        help="once: chạy 1 vòng | loop: chạy liên tục | report: xem tài khoản")
    parser.add_argument("--interval", type=int, default=30, help="số phút giữa các vòng (loop)")
    parser.add_argument("--symbols", nargs="+", help="ví dụ: --symbols AAPL MSFT")
    parser.add_argument("--engine", choices=["full", "legacy"], default="full",
                        help="full: pipeline mới (mặc định) | legacy: bot gốc của bài")
    parser.add_argument("--disable", nargs="+", default=[], choices=_SHORT_FLAGS, metavar="THÀNH_PHẦN",
                        help=f"tắt thành phần của engine full: {' '.join(_SHORT_FLAGS)}")
    parser.add_argument("--dry-run", action="store_true",
                        help="làm mọi bước, ghi DB nhưng KHÔNG gửi lệnh (once/loop)")
    parser.add_argument("--llm", action="store_true",
                        help="bật LLMAgentStrategy (cần LLM_API_KEY; thiếu thì chạy không LLM)")
    parser.add_argument("--reset-kill-switch", action="store_true",
                        help="gỡ trạng thái ngắt (live-paper, hoặc live-dry nếu kèm --dry-run) trước khi chạy")
    return parser


def apply_args(config, args) -> None:
    """Chép tuỳ chọn CLI vào TradingConfig."""
    if args.symbols:
        config.symbols = [s.upper() for s in args.symbols]
    config.engine = args.engine
    config.disable(args.disable)
    config.dry_run = args.dry_run
    config.use_llm = args.llm


def main() -> None:
    args = build_parser().parse_args()

    # import muộn để `--help` chạy được kể cả khi chưa có key
    from .bot import TradingBot
    from .config import TradingConfig

    config = TradingConfig.from_env()
    apply_args(config, args)

    if args.reset_kill_switch:  # chạy TRƯỚC khi tạo LiveBot (LiveBot đọc trạng thái lúc khởi tạo)
        from .live import RUN_ID_DRY, RUN_ID_PAPER, reset_kill_switch

        print(reset_kill_switch(RUN_ID_DRY if config.dry_run else RUN_ID_PAPER))

    if args.mode != "report" and config.engine == "full":
        from .bot import setup_logging
        from .live import LiveBot

        setup_logging()
        bot = LiveBot(config)
    else:
        bot = TradingBot(config)  # legacy, hoặc report (chỉ đọc tài khoản)

    if args.mode == "once":
        bot.run_trading_cycle()
    elif args.mode == "loop":
        bot.run_continuous(interval_minutes=args.interval)
    else:
        print_report(bot.generate_performance_report(), ",.2f")  # như bài gốc
        from .live import live_report

        stats = live_report()  # thêm: hiệu quả của engine full (run_id "live-paper") từ DB
        print("\n--- LiveBot (live-paper, từ DB) ---")
        if stats:
            print_report(stats, ",.4f")
        else:
            print("chưa có dữ liệu")


def print_report(report: dict, fmt: str) -> None:
    for k, v in report.items():
        print(f"{k:>14}: {v:{fmt}}" if isinstance(v, float) else f"{k:>14}: {v}")
