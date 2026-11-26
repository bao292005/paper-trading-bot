"""Test offline trang /tester ở chế độ crypto: options theo asset, kiểm tra input, config gửi cho run_tester,
lưu DB với qty lẻ. run_tester/run_optimize giả; không gọi mạng, không đặt lệnh."""

import sqlite3
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from paper_bot.web.app import create_app

HOST = "http://127.0.0.1:8600"
GOOD = {"X-Requested-With": "paper-web", "Origin": HOST}
BODY = {"asset": "crypto", "symbols": ["BTC/USD", "ETH/USD"], "strategy": "B2_goc"}
SEEN = []


def fake_result(cfg):
    SEEN.append(cfg)
    return {"config": cfg, "report": {"total_net_profit": 1.0},
            "curve": [{"date": "2022-01-03", "equity": 100000.0, "balance": 100000.0, "cash": 100000.0, "exposure": 0.0},
                      {"date": "2022-01-04", "equity": 100010.0, "balance": 100000.0, "cash": 99000.0, "exposure": 1000.0}],
            "trades": [], "per_window": [],
            "replay": [{"date": "2022-01-03", "equity": 1, "balance": 1, "levels": [], "orders": [],
                        "fills": [{"symbol": "BTC/USD", "side": "buy", "qty": 0.012345, "price": 40000.0, "fee": 1.2}]}]}


def fake_optimize(cfg, grid, criterion="sharpe", n_jobs=None, progress=None, heatmap_params=None):
    SEEN.append(cfg)
    progress(1, 1)
    return {"grid": grid, "criterion": criterion, "rows": [], "note": "overfit"}


@pytest.fixture
def env(tmp_path):
    SEEN.clear()
    db = tmp_path / "t.db"
    app = create_app(db_path=db)
    app.state.run_tester_fn = fake_result
    app.state.run_optimize_fn = fake_optimize
    return SimpleNamespace(app=app, db=db, client=TestClient(app, base_url=HOST))


def post(env, path, body):
    return env.client.post(path, json=body, headers=GOOD)


def poll(client, job_id):
    for _ in range(250):
        j = client.get(f"/api/tester/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.02)
    raise AssertionError("job không xong")


def test_options_crypto(env):
    o = env.client.get("/api/tester/options", params={"asset": "crypto"}).json()
    assert o["universe"] == ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD"] and o["benchmark"] == "BTC/USD"
    assert (o["min_date"], o["max_date"]) == ("2021-01-01", "2024-12-31")
    assert (o["default_from"], o["default_to"], o["default_cost_bps"]) == ("2022-01-01", "2024-12-31", 25.0)
    assert o["is_period"] == ["2022-01-01", "2023-12-31"] and o["oos_period"] == ["2024-01-01", "2024-12-31"]


def test_options_stock_giu_nguyen(env):
    o = env.client.get("/api/tester/options").json()
    assert "AAPL" in o["universe"] and o["min_date"] == "2019-01-01" and o["default_cost_bps"] == 10.0
    assert o["is_period"][0] == "2019-01-01" and o["oos_period"][1] == "2024-12-31"
    assert env.client.get("/api/tester/options", params={"asset": "forex"}).status_code == 400


def test_run_crypto_mac_dinh_dev_va_phi_25(env):
    j = poll(env.client, post(env, "/api/tester/run", BODY).json()["job_id"])
    assert j["status"] == "done"
    cfg = SEEN[-1]
    assert cfg["asset"] == "crypto" and (cfg["from"], cfg["to"], cfg["cost_bps"]) == ("2022-01-01", "2024-12-31", 25.0)
    assert cfg["symbols"] == ["BTC/USD", "ETH/USD"]


def test_run_stock_khong_co_khoa_asset(env):
    body = {"symbols": ["AAPL"], "strategy": "B2_goc", "from": "2019-01-01", "to": "2019-06-30"}
    poll(env.client, post(env, "/api/tester/run", body).json()["job_id"])
    assert "asset" not in SEEN[-1] and SEEN[-1]["cost_bps"] == 10.0
    poll(env.client, post(env, "/api/tester/run", {**body, "asset": "stock"}).json()["job_id"])
    assert "asset" not in SEEN[-1]


@pytest.mark.parametrize("patch", [
    {"asset": "forex"}, {"symbols": ["AAPL"]}, {"symbols": ["BTCUSD"]}, {"symbols": ["DOGE/USD"]},
    {"symbols": ["BTC/USD", "AAPL"]}, {"from": "2020-12-31"}, {"to": "2025-01-01"},
    {"from": "2024-12-20", "to": "2024-12-31"}, {"cost_bps": 500},
])
def test_crypto_input_xau_bi_chan(env, patch):
    r = post(env, "/api/tester/run", {**BODY, **patch})
    assert 400 <= r.status_code < 500, patch
    assert not env.app.state.jobs.jobs


def test_stock_khong_nhan_ma_crypto(env):
    r = post(env, "/api/tester/run", {"symbols": ["BTC/USD"], "from": "2019-01-01", "to": "2019-06-30"})
    assert r.status_code == 400


def test_crypto_cho_tu_2021(env):
    r = post(env, "/api/tester/run", {**BODY, "from": "2021-01-01", "to": "2021-06-30"})
    assert r.status_code == 200
    assert poll(env.client, r.json()["job_id"])["status"] == "done" and SEEN[-1]["from"] == "2021-01-01"


def test_optimize_crypto_khong_gui_ngay_va_giu_asset(env):
    body = {**BODY, "grid": {"ma_short": [5, 10]}, "criterion": "sharpe"}
    j = poll(env.client, post(env, "/api/tester/optimize", body).json()["job_id"])
    assert j["status"] == "done"
    assert SEEN[-1]["asset"] == "crypto" and "from" not in SEEN[-1] and SEEN[-1]["cost_bps"] == 25.0


def test_save_crypto_qty_le(env):
    j = poll(env.client, post(env, "/api/tester/run", BODY).json()["job_id"])
    r = post(env, "/api/tester/save", {"job_id": j["id"], "name": "crypto-thu"})
    assert r.status_code == 200 and r.json()["run_id"] == "bt-tester-crypto-thu"
    con = sqlite3.connect(env.db)
    qty, sym = con.execute("SELECT qty, symbol FROM fills WHERE run_id='bt-tester-crypto-thu'").fetchone()
    con.close()
    assert sym == "BTC/USD" and abs(qty - 0.012345) < 1e-9


def test_trang_tester_co_o_chon_asset(env):
    html = env.client.get("/tester").text
    assert 'id="s-asset"' in html and 'id="o-periods"' in html
