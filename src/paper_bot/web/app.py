"""FastAPI app: ghép các router. create_app() nhận các phụ thuộc (để test đưa đồ giả vào)."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import dashboard
from ..data.intraday import load_intraday
from .api import live, markers, market, orders, trading
from .hub import LiveHub, ny_now

WEB_DIR = Path(__file__).resolve().parent


def create_app(load_intraday_fn=None, trading_client_fn=None, db_path=None,
               adapter_factory=None, seed_fn=None, bars_dir=None) -> FastAPI:
    """Mọi tham số None = dùng bản thật (Alpaca thật, data/trading.db, data/bars)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.live_hub.shutdown()  # nhả kết nối stream khi tắt server

    app = FastAPI(title="Paper trading web", lifespan=lifespan)
    state = app.state
    state.load_intraday = load_intraday_fn or load_intraday
    state.trading_client_fn = trading_client_fn or trading.make_trading_client
    state.trading_client = None  # tạo lười ở lần gọi đầu
    state.db_path = db_path or dashboard.DB_PATH
    state.bars_dir = bars_dir

    def default_seed(symbol):  # nến phút hôm nay, để nến khung lớn đang chạy có đủ phần đã qua
        today = ny_now().normalize()
        return state.load_intraday(symbol, "1m", today, today)

    state.live_hub = LiveHub(adapter_factory or live.make_stream_adapter, seed_fn or default_seed)

    for module in (market, trading, orders, markers, live):
        app.include_router(module.router)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "templates" / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    return app
