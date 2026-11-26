"""Test offline realtime: LiveHub + WS /ws/live với stream giả (không kết nối Alpaca)."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from paper_bot.web.api.timeutil import ny_to_unix
from paper_bot.web.app import create_app
from paper_bot.web.hub import Client, LiveHub

OPEN_CLOCK = lambda: pd.Timestamp("2024-03-04 10:00")  # thứ 2, đang trong phiên
SAT_CLOCK = lambda: pd.Timestamp("2024-03-09 11:00")  # thứ 7


def bar(symbol, hhmm_utc, o, h, l, c, v):
    hh, mm = map(int, hhmm_utc.split(":"))
    return SimpleNamespace(symbol=symbol, timestamp=datetime(2024, 3, 4, hh, mm, tzinfo=timezone.utc),
                           open=o, high=h, low=l, close=c, volume=v)


class FakeWS:
    def __init__(self):
        self.msgs = []

    async def send_json(self, msg):
        self.msgs.append(msg)

    def bars(self):
        return [m for m in self.msgs if m["type"] == "bar"]


class FakeAdapter:
    """Giả stream: ghi lại subscribe/stop; run() chờ tới khi stop."""
    instances = []

    def __init__(self):
        self.subscribed, self.stopped, self.handler, self.connected = [], False, None, True
        self._stop = None
        FakeAdapter.instances.append(self)

    async def subscribe(self, handler, symbols):
        self.handler = handler
        self.subscribed.append(list(symbols))

    async def unsubscribe(self, symbols):
        self.subscribed.append(["-"] + list(symbols))

    async def run(self):
        self._stop = asyncio.Event()
        await self._stop.wait()

    async def stop(self):
        self.stopped = True
        if self._stop:
            self._stop.set()


@pytest.fixture(autouse=True)
def reset_instances():
    FakeAdapter.instances.clear()


def seed_30_minutes(symbol):
    idx = pd.date_range("2024-03-04 09:30", periods=30, freq="1min")
    i = np.arange(30, dtype=float)
    return pd.DataFrame({"open": i, "high": i + 0.5, "low": i - 0.5, "close": i + 0.1, "volume": 10.0}, index=idx)


def make_hub(**kw):
    kw.setdefault("clock", OPEN_CLOCK)
    return LiveHub(FakeAdapter, **kw)


async def wait_until(cond, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("hết thời gian chờ")


def test_gop_nen_phut_len_khung_5m_va_nhan_nen_sua():
    async def scenario():
        hub, ws = make_hub(seed_fn=seed_30_minutes), FakeWS()
        await hub.add(Client(ws, "AAPL", "5m"))
        seed = ws.bars()[0]  # nến 09:55 gộp từ 5 phút seed 25..29
        assert ws.msgs[0]["type"] == "status" and ws.msgs[0]["market"] == "open"
        assert seed == {"type": "bar", "symbol": "AAPL", "tf": "5m", "time": ny_to_unix("2024-03-04 09:55"),
                        "open": 25, "high": 29.5, "low": 24.5, "close": 29.1, "volume": 50}
        await hub._on_bar(bar("AAPL", "15:00", 100, 101, 99, 100.5, 10))  # 10:00 NY
        await hub._on_bar(bar("AAPL", "15:01", 100.5, 105, 100, 104, 20))
        await hub._on_bar(bar("AAPL", "15:00", 100, 101, 98, 100.5, 12))  # updatedBars: sửa nến 10:00
        last = ws.bars()[-1]
        assert last["time"] == ny_to_unix("2024-03-04 10:00")  # nến 5m mới, tách khỏi 09:55
        assert (last["open"], last["high"], last["low"], last["close"], last["volume"]) == (100, 105, 98, 104, 32)
        await hub._on_bar(bar("MSFT", "15:02", 1, 1, 1, 1, 1))  # mã không ai xem: bỏ
        assert "MSFT" not in hub.minutes
        await hub.shutdown()

    asyncio.run(scenario())


def test_chi_mot_ket_noi_cho_nhieu_client_va_nha_khi_het_nguoi_xem():
    async def scenario():
        hub = make_hub()
        a, b, c = Client(FakeWS(), "AAPL", "1m"), Client(FakeWS(), "AAPL", "1h"), Client(FakeWS(), "NVDA", "1m")
        await hub.add(a)
        await wait_until(lambda: FakeAdapter.instances and FakeAdapter.instances[0].subscribed)
        await hub.add(b)  # cùng mã: không subscribe thêm
        await hub.add(c)
        assert len(FakeAdapter.instances) == 1  # đúng 1 stream
        ad = FakeAdapter.instances[0]
        assert ad.subscribed == [["AAPL"], ["NVDA"]]
        await hub.remove(a)
        assert ad.subscribed[-1] != ["-", "AAPL"]  # b vẫn xem AAPL
        await hub.remove(c)
        assert ad.subscribed[-1] == ["-", "NVDA"] and not ad.stopped
        await hub.remove(b)
        assert ad.stopped and hub.adapter is None and not hub.refs and hub.stream_state == "idle"

    asyncio.run(scenario())


def test_thi_truong_dong_cua_gui_trang_thai():
    async def scenario():
        hub, ws = make_hub(clock=SAT_CLOCK), FakeWS()
        await hub.add(Client(ws, "AAPL", "1m"))
        assert ws.msgs[0]["market"] == "closed" and "market closed" in ws.msgs[0]["message"]
        await hub.broadcast_status()
        assert ws.msgs[-1]["market"] == "closed"
        await hub.shutdown()

    asyncio.run(scenario())


def test_stream_loi_thi_noi_lai_voi_backoff_tang_dan():
    class Failing(FakeAdapter):
        async def run(self):
            raise ConnectionError("boom")

    delays = []

    async def fast_sleep(d):
        delays.append(d)
        await asyncio.sleep(0)

    async def scenario():
        hub = LiveHub(Failing, clock=OPEN_CLOCK, sleep=fast_sleep, backoff_max=10)
        ws = FakeWS()
        await hub.add(Client(ws, "AAPL", "1m"))
        await wait_until(lambda: len(delays) >= 6)
        await hub.shutdown()
        assert delays[:6] == [1, 2, 4, 8, 10, 10]  # gấp đôi, chặn ở backoff_max
        assert len(FakeAdapter.instances) >= 6  # mỗi lần nối lại tạo stream mới
        err = [m for m in ws.msgs if m["type"] == "status" and m["stream"] == "reconnecting"]
        assert err and "boom" in err[-1]["message"]
        assert all(i.subscribed[0] == ["AAPL"] for i in FakeAdapter.instances if i.subscribed)  # subscribe lại sau khi nối

    asyncio.run(scenario())


class PushAdapter(FakeAdapter):
    """Stream giả đẩy 1 nến ngay khi chạy (thay cho Alpaca)."""

    async def run(self):
        await self.handler(bar("AAPL", "15:00", 10, 12, 9, 11, 7))
        await super().run()


# TestClient không dùng base_url cho Host của websocket nên đặt tay
LOCAL_HEADERS = {"host": "127.0.0.1:8600", "origin": "http://127.0.0.1:8600"}


def local_client(**kw):
    app = create_app(adapter_factory=PushAdapter, seed_fn=lambda s: seed_30_minutes(s).iloc[0:0], **kw)
    return TestClient(app)


def test_ws_nhan_trang_thai_roi_nen_tu_stream_gia(tmp_path):
    with local_client(db_path=tmp_path / "t.db") as c:
        with c.websocket_connect("/ws/live?symbol=aapl&tf=5m", headers=LOCAL_HEADERS) as ws:
            first = ws.receive_json()
            assert first["type"] == "status" and first["market"] in ("open", "closed")
            msg = ws.receive_json()
            while msg["type"] != "bar":
                msg = ws.receive_json()
            assert msg["symbol"] == "AAPL" and msg["tf"] == "5m" and msg["time"] == ny_to_unix("2024-03-04 10:00")
            assert (msg["open"], msg["high"], msg["low"], msg["close"], msg["volume"]) == (10, 12, 9, 11, 7)
        assert len(FakeAdapter.instances) == 1


def test_ws_tu_choi_input_la_va_origin_la(tmp_path):
    with local_client(db_path=tmp_path / "t.db") as c:
        for url, headers in (("/ws/live?symbol=AA%20PL&tf=1m", LOCAL_HEADERS),
                             ("/ws/live?symbol=AAPL&tf=2m", LOCAL_HEADERS),
                             ("/ws/live?symbol=AAPL&tf=1m", {**LOCAL_HEADERS, "origin": "http://evil.example.com"}),
                             ("/ws/live?symbol=AAPL&tf=1m", {**LOCAL_HEADERS, "host": "evil.example.com"})):
            with pytest.raises(WebSocketDisconnect):  # server đóng ngay khi bắt tay (policy violation)
                with c.websocket_connect(url, headers=headers) as ws:
                    ws.receive_json()
        assert FakeAdapter.instances == []  # không ai được xem thì không mở stream
