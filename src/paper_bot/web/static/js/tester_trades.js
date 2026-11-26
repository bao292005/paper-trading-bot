// Tab "Lệnh": bảng deal sắp xếp được (bấm tiêu đề cột), bấm dòng để mở, xuất CSV.
// [khoá, nhãn, là số?]
const TRADE_COLS = [
  ["id", "#", true], ["entry_date", "Vào (ngày)", false], ["exit_date", "Ra (ngày)", false], ["symbol", "Mã", false],
  ["side", "Mua/Bán", false], ["qty", "Qty", true], ["entry_price", "Giá vào", true], ["exit_price", "Giá ra", true],
  ["stop_loss", "SL", true], ["take_profit", "TP", true], ["exit_reason", "Lý do ra", false],
  ["pnl", "P/L", true], ["fee", "Phí", true],
];
const TR = { rows: [], key: "id", asc: true, onPick: null };
const SIDE_VN = { long: "Mua" };

function setTrades(trades, onPick) {
  TR.rows = trades;
  TR.onPick = onPick;
  renderTrades();
}

function sortedTrades() {
  const k = TR.key, dir = TR.asc ? 1 : -1;
  return [...TR.rows].sort((a, b) => {
    const x = a[k], y = b[k];
    if (x === y) return 0;
    if (x === null || x === undefined) return 1;  // ô trống luôn xuống cuối
    if (y === null || y === undefined) return -1;
    return (x < y ? -1 : 1) * dir;
  });
}

function renderTrades() {
  if (!TR.rows.length) { $("trades-box").innerHTML = '<span class="muted">Không có lệnh nào.</span>'; return; }
  const head = TRADE_COLS.map(([k, label, isNum]) =>
    `<th class="sortable ${isNum ? "num" : ""}" data-k="${k}">${label}${TR.key === k ? (TR.asc ? " ▲" : " ▼") : ""}</th>`).join("");
  const body = sortedTrades().map((t) => `<tr class="clickable" data-id="${t.id}">` + TRADE_COLS.map(([k, , isNum]) => {
    const v = t[k];
    const text = k === "side" ? (SIDE_VN[v] || v) : isNum && typeof v === "number" && k !== "id" && k !== "qty" ? fmt.num(v, 2) : (v ?? "-");
    return `<td class="${isNum ? "num" : ""} ${k === "pnl" ? fmt.cls(v) : ""}">${esc(text)}</td>`;
  }).join("") + "</tr>").join("");
  $("trades-box").innerHTML = `<table><tr>${head}</tr>${body}</table>`;
  $("trades-box").querySelectorAll("th.sortable").forEach((th) => th.onclick = () => {
    TR.asc = TR.key === th.dataset.k ? !TR.asc : true;
    TR.key = th.dataset.k;
    renderTrades();
  });
  $("trades-box").querySelectorAll("tr.clickable").forEach((tr) => tr.onclick = () => {
    const t = TR.rows.find((x) => String(x.id) === tr.dataset.id);
    if (t && TR.onPick) TR.onPick(t);
  });
}

// CSV: ô chữ bắt đầu bằng = + - @ được thêm dấu ' phía trước (chống công thức Excel)
function csvCell(v) {
  let s = v === null || v === undefined ? "" : String(v);
  if (typeof v === "string" && /^[=+\-@]/.test(s)) s = "'" + s;
  return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function exportTradesCsv() {
  const lines = [TRADE_COLS.map(([k]) => k).join(",")];
  for (const t of sortedTrades()) lines.push(TRADE_COLS.map(([k]) => csvCell(t[k])).join(","));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  a.download = "tester-trades.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}
