"""Test JS bar replay + luyện tập: hàm thuần (replay_math.js...) bằng node, cú pháp + ≤150 dòng, trang chủ nạp đủ script."""

import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from test_web_zoom import JS, run_js

from paper_bot.web.app import create_app

NEW_JS = ["replay_math.js", "replay_data.js", "replay.js", "practice.js", "practice_orders.js",
          "practice_drag.js", "practice_stats.js"]
HOLDOUT = ("Luyện tập trên dữ liệu ≥ 2025 sẽ 'nhìn thấy' giai đoạn holdout. Không ảnh hưởng gì tới bot, "
           "nhưng đừng dùng kết quả luyện tập để chỉnh bot.")
need_node = pytest.mark.skipif(shutil.which("node") is None, reason="không có node")


def rm(expr):
    return run_js(["replay_math.js"], f"return {expr};")


@pytest.mark.parametrize("name", NEW_JS)
def test_file_replay_ngan_va_dung_cu_phap(name):
    text = (JS / name).read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 150, f"{name} quá 150 dòng"
    if shutil.which("node"):
        res = subprocess.run(["node", "--check", str(JS / name)], capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, res.stderr


def test_trang_chu_nap_script_replay_va_ghi_chu_holdout(tmp_path):
    client = TestClient(create_app(trading_client_fn=lambda: None, db_path=tmp_path / "x.db", bars_dir=tmp_path))
    html = client.get("/").text
    names = [u.rsplit("/", 1)[1] for u in re.findall(r'src="(/static/js/[^"]+)"', html)]
    for n in NEW_JS:
        assert n in names and names.index(n) < names.index("main.js"), n
    assert HOLDOUT in html


@need_node
def test_ngay_gio_theo_mui_gio_ra_unix():
    r = rm("""[RM.zonedToUnix("2024-03-04 09:30", "America/New_York"), RM.zonedToUnix("2024-07-01 09:30", "America/New_York"),
              RM.zonedToUnix("2024-03-04 21:30", "Asia/Ho_Chi_Minh"), RM.zonedToUnix("2024-03-04", "UTC"),
              RM.zonedToUnix("2024-03-04T09:30", "America/New_York")]""")
    assert r == [1709562600, 1719840600, 1709562600, 1709510400, 1709562600]  # EST -5, EDT -4, VN +7
    assert rm('[RM.zonedToUnix("2024-13-01", "UTC"), RM.zonedToUnix("abc", "UTC"), RM.zonedToUnix("", "UTC"), '
              'RM.zonedToUnix("2024-03-04 25:00", "UTC")]') == [None, None, None, None]
    assert rm('RM.tzOffset(1709562600, "Asia/Ho_Chi_Minh")') == 25200


@need_node
def test_gop_theo_time_toc_do_va_phim():
    assert rm("RM.mergeByTime([{time:1,v:1},{time:2,v:2},{time:3,v:3}], [{time:3,v:9},{time:4,v:4}])") == [
        {"time": 1, "v": 1}, {"time": 2, "v": 2}, {"time": 3, "v": 9}, {"time": 4, "v": 4}]
    assert rm("RM.mergeByTime([{time:1}], [])") == [{"time": 1}]
    assert rm("[RM.nextSpeed(0.5), RM.nextSpeed(1), RM.nextSpeed(3), RM.nextSpeed(10), RM.nextSpeed(7)]") == [1, 3, 10, 0.5, 0.5]
    assert rm("[RM.delayMs(0.5), RM.delayMs(1), RM.delayMs(10), RM.delayMs(0)]") == [2000, 1000, 100, 1000]
    assert rm("""[RM.ignoreKey({target: {tagName: "INPUT"}}), RM.ignoreKey({target: {tagName: "DIV"}}),
                  RM.ignoreKey({target: {tagName: "DIV"}, metaKey: true}), RM.ignoreKey({target: {tagName: "SELECT"}})]""") == [
        True, False, True, True]


@need_node
def test_snap_time_va_holdout():
    day = 1709510400  # 2024-03-04 00:00 UTC; nến 1D đặt 12:00 UTC
    assert rm(f"RM.snapTime([{day - 86400 + 43200}, {day + 43200}], {day + 52200}, '1D')") == day + 43200
    assert rm(f"RM.snapTime([{day + 43200}], {day + 86400 + 100}, '1D')") is None
    assert rm("[RM.snapTime([100, 400, 700], 650, '5m'), RM.snapTime([100, 400, 700], 400, '5m'), "
              "RM.snapTime([100, 400], 50, '5m')]") == [400, 400, None]
    assert rm("[RM.inHoldout(1735689599), RM.inHoldout(1735689600)]") == [False, True]


FMT = "globalThis.fmt = { qty: (v) => String(v), num: (v) => String(v), usd: (v) => '$' + v, cls: () => '' };"


@need_node
def test_duong_gia_luyen_tap_keo_duoc():
    code = FMT + """
    PR.snap = { position: { qty: 10, avg_price: 100 }, orders: [
      { id: "s1", role: "sl", side: "sell", price: 95 }, { id: "t1", role: "tp", side: "sell", price: 110 },
      { id: "e1", role: "entry", side: "buy", type: "limit", price: 90, sl: 85, tp: null }] };
    return practiceLineItems().map((x) => [x.price, x.oid || null, x.field || null]);"""
    r = run_js(["replay_math.js", "practice.js"], code)
    assert r == [[100, None, None], [95, "s1", "price"], [110, "t1", "price"], [90, "e1", "price"], [85, "e1", "sl"]]


@need_node
def test_form_lenh_luyen_tap_va_lam_tron_gia():
    code = """
    const set = (id, v) => { document.getElementById(id).value = v; };
    set("pr-qty", "2"); set("pr-price", ""); set("pr-sl", "95"); set("pr-tp", "");
    const out = [practiceOrderBody("buy", "market")];
    try { practiceOrderBody("buy", "limit"); } catch (e) { out.push(e.message); }
    set("pr-qty", "0"); try { practiceOrderBody("sell", "market"); } catch (e) { out.push(e.message); }
    set("pr-qty", "1"); set("pr-price", "101.5"); out.push(practiceOrderBody("sell", "stop"));
    out.push([prRoundPrice(123.456), prRoundPrice(1.234567)]);
    out.push(prsEquity([{time: 5, value: 1}, {time: 3, value: 2}, {time: 5, value: 7}, {time: 4, value: NaN}]));
    return out;"""
    r = run_js(["replay_math.js", "practice.js", "practice_orders.js", "practice_drag.js", "practice_stats.js"], code)
    assert r[0] == {"side": "buy", "type": "market", "qty": 2, "sl": 95, "tp": None}
    assert r[1] == "Lệnh limit/stop cần giá" and r[2] == "Số lượng phải > 0"
    assert r[3] == {"side": "sell", "type": "stop", "qty": 1, "price": 101.5}  # bán: không gắn SL/TP
    assert r[4] == [123.46, 1.2346]
    assert r[5] == [{"time": 3, "value": 2}, {"time": 5, "value": 7}]
