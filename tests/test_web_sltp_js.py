"""JS nút SL/TP: hàm thuần SLT (sltp_math.js), kéo box vị thế tự do, cột panel, trang chủ nạp script."""

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from paper_bot.web.app import create_app
from test_web_zoom import JS, run_js

need_node = pytest.mark.skipif(shutil.which("node") is None, reason="không có node")
SLTP_JS = ["sltp_math.js", "sltp_panel.js", "sltp.js", "panel.js", "practice.js"]
MATH = ["position_math.js", "sltp_math.js"]


def slt(expr):
    return run_js(MATH, f"return {expr};")


@pytest.mark.parametrize("name", SLTP_JS)
def test_file_ngan_va_dung_cu_phap(name):
    text = (JS / name).read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 150, f"{name} quá 150 dòng"
    if shutil.which("node"):
        res = subprocess.run(["node", "--check", str(JS / name)], capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, res.stderr


@need_node
def test_check_long_short():
    assert slt('SLT.check("long", 90, 110, 100)') == {"err": None, "cross": False}
    assert slt('SLT.check("long", 90, 110, 85)')["cross"] is True     # giá đã dưới SL
    assert slt('SLT.check("long", 90, 110, 115)')["cross"] is True    # giá đã trên TP
    assert slt('SLT.check("long", 110, 90, 100)')["err"]              # sai thứ tự -> chặn hẳn
    assert slt('SLT.check("long", 0, 110, 100)')["err"]
    assert slt('SLT.check("short", 110, 90, 100)') == {"err": None, "cross": False}
    assert slt('SLT.check("short", 110, 90, 112)')["cross"] is True
    assert slt('SLT.check("short", 90, 110, 100)')["err"]
    assert slt('SLT.check("long", 90, 110, 0)')["cross"] is False     # chưa có giá -> không báo vượt


@need_node
def test_levels_mac_dinh_va_muc_dang_co():
    lv = slt('SLT.levels("long", 100, 101, 2, 0.01, null)')
    assert lv["entry"] == 100 and lv["sl"] < 100 < lv["tp"]
    assert slt('SLT.levels("long", 100, 101, 2, 0.01, {sl: 95, tp: 120})') == {"entry": 100, "sl": 95, "tp": 120}
    part = slt('SLT.levels("long", 100, 101, 2, 0.01, {sl: 95, tp: null})')
    assert part["sl"] == 95 and part["tp"] > 101
    # giá đã chạy xa giá vốn -> mặc định tính quanh giá hiện tại, không bị vượt qua
    far = slt('SLT.levels("long", 100, 150, 2, 0.01, null)')
    assert far["sl"] < 150 < far["tp"]
    sh = slt('SLT.levels("short", 100, 99, 2, 0.01, null)')
    assert sh["tp"] < 99 < sh["sl"]


@need_node
def test_status_text():
    base = '{enabled: true, running: true, interval: 3, now: 1000, last_check: 995, entries: [{}]}'
    ok = slt(f"SLT.statusText({base})")
    assert "●" in ok["text"] and "5s" in ok["text"] and "1 mã" in ok["text"] and ok["cls"] == "up"
    assert slt(f"SLT.statusText({{...{base}, enabled: false}})")["cls"] == "err"
    assert "TẮT" in slt(f"SLT.statusText({{...{base}, enabled: false}})")["text"]
    assert "chưa chạy" in slt(f"SLT.statusText({{...{base}, running: false}})")["text"]
    assert slt(f"SLT.statusText({{...{base}, last_check: 900}})")["cls"] == "err"     # quá lâu không kiểm
    err = slt(f'SLT.statusText({{...{base}, last_error: "mạng", last_error_at: 990}})')
    assert "lỗi: mạng" in err["text"] and err["cls"] == "err"
    assert slt("SLT.statusText(null)")["cls"] == "err"
    assert slt("[SLT.ago(12), SLT.ago(600), SLT.ago(7200), SLT.ago(-1)]") == ["12s", "10 phút", "2 giờ", "-"]


@need_node
def test_keo_box_vi_the_tu_do():
    # vị thế: SL kéo qua được giá vốn (chỉ cần SL < TP); box nháp vẫn bị giá vào chặn
    lv = '{entry: 100, sl: 95, tp: 110, side: "long"}'
    assert slt(f'PM.drag({lv}, "sl", 103, 0.01, 0, true).sl') == 103
    assert slt(f'PM.drag({lv}, "sl", 103, 0.01, 0, false).sl') == 99.99
    assert slt(f'PM.drag({lv}, "sl", 112, 0.01, 0, true).sl') == 109.99
    assert slt('PM.valid({entry: 100, sl: 103, tp: 110, side: "long"}, true)') is True
    assert slt('PM.valid({entry: 100, sl: 103, tp: 110, side: "long"})') is False
    assert slt('PM.valid({entry: 100, sl: 112, tp: 110, side: "long"}, true)') is False


@need_node
def test_cot_panel_va_vi_the():
    code = """
      globalThis.fmt = { num: (x) => String(x) }; globalThis.esc = (s) => String(s);
      SLTP.levels = { AAPL: { sl: 90, tp: 120, source: "sàn" }, "BTC/USD": { sl: 1, tp: 2, source: "server", triggering: true } };
      globalThis.PANEL = { positions: [{ symbol: "AAPL", qty: -3, avg_entry_price: 100 }] };
      globalThis.PR = { snap: { symbol: "SPY", position: { qty: 2, avg_price: 50 },
                                orders: [{ role: "sl", price: 45 }, { role: "tp", price: 60 }] } };
      return [sltpCells("AAPL"), sltpCells("BTC/USD"), sltpCells("MSFT"), sltpPos("real", "AAPL"), sltpPos("real", "MSFT"),
              sltpPos("practice", "SPY"), sltpExisting("practice", "SPY"), sltpExisting("real", "AAPL")];
    """
    a, btc, none, pr, miss, pp, pex, rex = run_js(["sltp_math.js", "sltp_panel.js", "sltp.js"], code)
    assert "90" in a and "120" in a and "sàn" in a
    assert "⏳" in btc and "server" in btc
    assert none.count("-") == 2
    assert pr == {"qty": -3, "avg": 100} and miss is None
    assert pp == {"qty": 2, "avg": 50} and pex == {"sl": 45, "tp": 60}
    assert rex["sl"] == 90


def test_trang_chu_nap_script_sltp(tmp_path):
    client = TestClient(create_app(trading_client_fn=lambda: None, db_path=tmp_path / "x.db", bars_dir=tmp_path,
                                   start_sltp_monitor=False))
    html = client.get("/").text
    order = [html.index(f"/static/js/{n}") for n in ["position_sync.js", "sltp_math.js", "sltp_panel.js", "sltp.js", "main.js"]]
    assert order == sorted(order)
    for i in ["sltp-status", "sltp-toggle", "sltp-edit", "sltp-sl", "sltp-tp", "sltp-apply", "sltp-remove"]:
        assert f'id="{i}"' in html, i
    assert "data-sltp" in (JS / "panel.js").read_text(encoding="utf-8")
    assert "data-psltp" in (JS / "practice.js").read_text(encoding="utf-8")
