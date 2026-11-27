"""Test công cụ Long Position (JS): hàm thuần position_math.js bằng node, file ngắn + đúng cú pháp, trang chủ nạp script."""

import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from paper_bot.web.app import create_app
from test_web_zoom import JS, run_js

need_node = pytest.mark.skipif(shutil.which("node") is None, reason="không có node")
POS_JS = ["position_math.js", "position_box.js", "position_drag.js", "position_orders.js", "position_sync.js"]


def pm(expr):
    return run_js(["position_math.js"], f"return {expr};")


@pytest.mark.parametrize("name", POS_JS)
def test_file_ngan_va_dung_cu_phap(name):
    text = (JS / name).read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 150, f"{name} quá 150 dòng"
    if shutil.which("node"):
        res = subprocess.run(["node", "--check", str(JS / name)], capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, res.stderr


def test_trang_chu_nap_script_va_nut_long(tmp_path):
    client = TestClient(create_app(trading_client_fn=lambda: None, db_path=tmp_path / "x.db", bars_dir=tmp_path))
    html = client.get("/").text
    names = [u.rsplit("/", 1)[1] for u in re.findall(r'src="(/static/js/[^"]+)"', html)]
    for n in POS_JS:
        assert n in names and names.index(n) < names.index("main.js"), n
    assert 'id="pos-long-btn"' in html and 'id="pos-short-btn"' in html
    assert re.search(r'id="pos-short-btn"[^>]*disabled', html)  # Short luôn tắt (không bán khống)


@need_node
def test_snap_lam_tron_va_floor():
    assert pm("[PM.snap(100.126, 0.01), PM.snap(1.23456, 0.0001), PM.snap(99.999, 0.01), PM.snap(5, 0)]") == [
        100.13, 1.2346, 100.0, 5.0]
    assert pm("[PM.floorStep(12.99, 1), PM.floorStep(0.123456, 0.0001), PM.floorStep(0.3, 0.1)]") == [12, 0.1234, 0.3]
    assert pm("[PM.decimals(0.01), PM.decimals(1), PM.decimals(1e-7), PM.decimals(0.0001)]") == [2, 0, 7, 4]


@need_node
def test_atr_va_muc_mac_dinh():
    bars = "[" + ",".join("{high: %d, low: %d, close: %d}" % (101 + i, 99 + i, 100 + i) for i in range(15)) + "]"
    assert pm(f"PM.atr({bars})") == 2
    assert pm(f"PM.atr({bars}.slice(0, 10))") is None
    assert pm("PM.defaults(100, 2, 0.01)") == {"entry": 100, "sl": 96, "tp": 106}
    assert pm("PM.defaults(100, null, 0.01)") == {"entry": 100, "sl": 98, "tp": 103}
    assert pm("PM.defaults(1, 5, 0.01)") == {"entry": 1, "sl": 0.98, "tp": 1.03}  # ATR quá lớn -> dùng %


@need_node
def test_qty_theo_rui_ro_va_chan_tien_mat_tran():
    # stock: rủi ro 1000$, entry 100, SL 95 -> per = 5 + 0.195 phí -> 192.49 -> 192 cổ
    r = pm("PM.riskQty({risk: 1000, entry: 100, sl: 95, asset: 'stock', cash: 1e9})")
    assert r == {"qty": 192, "capped": None}
    assert pm("PM.riskQty({risk: 1000, entry: 100, sl: 95, asset: 'stock', cash: 5000})") == {"qty": 49, "capped": "cash"}
    assert pm("PM.riskQty({risk: 1e6, entry: 10, sl: 9.99, asset: 'stock', cash: 1e12})")["qty"] == 10000
    # crypto: qty lẻ theo bước; trần 200k$ notional
    c = pm("PM.riskQty({risk: 100, entry: 50000, sl: 49000, asset: 'crypto', step: 0.0001, cash: 1e9})")
    assert c["qty"] == 0.0801 and c["capped"] is None  # 100 / (1000 + 99000×0.25%) = 0.08016
    c = pm("PM.riskQty({risk: 1e6, entry: 50000, sl: 49900, asset: 'crypto', step: 0.0001, cash: 1e9})")
    assert c == {"qty": 4, "capped": "max"}
    assert pm("PM.riskQty({risk: 100, entry: 100, sl: 101, asset: 'stock', cash: 1e9})")["qty"] == 0


@need_node
def test_stats_rr_va_phi():
    s = pm("PM.stats({entry: 100, sl: 95, tp: 115, qty: 10, asset: 'stock'})")
    assert s["rr"] == 3 and round(s["tpPct"], 6) == 15 and round(s["slPct"], 6) == 5
    assert round(s["profit"], 4) == round(10 * (15 - 0.215), 4) and round(s["loss"], 4) == round(10 * (5 + 0.195), 4)
    c = pm("PM.stats({entry: 100, sl: 95, tp: 115, qty: 1, asset: 'crypto'})")
    assert round(c["loss"], 4) == round(5 + 195 * 0.0025, 4)
    assert pm("PM.stats({entry: 100, sl: 99.7, tp: 100.5, qty: 1, asset: 'stock'})")["rr"] == 1.67


@need_node
def test_de_xuat_loai_lenh():
    assert pm("[PM.suggestType(100.04, 100), PM.suggestType(99, 100), PM.suggestType(101, 100), PM.suggestType(5, null)]") == [
        "market", "limit", "stop", "limit"]


@need_node
def test_rang_buoc_keo_sl_entry_tp():
    lv = "{entry: 100, sl: 95, tp: 110}"
    assert pm(f"PM.drag({lv}, 'tp', 120.004, 0.01)") == {"entry": 100, "sl": 95, "tp": 120}
    assert pm(f"PM.drag({lv}, 'tp', 90, 0.01)")["tp"] == 100.01      # TP không xuống dưới entry
    assert pm(f"PM.drag({lv}, 'sl', 105, 0.01)")["sl"] == 99.99      # SL không vượt entry
    assert pm(f"PM.drag({lv}, 'sl', -3, 0.01)")["sl"] == 0.01        # SL > 0
    assert pm(f"PM.drag({lv}, 'entry', 102, 0.01)") == {"entry": 102, "sl": 95, "tp": 110}  # SL/TP giữ nguyên
    assert pm(f"PM.drag({lv}, 'entry', 200, 0.01)")["entry"] == 109.99
    assert pm(f"PM.drag({lv}, 'entry', 1, 0.01)")["entry"] == 95.01
    assert pm(f"PM.drag({lv}, 'body', 103, 0.01, 100)") == {"entry": 103, "sl": 98, "tp": 113}  # dời cả box
    assert pm(f"PM.drag({lv}, 'body', 0, 0.01, 100)") == {"entry": 5.01, "sl": 0.01, "tp": 15.01}
    assert pm("[PM.valid({entry: 100, sl: 95, tp: 110}), PM.valid({entry: 100, sl: 100, tp: 110}), "
              "PM.valid({entry: 100, sl: 95, tp: 100})]") == [True, False, False]


@need_node
def test_box_tu_tai_khoan_luyen_tap_va_body_lenh():
    snap = ("PR = {snap: {position: {qty: 5, avg_price: 100}, orders: ["
            "{id: 'e1', role: 'entry', side: 'buy', type: 'limit', qty: 3, price: 90, sl: 85, tp: 99, created: 7},"
            "{id: 'e2', role: 'entry', side: 'buy', type: 'limit', qty: 3, price: 91, sl: null, tp: null, created: 8},"
            "{id: 's1', role: 'sl', side: 'sell', price: 95, created: 9}, {id: 't1', role: 'tp', side: 'sell', price: 110}]}};")
    r = run_js(["position_math.js", "position_sync.js"], f"{snap} return psPracticeSpecs();")
    assert [(b["key"], b["kind"], b["entry"], b["sl"], b["tp"], b["ids"]) for b in r] == [
        ("pr-e1", "pending", 90, 85, 99, {"entry": "e1"}), ("pr-pos", "position", 100, 95, 110, {"sl": "s1", "tp": "t1"})]
    body = "CH = {symbol: 'AAPL', asset: 'stock'}; const b = {entry: 100, sl: 95, tp: 110, qty: 3, type: 'limit'};"
    assert run_js(["position_math.js", "position_orders.js"], body + " return posOrderBody(b);") == {
        "symbol": "AAPL", "asset": "stock", "type": "limit", "qty": 3, "entry": 100, "sl": 95, "tp": 110}
    errs = run_js(["position_math.js", "position_orders.js"], body + """
      const tryIt = (x) => { try { posOrderBody({...b, ...x}); return null; } catch (e) { return e.message; } };
      return [tryIt({sl: 100}), tryIt({qty: 0}), tryIt({qty: 1.5})];""")
    assert all(errs)


@need_node
def test_pl_chua_chot_theo_r():
    assert pm("PM.unreal(100, 95, 110, 10)") == {"pl": 100, "r": 2}
    assert pm("PM.unreal(100, 95, 97.5, 2)") == {"pl": -5, "r": -0.5}
