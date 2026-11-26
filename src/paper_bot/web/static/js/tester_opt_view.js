// Tab Optimization (phần hiển thị): bảng xếp hạng IS/OOS, ô "tốt nhất theo IS", heatmap IS | OOS.
const OPT_COLS = [["total_net_profit", "Lãi ròng"], ["total_trades", "Lệnh"], ["equity_drawdown_maximal_pct", "DD %"]];
const paramText = (p) => Object.entries(p).map(([k, v]) => k.replace("risk.", "") + "=" + v).join(", ");

function metricCell(m, key) {
  return `<td>${m ? fmt.num(m[key], key === "total_trades" ? 0 : 2) : "-"}</td>`;
}

function optRow(r, metric) {
  const cls = r.is_rank === 1 ? ' class="opt-best"' : "";
  if (r.error) return `<tr><td>-</td><td>${esc(paramText(r.params))}</td><td colspan="9" class="muted">${esc(r.error)}</td></tr>`;
  return `<tr data-p='${esc(JSON.stringify(r.params))}'${cls}><td>${r.is_rank}</td><td>${esc(paramText(r.params))}</td>` +
    metricCell(r.is, metric) + OPT_COLS.map(([k]) => metricCell(r.is, k)).join("") +
    `<td>${r.oos_rank}</td>` + metricCell(r.oos, metric) + OPT_COLS.map(([k]) => metricCell(r.oos, k)).join("") + "</tr>";
}

function optTable(res) {
  const heads = OPT_COLS.map(([, l]) => l);
  const th = (tag) => `<th>${tag} ${res.metric}</th>` + heads.map((h) => `<th>${tag} ${h}</th>`).join("");
  return `<table><thead><tr><th>Hạng IS</th><th>Tham số</th>${th("IS")}<th>Hạng OOS</th>${th("OOS")}</tr></thead>` +
    `<tbody>${res.rows.map((r) => optRow(r, res.metric)).join("")}</tbody></table>`;
}

function bestBox(res) {
  const b = res.best_is;
  if (!b) return "Không có tổ hợp hợp lệ.";
  return `<b>Tốt nhất theo IS:</b> ${esc(paramText(b.params))} · IS ${res.metric} = ${fmt.num(b.is[res.metric])} · ` +
    `OOS ${res.metric} = ${fmt.num(b.oos[res.metric])} · <b>hạng OOS ${b.oos_rank}/${res.n_combos}</b>`;
}

// Ô màu theo giá trị: đỏ (thấp) -> xanh (cao), thang riêng cho từng bản đồ
function heatGrid(hm, tag, title) {
  const flat = hm[tag].flat().filter((v) => v !== null);
  const lo = Math.min(...flat), hi = Math.max(...flat);
  const color = (v) => v === null ? "#2a2e39" : `hsl(${hi > lo ? 120 * (v - lo) / (hi - lo) : 60}, 55%, 32%)`;
  const cols = hm.x_values.length;
  const cells = hm[tag].map((row, i) => row.map((v, j) =>
    `<div style="background:${color(v)}" title="${hm.x}=${hm.x_values[j]}, ${hm.y}=${hm.y_values[i]}: ${fmt.num(v)}">${fmt.num(v, 2)}</div>`).join("")).join("");
  return `<div><b>${title}</b> <span class="muted small">(${hm.metric}; x = ${hm.x}, y = ${hm.y})</span>` +
    `<div class="heat" style="grid-template-columns: repeat(${cols}, minmax(34px, 1fr))">${cells}</div>` +
    `<div class="small muted">x: ${hm.x_values.join(", ")} · y (từ trên xuống): ${hm.y_values.join(", ")}</div></div>`;
}

function renderOptimize(res) {
  $("o-note").textContent = res.note;
  $("o-best").innerHTML = bestBox(res);
  $("o-best").hidden = false;
  const hm = res.heatmap;
  $("o-heat").innerHTML = hm && hm.is.flat().some((v) => v !== null) ? heatGrid(hm, "is", "IS " + res.is_period.join(" → ")) +
    heatGrid(hm, "oos", "OOS " + res.oos_period.join(" → ")) : "";
  $("o-table").innerHTML = optTable(res);
  $("o-table").onclick = (e) => {
    const tr = e.target.closest("tr[data-p]");
    if (tr) runCombo(JSON.parse(tr.dataset.p));
  };
}
