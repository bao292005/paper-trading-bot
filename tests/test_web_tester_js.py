"""Chạy thử logic JS thuần của /tester bằng node (DOM giả): parseValues, phát lại Visual mode. Bỏ qua nếu không có node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src" / "paper_bot" / "web" / "static" / "js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="không có node")

# Nạp các file JS vào cùng 1 context với DOM tối giản, rồi chạy đoạn `code` (in kết quả JSON ra stdout)
RUNNER = """
const fs = require("fs"), vm = require("vm");
const els = {};
const el = (id) => els[id] || (els[id] = { id, value: "", textContent: "", innerHTML: "", style: {}, hidden: false,
  classList: { toggle() {}, add() {}, remove() {} }, firstElementChild: { style: {} } });
const ctx = { document: { getElementById: el, querySelectorAll: () => [] }, console, setTimeout, clearTimeout, Intl, Date, Math,
  LightweightCharts: {} };
vm.createContext(ctx);
for (const f of process.argv[1].split(",")) vm.runInContext(fs.readFileSync(f, "utf8"), ctx, { filename: f });
const out = vm.runInContext(process.argv[2], ctx);
console.log(JSON.stringify(out));
"""


def run_js(files, code):
    paths = ",".join(str(JS / f) for f in files)
    res = subprocess.run(["node", "-e", RUNNER, paths, code], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


OPT_FILES = ["api.js", "tester_form.js", "tester_job.js", "tester_opt.js"]


def test_parse_values_list_and_range():
    code = """
    TESTER.opts = { max_combos: 500 };
    [parseValues("5, 10,15,10"), parseValues("5:5:25"), parseValues("0.01:0.005:0.02"), parseValues("")]
    """
    assert run_js(OPT_FILES, code) == [[5, 10, 15], [5, 10, 15, 20, 25], [0.01, 0.015, 0.02], []]


@pytest.mark.parametrize("text", ["a,b", "5:0:10", "10:1:5", "1:2", "1,,2", "1:0.0001:1000"])
def test_parse_values_rejects_bad(text):
    code = f"""
    TESTER.opts = {{ max_combos: 500 }};
    (() => {{ try {{ parseValues({json.dumps(text)}); return "ok"; }} catch (e) {{ return "err"; }} }})()
    """
    assert run_js(OPT_FILES, code) == "err"


def test_render_optimize_best_and_heatmap():
    code = """
    const m = (s, n) => ({ sharpe_ratio: s, total_net_profit: n, total_trades: 10, equity_drawdown_maximal_pct: 5,
                           profit_factor: 1, recovery_factor: 1 });
    const rows = [
      { params: { ma_short: 5, ma_long: 20 }, is: m(1.5, 100), oos: m(0.2, 10), is_rank: 1, oos_rank: 2 },
      { params: { ma_short: 10, ma_long: 20 }, is: m(1.0, 80), oos: m(0.9, 50), is_rank: 2, oos_rank: 1 },
      { params: { ma_short: 25, ma_long: 20 }, error: "ma_short >= ma_long", is_rank: 3, oos_rank: 3 }];
    const hm = { x: "ma_short", y: "ma_long", x_values: [5, 10], y_values: [20], metric: "sharpe_ratio",
                 is: [[1.5, 1.0]], oos: [[0.2, 0.9]] };
    renderOptimize({ rows, best_is: { params: rows[0].params, is: rows[0].is, oos: rows[0].oos, oos_rank: 2 }, heatmap: hm,
                     metric: "sharpe_ratio", n_combos: 3, is_period: ["2019-01-01", "2022-12-31"],
                     oos_period: ["2023-01-01", "2024-12-31"], note: "NOTE-OVERFIT" });
    const g = (id) => document.getElementById(id);
    [g("o-best").innerHTML, g("o-heat").innerHTML, g("o-table").innerHTML, g("o-note").textContent]
    """
    best, heat, table, note = run_js(OPT_FILES + ["tester_opt_view.js"], code)
    assert "hạng OOS 2/3" in best and "ma_short=5" in best
    assert heat.count('class="heat"') == 2 and "OOS 2023-01-01" in heat
    assert table.count("opt-best") == 1 and table.count("data-p=") == 2 and "ma_short &gt;= ma_long" in table
    assert note == "NOTE-OVERFIT"


def test_replay_steps_through_days():
    code = """
    const calls = { data: 0, push: 0, lines: [] };
    setVisualData = (bars, markers, eq) => { calls.data++; calls.n = [bars.length, markers.length, eq.length]; };
    pushVisual = (bar, markers, eq) => { calls.push++; calls.m = markers.length; };
    setVisualLines = (items) => { calls.lines = items.map((x) => x.title); };
    const day = (d, fills, levels) => ({ date: d, equity: 100, balance: 100, fills, orders: [], levels });
    const buy = { symbol: "AAPL", side: "buy", qty: 5, price: 10 };
    const days = [day("2020-01-02", [], []), day("2020-01-03", [buy], [{ symbol: "AAPL", entry_price: 10, qty: 5, stop_loss: 9, take_profit: 12 }]),
                  day("2020-01-06", [], [])];
    const bars = days.map((d, i) => ({ time: dayUnix(d.date), open: 1, high: 2, low: 1, close: 2 }));
    setupReplay({ replay: days }, "AAPL", bars);
    const first = [calls.data, calls.n.slice()];
    const adv1 = advanceReplay(), titles1 = calls.lines.slice();
    const adv2 = advanceReplay(), adv3 = advanceReplay();
    [first, adv1, titles1, adv2, adv3, calls.push, calls.m, RP.i]
    """
    files = ["api.js", "tester_form.js", "tester_replay.js"]
    first, adv1, titles1, adv2, adv3, pushes, markers, i = run_js(files, code)
    assert first == [1, [1, 0, 1]]
    assert adv1 is True and titles1 == ["Vào 5", "SL", "TP"]
    assert adv2 is True and adv3 is False  # 3 ngày: bước 1, bước 2 ok, bước 3 hết
    assert pushes == 2 and markers == 1 and i == 2
