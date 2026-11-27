// Tab "Thống kê" của tài khoản luyện tập: chỉ số kiểu MT5 + equity nhỏ + bảng lệnh + danh sách phiên cũ (DB).
const PRS = { chart: null, line: null, viewing: null };

const PRS_ROWS = [
  ["Lãi ròng", "total_net_profit", "usd"], ["Profit factor", "profit_factor", "num"],
  ["Gross profit", "gross_profit", "usd"], ["Gross loss", "gross_loss", "usd"],
  ["Expected payoff", "expected_payoff", "usd"], ["Recovery factor", "recovery_factor", "num"],
  ["Sharpe (năm hoá)", "sharpe_ratio", "num"], ["Max DD equity", "equity_drawdown_maximal", "usd", "equity_drawdown_maximal_pct"],
  ["Số lệnh", "total_trades", "int"], ["Thắng", "profit_trades", "int", "profit_trades_pct"],
  ["Thua", "loss_trades", "int", "loss_trades_pct"], ["Lệnh lãi lớn nhất", "largest_profit_trade", "usd"],
  ["Lệnh lỗ lớn nhất", "largest_loss_trade", "usd"], ["Chuỗi thắng dài nhất", "max_consecutive_wins", "int"],
  ["Chuỗi thua dài nhất", "max_consecutive_losses", "int"],
];

function prsVal(v, kind) {
  if (v === null || v === undefined) return "—";
  if (kind === "usd") return fmt.usd(v);
  if (kind === "int") return String(v);
  return fmt.num(v);
}

// Chuỗi equity tăng dần theo time, trùng time giữ điểm sau cùng (yêu cầu của Lightweight Charts)
function prsEquity(points) {
  const m = new Map();
  for (const p of points || []) if (p && Number.isFinite(p.value)) m.set(p.time, p.value);
  return [...m.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time, value }));
}

function prsEnsureChart() {
  if (PRS.chart) return;
  const LW = LightweightCharts;
  PRS.chart = LW.createChart(document.getElementById("pr-eq-chart"), {
    autoSize: true,
    layout: { background: { type: LW.ColorType.Solid, color: "#241d12" }, textColor: "#d1d4dc" },
    grid: { vertLines: { visible: false }, horzLines: { color: "#2a2e39" } },
    rightPriceScale: { borderColor: "#2a2e39" },
    timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: false },
    localization: { timeFormatter: (s) => nyDay(s) + " " + nyClock(s) },
    handleScroll: false, handleScale: false,
  });
  PRS.line = PRS.chart.addSeries(LW.AreaSeries, { lineColor: "#ff9800", topColor: "rgba(255,152,0,.35)",
    bottomColor: "rgba(255,152,0,0)", lineWidth: 2, priceLineVisible: false });
}

function prsTrades(trades) {
  if (!trades || !trades.length) return "Chưa có lệnh đóng";
  const rows = trades.map((t) =>
    `<tr><td>${nyDay(t.entry_time)} ${nyClock(t.entry_time)}</td><td class="num">${fmt.qty(t.qty)}</td>` +
    `<td class="num">${fmt.num(t.entry_price)}</td><td class="num">${fmt.num(t.exit_price)}</td>` +
    `<td class="small muted">${esc(t.exit_reason || "")}</td><td class="num ${fmt.cls(t.pnl)}">${fmt.num(t.pnl)}</td></tr>`).join("");
  return `<table><tr><th>Vào</th><th class="num">Qty</th><th class="num">Giá vào</th><th class="num">Giá ra</th>` +
         `<th>Lý do</th><th class="num">P/L</th></tr>${rows}</table>`;
}

// data: {report, equity [{time,value}], trades}; title: nhãn hiển thị
function practiceShowStats(data, title) {
  const r = (data && data.report) || {};
  document.getElementById("pr-stats-title").textContent = title || "";
  document.getElementById("pr-report").innerHTML = PRS_ROWS.map(([label, key, kind, pct]) => {
    const extra = pct && r[pct] !== null && r[pct] !== undefined ? ` (${fmt.num(r[pct])}%)` : "";
    const cls = kind === "usd" ? fmt.cls(r[key] || 0) : "";
    return prKv(label, prsVal(r[key], kind) + extra, cls);
  }).join("") + ((data && data.notes && data.notes.length) ? `<div class="small muted">${esc(data.notes.join(" · "))}</div>` : "");
  prsEnsureChart();
  const eq = prsEquity(data && data.equity);
  PRS.line.setData(eq);
  if (eq.length) PRS.chart.timeScale().fitContent();
  document.getElementById("pr-trades").innerHTML = prsTrades(data && data.trades);
}

function prsTabOpen() { return document.getElementById("side").dataset.tab === "stats"; }

// Phiên đang chạy: cập nhật thống kê khi tab Thống kê đang mở (force: vừa mở tab)
async function practiceStatsLive(force) {
  if (!PR.sid || !prsTabOpen()) return;
  if (PRS.viewing && !force) return;  // đang xem phiên cũ -> không ghi đè
  PRS.viewing = null;
  try {
    const d = await getJSON(`/api/practice/${PR.sid}/stats`);
    practiceShowStats(d, `Phiên đang chạy ${PR.sid}`);
  } catch (e) { practiceMsg("Thống kê lỗi: " + e.message, true); }
}

async function practiceLoadHistory() {
  const el = document.getElementById("pr-history");
  try {
    const d = await getJSON("/api/practice/history");
    if (!d.sessions.length) { el.textContent = "Chưa có phiên nào được lưu"; return; }
    el.innerHTML = `<table><tr><th>Phiên</th><th>Mã</th><th class="num">Lệnh</th><th class="num">Lãi ròng</th></tr>` +
      d.sessions.map((s) => `<tr class="clickable" data-prun="${esc(s.run_id)}"><td class="small">${esc(nyDay(s.start || 0))}</td>` +
        `<td>${esc(s.symbol || "")}</td><td class="num">${s.trades ?? "—"}</td>` +
        `<td class="num ${fmt.cls(s.net_profit || 0)}">${prsVal(s.net_profit, "usd")}</td></tr>`).join("") + "</table>";
  } catch (e) { el.textContent = "Lỗi tải danh sách: " + e.message; }
}

async function practiceOpenHistory(runId) {
  try {
    const d = await getJSON(`/api/practice/history/${encodeURIComponent(runId)}`);
    PRS.viewing = runId;
    practiceShowStats(d, `${runId} · ${d.symbol || ""} ${nyDay(d.start || 0)} → ${nyDay(d.end || 0)}`);
  } catch (e) { practiceMsg("Không mở được phiên: " + e.message, true); }
}

function initPracticeStats() {
  document.getElementById("pr-history").addEventListener("click", (ev) => {
    const tr = ev.target.closest("tr[data-prun]");
    if (tr) practiceOpenHistory(tr.dataset.prun);
  });
  practiceLoadHistory();
}
