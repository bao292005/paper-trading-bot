"""Test logic zoom của web (zoom_math.js, data.js lazy-load) bằng node + chart giả. Bỏ qua nếu không có node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src" / "paper_bot" / "web" / "static" / "js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="không có node")

# Nạp các file JS vào 1 context, chạy `code` (được phép dùng await), in kết quả JSON
RUNNER = """
const fs = require("fs"), vm = require("vm");
const els = {};
const el = (id) => els[id] || (els[id] = { id, value: "", textContent: "", innerHTML: "", className: "", style: {}, hidden: false });
const ctx = { document: { getElementById: el }, console, setTimeout, clearTimeout, Math, Promise };
vm.createContext(ctx);
for (const f of process.argv[1].split(",")) vm.runInContext(fs.readFileSync(f, "utf8"), ctx, { filename: f });
vm.runInContext("(async () => {" + process.argv[2] + "})()", ctx).then((o) => console.log(JSON.stringify(o)));
"""


def run_js(files, code):
    paths = ",".join(str(JS / f) for f in files)
    res = subprocess.run(["node", "-e", RUNNER, paths, code], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def zm(expr):
    return run_js(["zoom_math.js"], f"return {expr};")


def test_zoom_in_giu_neo_tuong_doi():
    r = zm("ZM.zoomRange({from: 0, to: 100}, 0.8, 50)")
    assert r == {"from": 10, "to": 90}
    # neo ở mép phải: mép phải đứng yên
    r = zm("ZM.zoomRange({from: 0, to: 100}, 0.5, 100)")
    assert r == {"from": 50, "to": 100}


def test_zoom_mac_dinh_quanh_tam_va_gioi_han():
    assert zm("ZM.zoomRange({from: 0, to: 100}, 1.25)") == {"from": -12.5, "to": 112.5}
    r = zm("ZM.zoomRange({from: 0, to: 10}, 0.1)")  # không nhỏ hơn MIN_BARS
    assert r["to"] - r["from"] == 8


def test_cuon_ngang_10_phan_tram():
    assert zm("ZM.scrollRange({from: 100, to: 200}, 0.1)") == {"from": 110, "to": 210}
    assert zm("ZM.scrollRange({from: 100, to: 200}, -0.1)") == {"from": 90, "to": 190}


def test_reset_150_nen_cuoi_va_du_lieu_ngan():
    assert zm("ZM.lastNRange(1000)") == {"from": 849.5, "to": 1004}
    assert zm("ZM.lastNRange(40)") == {"from": -0.5, "to": 44}  # ít hơn 150 nến: bắt đầu từ nến đầu
    assert zm("ZM.alignStart({from: 300, to: 400})") == {"from": -0.5, "to": 99.5}


def test_shift_range_va_mep_phai():
    assert zm("ZM.shiftRange({from: 3, to: 120}, 500)") == {"from": 503, "to": 620}
    assert zm("[ZM.atRightEdge({from: 0, to: 99.6}, 100), ZM.atRightEdge({from: 0, to: 80}, 100)]") == [True, False]


def test_box_selection_doi_toa_do():
    code = """
    const lg = (x) => x / 10, pr = (y) => 200 - y;  // x=100 -> logic 10; y=50 -> giá 150
    return [ZM.boxSelection({x: 200, y: 100}, {x: 100, y: 50}, lg, pr, true),
            ZM.boxSelection({x: 200, y: 100}, {x: 100, y: 50}, lg, pr, false),
            ZM.boxSelection({x: 100, y: 50}, {x: 104, y: 90}, lg, pr, true),    // rộng < 5px
            ZM.boxSelection({x: 100, y: 50}, {x: 160, y: 53}, lg, pr, true),    // cao < 5px
            ZM.boxSelection({x: 100, y: 50}, {x: 160, y: 90}, () => null, pr, true)];
    """
    a, b, small_w, small_h, none = run_js(["zoom_math.js"], code)
    assert a == {"logical": {"from": 10, "to": 20}, "price": {"from": 100, "to": 150}}
    assert b["price"] is None and b["logical"] == a["logical"]
    assert small_w is None and small_h is None and none is None


def test_doi_khung_giu_khoang_thoi_gian():
    code = """
    const bars = Array.from({length: 100}, (_, i) => ({ time: 1000 + i * 10 }));  // time 1000..1990
    return [ZM.timeRangeToLogical(bars, { from: 1200, to: 1500, atEdge: false }),   // nến 20..50
            ZM.timeRangeToLogical(bars, { from: 1200, to: 1990, atEdge: true }),    // đang ở mép phải
            ZM.timeRangeToLogical(bars, { from: 500, to: 1500 }),                   // chưa có dữ liệu cũ đến vậy
            ZM.timeRangeToLogical(bars, { from: 1200, to: 1250 }),                  // chỉ 6 nến
            ZM.timeRangeToLogical([], { from: 1, to: 2 })];
    """
    a, edge, old, few, empty = run_js(["zoom_math.js"], code)
    assert a == {"from": 19.5, "to": 50.5}
    assert edge == {"from": 19.5, "to": 99 + 5}
    assert old is None and few is None and empty is None


# ---- data.js với chart giả: lazy-load không làm nhảy vùng nhìn, đổi khung giữ khoảng thời gian ----
FAKE = """
const calls = [];
const noop = { setData() {}, update() {} };
globalThis.CH = { symbol: "AAPL", tf: "1D", asset: "stock", ma: { ma10: noop, ma30: noop, ma200: noop }, rsi: null,
  candles: { setData(d) { calls.push(["candles", d.length]); } }, volume: noop, zoom: null,
  chart: { timeScale: () => ({ getVisibleLogicalRange: () => ({ from: 5, to: 120 }),
    setVisibleLogicalRange: (r) => calls.push(["logical", r.from, r.to]) }) } };
