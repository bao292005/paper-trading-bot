"""Test công cụ Short Position (JS, node): position_math với side="short" (R:R, qty, kéo TP<entry<SL, loại lệnh),
box từ tài khoản luyện tập đang short, body lệnh short."""

import shutil

import pytest

from test_web_zoom import run_js

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="không có node")


def pm(expr):
    return run_js(["position_math.js"], f"return {expr};")


def test_qty_theo_rui_ro_short():
    # rủi ro 1000$, short 100, SL 105 -> per = 5 + 205×0.1% = 5.205 -> 192.1 -> 192 cổ
    assert pm("PM.riskQty({risk: 1000, entry: 100, sl: 105, asset: 'stock', cash: 1e9, side: 'short'})") == {
        "qty": 192, "capped": None}
    assert pm("PM.riskQty({risk: 1000, entry: 100, sl: 105, asset: 'stock', cash: 5000, side: 'short'})")["capped"] == "cash"
    # SL dưới giá vào là sai cho short -> qty 0
    assert pm("PM.riskQty({risk: 1000, entry: 100, sl: 95, asset: 'stock', cash: 1e9, side: 'short'})")["qty"] == 0


def test_stats_rr_short():
    s = pm("PM.stats({entry: 100, sl: 105, tp: 85, qty: 10, asset: 'stock', side: 'short'})")
    assert s["rr"] == 3 and round(s["tpPct"], 6) == 15 and round(s["slPct"], 6) == 5
    assert round(s["profit"], 4) == round(10 * (15 - 0.185), 4) and round(s["loss"], 4) == round(10 * (5 + 0.205), 4)
    assert pm("PM.stats({entry: 100, sl: 95, tp: 110, qty: 1, asset: 'stock', side: 'short'})")["rr"] is None


def test_de_xuat_loai_lenh_short():
    # short: ≈ giá -> market; vào CAO hơn giá -> limit bán; THẤP hơn -> stop bán
    assert pm("[PM.suggestType(100.04, 100, 'short'), PM.suggestType(101, 100, 'short'), "
              "PM.suggestType(99, 100, 'short')]") == ["market", "limit", "stop"]


def test_keo_short_giu_tp_entry_sl():
    lv = "{entry: 100, sl: 105, tp: 90, side: 'short'}"
    assert pm(f"PM.drag({lv}, 'tp', 80, 0.01)") == {"entry": 100, "sl": 105, "tp": 80, "side": "short"}
    assert pm(f"PM.drag({lv}, 'tp', 110, 0.01)")["tp"] == 99.99   # TP không lên trên entry
    assert pm(f"PM.drag({lv}, 'tp', -3, 0.01)")["tp"] == 0.01     # TP > 0
    assert pm(f"PM.drag({lv}, 'sl', 95, 0.01)")["sl"] == 100.01   # SL không xuống dưới entry
    assert pm(f"PM.drag({lv}, 'sl', 120, 0.01)")["sl"] == 120
    assert pm(f"PM.drag({lv}, 'entry', 200, 0.01)")["entry"] == 104.99
    assert pm(f"PM.drag({lv}, 'entry', 1, 0.01)")["entry"] == 90.01
    assert pm(f"PM.drag({lv}, 'body', 103, 0.01, 100)") == {"entry": 103, "sl": 108, "tp": 93, "side": "short"}
    assert pm(f"PM.drag({lv}, 'body', 0, 0.01, 100)") == {"entry": 10.01, "sl": 15.01, "tp": 0.01, "side": "short"}
    assert pm("[PM.valid({entry: 100, sl: 105, tp: 90, side: 'short'}), PM.valid({entry: 100, sl: 95, tp: 110, side: 'short'}), "
              "PM.valid({entry: 100, sl: 105, tp: 0, side: 'short'})]") == [True, False, False]


def test_muc_mac_dinh_va_pl_short():
    assert pm("PM.defaults(100, 2, 0.01, 'short')") == {"entry": 100, "sl": 104, "tp": 94}
    assert pm("PM.defaults(100, null, 0.01, 'short')") == {"entry": 100, "sl": 102, "tp": 97}
    assert pm("PM.defaults(1, 5, 0.01, 'short')") == {"entry": 1, "sl": 1.02, "tp": 0.97}  # ATR quá lớn -> %
    assert pm("PM.unreal(100, 105, 90, -10, 'short')") == {"pl": 100, "r": 2}
    assert pm("PM.unreal(100, 105, 102.5, -2, 'short')") == {"pl": -5, "r": -0.5}


def test_form_luyen_tap_lenh_mo_theo_chieu_va_nhan():
    js = ("const r = []; for (const q of [0, 5, -5]) { PR = {snap: {position: q ? {qty: q} : null}};"
          " r.push([prOpens('buy'), prOpens('sell')]); } return r;")
    # không vị thế: mua/bán đều mở; long: chỉ mua mở; short: chỉ bán mở (lệnh ngược chiều = đóng, không SL/TP)
    assert run_js(["practice_orders.js"], js) == [[True, True], [True, False], [False, True]]
    # form: SL/TP chỉ gắn cho lệnh mua mở long (short kèm SL/TP đi qua box)
    js2 = "const r = []; for (const q of [0, -5]) { PR = {snap: {position: q ? {qty: q} : null}}; r.push(prSlTp('buy'), prSlTp('sell')); } return r;"
    assert run_js(["practice_orders.js"], js2) == [True, False, False, False]
    assert run_js(["api.js"], "return [sideTag(-1), sideTag(2)];") == [
        '<span class="tag down">SHORT</span>', '<span class="tag up">LONG</span>']


def test_box_short_tu_luyen_tap_va_body_lenh():
    snap = ("PR = {snap: {position: {qty: -5, avg_price: 100}, orders: ["
            "{id: 'e1', role: 'entry', side: 'sell', type: 'limit', qty: 3, price: 105, sl: 110, tp: 95, created: 7},"
            "{id: 'c1', role: 'entry', side: 'buy', type: 'limit', qty: 1, price: 90, sl: null, tp: null, reduce: true},"
            "{id: 's1', role: 'sl', side: 'buy', price: 110, created: 9}, {id: 't1', role: 'tp', side: 'buy', price: 90}]}};")
    r = run_js(["position_math.js", "position_sync.js"], f"{snap} return psPracticeSpecs();")
    assert [(b["key"], b["kind"], b["side"], b["entry"], b["sl"], b["tp"]) for b in r] == [
        ("pr-e1", "pending", "short", 105, 110, 95), ("pr-pos", "position", "short", 100, 110, 90)]
    body = "CH = {symbol: 'AAPL', asset: 'stock'}; const b = {side: 'short', entry: 100, sl: 105, tp: 90, qty: 3, type: 'limit'};"
    assert run_js(["position_math.js", "position_orders.js"], body + " return posOrderBody(b);") == {
        "symbol": "AAPL", "asset": "stock", "type": "limit", "qty": 3, "entry": 100, "sl": 105, "tp": 90, "side": "short"}
    err = run_js(["position_math.js", "position_orders.js"], body + """
      try { posOrderBody({...b, sl: 95}); return null; } catch (e) { return e.message; }""")
    assert "TP < giá vào < SL" in err
