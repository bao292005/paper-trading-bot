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


def ny_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=NY).tz_localize(None)


def market_is_open(now: pd.Timestamp) -> bool:
    """Phiên chính 09:30-16:00 thứ 2-6 (chưa tính ngày lễ)."""
    return now.day_of_week < 5 and time(9, 30) <= now.time() < time(16, 0)


class LiveHub:
    def __init__(self, adapter_factory, seed_fn=None, clock=ny_now, sleep=asyncio.sleep,
                 heartbeat=30.0, backoff_start=1.0, backoff_max=60.0):
        self.adapter_factory, self.seed_fn, self.clock, self.sleep = adapter_factory, seed_fn, clock, sleep
        self.heartbeat, self.backoff_start, self.backoff_max = heartbeat, backoff_start, backoff_max
        self.clients = set()
        self.refs: Counter = Counter()  # mã -> số client đang xem
        self.minutes: Dict[str, Dict[pd.Timestamp, dict]] = {}  # mã -> {phút NY: ohlcv}
        self.adapter = None
        self.stream_state, self.last_error = "idle", ""
        self._tasks = []

    # ---------- client ----------
    async def add(self, client: Client) -> None:
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
            await self.shutdown()

    async def shutdown(self) -> None:
        """Đóng kết nối Alpaca (nhả slot kết nối duy nhất) và dừng các task nền."""
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
        ts = (ts.tz_localize("UTC") if ts.tzinfo is None else ts).tz_convert(NY).tz_localize(None)
        if bar.symbol not in self.refs or not market_is_open(ts):
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
        key = snap_to_bar(max(store), tf)
        mins = [store[t] for t in sorted(store) if snap_to_bar(t, tf) == key]
        return {"time": key, "open": mins[0]["open"], "high": max(m["high"] for m in mins),
                "low": min(m["low"] for m in mins), "close": mins[-1]["close"],
                "volume": sum(m["volume"] for m in mins)}

    async def _push_candle(self, client: Client) -> None:
        c = self.candle(client.symbol, client.tf)
        if c:
            await self._send(client, {"type": "bar", "symbol": client.symbol, "tf": client.tf, **c})

    async def _send(self, client: Client, msg: dict) -> None:
        try:
            await client.ws.send_json(msg)
        except Exception:  # trình duyệt đã đóng: bỏ client (endpoint cũng sẽ gọi remove)
            self.clients.discard(client)

    # ---------- trạng thái ----------
    def status(self) -> dict:
        market = "open" if market_is_open(self.clock()) else "closed"
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
