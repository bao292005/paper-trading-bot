"""Test trang /tester: 200, file tham chiếu tải được, mọi id mà JS dùng đều có trong HTML, link qua lại với "/"."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paper_bot.web.app import create_app

STATIC_JS = Path(__file__).resolve().parents[1] / "src" / "paper_bot" / "web" / "static" / "js"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(trading_client_fn=lambda: None, db_path=tmp_path / "x.db", bars_dir=tmp_path))


def test_tester_page_200_and_files_load(client):
    res = client.get("/tester")
    assert res.status_code == 200 and "text/html" in res.headers["content-type"]
    urls = re.findall(r'(?:src|href)="(/static/[^"]+)"', res.text)
    assert any(u.endswith("tester_main.js") for u in urls) and any(u.endswith("tester.css") for u in urls)
    for url in urls:
        assert client.get(url).status_code == 200, url


def test_links_between_pages(client):
    assert 'href="/tester"' in client.get("/").text
    assert 'href="/"' in client.get("/tester").text


def test_js_files_short_and_ids_exist(client):
    html = client.get("/tester").text
    html_ids = set(re.findall(r'id="([^"]+)"', html))
    js_files = sorted(STATIC_JS.glob("tester*.js"))
    assert js_files
    for f in js_files:
        text = f.read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 150, f"{f.name} quá 150 dòng"
        for el_id in re.findall(r"\$\(\"([a-z0-9-]+)\"\)", text):
            assert el_id in html_ids or f.name == "tester_form.js", (f.name, el_id)  # id tạo bằng JS: form kiểm tay
    # id do tester_form.js tạo động (fieldId, f-<flag>) không kiểm ở đây; id tĩnh phải có
    form = (STATIC_JS / "tester_form.js").read_text(encoding="utf-8")
    for el_id in re.findall(r"\$\(\"([a-z0-9-]+)\"\)", form):
        assert el_id in html_ids, el_id
