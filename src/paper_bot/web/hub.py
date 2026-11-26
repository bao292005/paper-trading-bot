"""LiveHub: giữ ĐÚNG 1 kết nối stream Alpaca (gói miễn phí chỉ cho 1) cho mọi trình duyệt đang xem.

Luồng: stream gửi nến PHÚT (bars + updatedBars) -> hub lưu theo mã -> gộp lên khung tf của từng
client -> đẩy "nến đang chạy" xuống websocket. Kết nối lỗi thì tự nối lại, chờ lâu dần (backoff).

`adapter_factory()` trả đối tượng có: async subscribe(handler, symbols), async unsubscribe(symbols),
async run(), async stop(), thuộc tính connected. Bản thật bọc StockDataStream (api/live.py), test dùng bản giả.
"""

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import time
from typing import Dict

import pandas as pd

from .api.timeutil import NY, snap_to_bar

logger = logging.getLogger("paper_bot")


@dataclass(eq=False)
class Client:
    ws: object  # có send_json()
    symbol: str
    tf: str
    asset: str = "stock"  # "stock" | "crypto"


def ny_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=NY).tz_localize(None)


def utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def market_is_open(now: pd.Timestamp) -> bool:
    """Phiên chính 09:30-16:00 thứ 2-6 (chưa tính ngày lễ)."""
    return now.day_of_week < 5 and time(9, 30) <= now.time() < time(16, 0)


