"""CLI Strategy Tester (chỉ giai đoạn phát triển, không bao giờ chạm holdout >= 2025-01-01).

Cách chạy:
  uv run python scripts/tester.py run cau-hinh.json [--out tien-to]
  uv run python scripts/tester.py optimize cau-hinh.json [--out tien-to] [--jobs N]

File cấu hình:
  run:      đúng dict config của run_tester (xem tester/run.py), vd
            {"symbols": ["AAPL", "MSFT"], "strategy": "B2_goc", "from": "2019-01-01", "to": "2024-12-31"}
  optimize: {"config": {...}, "grid": {"ma_short": [5, 10], "ma_long": [30, 50]},
             "criterion": "sharpe", "is_period": [..], "oos_period": [..], "heatmap_params": [x, y]}
Kết quả: <tien-to>.json (đầy đủ) + <tien-to>.md (tóm tắt). Mặc định tiền tố = tên file cấu hình + -run/-opt.
"""

import argparse
import json
import sys
from pathlib import Path

from paper_bot.backtest.tester import report_markdown, run_tester
from paper_bot.backtest.tester.optimize import IS_PERIOD, OOS_PERIOD, run_optimize


def fmt(v):
    """Số -> chuỗi gọn cho bảng markdown."""
    if v is None:
        return "—"
    return f"{v:,.3f}" if isinstance(v, float) else str(v)


def run_markdown(out: dict) -> str:
    cfg = out["config"]
    head = (f"# Strategy Tester — {cfg['strategy']} {cfg['from']} → {cfg['to']}\n\n"
            f"Mã: {', '.join(cfg['symbols'])} · mode {cfg['mode']} · fill {cfg['fill_model']}\n\n")
    return head + report_markdown(out["report"]) + f"\n\nfill_stats: `{out['fill_stats']}`\n"


def optimize_markdown(out: dict) -> str:
    m, names = out["metric"], list(out["grid"])
    lines = [f"# Tối ưu tham số — tiêu chí {out['criterion']} ({m})", "",
             f"IS {out['is_period'][0]} → {out['is_period'][1]} · "
             f"OOS {out['oos_period'][0]} → {out['oos_period'][1]} · {out['n_combos']} tổ hợp", "",
             f"> {out['note']}", "",
             "| " + " | ".join(names) + f" | IS {m} | OOS {m} | IS rank | OOS rank | IS trades | OOS trades |",
             "|" + "---|" * (len(names) + 6)]
    for r in out["rows"]:
        p = [fmt(r["params"][n]) for n in names]
        if "error" in r:
            lines.append("| " + " | ".join(p) + f" | lỗi: {r['error']} | | {r['is_rank']} | "
                         f"{r['oos_rank']} | | |")
            continue
        lines.append("| " + " | ".join(p + [fmt(r["is"][m]), fmt(r["oos"][m]), str(r["is_rank"]),
                                             str(r["oos_rank"]), fmt(r["is"]["total_trades"]),
                                             fmt(r["oos"]["total_trades"])]) + " |")
    b = out["best_is"]
    if b:
        lines += ["", f"**Tốt nhất IS:** `{b['params']}` — IS {fmt(b['is'][m])}, OOS {fmt(b['oos'][m])}, "
                      f"xếp hạng OOS {b['oos_rank']}/{out['n_combos']}."]
    hm = out["heatmap"]
    if hm:
        for tag in ("is", "oos"):
            lines += ["", f"Heatmap {tag.upper()} ({m}): hàng = {hm['y']}, cột = {hm['x']}", "",
                      "| | " + " | ".join(fmt(x) for x in hm["x_values"]) + " |",
                      "|" + "---|" * (len(hm["x_values"]) + 1)]
            for y, row in zip(hm["y_values"], hm[tag]):
                lines.append(f"| {fmt(y)} | " + " | ".join(fmt(v) for v in row) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Strategy Tester kiểu MT5")
    ap.add_argument("cmd", choices=["run", "optimize"])
    ap.add_argument("config", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--jobs", type=int, default=None)
    a = ap.parse_args(argv)
    spec = json.loads(a.config.read_text())
    prefix = a.out or a.config.with_name(a.config.stem + ("-run" if a.cmd == "run" else "-opt"))
    if a.cmd == "run":
        out = run_tester(spec)
        md = run_markdown(out)
    else:
        def progress(done, total):
            print(f"\r{done}/{total}", end="", file=sys.stderr, flush=True)
        out = run_optimize(spec["config"], spec["grid"], spec.get("criterion", "sharpe"),
                           tuple(spec.get("is_period", IS_PERIOD)),
                           tuple(spec.get("oos_period", OOS_PERIOD)),
                           n_jobs=a.jobs or spec.get("n_jobs"), progress=progress,
                           heatmap_params=spec.get("heatmap_params"))
        print(file=sys.stderr)
        md = optimize_markdown(out)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{prefix}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    Path(f"{prefix}.md").write_text(md)
    print(md)
    print(f"Đã ghi {prefix}.json và {prefix}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