globalThis.setMarkers = () => {};
const mk = (t0, n) => Array.from({ length: n }, (_, i) => ({ time: t0 + i }));
"""
DATA_FILES = ["zoom_math.js", "data.js"]


def test_lazy_load_giu_nguyen_vung_nhin():
    code = FAKE + """
    D.bars = mk(1000, 200); D.volume = mk(1000, 200); D.oldest = 1000;
    globalThis.getJSON = async (url) => url === "/api/bars" ? { bars: mk(900, 100), volume: mk(900, 100), oldest: 900 } : {};
    await loadOlder();
    return { calls, n: D.bars.length, first: D.bars[0].time, oldest: D.oldest, loading: D.loading };
    """
    r = run_js(DATA_FILES, code)
    assert r["calls"][-1] == ["logical", 105, 220]  # 5+100, 120+100: đúng số nến vừa thêm
    assert r["n"] == 300 and r["first"] == 900 and r["oldest"] == 900 and r["loading"] is False


def test_lazy_load_het_du_lieu_khong_doi_vung():
    code = FAKE + """
    D.bars = mk(1000, 50); D.oldest = 1000;
    globalThis.getJSON = async (url) => url === "/api/bars" ? { bars: [], volume: [], oldest: null } : {};
    await loadOlder();
    return { calls, exhausted: D.exhausted };
    """
    r = run_js(DATA_FILES, code)
    assert r["exhausted"] is True and not any(c[0] == "logical" for c in r["calls"])


def test_load_all_reset_hoac_khoi_phuc_theo_khung():
    code = FAKE + """
    const log = [];
    CH.zoom = { saveRange: () => log.push("save"), restoreRange: (b) => log.push("restore:" + b.length), reset: () => log.push("reset") };
    globalThis.getJSON = async (url) => url === "/api/bars" ? { bars: mk(1, 30), volume: mk(1, 30), oldest: 1 } : {};
    await loadAll(false);   // lần đầu / đổi mã
    await loadAll(true);    // đổi khung
    return log;
    """
    assert run_js(DATA_FILES, code) == ["reset", "save", "restore:30"]


# ---- attachZoom với chart giả: nút, phím tắt, lưu/khôi phục vùng ----
FAKE_CHART = """
const keyFns = [], log = [];
globalThis.document = { addEventListener: (n, f) => keyFns.push(f), createElement: () => ({ style: {} }) };
globalThis.window = { addEventListener() {}, removeEventListener() {} };
globalThis.localStorage = { getItem: () => null, setItem() {} };
let view = { from: 100, to: 200 }, auto = true;
const ts = { getVisibleLogicalRange: () => view, setVisibleLogicalRange: (r) => { view = r; },
  getVisibleRange: () => ({ from: 1100, to: 1200 }), scrollToRealTime: () => log.push("rt"), fitContent: () => log.push("fit") };
