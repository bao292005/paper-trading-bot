"""FastAPI app: ghép các router. create_app() nhận các phụ thuộc (để test đưa đồ giả vào)."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import dashboard
from ..data.intraday import load_intraday
from .api import live, markers, market, orders, practice, replay, sltp, tester, trading
from .hub import LiveHub, ny_now, utc_now
from .jobs import JobManager
from .sltp_monitor import SLTPMonitor

WEB_DIR = Path(__file__).resolve().parent
GRACE_SECONDS = 300.0  # hết người xem thì giữ stream Alpaca thêm 5 phút (tải lại trang không mở kết nối mới)
# Tin trạng thái định kỳ < 20s: crypto trên Alpaca có khi cả phút không có trade, client vẫn biết WS còn sống
HEARTBEAT_SECONDS = 10.0
SLTP_INTERVAL = float(os.environ.get("PAPER_SLTP_INTERVAL", "3"))  # giây giữa 2 lần server kiểm SL/TP crypto


def create_app(load_intraday_fn=None, trading_client_fn=None, db_path=None,
               adapter_factory=None, seed_fn=None, bars_dir=None,
               crypto_adapter_factory=None, crypto_seed_fn=None, live_state_path=None,
               sltp_price_fn=None, start_sltp_monitor=None) -> FastAPI:
    """Mọi tham số None = dùng bản thật (Alpaca thật, data/trading.db, data/bars).
    start_sltp_monitor None = chỉ tự chạy SLTPMonitor khi dùng client Alpaca thật (test đưa client giả thì không)."""
    if start_sltp_monitor is None:
        start_sltp_monitor = trading_client_fn is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_sltp_monitor:
            app.state.sltp_monitor.start()  # canh SL/TP crypto mỗi 3s, sống cùng server
        yield
        app.state.sltp_monitor.stop()
        await app.state.live_hub.shutdown()  # nhả kết nối stream khi tắt server

    app = FastAPI(title="Paper trading web", lifespan=lifespan)
    state = app.state
    state.load_intraday = load_intraday_fn or load_intraday
    state.trading_client_fn = trading_client_fn or trading.make_trading_client
    state.trading_client = None  # tạo lười ở lần gọi đầu
    state.db_path = db_path or dashboard.DB_PATH
    state.bars_dir = bars_dir
    state.live_state_path = live_state_path  # None = data/live_state.json (SL/TP crypto bot tự quản)
    state.jobs = JobManager(max_running=2, ttl=3600)  # job tester chạy nền (tối đa 2, dọn sau 1 giờ)

    def default_seed(symbol):  # nến phút hôm nay, để nến khung lớn đang chạy có đủ phần đã qua
        today = ny_now().normalize()
        return state.load_intraday(symbol, "1m", today, today)

    def default_crypto_seed(symbol):  # crypto: "hôm nay" theo UTC
        today = utc_now().normalize()
        return state.load_intraday(symbol, "1m", today, today, asset="crypto")

    # test đưa adapter giả vào thì dùng luôn bản giả đó cho crypto (không mở kết nối thật)
    crypto_factory = crypto_adapter_factory or (adapter_factory or live.make_crypto_stream_adapter)
    # 1 server = 1 hub = tối đa 1 stream cổ phiếu + 1 stream crypto, sống suốt vòng đời server
    state.live_hub = LiveHub(adapter_factory or live.make_stream_adapter, seed_fn or default_seed,
                             crypto_adapter_factory=crypto_factory, crypto_seed_fn=crypto_seed_fn or default_crypto_seed,
                             grace=GRACE_SECONDS, heartbeat=HEARTBEAT_SECONDS)

    # 1 server = 1 monitor SL/TP crypto (client Alpaca riêng, nguồn SL/TP = live_state.json dùng chung với LiveBot)
    state.sltp_monitor = SLTPMonitor(state.trading_client_fn, price_fn=sltp_price_fn, state_path=live_state_path,
                                     db_path=state.db_path, interval=SLTP_INTERVAL)

    for module in (market, trading, orders, markers, live, tester, replay, practice, sltp):
        app.include_router(module.router)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "templates" / "index.html")

    @app.get("/tester", include_in_schema=False)
    def tester_page():
        return FileResponse(WEB_DIR / "templates" / "tester.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    return app
