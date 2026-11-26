"""Tối ưu tham số kiểu MT5: lưới tổ hợp, chạy IS và OOS, xếp hạng theo 1 tiêu chí.

grid: {"ma_short": [5, 10], "risk.atr_stop_mult": [1, 2], ...}
  tên không có tiền tố -> strategy_params; "risk.x" / "flags.x" / "strategy_params.x" -> mục đó.
"""

import itertools
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from ...config import asset_profile
from .run import normalize_config, run_tester

MAX_COMBOS = 500
CRITERIA = {"sharpe": "sharpe_ratio", "net_profit": "total_net_profit",
            "profit_factor": "profit_factor", "recovery_factor": "recovery_factor"}
SECTIONS = ("strategy_params", "risk", "flags")
METRICS = ("sharpe_ratio", "total_net_profit", "profit_factor", "recovery_factor",
           "total_trades", "equity_drawdown_maximal_pct")
IS_PERIOD = ("2019-01-01", "2022-12-31")
OOS_PERIOD = ("2023-01-01", "2024-12-31")
NOTE = ("Chọn tham số trên dữ liệu này rồi đánh giá lại trên cùng dữ liệu là overfit; "
        "kết quả chỉ có giá trị khi kiểm tra trên holdout một lần.")


def expand_grid(grid: dict) -> list:
    """Danh sách tổ hợp (dict tên -> giá trị). >MAX_COMBOS -> ValueError."""
    if not grid:
        raise ValueError("grid rỗng")
    for name, vals in grid.items():
        if not isinstance(vals, (list, tuple)) or not vals:
            raise ValueError(f"grid['{name}'] phải là danh sách khác rỗng")
        if "." in name and name.split(".", 1)[0] not in SECTIONS:
            raise ValueError(f"tiền tố lạ trong '{name}', dùng {SECTIONS}")
    n = 1
    for vals in grid.values():
        n *= len(vals)
    if n > MAX_COMBOS:
        raise ValueError(f"{n} tổ hợp > giới hạn {MAX_COMBOS}")
    names = list(grid)
    return [dict(zip(names, combo)) for combo in itertools.product(*grid.values())]


def apply_params(config: dict, params: dict) -> dict:
    """Ghép 1 tổ hợp vào config (không sửa config gốc)."""
    cfg = {**config}
    for sec in SECTIONS:
        cfg[sec] = dict(config.get(sec) or {})
    for name, v in params.items():
        sec, key = name.split(".", 1) if "." in name else ("strategy_params", name)
        cfg[sec][key] = v
    return cfg


def _one(config, params, is_period, oos_period, bars, forecasts):
    """Chạy 1 tổ hợp trên IS và OOS (hàm cấp module để ProcessPool pickle được)."""
    row = {"params": params}
    try:
        for tag, (a, b) in (("is", is_period), ("oos", oos_period)):
            cfg = {**apply_params(config, params), "from": a, "to": b}
            rep = run_tester(cfg, bars=bars, forecasts=forecasts, detail=False)["report"]
            row[tag] = {k: rep[k] for k in METRICS}
    except ValueError as e:  # tổ hợp không hợp lệ (vd ma_short >= ma_long)
        row["error"] = str(e)
    return row


def _rank(rows, tag, metric):
    """Gán rank 1..n theo metric giảm dần; None/lỗi xếp cuối."""
    def val(r):
        v = r.get(tag, {}).get(metric) if "error" not in r else None
        return (v is None, -(v or 0.0))
    for i, r in enumerate(sorted(rows, key=val), 1):
        r[f"{tag}_rank"] = i


def heatmap(rows, grid, x, y, metric):
    """Ma trận [y][x] của metric (IS và OOS); nếu còn tham số khác thì lấy max."""
    xs, ys = list(grid[x]), list(grid[y])
    out = {"x": x, "y": y, "x_values": xs, "y_values": ys, "metric": metric}
    for tag in ("is", "oos"):
        m = [[None] * len(xs) for _ in ys]
        for r in rows:
            v = r.get(tag, {}).get(metric)
            if v is None:
                continue
            i, j = ys.index(r["params"][y]), xs.index(r["params"][x])
            m[i][j] = v if m[i][j] is None else max(m[i][j], v)
        out[tag] = m
    return out


def run_optimize(config: dict, grid: dict, criterion: str = "sharpe", is_period=None,
                 oos_period=None, n_jobs=None, progress=None, heatmap_params=None,
                 bars=None, forecasts=None) -> dict:
    """Chạy cả lưới. progress(done, total) gọi sau mỗi tổ hợp. Trả dict JSON được.
    is/oos_period None -> theo asset: cổ phiếu IS 2019-2022 / OOS 2023-2024,
    crypto IS 2022-2023 / OOS 2024 (prereg crypto)."""
    if criterion not in CRITERIA:
        raise ValueError(f"criterion phải thuộc {tuple(CRITERIA)}")
    prof = asset_profile(config.get("asset", "stock"))
    is_period = is_period or prof["is_period"] or IS_PERIOD
    oos_period = oos_period or prof["oos_period"] or OOS_PERIOD
    combos = expand_grid(grid)
    for a, b in (is_period, oos_period):  # holdout / khoá lạ -> lỗi ngay, chưa chạy gì
        normalize_config({**apply_params(config, combos[0]), "from": a, "to": b})
    metric = CRITERIA[criterion]
    n_jobs = n_jobs or max(1, (os.cpu_count() or 2) - 1)
    t0 = time.time()
    args = (is_period, oos_period, bars, forecasts)
    rows = []
    if n_jobs == 1:
        for p in combos:
            rows.append(_one(config, p, *args))
            if progress:
                progress(len(rows), len(combos))
    else:
        with ProcessPoolExecutor(max_workers=n_jobs) as ex:
            futs = [ex.submit(_one, config, p, *args) for p in combos]
            for f in as_completed(futs):
                rows.append(f.result())
                if progress:
                    progress(len(rows), len(combos))
    rows.sort(key=lambda r: combos.index(r["params"]))  # thứ tự ổn định theo lưới
    _rank(rows, "is", metric)
    _rank(rows, "oos", metric)
    best = next((r for r in rows if r["is_rank"] == 1 and "error" not in r), None)
    hp = heatmap_params or (list(grid)[:2] if len(grid) >= 2 else None)
    return {
        "config": config, "grid": grid, "criterion": criterion, "metric": metric,
        "is_period": list(is_period), "oos_period": list(oos_period), "n_combos": len(combos),
        "rows": sorted(rows, key=lambda r: r["is_rank"]),
        "best_is": None if best is None else {"params": best["params"], "is": best["is"],
                                              "oos": best["oos"], "oos_rank": best["oos_rank"]},
        "heatmap": heatmap(rows, grid, hp[0], hp[1], metric) if hp else None,
        "note": NOTE, "timing_s": time.time() - t0,
    }
