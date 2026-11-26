"""Test ô chọn múi giờ hiển thị (chart.js): mặc định giờ VN, đổi múi giờ đổi nhãn, nhớ lựa chọn."""

import shutil

import pytest
from fastapi.testclient import TestClient

from paper_bot.web.app import create_app

from test_web_zoom import run_js  # pytest chạy kiểu rootdir/tests trong sys.path (như các test khác)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="không có node")

# localStorage giả gắn vào context trước khi nạp chart.js (file phụ tạo trong test)
STORE = "globalThis.__s = {}; globalThis.localStorage = { getItem: (k) => (k in __s ? __s[k] : null), setItem: (k, v) => { __s[k] = String(v); } };"
T = 1795714500  # 2026-11-26 17:35:00 UTC


@pytest.fixture
def setup_js(tmp_path):
    p = tmp_path / "store.js"
    p.write_text(STORE, encoding="utf-8")
    return str(p)


def test_mac_dinh_gio_vn_va_doi_mui_gio(setup_js):
    out = run_js([setup_js, "chart.js"], f"""
        const vn = [CH.tz, nyClock({T}), nyDay({T})];
        setChartTz("UTC"); const utc = [CH.tz, nyClock({T})];
        setChartTz("America/New_York"); const ny = [CH.tz, nyClock({T})];
        setChartTz("Mars/Olympus"); const bad = CH.tz;
        return {{vn, utc, ny, bad, saved: localStorage.getItem("ptb.chartTz")}};""")
    assert out["vn"] == ["Asia/Ho_Chi_Minh", "00:35", "2026-11-27"]  # 17:35 UTC = 00:35 sáng hôm sau ở VN
    assert out["utc"] == ["UTC", "17:35"]
    assert out["ny"] == ["America/New_York", "12:35"]
    assert out["bad"] == "Asia/Ho_Chi_Minh"  # giá trị lạ -> mặc định
    assert out["saved"] == "Asia/Ho_Chi_Minh"


def test_nho_lua_chon_khi_tai_lai(tmp_path):
    # localStorage đã lưu "UTC" từ lần trước -> nạp chart.js phải dùng UTC
    p = tmp_path / "store_utc.js"
    p.write_text(STORE + 'localStorage.setItem("ptb.chartTz", "UTC");', encoding="utf-8")
    assert run_js([str(p), "chart.js"], "return CH.tz;") == "UTC"


def test_trang_chu_co_o_chon_mui_gio():
    html = TestClient(create_app()).get("/").text
    assert 'id="tz-select"' in html
