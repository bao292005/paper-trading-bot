"""Test hàm thuần 'Lệnh của tôi' (fills_math.js, fills_lines.js) bằng node. Bỏ qua nếu không có node."""

import shutil
import subprocess

import pytest

from test_web_zoom import JS, run_js

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="không có node")
FILES = ["fills_math.js", "fills_lines.js"]


def js(expr):
    return run_js(FILES, f"return {expr};")


@pytest.mark.parametrize("name", ["fills_math.js", "fills_lines.js"])
def test_node_check(name):
    res = subprocess.run(["node", "--check", str(JS / name)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_snap_nen_chua_thoi_diem_khop():
    bars = "[{time: 1000}, {time: 1060}, {time: 1120}]"
    assert js(f"FM.snapTime({bars}, 1090, '1m', 'crypto')") == 1060
    assert js(f"FM.snapTime({bars}, 1060, '1m', 'crypto')") == 1060
    assert js(f"FM.snapTime({bars}, 5000, '1m', 'crypto')") == 1120  # sau nến cuối: dồn vào nến cuối
    assert js(f"FM.snapTime({bars}, 999, '1m', 'crypto')") is None  # trước nến đầu
    assert js("FM.snapTime([], 1, '1m', 'crypto')") is None


def test_snap_khung_ngay_theo_mui_gio_tai_san():
    # 2024-03-05 03:00 UTC = 2024-03-04 22:00 New York -> cổ phiếu thuộc ngày 04, crypto thuộc ngày 05
    sec = 1709607600
    bars = "[{time: 1709553600}, {time: 1709640000}]"  # 12:00 UTC ngày 04 và 05
    assert js(f"FM.snapTime({bars}, {sec}, '1D', 'stock')") == 1709553600
    assert js(f"FM.snapTime({bars}, {sec}, '1D', 'crypto')") == 1709640000


def test_pnl_fifo():
    fills = ("[{id:'a',time:1,side:'buy',qty:2,price:100},{id:'b',time:2,side:'buy',qty:1,price:110},"
             "{id:'c',time:3,side:'sell',qty:2.5,price:120}]")
    r = js(f"FM.pnlFifo({fills}, false)")
    assert r["c"]["qty"] == 2.5 and abs(r["c"]["pnl"] - (2 * 20 + 0.5 * 10)) < 1e-9
    assert "a" not in r and "b" not in r


def test_pnl_short_va_long_only():
    fills = "[{id:'s',time:1,side:'sell',qty:1,price:100},{id:'b',time:2,side:'buy',qty:1,price:90}]"
    assert js(f"FM.pnlFifo({fills}, false)")["b"]["pnl"] == 10  # short lời khi giá giảm
    assert js(f"FM.pnlFifo({fills}, true)") == {}  # crypto: bán không có lô bị bỏ qua


def test_markers_va_hop_nhat():
    code = """
    const bars = [{time: 1000}, {time: 1060}];
    const fills = [{id:'x',time:1070,side:'buy',qty:0.02,price:1722.8,exit_kind:null},
                   {id:'y',time:1010,side:'sell',qty:0.02,price:1725.1,exit_kind:'tp'},
                   {id:'z',time:10,side:'buy',qty:1,price:1}];
    const m = FM.markers(fills, bars, '1m', 'crypto');
    const merged = FM.merge([{time: 1060, kind: 'signal'}], m);
    return { m: m.map((x) => [x.time, x.position, x.shape, x.text]), merged: merged.map((x) => [x.time, x.kind]) };
    """
    r = run_js(FILES, code)
    assert r["m"] == [[1060, "belowBar", "arrowUp", "B 0.02 @1722.8"], [1000, "aboveBar", "arrowDown", "TP 0.02 @1725.1"]]
    assert r["merged"] == [[1000, "myfill"], [1060, "signal"], [1060, "myfill"]]  # ổn định + sắp theo giờ


def test_chong_lap_toast():
    code = """
    const seen = new Set();
    const f = (id, t) => ({id, time: t});
    const seed = FM.newFills(seen, [f('a', 5), f('b', 6)], Infinity);
    const second = FM.newFills(seen, [f('a', 5), f('c', 100), f('old', 1)], 50);
    const third = FM.newFills(seen, [f('c', 100), f('d', 101)], 50);
    return [seed.length, second.map((x) => x.id), third.map((x) => x.id), seen.size];
    """
    assert run_js(FILES, code) == [0, ["c"], ["d"], 5]


def test_toast_text():
    code = """
    return [FM.toastText({side:'buy',qty:0.02,price:1722.75,exit_kind:null}, 'ETH/USD'),
            FM.toastText({side:'sell',qty:0.02,price:1725.1,exit_kind:'tp'}, 'ETH/USD'),
            FM.toastText({side:'sell',qty:1,price:90,exit_kind:'sl'}, 'AAPL')];
    """
    assert run_js(FILES, code) == ["✅ Đã khớp MUA 0.02 ETH/USD @1722.75", "🎯 TP khớp BÁN 0.02 @1725.10",
                                   "🛑 SL khớp BÁN 1 @90.00"]


def test_duong_gia_vi_the_va_lenh_cho():
    code = """
    const pos = [{symbol: 'ETH/USD', qty: 0.02, avg_entry_price: 1722.75}];
    const orders = [{id: 'o1', symbol: 'ETH/USD', side: 'buy', type: 'limit', qty: 0.02, limit_price: 1700, stop_price: null},
                    {id: 'o2', symbol: 'ETH/USD', side: 'sell', type: 'stop', qty: null, limit_price: null, stop_price: 1650},
                    {id: 'o3', symbol: 'BTC/USD', side: 'buy', type: 'limit', qty: 1, limit_price: 5}];
    return FL.items('ETH/USD', pos, orders, []).map((i) => [i.price, i.title, !!i.dashed]);
    """
    assert run_js(FILES, code) == [[1722.75, "Vị thế 0.02 @1722.75", False], [1700, "BUY LMT 0.02", True],
                                   [1650, "SELL STP", True]]


def test_duong_gia_bo_qua_cai_da_co_box():
    code = """
    const pos = [{symbol: 'ETH/USD', qty: 0.02, avg_entry_price: 1722.75}];
    const orders = [{id: 'o1', symbol: 'ETH/USD', side: 'sell', type: 'limit', qty: 0.02, limit_price: 1800},
                    {id: 'o2', symbol: 'ETH/USD', side: 'sell', type: 'stop', qty: 0.02, stop_price: 1650},
                    {id: 'o3', symbol: 'ETH/USD', side: 'buy', type: 'limit', qty: 0.02, limit_price: 1600}];
    const box = {symbol: 'ETH/USD', account: 'real', kind: 'position', ids: {tp: 'o1'}, sl: 1650, tp: 1800};
    const draft = {symbol: 'ETH/USD', account: 'real', kind: 'draft', sl: 1600};
    return FL.items('ETH/USD', pos, orders, [box, draft]).map((i) => i.title);
    """
    assert run_js(FILES, code) == ["BUY LMT 0.02"]  # vị thế + o1 (theo id) + o2 (theo giá SL) do box vẽ; draft bị bỏ qua
