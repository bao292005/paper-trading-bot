// Tab "Báo cáo": bảng chỉ số kiểu MT5, chia 2 cột (nhãn | giá trị | nhãn | giá trị).
const money = (v) => v === null || v === undefined ? "—" : fmt.num(v, 2);
const pct = (v) => v === null || v === undefined ? "—" : (v * 100).toFixed(2) + "%";
const num2 = (v) => v === null || v === undefined ? "—" : fmt.num(v, 2);

// [nhãn, khoá, định dạng, khoá phụ]
const ROWS = [
  [["Initial deposit", "initial_deposit", money], ["Total net profit", "total_net_profit", money]],
  [["Gross profit", "gross_profit", money], ["Gross loss", "gross_loss", money]],
  [["Profit factor", "profit_factor", num2], ["Expected payoff", "expected_payoff", money]],
  [["Recovery factor", "recovery_factor", num2], ["Sharpe ratio (năm hoá, theo ngày)", "sharpe_ratio", num2]],
  [["Balance drawdown absolute", "balance_drawdown_absolute", money], ["Equity drawdown absolute", "equity_drawdown_absolute", money]],
  [["Balance drawdown maximal", "balance_drawdown_maximal", money, "balance_drawdown_maximal_pct"],
   ["Equity drawdown maximal", "equity_drawdown_maximal", money, "equity_drawdown_maximal_pct"]],
  [["Balance drawdown relative", "balance_drawdown_relative_pct", pct, "balance_drawdown_relative"],
   ["Equity drawdown relative", "equity_drawdown_relative_pct", pct, "equity_drawdown_relative"]],
  [["Total trades", "total_trades", String], ["Final balance / equity", "final_balance", money, "final_equity"]],
  [["Short trades (won %)", "short_trades", String, "short_won_pct"], ["Long trades (won %)", "long_trades", String, "long_won_pct"]],
  [["Profit trades (% of total)", "profit_trades", String, "profit_trades_pct"], ["Loss trades (% of total)", "loss_trades", String, "loss_trades_pct"]],
  [["Largest profit trade", "largest_profit_trade", money], ["Largest loss trade", "largest_loss_trade", money]],
  [["Average profit trade", "average_profit_trade", money], ["Average loss trade", "average_loss_trade", money]],
  [["Max consecutive wins ($)", "max_consecutive_wins", String, "max_consecutive_wins_money"],
   ["Max consecutive losses ($)", "max_consecutive_losses", String, "max_consecutive_losses_money"]],
  [["Maximal consecutive profit (count)", "maximal_consecutive_profit", money, "maximal_consecutive_profit_count"],
   ["Maximal consecutive loss (count)", "maximal_consecutive_loss", money, "maximal_consecutive_loss_count"]],
  [["Average consecutive wins", "average_consecutive_wins", num2], ["Average consecutive losses", "average_consecutive_losses", num2]],
  [["Average holding time (ngày)", "average_holding_days", num2], ["Phí tổng / lệnh còn mở cuối kỳ", "total_fees", money, "open_at_end"]],
];

function cell([label, key, f, key2], rep) {
  const v = rep[key];
  let text = f(v);
  if (key2) {  // giá trị phụ trong ngoặc: % hoặc số lượng, tuỳ khoá
    const v2 = rep[key2];
    text += " (" + (key2.endsWith("_pct") || key2.endsWith("won_pct") ? pct(v2) : key2.includes("count") || key2 === "open_at_end" ? v2 : money(v2)) + ")";
  }
  const cls = key.includes("profit") && typeof v === "number" ? fmt.cls(v) : "";
  return `<td class="lbl">${esc(label)}</td><td class="val ${cls}">${esc(text)}</td>`;
}

function renderReport(res) {
  const rep = res.report, c = res.config, fs = res.fill_stats || {};
  const head = `<div class="sum-line"><b>${esc(c.strategy)}</b> · ${esc(c.mode)} · ${esc(c.fill_model)} · ${esc(c.symbols.join(", "))}<br>
    <span class="muted">${esc(rep.period_start)} → ${esc(rep.period_end)} (${rep.bars} ngày) · chạy ${(res.timing.total_s).toFixed(1)}s` +
    (c.fill_model === "minute" ? ` · ngày mơ hồ ${fs.ambiguous ?? 0}, fallback ${fs.fallback ?? 0}` : "") + `</span></div>`;
  const body = ROWS.map((pair) => "<tr>" + pair.map((it) => cell(it, rep)).join("") + "</tr>").join("");
  $("report-box").className = "pad";
  $("report-box").innerHTML = head + `<table class="mt5">${body}</table>` + windowsTable(res.per_window);
}

// walkforward: bảng kết quả từng cửa sổ test (cột lấy từ dữ liệu server)
function windowsTable(rows) {
  if (!rows || !rows.length) return "";
  const cols = Object.keys(rows[0]);
  const fmtv = (v) => typeof v === "number" ? fmt.num(v, 3) : esc(v);
  return `<h4>Các cửa sổ walk-forward</h4><table><tr>${cols.map((k) => `<th>${esc(k)}</th>`).join("")}</tr>` +
    rows.map((r) => "<tr>" + cols.map((k) => `<td>${fmtv(r[k])}</td>`).join("") + "</tr>").join("") + "</table>";
}
