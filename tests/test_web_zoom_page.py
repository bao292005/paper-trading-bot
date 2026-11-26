"""Test trang "/" và "/tester" có đủ nút zoom + nạp đủ script zoom; file JS zoom ngắn gọn và đúng cú pháp."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paper_bot.web.app import create_app

JS = Path(__file__).resolve().parents[1] / "src" / "paper_bot" / "web" / "static" / "js"
ZOOM_FILES = ["zoom_math.js", "pricescale.js", "zoombox.js", "zoom.js"]
BUTTONS = ["mode", "auto", "out", "in", "reset", "latest", "fit"]


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(trading_client_fn=lambda: None, db_path=tmp_path / "x.db", bars_dir=tmp_path))


@pytest.mark.parametrize("path,prefix,bar", [("/", "z", "zoombar"), ("/tester", "vz", "vz-bar")])
def test_trang_co_nut_zoom_va_script(client, path, prefix, bar):
    html = client.get(path).text
    assert f'id="{bar}"' in html
    for b in BUTTONS:
        assert f'id="{prefix}-{b}"' in html and f'data-z="{b}"' in html, b
    # mỗi nút có tooltip ghi phím tắt (trừ ô chọn chế độ đã có title riêng)
    for key in ("phím -", "phím +", "phím 0", "phím End", "phím A", "phím L"):
        assert key in html, key
    assert "Shift + kéo" in html
    urls = re.findall(r'src="(/static/js/[^"]+)"', html)
    names = [u.rsplit("/", 1)[1] for u in urls]
    for f in ZOOM_FILES:  # zoom_math phải nạp trước các file dùng nó
        assert f in names, f
    assert names.index("zoom_math.js") < names.index("zoom.js")
    for url in urls:
        assert client.get(url).status_code == 200, url


def test_index_nap_zoom_truoc_chart_dung_ZM(client):
    names = [u.rsplit("/", 1)[1] for u in re.findall(r'src="(/static/js/[^"]+)"', client.get("/").text)]
    assert names.index("zoom_math.js") < names.index("chart.js")  # chart.js dùng ZM.PAD khi tạo chart


@pytest.mark.parametrize("name", ZOOM_FILES)
def test_file_zoom_ngan_va_dung_cu_phap(name):
    text = (JS / name).read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 150, f"{name} quá 150 dòng"
    if shutil.which("node"):
        res = subprocess.run(["node", "--check", str(JS / name)], capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, res.stderr
