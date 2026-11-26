"""Test offline API /api/tester: run_tester / run_optimize giả cho nhanh. Không đặt lệnh, không gọi mạng."""

import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from paper_bot.web.app import create_app
from paper_bot.web.jobs import JobManager

HOST = "http://127.0.0.1:8600"
GOOD = {"X-Requested-With": "paper-web", "Origin": HOST}
BODY = {"symbols": ["AAPL", "MSFT"], "strategy": "B2_goc", "from": "2019-01-01", "to": "2019-06-30"}


def fake_result(cfg):
    return {"config": cfg, "report": {"total_net_profit": 12.5},
            "curve": [{"date": "2019-01-02", "equity": 100000.0, "balance": 100000.0, "cash": 100000.0, "exposure": 0.0},
                      {"date": "2019-01-03", "equity": 100050.0, "balance": 100000.0, "cash": 99000.0, "exposure": 1000.0}],
            "trades": [], "per_window": [],
            "replay": [{"date": "2019-01-02", "equity": 1, "balance": 1, "levels": [], "orders": [],
                        "fills": [{"symbol": "AAPL", "side": "buy", "qty": 5, "price": 150.0, "fee": 0.1, "reason": "entry"}]},
                       {"date": "2019-01-03", "equity": 1, "balance": 1, "levels": [], "orders": [], "fills": []}]}


def fake_optimize(cfg, grid, criterion="sharpe", n_jobs=None, progress=None, heatmap_params=None):
    for i in range(1, 4):
        progress(i, 3)
    return {"grid": grid, "criterion": criterion, "rows": [], "note": "overfit"}


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "t.db"
    app = create_app(db_path=db)
    app.state.run_tester_fn = lambda cfg: fake_result(cfg)
    app.state.run_optimize_fn = fake_optimize
    return SimpleNamespace(app=app, db=db, client=TestClient(app, base_url=HOST))


def poll(client, job_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/tester/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.02)
    raise AssertionError("job không xong")


def post(env, path, body, headers=GOOD):
    return env.client.post(path, json=body, headers=headers)


def test_run_job_poll_result(env):
    r = post(env, "/api/tester/run", BODY)
    assert r.status_code == 200
    j = poll(env.client, r.json()["job_id"])
    assert j["status"] == "done" and j["result"]["report"]["total_net_profit"] == 12.5
    assert j["result"]["config"]["symbols"] == ["AAPL", "MSFT"]


def test_optimize_job_progress(env):
    body = {**BODY, "grid": {"ma_short": [5, 10], "risk.risk_per_trade": [0.01, 0.02]}, "criterion": "sharpe"}
    j = poll(env.client, post(env, "/api/tester/optimize", body).json()["job_id"])
    assert j["status"] == "done" and j["progress"] == {"done": 3, "total": 3}


def test_job_error_reported(env):
    def boom(cfg):
        raise ValueError("không có dữ liệu cho: ['AAPL']")
    env.app.state.run_tester_fn = boom
    j = poll(env.client, post(env, "/api/tester/run", BODY).json()["job_id"])
    assert j["status"] == "error" and "không có dữ liệu" in j["error"]


def test_options_and_unknown_job(env):
    o = env.client.get("/api/tester/options").json()
    assert "AAPL" in o["universe"] and o["max_date"] == "2024-12-31" and o["max_combos"] == 500
    assert env.client.get("/api/tester/jobs/abcdef123456").status_code == 404
    assert env.client.get("/api/tester/jobs/../../etc").status_code in (404, 405)


@pytest.mark.parametrize("patch", [
    {"symbols": []}, {"symbols": ["ZZZZ"]}, {"symbols": "AAPL"}, {"symbols": [f"S{i}" for i in range(21)]},
    {"strategy": "X"}, {"mode": "x"}, {"fill_model": "tick"}, {"from": "2019-13-01"}, {"from": "abc"},
    {"from": "2018-01-01"}, {"from": "2019-07-01"}, {"deposit": -5}, {"deposit": "1000"}, {"cost_bps": 1e9},
    {"deposit": True}, {"strategy_params": {"ma_short": 40, "ma_long": 30}},
    {"strategy_params": {"ma_short": 1.5}}, {"strategy_params": {"evil": 1}}, {"risk": {"max_positions": 3}},
    {"risk": {"atr_stop_mult": 99}}, {"strategy": "FULL", "flags": {"use_x": True}},
    {"strategy": "FULL", "flags": {"use_regime": "yes"}},
])
def test_bad_input_4xx(env, patch):
    r = post(env, "/api/tester/run", {**BODY, **patch})
    assert 400 <= r.status_code < 500, patch
    assert not env.app.state.jobs.jobs  # không tạo job


