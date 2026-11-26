"""Test run_tester THẬT qua API web trên dữ liệu nhỏ (2 mã, 6 tháng 2019, B2). Skip nếu chưa có cache nến."""

import time

import pytest
from fastapi.testclient import TestClient

from paper_bot.data.cache import BARS_DIR
from paper_bot.web.app import create_app

HOST = "http://127.0.0.1:8600"
GOOD = {"X-Requested-With": "paper-web", "Origin": HOST}

pytestmark = pytest.mark.skipif(not (BARS_DIR / "AAPL.parquet").exists() or not (BARS_DIR / "MSFT.parquet").exists(),
                                reason="chưa có cache nến AAPL/MSFT")


def wait_job(client, job_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/tester/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.2)
    raise AssertionError("job quá lâu")


def test_real_run_b2_small(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "t.db"), base_url=HOST)
    body = {"symbols": ["AAPL", "MSFT"], "strategy": "B2_goc", "from": "2019-01-01", "to": "2019-06-30"}
    job = client.post("/api/tester/run", json=body, headers=GOOD).json()["job_id"]
    j = wait_job(client, job)
    assert j["status"] == "done", j["error"]
    res = j["result"]
    assert res["config"]["to"] == "2019-06-30" and res["report"]["total_trades"] is not None
    assert len(res["curve"]) > 100 and len(res["replay"]) == len(res["curve"])
    assert all(t["exit_date"] <= "2019-06-30" for t in res["trades"])  # không rò rỉ sau `to`
    # lưu vào DB khi người dùng bấm
    r = client.post("/api/tester/save", json={"job_id": job, "name": "real-test"}, headers=GOOD)
    assert r.status_code == 200