const scale = { options: () => ({ autoScale: auto }),
  applyOptions: (o) => { if ("autoScale" in o) auto = o.autoScale; if ("mode" in o) log.push("mode" + o.mode); },
  setVisibleRange: (r) => { auto = false; log.push("price" + r.from); } };
const data = Array.from({ length: 300 }, (_, i) => ({ time: 1000 + i }));
const series = { data: () => data, priceScale: () => scale, coordinateToPrice: (y) => y };
const chart = { timeScale: () => ts, priceScale: () => ({ width: () => 50 }), applyOptions() {}, paneSize: () => ({ height: 300 }) };
const el = { clientWidth: 800, clientHeight: 400, offsetParent: {}, appendChild() {}, addEventListener() {},
  getBoundingClientRect: () => ({ left: 0, top: 0 }) };
const key = (k, tag) => keyFns.forEach((f) => f({ key: k, target: { tagName: tag || "DIV" }, preventDefault() {} }));
const z = attachZoom(chart, series, el, {});
"""
ZOOM_FILES = ["zoom_math.js", "pricescale.js", "zoombox.js", "zoom.js"]


def test_attach_zoom_nut_va_phim_tat():
    code = FAKE_CHART + """
    const out = {};
    z.zoomIn(); out.in = view;                      // 78% quanh tâm (nến cuối chưa hiện)
    z.zoomOut(); out.back = [view.to - view.from];
    z.reset(); out.reset = [view, auto];            // ~150 nến cuối, auto giá
    key("-"); out.key_minus = view.to - view.from;  // phím - thu nhỏ
    key("-", "INPUT"); out.in_input = view.to - view.from;  // gõ trong ô input thì bỏ qua
    z.reset(); key("ArrowLeft"); out.left = view.from;
    key("l"); key("a"); out.log_auto = [log.filter((x) => x === "mode1").length, auto];
    key("End"); key("0"); out.misc = [log.includes("rt"), view];
    return out;
    """
    r = run_js(ZOOM_FILES, code)
    assert r["in"] == {"from": 111, "to": 189}
    assert round(r["back"][0]) == 100
    assert r["reset"] == [{"from": 149.5, "to": 304}, True]
    assert round(r["key_minus"], 3) == round(154.5 / 0.78, 3)
    assert r["in_input"] == r["key_minus"]
    assert r["left"] == pytest.approx(149.5 - 15.45)  # cuộn 10% vùng nhìn sang trái
    assert r["log_auto"] == [1, False]
    assert r["misc"] == [True, {"from": 149.5, "to": 304}]


def test_attach_zoom_luu_va_khoi_phuc_vung_thoi_gian():
    code = FAKE_CHART + """
    z.setAuto(false);
    const saved = z.saveRange();                    // đang ở mép phải (to=200 < 299 -> chưa phải mép phải)
    z.restoreRange(data);
    const a = [saved, view, auto];
    view = { from: 250, to: 310 }; z.saveRange();   // thấy nến cuối -> atEdge
    z.restoreRange(data);
    const b = view;
    z.saveRange();
    z.restoreRange(data.slice(0, 5));               // dữ liệu mới không phủ -> lùi về reset
    return [a, b, view];
    """
    a, b, c = run_js(ZOOM_FILES, code)
    assert a[0] == {"from": 1100, "to": 1200, "atEdge": False}
    assert a[1] == {"from": 99.5, "to": 200.5} and a[2] is True  # cùng khoảng thời gian, auto giá bật lại
    assert b == {"from": 99.5, "to": 304}  # đang ở mép phải thì tiếp tục theo nến mới
    assert c == {"from": 149.5, "to": 304}  # không phủ -> reset: 150 nến cuối của series (300 nến)