def test_holdout_date_400_with_message(env):
    r = post(env, "/api/tester/run", {**BODY, "to": "2025-01-01"})
    assert r.status_code == 400 and "HOLDOUT" in r.json()["detail"]


def test_grid_limits(env):
    big = {"ma_short": list(range(2, 12)), "ma_long": list(range(20, 40)), "rsi_period": list(range(2, 5))}  # 600
    r = post(env, "/api/tester/optimize", {**BODY, "grid": big})
    assert r.status_code == 400 and "500" in r.json()["detail"]
    for grid in ({}, {"ma_short": []}, {"x": [1]}, {"ma_short": [5], "ma_long": [20], "rsi_period": [5],
                                                    "rsi_oversold": [30]}, {"risk.atr_stop_mult": [1]}):
        assert post(env, "/api/tester/optimize", {**BODY, "grid": grid}).status_code == 400
    assert post(env, "/api/tester/optimize", {**BODY, "grid": {"ma_short": [5]}, "criterion": "x"}).status_code == 400


def test_origin_and_header_guard(env):
    assert post(env, "/api/tester/run", BODY, {**GOOD, "Origin": "http://evil.com"}).status_code == 403
    assert post(env, "/api/tester/run", BODY, {"Origin": HOST}).status_code == 403
    assert env.client.post("/api/tester/run", content="{}", headers={**GOOD, "Content-Type": "text/plain"}).status_code == 415
    assert not env.app.state.jobs.jobs


def test_b2_ignores_full_only_risk(env):
    j = poll(env.client, post(env, "/api/tester/run", {**BODY, "risk": {"atr_stop_mult": 3, "risk_per_trade": 0.01}}).json()["job_id"])
    assert j["result"]["config"]["risk"] == {"risk_per_trade": 0.01}


def test_max_two_jobs(env):
    gate = threading.Event()
    env.app.state.run_tester_fn = lambda cfg: (gate.wait(5), fake_result(cfg))[1]
    ids = [post(env, "/api/tester/run", BODY) for _ in range(3)]
    assert [r.status_code for r in ids] == [200, 200, 429]
    gate.set()
    poll(env.client, ids[0].json()["job_id"])


def test_job_ttl_cleanup():
    now = [1000.0]
    jm = JobManager(ttl=3600, clock=lambda: now[0])
    jid = jm.submit("run", lambda p: {"ok": 1})
    for _ in range(100):
        if jm.get(jid).status == "done":
            break
        time.sleep(0.01)
    now[0] += 3601
    assert jm.get(jid) is None


def test_save_to_db(env):
    jid = post(env, "/api/tester/run", BODY).json()["job_id"]
    poll(env.client, jid)
    assert post(env, "/api/tester/save", {"job_id": jid, "name": "Bad Name"}).status_code == 400
    assert post(env, "/api/tester/save", {"job_id": jid, "name": "../x"}).status_code == 400
    assert post(env, "/api/tester/save", {"job_id": "nope", "name": "ok"}).status_code == 400
    r = post(env, "/api/tester/save", {"job_id": jid, "name": "demo-1"})
    assert r.status_code == 200 and r.json()["run_id"] == "bt-tester-demo-1"
    conn = sqlite3.connect(env.db)
    assert conn.execute("SELECT COUNT(*) FROM equity WHERE run_id='bt-tester-demo-1'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM fills WHERE run_id='bt-tester-demo-1'").fetchone()[0] == 1
    conn.close()


def test_nothing_written_without_save(env):
    poll(env.client, post(env, "/api/tester/run", BODY).json()["job_id"])
    assert not env.db.exists()  # chạy tester không tạo/ghi DB
