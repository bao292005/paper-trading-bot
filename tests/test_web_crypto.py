"""Test offline phần CRYPTO của web: API dữ liệu, đặt lệnh, nút chạy bot, vị thế, hub realtime. Không gọi Alpaca thật."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from paper_bot.ui.core import build_once_command
from paper_bot.web.api import orders as orders_api
from paper_bot.web.api.timeutil import snap_to_bar, utc_to_unix
from paper_bot.web.app import create_app
from paper_bot.web.hub import Client, LiveHub

HOST = "http://127.0.0.1:8600"
GOOD = {"X-Requested-With": "paper-web", "Origin": HOST}


class FakeTrading:
    def __init__(self):
        self.submitted = []

    def submit_order(self, order_data=None, **k):
        self.submitted.append(order_data)
        return SimpleNamespace(id="c-1", status=SimpleNamespace(value="accepted"))

    def get_asset(self, symbol):
        return SimpleNamespace(min_order_size="0.001")

    def get_all_positions(self):
        mk = lambda s, q: SimpleNamespace(symbol=s, qty=q, side="long", avg_entry_price="100", current_price="110",
                                          market_value="11", unrealized_pl="1", unrealized_plpc="0.1")
        return [mk("AAPL", "10"), mk("BTCUSD", "0.123456")]

    def get_orders(self, filter=None):
        return []


def fake_load(symbol, tf, start, end, asset=None):
    idx = pd.date_range("2024-03-09 00:00", periods=5, freq="1min")  # thứ 7: crypto vẫn có nến
    return pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 3.0}, index=idx)


@pytest.fixture
def env(tmp_path):
    fake = FakeTrading()
    app = create_app(load_intraday_fn=fake_load, trading_client_fn=lambda: fake, db_path=tmp_path / "t.db",
                     adapter_factory=lambda: None, bars_dir=tmp_path)
    return SimpleNamespace(client=TestClient(app, base_url=HOST), fake=fake)


def order(**kw):
    base = {"asset": "crypto", "symbol": "BTC/USD", "side": "buy", "type": "limit", "qty": 0.0123,
            "limit_price": 50000.0, "confirm": "XAC NHAN"}
    return {**base, **kw}


def post(env, body, headers=GOOD):
    return env.client.post("/api/orders", json=body, headers=headers)


# ---------------- dữ liệu ----------------
def test_symbols_crypto_la_5_ma_whitelist(env):
    body = env.client.get("/api/symbols", params={"asset": "crypto"}).json()
    assert body["symbols"] == ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD"] and body["asset"] == "crypto"


def test_bars_crypto_gio_utc_va_chap_nhan_cuoi_tuan(env):
    body = env.client.get("/api/bars", params={"symbol": "BTC/USD", "tf": "1m", "asset": "crypto"}).json()
    assert body["asset"] == "crypto" and len(body["bars"]) == 5
    assert body["bars"][0]["time"] == utc_to_unix("2024-03-09 00:00")  # UTC, không đổi sang giờ New York


def test_ma_la_hoac_cho_phieu_bi_chan_o_che_do_crypto(env):
    for sym in ("DOGE/USD", "BTCUSD", "AAPL"):
        res = env.client.get("/api/bars", params={"symbol": sym, "tf": "1D", "asset": "crypto"})
        assert res.status_code == 400, sym
    assert env.client.get("/api/bars", params={"symbol": "BTC/USD", "asset": "xyz"}).status_code == 400


def test_snap_to_bar_crypto_24_7():
    assert snap_to_bar(pd.Timestamp("2024-03-09 03:07"), "5m", "crypto") == utc_to_unix("2024-03-09 03:05")
    assert snap_to_bar(pd.Timestamp("2024-03-09"), "1h", "crypto") == utc_to_unix("2024-03-09 23:00")  # ngày không giờ = cuối ngày


# ---------------- đặt lệnh ----------------
def test_lenh_crypto_hop_le_qty_le_gtc_khong_bracket(env):
    res = post(env, order())
    assert res.status_code == 200 and res.json()["symbol"] == "BTC/USD"
    req = env.fake.submitted[0]
    assert req.symbol == "BTC/USD" and req.qty == 0.0123 and req.limit_price == 50000.0
    assert req.time_in_force.value == "gtc" and req.order_class is None


def test_lenh_crypto_market_va_stop_limit(env):
    assert post(env, order(type="market", limit_price=None, ref_price=50000)).status_code == 200
    res = post(env, order(type="stop_limit", stop_price=49000.0, limit_price=48900.0, side="sell"))
    assert res.status_code == 200 and env.fake.submitted[-1].stop_price == 49000.0


@pytest.mark.parametrize("patch", [
    {"stop_loss": 40000.0},                      # có SL: bị chặn (SL/TP do bot tự quản)
    {"take_profit": 60000.0},                    # có TP: bị chặn
    {"symbol": "DOGE/USD"},                      # mã lạ
    {"symbol": "BTCUSD"},                        # sai dạng
    {"symbol": "AAPL"},                          # cổ phiếu ở chế độ crypto
    {"qty": 0}, {"qty": -1}, {"qty": True},      # qty không hợp lệ
    {"qty": 0.0005},                             # dưới min_order_size 0.001
    {"qty": 5, "limit_price": 50000.0},          # 250k$ > 200k$/lệnh
    {"type": "stop"},                            # loại lệnh không hỗ trợ
    {"type": "stop_limit"},                      # thiếu stop_price
])
def test_lenh_crypto_sai_bi_chan_truoc_khi_gui(env, patch):
    res = post(env, order(**patch))
    assert res.status_code == 422, res.text
    assert env.fake.submitted == []


def test_lenh_crypto_van_can_xac_nhan_va_header(env):
    assert post(env, order(confirm="xac nhan")).status_code == 403
    assert post(env, order(), headers={"Origin": HOST}).status_code == 403
    assert post(env, order(), headers={"X-Requested-With": "paper-web", "Origin": "http://evil.com"}).status_code == 403
    assert env.fake.submitted == []


def test_lenh_co_phieu_van_nhu_cu_va_asset_la_bi_chan(env):
    assert post(env, {"symbol": "AAPL", "side": "buy", "type": "limit", "qty": 1, "limit_price": 150.0,
                      "confirm": "XAC NHAN"}).status_code == 200
    assert post(env, order(asset="forex")).status_code == 422
    assert post(env, {"symbol": "AAPL", "side": "buy", "type": "limit", "qty": 1.5, "limit_price": 150.0,
                      "confirm": "XAC NHAN"}).status_code == 422  # cổ phiếu vẫn chỉ nhận số nguyên


# ---------------- vị thế ----------------
def test_positions_chuan_hoa_ma_crypto_va_loc_theo_asset(env):
    allp = env.client.get("/api/positions").json()["positions"]
    assert [p["symbol"] for p in allp] == ["AAPL", "BTC/USD"] and "asset" not in allp[0]
    crypto = env.client.get("/api/positions", params={"asset": "crypto"}).json()["positions"]
    assert [(p["symbol"], p["qty"], p["asset"]) for p in crypto] == [("BTC/USD", 0.123456, "crypto")]
    stock = env.client.get("/api/positions", params={"asset": "stock"}).json()["positions"]
    assert [p["symbol"] for p in stock] == ["AAPL"]


# ---------------- nút chạy bot ----------------
def test_build_once_command_crypto():
    cmd = build_once_command(["BTC/USD", "ETH/USD"], asset="crypto")
    assert cmd[-1] == "--dry-run" and ["--asset", "crypto"] == cmd[cmd.index("--asset"):cmd.index("--asset") + 2]
    assert "--asset" not in build_once_command(["AAPL"])  # cổ phiếu không đổi
    for bad in (["AAPL"], ["DOGE/USD"], ["BTCUSD"], []):
        with pytest.raises(ValueError):
            build_once_command(bad, asset="crypto")
    with pytest.raises(ValueError):
        build_once_command(["BTC/USD"], engine="legacy", asset="crypto")
    with pytest.raises(ValueError):
        build_once_command(["BTC/USD"], asset="forex")
    with pytest.raises(PermissionError):
        build_once_command(["BTC/USD"], asset="crypto", dry_run=False)


def test_nut_chay_bot_crypto_truyen_asset(env, monkeypatch):
    calls = []
    monkeypatch.setattr(orders_api, "run_command", lambda cmd, allow_real=False: calls.append((cmd, allow_real)) or
                        {"returncode": 0, "timed_out": False, "stdout": "ok", "stderr": ""})
    res = env.client.post("/api/bot/run", json={"symbols": ["BTC/USD"], "asset": "crypto"}, headers=GOOD)
    assert res.status_code == 200 and calls[0][1] is False
    assert "--asset" in calls[0][0] and "--dry-run" in calls[0][0]
    bad = env.client.post("/api/bot/run", json={"symbols": ["NVDA"], "asset": "crypto"}, headers=GOOD)
    assert bad.status_code == 422 and len(calls) == 1


# ---------------- hub realtime ----------------
class FakeAdapter:
    def __init__(self):
        self.subscribed, self.handler, self._stop = [], None, None

    async def subscribe(self, handler, symbols):
        self.handler = handler
        self.subscribed.append(list(symbols))

    async def unsubscribe(self, symbols):
        pass

    async def run(self):
        self._stop = asyncio.Event()
        await self._stop.wait()

    async def stop(self):
        if self._stop:
            self._stop.set()


class FakeWS:
    def __init__(self):
        self.msgs = []

    async def send_json(self, msg):
        self.msgs.append(msg)


def crypto_bar(symbol, hhmm, o, h, l, c, v):
    hh, mm = map(int, hhmm.split(":"))
    return SimpleNamespace(symbol=symbol, timestamp=datetime(2024, 3, 9, hh, mm, tzinfo=timezone.utc),
                           open=o, high=h, low=l, close=c, volume=v)


def test_hub_crypto_toi_da_1_stream_moi_loai_va_nen_utc_cuoi_tuan():
    async def scenario():
        adapters = {"stock": [], "crypto": []}

        def factory(kind):
            def make():
                adapters[kind].append(FakeAdapter())
                return adapters[kind][-1]
            return make

        hub = LiveHub(factory("stock"), clock=lambda: pd.Timestamp("2024-03-09 11:00"),  # thứ 7: cổ phiếu đóng cửa
                      crypto_adapter_factory=factory("crypto"))
        w1, w2, w3 = FakeWS(), FakeWS(), FakeWS()
        c1, c2, c3 = Client(w1, "BTC/USD", "5m", "crypto"), Client(w2, "ETH/USD", "1m", "crypto"), Client(w3, "AAPL", "1m")
        for c in (c1, c2, c3):
            await hub.add(c)
        for _ in range(100):  # chờ 2 vòng supervise nối stream
            if adapters["crypto"] and adapters["crypto"][0].handler and adapters["stock"] and adapters["stock"][0].handler:
                break
            await asyncio.sleep(0.01)
        assert len(adapters["crypto"]) == 1 and len(adapters["stock"]) == 1  # 2 client crypto dùng chung 1 stream
        assert w1.msgs[0]["market"] == "open" and w3.msgs[0]["market"] == "closed"  # crypto 24/7
        await hub.crypto_hub._on_bar(crypto_bar("BTC/USD", "03:07", 10, 11, 9, 10.5, 1))
        await hub.crypto_hub._on_bar(crypto_bar("BTC/USD", "03:08", 10.5, 12, 10, 11, 2))
        bars = [m for m in w1.msgs if m["type"] == "bar"]
        assert bars[-1]["time"] == utc_to_unix("2024-03-09 03:05") and bars[-1]["asset"] == "crypto"
        assert (bars[-1]["open"], bars[-1]["high"], bars[-1]["close"], bars[-1]["volume"]) == (10, 12, 11, 3)
        assert not [m for m in w2.msgs if m["type"] == "bar"] and not [m for m in w3.msgs if m["type"] == "bar"]
        await hub.remove(c1)
        await hub.remove(c2)  # hết người xem crypto: nhả stream crypto, stock giữ nguyên
        assert hub.crypto_hub.adapter is None and hub.adapter is not None
        await hub.shutdown()
        assert hub.adapter is None

    asyncio.run(scenario())


def test_ws_crypto_chi_nhan_ma_whitelist(env):
    from starlette.websockets import WebSocketDisconnect

    for params in ({"symbol": "DOGE/USD", "tf": "1m", "asset": "crypto"}, {"symbol": "AAPL", "tf": "1m", "asset": "crypto"},
                   {"symbol": "BTC/USD", "tf": "1m"}):  # mã crypto mà không khai asset -> không hợp lệ
        with pytest.raises(WebSocketDisconnect):
            with env.client.websocket_connect("/ws/live", params=params, headers={"origin": HOST}):
                pass
