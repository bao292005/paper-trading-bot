"""Test trang chủ "/": trả 200, có các phần tử chính, mọi file JS/CSS được tham chiếu đều tải được."""

import re

import pytest
from fastapi.testclient import TestClient

from paper_bot.web.app import create_app

MAIN_IDS = ["symbol-input", "tf-buttons", "chk-ma10", "chk-ma30", "chk-ma200", "chk-rsi", "chk-vol",
            "run-select", "live-status", "chart", "legend", "tooltip", "account", "positions", "orders",
            # bước 4: form lệnh, huỷ/modal xác nhận, nút chạy bot
            "order-form", "ord-symbol", "ord-side", "ord-type", "ord-qty", "ord-limit", "ord-sl", "ord-tp", "ord-msg",
            "modal", "modal-input", "modal-ok", "modal-cancel", "bot-symbols", "bot-dry", "bot-real", "bot-output",
            # crypto: chọn loại tài sản, giá stop, ghi chú SL/TP do bot tự quản
            "asset-select", "ord-stop", "ord-note",
            # bar replay + tài khoản luyện tập
            "replay-btn", "replay-bar", "replay-pick", "replay-run", "rp-tz", "rp-date", "rp-go", "rp-cancel",
            "rp-new", "rp-play", "rp-step", "rp-speed", "rp-time", "rp-exit", "rp-holdout", "side", "side-tabs",
            "pr-account", "pr-notes", "pr-position", "pr-orders", "pr-form", "pr-side", "pr-type", "pr-qty",
            "pr-price", "pr-sl", "pr-tp", "pr-submit", "pr-close", "pr-msg",
            "pr-stats-title", "pr-eq-chart", "pr-report", "pr-trades", "pr-history"]


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(trading_client_fn=lambda: None, db_path=tmp_path / "x.db", bars_dir=tmp_path))


def test_trang_chu_200_va_co_id_chinh(client):
    res = client.get("/")
    assert res.status_code == 200 and "text/html" in res.headers["content-type"]
    for el_id in MAIN_IDS:
        assert f'id="{el_id}"' in res.text, el_id
    for tf in ("1m", "5m", "15m", "1h", "1D"):
        assert f'data-tf="{tf}"' in res.text


def test_moi_file_tham_chieu_deu_tai_duoc(client):
    html = client.get("/").text
    urls = re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
    assert any(u.endswith("lightweight-charts.standalone.production.js") for u in urls)
    assert any(u.endswith("/js/main.js") for u in urls)
    for url in urls:
        assert client.get(url).status_code == 200, url


def test_api_runs_khong_co_db_tra_rong(client):
    assert client.get("/api/runs").json() == {"runs": []}
