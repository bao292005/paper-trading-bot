"""Test JS realtime v2 bằng node: chỉ báo incremental khớp backend, update nến cùng time / nến mới, poll dự phòng."""

import json

import numpy as np
import pandas as pd

from paper_bot.data.features import rsi_series
from test_web_zoom import pytestmark, run_js  # noqa: F401  (skip khi không có node)

FILES = ["data.js", "indicators_live.js", "live.js"]

# Đồ giả cho trình duyệt: CH với các series ghi lại update(), legend, setInterval
SETUP = """
const mk = () => ({ calls: [], update(p) { this.calls.push(p); } });
globalThis.CH = { symbol: "AAPL", tf: "1m", asset: "stock", candles: mk(), volume: mk(),
                  ma: { ma10: mk(), ma30: mk(), ma200: mk() }, rsi: mk() };
globalThis.legendCalls = 0;
globalThis.refreshLegend = () => { legendCalls++; };
globalThis.setInterval = () => 7;
globalThis.clearInterval = () => {};
"""


def closes(n=400, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 1, n)).round(2)
    if n >= 170:
        c[150:170] = 200 + np.arange(20)  # đoạn tăng liên tục: RSI = 100
    return c


def test_chi_bao_incremental_khop_backend_moi_vi_tri():
    c = closes()
    df = pd.DataFrame({"close": c})
    want = {f"ma{w}": df["close"].rolling(w).mean() for w in (10, 30, 200)}
    want["rsi14"] = rsi_series(df, 14)
    got = run_js(["indicators_live.js"], f"""
        const c = {json.dumps(c.tolist())};
        const out = [];
        for (let i = 1; i <= c.length; i++) out.push(IL.liveValues(c.slice(0, i).map((x) => ({{ close: x }}))));
        return out;""")
    for k, s in want.items():
        for i, v in enumerate(s):
            if pd.isna(v):
                assert got[i][k] is None, (k, i)
            else:
                assert abs(got[i][k] - v) < 1e-6, (k, i, got[i][k], v)
    assert got[169]["rsi14"] == 100


def test_upsert_last_cung_time_thay_time_moi_them_time_cu_bo():
    r = run_js(["indicators_live.js"], """
        const a = [{ time: 1, value: 1 }];
        const p1 = IL.upsertLast(a, 1, 5), p2 = IL.upsertLast(a, 2, 6), p3 = IL.upsertLast(a, 1, 9);
        const p4 = IL.upsertLast(a, 3, null);
        return { a, p1, p2, p3, p4 };""")
    assert r["a"] == [{"time": 1, "value": 5}, {"time": 2, "value": 6}]
    assert r["p1"] == {"time": 1, "value": 5} and r["p3"] is None and r["p4"] is None


def _bars_js(c):
    """Khởi tạo D.bars/volume/ind như sau loadAll (chỉ báo tính lại toàn bộ bằng IL trên prefix)."""
    return f"""
        const c = {json.dumps(c.tolist())};
        D.bars = c.map((x, i) => ({{ time: 1000 + 60 * i, open: x, high: x + 1, low: x - 1, close: x }}));
        D.volume = c.map((x, i) => ({{ time: 1000 + 60 * i, value: 10 }}));
        D.ind = {{ ma10: [], ma30: [], ma200: [], rsi14: [] }};
        for (let i = 1; i <= c.length; i++) {{
            const v = IL.liveValues(D.bars.slice(0, i));
            for (const k of IND_KEYS) if (v[k] !== null) D.ind[k].push({{ time: D.bars[i - 1].time, value: v[k] }});
        }}
        const lastT = D.bars[D.bars.length - 1].time;
    """


def test_nen_cung_time_thay_nen_moi_them_va_chi_bao_cap_nhat():
    c = closes(250)
    r = run_js(FILES, SETUP + _bars_js(c) + """
        const n0 = D.bars.length;
        applyLiveBar({ type: "bar", symbol: "AAPL", tf: "1m", time: lastT, open: 1, high: 999, low: 0.5, close: 500, volume: 77, ts: 123 });
        const same = { n: D.bars.length, close: D.bars[n0 - 1].close, vol: D.volume[n0 - 1].value,
                       ma10: D.ind.ma10[D.ind.ma10.length - 1], nMa: D.ind.ma10.length, lastTs: LIVE.lastTs };
        applyLiveBar({ type: "bar", symbol: "AAPL", tf: "1m", time: lastT + 60, open: 500, high: 501, low: 499, close: 501, volume: 3 });
        const fresh = { n: D.bars.length, ma10: D.ind.ma10[D.ind.ma10.length - 1], rsi: D.ind.rsi14[D.ind.rsi14.length - 1] };
        applyLiveBar({ type: "bar", symbol: "AAPL", tf: "1m", time: lastT, open: 1, high: 1, low: 1, close: 1, volume: 1 });  // cũ: bỏ
        applyLiveBar({ type: "bar", symbol: "MSFT", tf: "1m", time: lastT + 60, open: 1, high: 1, low: 1, close: 1, volume: 1 });
        globalThis.RP = { on: true };
        applyLiveBar({ type: "bar", symbol: "AAPL", tf: "1m", time: lastT + 120, open: 1, high: 1, low: 1, close: 1, volume: 1 });
        const closes = D.bars.map((b) => b.close);
        return { n0, same, fresh, nAfter: D.bars.length, lastClose: D.bars[D.bars.length - 1].close,
                 candleCalls: CH.candles.calls.length, ma10Calls: CH.ma.ma10.calls.length, rsiCalls: CH.rsi.calls.length,
                 legendCalls, closes, lastT };""")
    n0 = r["n0"]
    assert r["same"]["n"] == n0 and r["same"]["close"] == 500 and r["same"]["vol"] == 77 and r["same"]["lastTs"] == 123
    assert r["same"]["nMa"] == n0 - 9  # thay điểm cuối, không thêm
    want_same = (sum(c[-10:-1]) + 500) / 10
    assert abs(r["same"]["ma10"]["value"] - want_same) < 1e-9 and r["same"]["ma10"]["time"] == r["lastT"]
    assert r["fresh"]["n"] == n0 + 1 and r["fresh"]["ma10"]["time"] == r["lastT"] + 60
    # so với tính lại toàn bộ bằng pandas
    df = pd.DataFrame({"close": r["closes"]})
    assert abs(r["fresh"]["ma10"]["value"] - df["close"].rolling(10).mean().iloc[-1]) < 1e-9
    assert abs(r["fresh"]["rsi"]["value"] - rsi_series(df, 14).iloc[-1]) < 1e-9
    assert r["nAfter"] == n0 + 1 and r["lastClose"] == 501  # nến cũ / mã khác / replay: bỏ qua
    assert r["candleCalls"] == 2 and r["ma10Calls"] == 2 and r["rsiCalls"] == 2 and r["legendCalls"] == 2


