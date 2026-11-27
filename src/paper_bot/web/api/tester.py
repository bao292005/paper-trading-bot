"""API Strategy Tester (kiểu MT5): chỉ đọc dữ liệu + tính toán, KHÔNG đặt lệnh.

  GET  /api/tester/options          danh sách mã, mặc định, giới hạn (cho form)
  POST /api/tester/run              tạo job run_tester      -> {job_id}
  POST /api/tester/optimize         tạo job run_optimize    -> {job_id}
  GET  /api/tester/jobs/{id}        trạng thái, tiến độ, kết quả
  POST /api/tester/save             ghi kết quả 1 job run vào DB với run_id 'bt-tester-<tên>'

POST đi qua guard (Origin/Host localhost + header + JSON). Input kiểm tra chặt ở đây;
sai thì 400 kèm thông báo tiếng Việt. Tester thật nạp lười (import nặng); test gán app.state.run_tester_fn.
"""

import math
import os
import re
from types import SimpleNamespace
from typing import Any, Dict

import pandas as pd
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ...config import (ASSET_CLASSES, BENCHMARK, CRYPTO_BENCHMARK, CRYPTO_UNIVERSE, FEATURE_FLAGS, UNIVERSE_50,
                       asset_profile)
from ..jobs import JobLimitError
from .order_rules import guard

router = APIRouter()

MAX_SYMBOLS, MAX_COMBOS, MAX_GRID_PARAMS = 20, 500, 3
MIN_DATE, MAX_DATE = "2019-01-01", "2024-12-31"
CRYPTO_MIN_DATE = "2021-01-01"  # nến crypto tải từ 2021-01-01 (warmup); dev mặc định từ 2022-01-01
HOLDOUT_MSG = ("Từ 2025-01-01 là dữ liệu HOLDOUT, để dành kiểm tra một lần cuối khi đã khoá cấu hình; "
               "tester không cho dùng. Chọn 'Đến ngày' <= 2024-12-31.")
