"""Chạy: uv run python -m paper_bot.web  (mặc định 127.0.0.1:8600, CHỈ localhost)."""

import argparse

import uvicorn

from .app import create_app

LOCAL_ONLY = ("127.0.0.1", "localhost", "::1")


def main() -> None:
    parser = argparse.ArgumentParser(description="Web app biểu đồ + giao dịch paper")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8600)
    args = parser.parse_args()
    if args.host not in LOCAL_ONLY:  # app có nút đặt lệnh, không được mở ra mạng
        raise SystemExit(f"Chỉ cho phép bind localhost ({', '.join(LOCAL_ONLY)}), nhận: {args.host}")
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
