"""WebSocket realtime: /ws/live?symbol=&tf= (+ adapter bọc StockDataStream thật)."""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...config import CRYPTO_UNIVERSE
from ...data.intraday import TIMEFRAMES
from ..hub import Client
from .security import origin_ok
from .timeutil import SYMBOL_RE

router = APIRouter()
logger = logging.getLogger("paper_bot")


class AlpacaStreamAdapter:
    """Bọc StockDataStream (feed IEX, gói miễn phí) / CryptoDataStream: trades + bars + updatedBars.

    - Subscribe qua thread riêng vì alpaca-py gửi lệnh subscribe bằng run_coroutine_threadsafe(...).result()
      (gọi thẳng trên event loop sẽ treo).
    - run() chạy MỘT phiên kết nối và raise khi lỗi. KHÔNG dùng _run_forever(): hàm đó tự nuốt lỗi
      "connection limit exceeded" và tự thử lại mỗi 1-30s -> hub không biết để chờ lâu hơn / báo UI.
    - stop() chờ socket đóng hẳn (close handshake) để nhả slot kết nối duy nhất của gói miễn phí."""

    def __init__(self, api_key: str, api_secret: str, asset: str = "stock"):
        self._done = None  # Event: phiên run() hiện tại đã kết thúc hẳn
        if asset == "crypto":  # crypto 24/7, feed mặc định (US)
            from alpaca.data.live import CryptoDataStream

            self._stream = CryptoDataStream(api_key, api_secret)
            return
        from alpaca.data.enums import DataFeed
        from alpaca.data.live import StockDataStream

        self._stream = StockDataStream(api_key, api_secret, feed=DataFeed.IEX)

    @property
    def connected(self) -> bool:
        return bool(getattr(self._stream, "_running", False))

    async def subscribe(self, handler, symbols) -> None:
        if symbols:
            s = self._stream
            for fn in (s.subscribe_trades, s.subscribe_bars, s.subscribe_updated_bars):
                await asyncio.to_thread(fn, handler, *symbols)

    async def unsubscribe(self, symbols) -> None:
        if symbols:
            s = self._stream
            for fn in (s.unsubscribe_trades, s.unsubscribe_bars, s.unsubscribe_updated_bars):
                try:
                    await asyncio.to_thread(fn, *symbols)
                except KeyError:  # mã chưa subscribe kênh này
                    pass

    async def run(self) -> None:
        """Một phiên: kết nối + xác thực + subscribe rồi nhận dữ liệu tới khi stop() / mất kết nối (raise)."""
        s = self._stream
        run = s._reserve_run()  # trạng thái dừng của phiên (API nội bộ alpaca-py, xem websocket.py)
        run.loop = s._loop = asyncio.get_running_loop()
        self._done = asyncio.Event()
        try:
            await s._start_ws()  # lỗi bắt tay/limit -> raise ra hub
            await s._send_subscribe_msg()
            s._running = True
            await s._consume(run)  # trả về khi stop(); mất kết nối thì raise
        finally:
            await self._close_socket()  # luôn đóng socket (kể cả socket mở dở khi bắt tay lỗi)
            if s._run is run:
                s._run = None
            self._done.set()

    async def _close_socket(self) -> None:
        try:
            await asyncio.wait_for(self._stream.close(), 10)  # close() của alpaca-py chờ bắt tay đóng
        except Exception as e:
            logger.warning(f"đóng socket stream lỗi: {e}")
            self._stream._ws, self._stream._running = None, False

    async def stop(self) -> None:
        """Dừng và CHỜ socket đóng hẳn rồi mới trả về (để hub tạo stream mới không đụng limit)."""
        self._stream.stop()
        await self._close_socket()
        if self._done is not None and not self._done.is_set():
            try:
                await asyncio.wait_for(self._done.wait(), 10)
            except asyncio.TimeoutError:
                logger.warning("phiên stream chưa kết thúc sau 10s")


def _make_adapter(asset: str) -> AlpacaStreamAdapter:
    from ...config import TradingConfig
    try:
        cfg = TradingConfig.from_env()
    except SystemExit as e:
        raise RuntimeError(str(e)) from None
    return AlpacaStreamAdapter(cfg.api_key, cfg.api_secret, asset)


def make_stream_adapter() -> AlpacaStreamAdapter:
    """Tạo adapter thật. Thiếu key -> RuntimeError (hub báo trạng thái lỗi rồi thử lại sau)."""
    return _make_adapter("stock")


def make_crypto_stream_adapter() -> AlpacaStreamAdapter:
    return _make_adapter("crypto")


def _valid(symbol, tf, asset: str = "stock") -> bool:
    if not isinstance(symbol, str) or tf not in TIMEFRAMES:
        return False
    if asset == "crypto":
        return symbol in CRYPTO_UNIVERSE  # đúng 5 mã whitelist dạng "BTC/USD"
    return asset == "stock" and bool(SYMBOL_RE.fullmatch(symbol))


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket, symbol: str, tf: str = "1m", asset: str = "stock"):
    """Đẩy nến đang chạy của `symbol` ở khung `tf`. Trình duyệt có thể gửi {"symbol","tf","asset"} để đổi."""
    symbol = symbol.upper()
    if not origin_ok(ws.headers.get("origin"), ws.headers.get("host")) or not _valid(symbol, tf, asset):
        await ws.close(code=1008)  # policy violation
        return
    await ws.accept()
    hub = ws.app.state.live_hub
    client = Client(ws, symbol, tf, asset)
    try:
        await hub.add(client)
        while True:
            try:
                req = json.loads(await ws.receive_text())
                new_sym, new_tf = str(req["symbol"]).upper(), req["tf"]
                new_asset = req.get("asset", client.asset)
            except (ValueError, KeyError, TypeError, AttributeError):
                continue  # tin nhắn lạ: bỏ qua
            if _valid(new_sym, new_tf, new_asset) and (new_sym, new_tf, new_asset) != (client.symbol, client.tf, client.asset):
                await hub.remove(client)
                client.symbol, client.tf, client.asset = new_sym, new_tf, new_asset
                await hub.add(client)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove(client)
