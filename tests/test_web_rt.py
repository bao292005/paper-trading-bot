"""Test offline Realtime v2: grace period, dọn stream lỗi, backoff khi limit exceeded, trade -> nến, throttle."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from paper_bot.web.api.timeutil import ny_to_unix
from paper_bot.web.hub import Client, LiveHub
from test_web_live import OPEN_CLOCK, FakeAdapter, FakeWS, bar, wait_until
from test_web_live import reset_instances  # noqa: F401  (fixture autouse: xoá FakeAdapter.instances mỗi test)


def trade(symbol, hh, mm, ss, price, size):
    """Trade giả (giờ UTC ngày 2024-03-04; 15:00 UTC = 10:00 NY)."""
    return SimpleNamespace(symbol=symbol, timestamp=datetime(2024, 3, 4, hh, mm, ss, tzinfo=timezone.utc),
                           price=price, size=size)


def make_hub(**kw):
    kw.setdefault("clock", OPEN_CLOCK)
    return LiveHub(FakeAdapter, **kw)


def test_tai_lai_trang_trong_grace_khong_tao_stream_moi():
    async def scenario():
        hub = make_hub(grace=60)
        a = Client(FakeWS(), "AAPL", "1m")
        await hub.add(a)
        await wait_until(lambda: FakeAdapter.instances and FakeAdapter.instances[0].subscribed)
        await hub.remove(a)  # đóng tab / tải lại trang
        ad = FakeAdapter.instances[0]
        assert not ad.stopped and hub.adapter is ad  # chưa đóng: đang chờ grace
        await hub.add(Client(FakeWS(), "MSFT", "5m"))  # trang mới (đổi mã luôn)
        await asyncio.sleep(0.05)
        assert len(FakeAdapter.instances) == 1 and not ad.stopped  # dùng lại kết nối cũ
        assert ad.subscribed == [["AAPL"], ["-", "AAPL"], ["MSFT"]]
        await hub.shutdown()
        assert ad.stopped and hub.adapter is None

    asyncio.run(scenario())


def test_het_grace_thi_dong_stream():
    async def scenario():
        hub = make_hub(grace=0.05)
        a = Client(FakeWS(), "AAPL", "1m")
        await hub.add(a)
        await wait_until(lambda: FakeAdapter.instances and FakeAdapter.instances[0].subscribed)
        await hub.remove(a)
        await wait_until(lambda: FakeAdapter.instances[0].stopped)
        assert hub.adapter is None and hub.stream_state == "idle" and not hub._tasks
        await hub.add(Client(FakeWS(), "AAPL", "1m"))  # quay lại sau grace: mở kết nối mới
        await wait_until(lambda: len(FakeAdapter.instances) == 2)
        await hub.shutdown()

    asyncio.run(scenario())


def test_limit_exceeded_cho_lau_va_don_stream_cu_truoc_khi_tao_moi():
    created_after_stop = []

    class Limited(FakeAdapter):
        def __init__(self):
            # lúc tạo stream mới thì mọi stream cũ phải đã đóng hẳn
            created_after_stop.append(all(i.stopped for i in FakeAdapter.instances))
            super().__init__()

        async def run(self):
            raise ValueError("connection limit exceeded")

    delays = []

    async def fast_sleep(d):
        delays.append(d)
        await asyncio.sleep(0)

    async def scenario():
        ws = FakeWS()
        hub = LiveHub(Limited, clock=OPEN_CLOCK, sleep=fast_sleep)
        await hub.add(Client(ws, "AAPL", "1m"))
        await wait_until(lambda: len(delays) >= 6)
        await hub.shutdown()
        assert delays[:6] == [30, 60, 120, 240, 300, 300]  # ≥30s, gấp đôi, chặn 5 phút
        assert all(created_after_stop)
        assert all(i.stopped for i in FakeAdapter.instances)
        st = [m for m in ws.msgs if m["type"] == "status" and m["stream"] == "limit"]
        assert st and "Alpaca đang giữ kết nối cũ, thử lại sau" in st[0]["message"] and st[0]["retry_in"] > 0

    asyncio.run(scenario())


def test_trade_cap_nhat_phut_dang_chay_va_bar_thang_trade():
    async def scenario():
        hub, ws = make_hub(), FakeWS()
        await hub.add(Client(ws, "AAPL", "5m"))
        await hub._on_event(trade("AAPL", 15, 0, 1, 100.0, 10))
        await hub._on_event(trade("AAPL", 15, 0, 20, 103.0, 5))
        await hub._on_event(trade("AAPL", 15, 0, 40, 99.0, 1))
        c = hub.candle("AAPL", "5m")
        assert (c["time"], c["open"], c["high"], c["low"], c["close"], c["volume"]) == \
            (ny_to_unix("2024-03-04 10:00"), 100, 103, 99, 99, 16)
        await hub._on_event(trade("AAPL", 15, 1, 5, 101.0, 2))  # sang phút 10:01, cùng nến 5m
        c = hub.candle("AAPL", "5m")
        assert (c["high"], c["low"], c["close"], c["volume"]) == (103, 99, 101, 18)
        await hub._on_event(bar("AAPL", "15:00", 100, 104, 98, 99.5, 20))  # bar chính thức 10:00 thay phút trade
        c = hub.candle("AAPL", "5m")
        assert (c["open"], c["high"], c["low"], c["close"], c["volume"]) == (100, 104, 98, 101, 22)
        await hub._on_event(trade("AAPL", 15, 0, 59, 50.0, 100))  # trade trễ của phút đã có bar: bỏ
        assert hub.candle("AAPL", "5m")["low"] == 98
        last = ws.bars()[-1]  # bar đẩy ngay, có ts của trade cuối
        assert last["time"] == ny_to_unix("2024-03-04 10:00") and "ts" in last
        assert hub.candle("AAPL", "1m")["close"] == 101  # khung 1m: chỉ phút cuối
        await hub.shutdown()

    asyncio.run(scenario())


def test_stock_ngoai_gio_bo_trade():
    async def scenario():
        hub, ws = make_hub(), FakeWS()
        await hub.add(Client(ws, "AAPL", "1m"))
        await hub._on_event(trade("AAPL", 22, 0, 0, 100.0, 1))  # 17:00 NY: after-hours
        assert hub.candle("AAPL", "1m") is None and not hub._dirty
        await hub.shutdown()

    asyncio.run(scenario())


def test_throttle_gop_nhieu_trade_thanh_mot_tin():
    async def scenario():
        hub, ws = make_hub(throttle=3600), FakeWS()  # flush tự động không chạy: gọi tay
        await hub.add(Client(ws, "AAPL", "1m"))
        for i in range(200):
            await hub._on_event(trade("AAPL", 15, 0, i % 60, 100.0 + i, 1))
        assert ws.bars() == []  # trade chưa đẩy ngay
        await hub.flush()
        await hub.flush()  # không có trade mới: không gửi thêm
        assert len(ws.bars()) == 1
        b = ws.bars()[0]
        assert b["close"] == 299 and b["volume"] == 200 and b["ts"] == pd.Timestamp("2024-03-04 15:00:19Z").timestamp()
        await hub.shutdown()

    asyncio.run(scenario())


def test_flush_loop_toi_da_khoang_4_tin_moi_giay():
    async def scenario():
        hub, ws = make_hub(throttle=0.25), FakeWS()
        await hub.add(Client(ws, "AAPL", "1m"))
        for k in range(50):  # ~50 trade trong ~1 giây
            await hub._on_event(trade("AAPL", 15, 0, 1, 100.0 + k, 1))
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        n = len(ws.bars())
        assert 2 <= n <= 6, n
        assert ws.bars()[-1]["close"] == 149
        await hub.shutdown()

    asyncio.run(scenario())


def test_adapter_that_raise_loi_limit_va_dong_socket():
    """Adapter thật (không mạng): run() không tự thử lại như _run_forever, mà raise cho hub và đóng socket."""
    from paper_bot.web.api.live import AlpacaStreamAdapter

    async def scenario():
        ad = AlpacaStreamAdapter("k", "s", "crypto")
        closed = []

        async def fail_start():
            raise ValueError("connection limit exceeded")

        async def fake_close():
            closed.append(True)

        ad._stream._start_ws, ad._stream.close = fail_start, fake_close
        try:
            await ad.run()
            raise AssertionError("phải raise")
        except ValueError as e:
            assert "limit" in str(e)
        assert closed and ad._done.is_set() and not ad.connected
        await ad.stop()  # gọi lại sau khi đã dừng: không treo
        assert len(closed) == 2

    asyncio.run(scenario())


def test_shutdown_dong_sach_ca_stock_va_crypto():
    async def scenario():
        hub = LiveHub(FakeAdapter, clock=OPEN_CLOCK, grace=300, crypto_adapter_factory=FakeAdapter)
        a, b = Client(FakeWS(), "AAPL", "1m"), Client(FakeWS(), "BTC/USD", "1m", "crypto")
        await hub.add(a)
        await hub.add(b)
        await wait_until(lambda: len(FakeAdapter.instances) == 2)
        await hub.remove(a)  # đang trong grace
        await hub.shutdown()
        assert all(i.stopped for i in FakeAdapter.instances)
        assert hub.adapter is None and hub.crypto_hub.adapter is None
        assert not hub._tasks and not hub.crypto_hub._tasks and hub._grace_task is None

    asyncio.run(scenario())
