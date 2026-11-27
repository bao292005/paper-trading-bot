// Panel phải: tài khoản, vị thế, lệnh chờ (tự làm mới 15s) + price line cho mã đang xem.
const PANEL = { account: null, positions: [], orders: [], timer: null };

function showErr(id, e) {
  document.getElementById(id).innerHTML = `<span class="err">${esc(e.message)}</span>`;
}

async function refreshAccount() {
  try {
    const a = await getJSON("/api/account");
    PANEL.account = a;  // vốn/tiền mặt cho qty theo rủi ro (Long Position)
    const kv = (k, v, cls = "") => `<div class="kv"><span class="muted">${k}</span><span class="${cls}">${v}</span></div>`;
    const pl = a.day_pl === null ? "-" :
      `${a.day_pl >= 0 ? "+" : ""}${fmt.usd(a.day_pl)} (${fmt.num(a.day_pl_pct)}%)`;
    document.getElementById("account").innerHTML =
      kv("Equity", fmt.usd(a.equity)) + kv("Tiền mặt", fmt.usd(a.cash)) +
      kv("Sức mua", fmt.usd(a.buying_power)) + kv("P/L ngày", pl, fmt.cls(a.day_pl));
  } catch (e) { showErr("account", e); }
}

async function refreshPositions() {
  try {
    PANEL.positions = (await getJSON("/api/positions")).positions;
    const rows = PANEL.positions.map((p) =>
      `<tr class="clickable" data-symbol="${esc(p.symbol)}"><td><b>${esc(p.symbol)}</b> ${sideTag(p.qty)}</td>` +
      `<td class="num">${fmt.qty(p.qty)}</td><td class="num">${fmt.num(p.avg_entry_price)}</td>` +
      `<td class="num ${fmt.cls(p.unrealized_pl)}">${fmt.num(p.unrealized_pl)}</td>` +
      `<td><button class="mini" data-close="${esc(p.symbol)}" title="Đóng cả vị thế bằng lệnh market ngược chiều">Đóng</button></td></tr>`).join("");
    document.getElementById("positions").innerHTML = rows
      ? `<table><tr><th>Mã</th><th class="num">Qty</th><th class="num">Giá vào</th><th class="num">P/L</th><th></th></tr>${rows}</table>`
      : "Không có vị thế";
  } catch (e) { PANEL.positions = []; showErr("positions", e); }
}

// Đóng cả vị thế: long -> BÁN market, short -> MUA market (cần XAC NHAN)
async function closePosition(sym) {
  const p = PANEL.positions.find((x) => x.symbol === sym);
  if (!p) return;
  const short = p.qty < 0;
  const html = summaryTable([["Mã", sym], ["Vị thế", `${short ? "SHORT" : "LONG"} ${fmt.qty(p.qty)}`],
    ["Lệnh", `${short ? "MUA" : "BÁN"} market ${fmt.qty(Math.abs(p.qty))}`]]) +
    (short ? `<p class="err small">${esc(POS_SHORT_WARN)}</p>` : "");
  const confirm = await askConfirm(`Đóng vị thế ${sym}`, html);
  if (!confirm) return;
  try {
    const r = await sendJSON("POST", "/api/position/close", { symbol: sym, confirm });
    ordMsg(`Đã gửi lệnh đóng ${sym}: ${r.side} ${fmt.qty(r.qty)} (${r.status || "?"})`, "ok");
    refreshPanel();
  } catch (e) { ordMsg("Lỗi đóng vị thế: " + e.message, "bad"); }
}

function orderPrice(o) { return o.limit_price !== null ? o.limit_price : o.stop_price; }

async function refreshOrders() {
  try {
    PANEL.orders = (await getJSON("/api/orders")).orders;
    const rows = PANEL.orders.map((o) =>
      `<tr class="clickable" data-symbol="${esc(o.symbol)}"><td><b>${esc(o.symbol)}</b></td>` +
      `<td class="${o.side === "buy" ? "up" : "down"}">${esc(o.side)}</td>` +
      `<td>${esc(o.type)}</td><td class="num">${fmt.qty(o.qty)}</td>` +
      `<td class="num">${fmt.num(orderPrice(o))}</td>` +
      `<td><button class="mini" data-cancel="${esc(o.id)}" title="Huỷ lệnh này">Huỷ</button></td></tr>`).join("");
    document.getElementById("orders").innerHTML = rows
      ? `<table><tr><th>Mã</th><th>Hướng</th><th>Loại</th><th class="num">Qty</th><th class="num">Giá</th><th></th></tr>${rows}</table>`
      : "Không có lệnh chờ";
  } catch (e) { PANEL.orders = []; showErr("orders", e); }
}

// Price line cho mã đang xem: giá vào (xanh dương), SL (đỏ), TP (xanh lá), lệnh chờ khác (cam)
function updatePriceLines() {
  if (typeof RP !== "undefined" && RP.on) { practiceRefreshChart(); return; }  // replay: đường giá của tài khoản luyện tập
  const items = [];
  const pos = PANEL.positions.find((p) => p.symbol === CH.symbol);
  if (pos) items.push({ price: pos.avg_entry_price, color: "#2196f3", title: `Vào ${fmt.qty(pos.qty)}` });
  for (const o of PANEL.orders.filter((x) => x.symbol === CH.symbol)) {
    const price = orderPrice(o);
    if (price === null || price === undefined) continue;
    const exit = pos && o.side === (pos.qty < 0 ? "buy" : "sell");  // lệnh đóng: long = bán, short = mua
    if (exit && o.stop_price !== null) items.push({ price, color: "#ef5350", title: "SL", dashed: true });
    else if (exit) items.push({ price, color: "#26a69a", title: "TP", dashed: true });
    else items.push({ price, color: "#ff9800", title: `Chờ ${o.side} ${o.type}`, dashed: true });
  }
  setPriceLines(items);
}

async function refreshPanel() {
  await Promise.all([refreshAccount(), refreshPositions(), refreshOrders()]);
  updatePriceLines();
  if (typeof posSyncReal === "function") posSyncReal();
}

function initPanel() {
  for (const id of ["positions", "orders"]) {
    document.getElementById(id).addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-cancel]");
      if (btn) { cancelOrder(btn.dataset.cancel); return; }
      const cl = ev.target.closest("button[data-close]");
      if (cl) { closePosition(cl.dataset.close); return; }
      const tr = ev.target.closest("tr[data-symbol]");
      if (tr) selectSymbol(tr.dataset.symbol, CRYPTO_SYMBOLS.includes(tr.dataset.symbol) ? "crypto" : "stock");
    });
  }
  refreshPanel();
  PANEL.timer = setInterval(refreshPanel, 15000);
}
