// Tài khoản luyện tập (chỉ khi replay): phiên giả lập ở server /api/practice/*, KHÔNG BAO GIỜ gửi lệnh lên Alpaca.
const PR = { sid: null, snap: null, ended: false, lineItems: [] };

function prKv(k, v, cls) { return `<div class="kv"><span class="muted">${k}</span><span class="${cls || ""}">${v}</span></div>`; }

function practiceRender() {
  const s = PR.snap;
  const acc = document.getElementById("pr-account");
  if (!s) { acc.textContent = "Chưa có phiên"; return; }
  const pct = s.initial ? (s.pl / s.initial) * 100 : 0;
  acc.innerHTML = prKv("Tiền mặt", fmt.usd(s.cash)) + prKv("Equity", fmt.usd(s.equity)) +
    prKv("P/L", `${s.pl >= 0 ? "+" : ""}${fmt.usd(s.pl)} (${fmt.num(pct)}%)`, fmt.cls(s.pl)) +
    prKv("Giá gần nhất", fmt.num(s.last_price)) + prKv("Phí", `${fmt.num(s.fee_bps, 0)} bps/chiều`) +
    prKv("Nến đã xem", String(s.bars_seen)) + (PR.ended ? prKv("Trạng thái", "đã kết thúc + lưu", "muted") : "");
  document.getElementById("pr-notes").textContent = (s.notes || []).join(" · ");
  const p = s.position;
  document.getElementById("pr-position").innerHTML = p
    ? `<table><tr><th>Mã</th><th class="num">Qty</th><th class="num">Giá vốn</th><th class="num">P/L</th><th></th></tr>` +
      `<tr><td><b>${esc(s.symbol)}</b> ${sideTag(p.qty)}</td><td class="num">${fmt.qty(p.qty)}</td><td class="num">${fmt.num(p.avg_price)}</td>` +
      `<td class="num ${fmt.cls(p.unrealized)}">${fmt.num(p.unrealized)}</td>` +
      `<td><button class="mini" data-psltp="1" title="Gắn / sửa / gỡ SL-TP cho vị thế luyện tập">SL/TP</button></td></tr></table>`
    : "Không có vị thế";
  if (typeof practiceSyncForm === "function") practiceSyncForm();  // SL/TP bật/tắt theo chiều vị thế mới
  const rows = s.orders.map((o) =>
    `<tr><td class="${o.side === "buy" ? "up" : "down"}">${esc(o.side)}</td><td>${esc(o.role === "entry" ? o.type : o.role.toUpperCase())}</td>` +
    `<td class="num">${fmt.qty(o.qty)}</td><td class="num">${o.price === null ? "mkt" : fmt.num(o.price)}</td>` +
    `<td class="small muted">${o.sl ? "SL " + fmt.num(o.sl) : ""} ${o.tp ? "TP " + fmt.num(o.tp) : ""}</td>` +
    `<td><button class="mini" data-pcancel="${esc(o.id)}">Huỷ</button></td></tr>`).join("");
  document.getElementById("pr-orders").innerHTML = rows
    ? `<table><tr><th>Hướng</th><th>Loại</th><th class="num">Qty</th><th class="num">Giá</th><th></th><th></th></tr>${rows}</table>`
    : "Không có lệnh chờ";
}

// Mũi tên tại các lần khớp (đặt vào nến chứa lúc khớp của khung đang xem)
function practiceMarkers() {
  if (!PR.snap) return [];
  const times = D.bars.map((b) => b.time);
  return PR.snap.fills.map((f) => {
    const t = RM.snapTime(times, f.time, CH.tf);
    if (t === null) return null;
    const buy = f.side === "buy";
    return { time: t, kind: "fill", position: buy ? "belowBar" : "aboveBar", shape: buy ? "arrowUp" : "arrowDown",
             color: buy ? "#26a69a" : "#ef5350", text: `${buy ? "B" : "S"} ${fmt.qty(f.qty)}@${fmt.num(f.price)}`,
             tooltip: { side: f.side, qty: f.qty, price: f.price, filled_at: `${nyDay(f.time)} ${nyClock(f.time)} · ${f.reason}` } };
  }).filter((m) => m);
}