def test_ws_im_lang_thi_poll_rest_va_ws_co_tin_lai_thi_ngung():
    c = closes(50)
    r = run_js(FILES, SETUP + _bars_js(c) + """
        CH.asset = "crypto"; CH.symbol = "BTC/USD"; LIVE.open = true;
        let asked = null;
        globalThis.getJSON = async (url, p) => { asked = { url, ...p };
            return { bars: [{ time: lastT, open: 1, high: 2, low: 0.5, close: 1.5 },
                            { time: lastT + 60, open: 1.5, high: 3, low: 1, close: 2.5 }],
                     volume: [{ time: lastT, value: 5 }, { time: lastT + 60, value: 6 }] }; };
        LIVE.lastMsgAt = Date.now() - 25000;  // im lặng 25s
        liveTick();
        await new Promise((ok) => setTimeout(ok, 10));
        const polling = !!LIVE.pollTimer, badgePoll = document.getElementById("live-status").textContent;
        const n = D.bars.length, last = D.bars[n - 1];
        applyLiveBar({ type: "bar", symbol: "BTC/USD", tf: "1m", asset: "crypto", time: lastT + 60,
                       open: 1.5, high: 3, low: 1, close: 2.7, volume: 7, ts: Date.now() / 1000 - 1.2 });
        liveTick();
        return { polling, badgePoll, asked, n, last, stopped: LIVE.pollTimer === null,
                 badge: document.getElementById("live-status").textContent,
                 cls: document.getElementById("live-status").className };""")
    assert r["polling"] and "dự phòng" in r["badgePoll"]
    assert r["asked"]["url"] == "/api/bars" and r["asked"]["asset"] == "crypto" and int(r["asked"]["start"]) < 1000 + 60 * 49
    assert r["n"] == 51 and r["last"]["close"] == 2.5  # nến cuối cũ được sửa + thêm nến mới
    assert r["stopped"] and "độ trễ 1." in r["badge"] and r["cls"] == "badge ok"


def test_heartbeat_connected_khong_poll_va_do_tre_do_luc_nhan():
    c = closes(50)
    r = run_js(FILES, SETUP + _bars_js(c) + """
        CH.asset = "crypto"; CH.symbol = "BTC/USD"; LIVE.open = true;
        globalThis.getJSON = async () => ({ bars: [], volume: [] });
        LIVE.lastMsgAt = Date.now() - 25000; liveTick();
        const polled = !!LIVE.pollTimer;
        showServerStatus({ type: "status", market: "open", stream: "connected" });  // heartbeat 10s
        liveTick();
        const m = { type: "bar", symbol: "BTC/USD", tf: "1m", asset: "crypto", time: lastT, open: 1, high: 2, low: 1, close: 2, volume: 1 };
        applyLiveBar({ ...m, ts: Date.now() / 1000 - 0.5 });
        const ts = LIVE.lastTs; LIVE.lag = 0.5;
        applyLiveBar({ ...m, ts });  // bar không kèm trade mới (ts cũ) -> giữ độ trễ cũ
        renderLiveBadge(Date.now() + 60000);  // 1 phút sau vẫn hiện độ trễ lúc nhận, không tăng dần
        return { polled, stopped: LIVE.pollTimer === null, badge: document.getElementById("live-status").textContent };""")
    assert r["polled"] and r["stopped"]
    assert "độ trễ 0.5s" in r["badge"]


def test_badge_vang_khi_limit_do_khi_mat_ket_noi():
    r = run_js(FILES, SETUP + """
        LIVE.open = true;
        showServerStatus({ type: "status", market: "open", stream: "limit", retry_in: 30, reason: "connection limit exceeded" });
        const a = [document.getElementById("live-status").textContent, document.getElementById("live-status").className];
        LIVE.open = false; LIVE.reconnectAt = Date.now() + 4000; renderLiveBadge(Date.now());
        const b = [document.getElementById("live-status").textContent, document.getElementById("live-status").className];
        return { a, b };""")
    assert "Alpaca đang giữ kết nối cũ, thử lại sau 30s" in r["a"][0] and r["a"][1] == "badge warn"
    assert "mất kết nối" in r["b"][0] and r["b"][1] == "badge bad"