class LiveHub:
    """Hub của MỘT loại tài sản. Hub stock giữ thêm `crypto_hub` (cùng lớp, asset="crypto") và chuyển client
    crypto sang đó -> tối đa 1 stream cổ phiếu + 1 stream crypto."""

    def __init__(self, adapter_factory, seed_fn=None, clock=None, sleep=asyncio.sleep,
                 heartbeat=30.0, backoff_start=1.0, backoff_max=60.0, asset="stock",
                 crypto_adapter_factory=None, crypto_seed_fn=None):
        self.asset = asset
        self.adapter_factory, self.seed_fn, self.sleep = adapter_factory, seed_fn, sleep
        self.clock = clock or (utc_now if asset == "crypto" else ny_now)
        self.heartbeat, self.backoff_start, self.backoff_max = heartbeat, backoff_start, backoff_max
        self.clients = set()
        self.refs: Counter = Counter()  # mã -> số client đang xem
        self.minutes: Dict[str, Dict[pd.Timestamp, dict]] = {}  # mã -> {phút NY (crypto: UTC): ohlcv}
        self.adapter = None
        self.stream_state, self.last_error = "idle", ""
        self._tasks = []
        self.crypto_hub = None
        if asset == "stock" and crypto_adapter_factory is not None:
            self.crypto_hub = LiveHub(crypto_adapter_factory, crypto_seed_fn, sleep=sleep, heartbeat=heartbeat,
                                      backoff_start=backoff_start, backoff_max=backoff_max, asset="crypto")

    def _hub_for(self, client: Client):
        return self.crypto_hub if client.asset == "crypto" and self.crypto_hub is not None else self

    # ---------- client ----------
    async def add(self, client: Client) -> None:
        hub = self._hub_for(client)
        if hub is not self:
            return await hub.add(client)
        if client.asset == "crypto" and self.asset != "crypto":
            return await self._send(client, {"type": "status", "market": "closed", "stream": "idle",
                                             "message": "Chưa bật stream crypto"})
        first = self.refs[client.symbol] == 0
        self.refs[client.symbol] += 1
        self.clients.add(client)
        await self._send(client, self.status())
        if first:
            await self._seed(client.symbol)
            if self.adapter is not None:
                await self.adapter.subscribe(self._on_bar, [client.symbol])
        await self._push_candle(client)
        if not self._tasks:
            self._tasks = [asyncio.create_task(self._supervise()), asyncio.create_task(self._beat())]

    async def remove(self, client: Client) -> None:
        hub = self._hub_for(client)
        if hub is not self:
            return await hub.remove(client)
        if client not in self.clients:
            return
        self.clients.discard(client)
        self.refs[client.symbol] -= 1
        if self.refs[client.symbol] <= 0:
            del self.refs[client.symbol]
            self.minutes.pop(client.symbol, None)
            if self.adapter is not None:
                try:
                    await self.adapter.unsubscribe([client.symbol])
                except Exception as e:
                    logger.warning(f"unsubscribe {client.symbol} lỗi: {e}")
        if not self.clients:
            await self._shutdown_own()  # hết người xem: nhả kết nối (hub kia không bị ảnh hưởng)

    async def shutdown(self) -> None:
        """Đóng mọi kết nối Alpaca (stock + crypto) và dừng các task nền."""
        if self.crypto_hub is not None:
            await self.crypto_hub.shutdown()
        await self._shutdown_own()

    async def _shutdown_own(self) -> None:
        """Đóng kết nối của riêng hub này (nhả slot kết nối duy nhất)."""
        tasks, self._tasks = self._tasks, []
        if self.adapter is not None:
            try:
                await self.adapter.stop()
            except Exception as e:
                logger.warning(f"stop stream lỗi: {e}")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.adapter, self.stream_state = None, "idle"

    # ---------- dữ liệu ----------
    async def _seed(self, symbol: str) -> None:
        """Nạp nến phút hôm nay từ REST để nến đang chạy ở khung lớn có đủ phần đã qua."""
        if self.seed_fn is None:
            return
        try:
            df = await asyncio.to_thread(self.seed_fn, symbol)
        except Exception as e:
            logger.warning(f"seed {symbol} lỗi: {e}")
            return
        store = self.minutes.setdefault(symbol, {})
        for ts, r in df.iterrows():
            store.setdefault(ts, {c: float(r[c]) for c in ("open", "high", "low", "close", "volume")})

    async def _on_bar(self, bar) -> None:
        """Callback của stream cho nến phút mới / nến phút được sửa (stream thắng dữ liệu seed)."""
        ts = pd.Timestamp(bar.timestamp)
        tz = "UTC" if self.asset == "crypto" else NY
        ts = (ts.tz_localize("UTC") if ts.tzinfo is None else ts).tz_convert(tz).tz_localize(None)
        if bar.symbol not in self.refs or not (self.asset == "crypto" or market_is_open(ts)):
            return
        store = self.minutes.setdefault(bar.symbol, {})
        store[ts] = {"open": float(bar.open), "high": float(bar.high), "low": float(bar.low),
                     "close": float(bar.close), "volume": float(bar.volume)}
        for old in [k for k in store if k.normalize() < ts.normalize()]:
            del store[old]  # chỉ giữ hôm nay
        for c in [c for c in self.clients if c.symbol == bar.symbol]:
            await self._push_candle(c)

    def candle(self, symbol: str, tf: str):
        """Nến khung tf đang chạy (gộp từ các nến phút của bucket chứa nến phút mới nhất)."""
        store = self.minutes.get(symbol)
        if not store:
            return None
        key = snap_to_bar(max(store), tf, self.asset)
        mins = [store[t] for t in sorted(store) if snap_to_bar(t, tf, self.asset) == key]
        return {"time": key, "open": mins[0]["open"], "high": max(m["high"] for m in mins),
                "low": min(m["low"] for m in mins), "close": mins[-1]["close"],
                "volume": sum(m["volume"] for m in mins)}

    async def _push_candle(self, client: Client) -> None:
        c = self.candle(client.symbol, client.tf)
        if c:
            extra = {"asset": "crypto"} if self.asset == "crypto" else {}  # tin cổ phiếu giữ nguyên như cũ
            await self._send(client, {"type": "bar", "symbol": client.symbol, "tf": client.tf, **extra, **c})

    async def _send(self, client: Client, msg: dict) -> None:
        try:
            await client.ws.send_json(msg)
        except Exception:  # trình duyệt đã đóng: bỏ client (endpoint cũng sẽ gọi remove)
            self.clients.discard(client)

    # ---------- trạng thái ----------
    def status(self) -> dict:
        market = "open" if self.asset == "crypto" or market_is_open(self.clock()) else "closed"  # crypto 24/7
        text = {"closed": "Thị trường đóng cửa (market closed)"}.get(market, "")
        if self.stream_state in ("reconnecting", "error"):
            text = f"Stream lỗi, đang nối lại: {self.last_error}"
        return {"type": "status", "market": market, "stream": self.stream_state, "message": text}

    async def broadcast_status(self) -> None:
        msg = self.status()
        for c in list(self.clients):
            await self._send(c, msg)

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat)
            await self.broadcast_status()

    # ---------- kết nối Alpaca ----------
    async def _supervise(self) -> None:
        delay = self.backoff_start
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            self.stream_state = "connecting"
            try:
                self.adapter = self.adapter_factory()
                await self.adapter.subscribe(self._on_bar, sorted(self.refs))
                self.stream_state = "connected"
                await self.broadcast_status()
                await self.adapter.run()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = str(e) or type(e).__name__
                logger.warning(f"Stream Alpaca lỗi: {self.last_error}")
            self.stream_state = "reconnecting"
            await self.broadcast_status()
            if loop.time() - started > 60:
                delay = self.backoff_start  # đã chạy ổn định thì reset backoff
            await self.sleep(delay)
            delay = min(delay * 2, self.backoff_max)