// Đường giá: giá vốn + lệnh chờ + SL/TP (kéo được: oid + field)
function practiceLineItems() {
  const s = PR.snap, items = [];
  if (!s) return items;
  if (s.position) items.push({ price: s.position.avg_price, color: "#2196f3", title: `Vào ${fmt.qty(s.position.qty)}` });
  for (const o of s.orders) {
    if (o.role === "sl") items.push({ price: o.price, color: "#ef5350", title: "SL", dashed: true, oid: o.id, field: "price" });
    else if (o.role === "tp") items.push({ price: o.price, color: "#26a69a", title: "TP", dashed: true, oid: o.id, field: "price" });
    else {
      if (o.price !== null) items.push({ price: o.price, color: "#ff9800", title: `Chờ ${o.side} ${o.type}`, dashed: true, oid: o.id, field: "price" });
      if (o.sl) items.push({ price: o.sl, color: "#ef5350", title: "SL (chờ)", dashed: true, oid: o.id, field: "sl" });
      if (o.tp) items.push({ price: o.tp, color: "#26a69a", title: "TP (chờ)", dashed: true, oid: o.id, field: "tp" });
    }
  }
  return items;
}

function practiceRefreshChart() {
  if (!RP.on) return;
  setMarkers(practiceMarkers());
  PR.lineItems = practiceLineItems();
  setPriceLines(PR.lineItems);
  if (typeof posSync === "function") posSync();  // box Long Position của tài khoản luyện tập
}

function practiceApply(snap) {
  PR.snap = snap;
  PR.ended = !!snap.ended;
  practiceRender();
  practiceRefreshChart();
  if (typeof practiceStatsLive === "function") practiceStatsLive();
}

function practiceMsg(text, bad) {
  const el = document.getElementById("pr-msg");
  el.textContent = text || "";
  el.className = "small " + (bad ? "err" : "muted");
}

async function practiceStart(cursor) {
  PR.sid = null; PR.snap = null; PR.ended = false;
  try {
    const s = await sendJSON("POST", "/api/practice/sessions", { symbol: CH.symbol, asset: CH.asset, cursor });
    PR.sid = s.id;
    practiceApply(s);
    practiceMsg(`Phiên ${s.id}: ${fmt.usd(s.initial)} tiền giả lập`);
  } catch (e) { practiceMsg(e.message, true); practiceRender(); }
}

async function practiceAdvance(to, steps) {
  if (!PR.sid || PR.ended) return;
  const s = await sendJSON("POST", `/api/practice/${PR.sid}/advance`, { to, steps });
  practiceApply(s);
  for (const f of s.new_fills) practiceMsg(`Khớp ${f.side === "buy" ? "MUA" : "BÁN"} ${fmt.qty(f.qty)} @ ${fmt.num(f.price)} (${f.reason})`);
}

// Kết thúc phiên: có lệnh khớp thì lưu DB (run_id practice-<id>) + hiện thống kê; phiên trống thì bỏ
async function practiceEnd(restart) {
  const sid = PR.sid;
  if (!sid || PR.ended) return;
  PR.sid = null;
  if (!PR.snap || !PR.snap.fills.length) return;
  try {
    const res = await sendJSON("POST", `/api/practice/${sid}/end`, {});
    PR.ended = true;
    practiceShowStats(res, `phiên ${sid} (đã lưu ${res.run_id})`);
    practiceLoadHistory();
    addRunOption(res.run_id);
    if (!restart) setLoadStatus(`Đã lưu phiên luyện tập ${res.run_id}`);
  } catch (e) { setLoadStatus("Lưu phiên luyện tập lỗi: " + e.message, true); }
}

function addRunOption(runId) {
  const sel = document.getElementById("run-select");
  if (![...sel.options].some((o) => o.value === runId)) sel.add(new Option(runId, runId));
}