NAME_RE = re.compile(r"[a-z0-9-]{1,40}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
CRITERIA = ("sharpe", "net_profit", "profit_factor", "recovery_factor")
# tên tham số -> (kiểu, min, max). "risk.x" thuộc mục risk, còn lại thuộc strategy_params
PARAM_SPECS = {
    "ma_short": (int, 2, 200), "ma_long": (int, 3, 400), "rsi_period": (int, 2, 60),
    "rsi_oversold": (float, 1, 49), "rsi_overbought": (float, 51, 99),
    "risk.atr_stop_mult": (float, 0.1, 10), "risk.take_profit_mult": (float, 0.1, 20),
    "risk.risk_per_trade": (float, 0.001, 0.2), "risk.position_pct": (float, 0.01, 1.0),
}
FULL_ONLY_RISK = ("atr_stop_mult", "take_profit_mult")  # B2_goc chỉ có risk_per_trade
STRATEGY_NAMES = ("B2_goc", "FULL", "MA_noSLTP", "MA_RR1")
# risk được dùng theo chiến lược; khoá khác gửi lên (từ form chung) bị bỏ qua sau khi kiểm tra giá trị
RISK_USED = {"B2_goc": {"risk_per_trade"}, "FULL": {"atr_stop_mult", "take_profit_mult", "risk_per_trade"},
             "MA_noSLTP": {"position_pct"}, "MA_RR1": {"position_pct", "atr_stop_mult"}}


def bad(msg: str):
    raise HTTPException(400, msg)


def num(v, name: str, kind, lo, hi):
    """Số hữu hạn trong [lo, hi]; kind=int thì phải là số nguyên. bool/chuỗi bị từ chối."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        bad(f"{name} phải là số")
    if kind is int and v != int(v):
        bad(f"{name} phải là số nguyên")
    if not lo <= v <= hi:
        bad(f"{name} phải trong khoảng {lo}..{hi}")
    return int(v) if kind is int else float(v)


def check_date(v, name: str) -> str:
    if not isinstance(v, str) or not DATE_RE.fullmatch(v):
        bad(f"{name} phải dạng YYYY-MM-DD")
    try:
        pd.Timestamp(v)
    except ValueError:
        bad(f"{name} không phải ngày hợp lệ")
    return v


def check_asset(v) -> str:
    if v not in ASSET_CLASSES:
        bad(f"asset phải thuộc {list(ASSET_CLASSES)}")
    return v


def asset_limits(asset: str) -> dict:
    """Giới hạn/mặc định form theo loại tài sản (cổ phiếu giữ nguyên như trước)."""
    if asset == "crypto":
        p = asset_profile("crypto")
        return {"universe": list(CRYPTO_UNIVERSE), "benchmark": CRYPTO_BENCHMARK, "min_date": CRYPTO_MIN_DATE,
                "default_from": p["dev_start"], "default_to": p["dev_end"], "cost_bps": p["cost_bps"],
                "is_period": list(p["is_period"]), "oos_period": list(p["oos_period"])}
    return {"universe": UNIVERSE_50, "benchmark": BENCHMARK, "min_date": MIN_DATE, "default_from": MIN_DATE,
            "default_to": MAX_DATE, "cost_bps": 10.0, "is_period": ["2019-01-01", "2022-12-31"],
            "oos_period": ["2023-01-01", "2024-12-31"]}


def check_symbols(v, asset: str = "stock") -> list:
    allowed = set(UNIVERSE_50) | {BENCHMARK} if asset == "stock" else set(CRYPTO_UNIVERSE) | {CRYPTO_BENCHMARK}
    if not isinstance(v, list) or not v or not all(isinstance(s, str) for s in v):
        bad("symbols phải là danh sách mã (1..20 mã)")
    out = list(dict.fromkeys(v))
    if len(out) > MAX_SYMBOLS:
        bad(f"tối đa {MAX_SYMBOLS} mã mỗi lần chạy")
    lạ = [s for s in out if s not in allowed]
    if lạ:
        bad(f"mã ngoài danh sách cho phép: {lạ}")
    return out


def check_section(body: dict, key: str) -> dict:
    v = body.get(key) or {}
    if not isinstance(v, dict):
        bad(f"{key} phải là object")
    return v


def build_config(body: Dict[str, Any], with_dates: bool = True) -> dict:
    """body từ trình duyệt -> config cho run_tester. Mọi giá trị đã kiểm tra và ép kiểu."""
    strategy = body.get("strategy", "B2_goc")
    if strategy not in STRATEGY_NAMES:
        bad(f"strategy phải thuộc {list(STRATEGY_NAMES)}")
    asset = check_asset(body.get("asset", "stock"))
    lim = asset_limits(asset)
    cfg: Dict[str, Any] = {"symbols": check_symbols(body.get("symbols"), asset), "strategy": strategy}
    if asset == "crypto":
        cfg["asset"] = "crypto"  # cổ phiếu KHÔNG thêm khoá này: config giữ y hệt trước
    if with_dates:
        start = check_date(body.get("from", lim["default_from"]), "Từ ngày")
        end = check_date(body.get("to", MAX_DATE), "Đến ngày")
        if end > MAX_DATE:
            bad(HOLDOUT_MSG)
        if start < lim["min_date"]:
            bad(f"Từ ngày phải >= {lim['min_date']} (dữ liệu phát triển bắt đầu từ đó)")
        if (pd.Timestamp(end) - pd.Timestamp(start)).days < 20:
            bad("khoảng thời gian phải dài ít nhất 20 ngày")
        cfg["from"], cfg["to"] = start, end
    if body.get("mode", "single") not in ("single", "walkforward"):
        bad("mode phải là single hoặc walkforward")
    if body.get("fill_model", "daily_ohlc") not in ("daily_ohlc", "minute"):
        bad("fill_model phải là daily_ohlc hoặc minute")
    cfg["mode"], cfg["fill_model"] = body.get("mode", "single"), body.get("fill_model", "daily_ohlc")
    cfg["deposit"] = num(body.get("deposit", 100000), "deposit", float, 1000, 10_000_000)
    cfg["cost_bps"] = num(body.get("cost_bps", lim["cost_bps"]), "cost_bps", float, 0, 100)
    cfg["strategy_params"], cfg["risk"], cfg["flags"] = {}, {}, {}
    for key, section in (("strategy_params", cfg["strategy_params"]), ("risk", cfg["risk"])):
        for name, v in check_section(body, key).items():
            full = name if key == "strategy_params" else f"risk.{name}"
            if full not in PARAM_SPECS:
                bad(f"{key}.{name} không được phép")
            val = num(v, full, *PARAM_SPECS[full])
            if key == "risk" and name not in RISK_USED[strategy]:
                continue  # chiến lược này không dùng tham số đó: bỏ qua (sau khi đã kiểm tra giá trị)
            section[name] = val
    sp = cfg["strategy_params"]
    if sp.get("ma_short", 10) >= sp.get("ma_long", 30):
        bad("ma_short phải nhỏ hơn ma_long")
    if strategy == "FULL":
        for name, v in check_section(body, "flags").items():
            if name not in FEATURE_FLAGS or not isinstance(v, bool):
                bad(f"flags.{name} không hợp lệ (cờ: {list(FEATURE_FLAGS)}, giá trị true/false)")
            cfg["flags"][name] = v
    return cfg


def build_grid(body: Dict[str, Any], strategy: str) -> dict:
    """grid {tên: [giá trị]}: 1..3 tham số trong PARAM_SPECS, tổng số tổ hợp <= 500."""
    grid = body.get("grid")
    if not isinstance(grid, dict) or not 1 <= len(grid) <= MAX_GRID_PARAMS:
        bad(f"grid cần 1..{MAX_GRID_PARAMS} tham số")
    out, combos = {}, 1
    for name, vals in grid.items():
        if name not in PARAM_SPECS:
            bad(f"tham số '{name}' không được phép trong grid")
        if name.startswith("risk.") and name.removeprefix("risk.") not in RISK_USED[strategy]:
            bad(f"'{name}' không dùng cho {strategy}")
        if not isinstance(vals, list) or not vals or len(vals) > MAX_COMBOS:
            bad(f"grid['{name}'] phải là danh sách 1..{MAX_COMBOS} giá trị")
        out[name] = list(dict.fromkeys(num(v, name, *PARAM_SPECS[name]) for v in vals))
        combos *= len(out[name])
    if combos > MAX_COMBOS:
        bad(f"{combos} tổ hợp > giới hạn {MAX_COMBOS}; giảm số giá trị hoặc số tham số")
    return out


# ---------- job ----------

def tester_fn(request: Request, name: str):
    """Hàm tester: test gán app.state.<name>_fn; mặc định import bản thật (nạp lười)."""
    fn = getattr(request.app.state, f"{name}_fn", None)
    if fn is None:
        from ...backtest import tester
        fn = getattr(tester, name)
    return fn


def start_job(request: Request, kind: str, work) -> dict:
    try:
        return {"job_id": request.app.state.jobs.submit(kind, work)}
    except JobLimitError as e:
        raise HTTPException(429, str(e)) from None


@router.get("/api/tester/options")
def options(asset: str = "stock"):
    lim = asset_limits(check_asset(asset))
    return {"asset": asset, "universe": lim["universe"], "benchmark": lim["benchmark"], "flags": list(FEATURE_FLAGS),
            "criteria": list(CRITERIA), "max_symbols": MAX_SYMBOLS, "max_combos": MAX_COMBOS,
            "min_date": lim["min_date"], "max_date": MAX_DATE, "holdout_note": HOLDOUT_MSG,
            "default_from": lim["default_from"], "default_to": lim["default_to"], "default_cost_bps": lim["cost_bps"],
            "is_period": lim["is_period"], "oos_period": lim["oos_period"],
            "params": {k: {"min": v[1], "max": v[2], "int": v[0] is int} for k, v in PARAM_SPECS.items()}}


@router.post("/api/tester/run", dependencies=[Depends(guard)])
def run(request: Request, body: Dict[str, Any] = Body(...)):
    cfg = build_config(body)
    fn = tester_fn(request, "run_tester")
    return start_job(request, "run", lambda progress: fn(cfg))


@router.post("/api/tester/optimize", dependencies=[Depends(guard)])
def optimize(request: Request, body: Dict[str, Any] = Body(...)):
    cfg = build_config(body, with_dates=False)
    grid = build_grid(body, cfg["strategy"])
    criterion = body.get("criterion", "sharpe")
    if criterion not in CRITERIA:
        bad(f"criterion phải thuộc {list(CRITERIA)}")
    hp = body.get("heatmap_params")
    if hp is not None and not (isinstance(hp, list) and len(hp) == 2 and all(h in grid for h in hp)):
        bad("heatmap_params phải là 2 tên tham số có trong grid")
    fn = tester_fn(request, "run_optimize")
    n_jobs = max(1, min(4, (os.cpu_count() or 2) - 1))  # chừa CPU cho máy
    return start_job(request, "optimize", lambda progress: fn(
        cfg, grid, criterion=criterion, n_jobs=n_jobs, progress=progress, heatmap_params=hp))


@router.get("/api/tester/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    job = request.app.state.jobs.get(job_id) if re.fullmatch(r"[0-9a-f]{12}", job_id) else None
    if job is None:
        raise HTTPException(404, "không có job này (có thể đã hết hạn sau 1 giờ)")
    return job.to_dict(request.app.state.jobs.clock())


# ---------- lưu vào DB (chỉ khi người dùng bấm) ----------

def to_backtest_like(result: dict):
    """Kết quả run_tester (dict) -> đối tượng có .equity (DataFrame) và .fills như BacktestResult."""
    eq = pd.DataFrame(result["curve"])
    eq.index = pd.to_datetime(eq.pop("date"))
    fills = [SimpleNamespace(order_id=f"{day['date']}-{i}", filled_at=pd.Timestamp(day["date"]),
                             **{k: f[k] for k in ("symbol", "side", "qty", "price", "fee")})
             for day in result["replay"] for i, f in enumerate(day["fills"])]
    return SimpleNamespace(equity=eq[["equity", "cash", "exposure"]], fills=fills)


@router.post("/api/tester/save", dependencies=[Depends(guard)])
def save(request: Request, body: Dict[str, Any] = Body(...)):
    name = body.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        bad("tên chỉ gồm a-z, 0-9 và dấu '-' (tối đa 40 ký tự)")
    job = request.app.state.jobs.get(str(body.get("job_id")))
    if job is None or job.kind != "run" or job.status != "done":
        bad("job không tồn tại, không phải job 'run' hoặc chưa xong")
    from ...backtest.report import save_result
    from ...storage import Store

    store = Store(path=request.app.state.db_path, run_id=f"bt-tester-{name}")
    try:
        save_result(to_backtest_like(job.result), store, metrics=job.result["report"])
    finally:
        store.conn.close()
    return {"ok": True, "run_id": f"bt-tester-{name}"}
