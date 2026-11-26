"""WebSocket realtime: /ws/live?symbol=&tf= (+ adapter bọc StockDataStream thật)."""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...config import CRYPTO_UNIVERSE
from ...data.intraday import TIMEFRAMES
from ..hub import Client
from .security import origin_ok
from .timeutil import SYMBOL_RE

router = APIRouter()


class AlpacaStreamAdapter:
    """Bọc StockDataStream (feed IEX, gói miễn phí). Subscribe qua thread riêng vì alpaca-py
    gửi lệnh subscribe bằng run_coroutine_threadsafe(...).result() (gọi trên event loop sẽ treo)."""

    def __init__(self, api_key: str, api_secret: str, asset: str = "stock"):
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
            await asyncio.to_thread(self._stream.subscribe_bars, handler, *symbols)
            await asyncio.to_thread(self._stream.subscribe_updated_bars, handler, *symbols)

    async def unsubscribe(self, symbols) -> None:
        if symbols:
            await asyncio.to_thread(self._stream.unsubscribe_bars, *symbols)
            await asyncio.to_thread(self._stream.unsubscribe_updated_bars, *symbols)

    async def run(self) -> None:
        await self._stream._run_forever()  # chạy tới khi stop() hoặc lỗi

    async def stop(self) -> None:
        self._stream.stop()  # không chặn, gọi từ event loop được


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
