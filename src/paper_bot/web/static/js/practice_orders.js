// Form lệnh luyện tập (không cần XAC NHAN, không đụng Alpaca) + phím B/S + huỷ lệnh + tab Tài khoản/Thống kê.
function prNum(id) {
  const v = document.getElementById(id).value.trim();
  return v === "" ? null : Number(v);
}

// Đọc form -> body gửi server; sai thì ném lỗi tiếng Việt
function practiceOrderBody(side, type) {
  const qty = prNum("pr-qty");
  if (!(qty > 0)) throw new Error("Số lượng phải > 0");
  const body = { side, type, qty };
  if (type !== "market") {
    body.price = prNum("pr-price");
    if (!(body.price > 0)) throw new Error("Lệnh limit/stop cần giá");
  }
  if (prSlTp(side)) {  // form: SL/TP chỉ cho lệnh MUA mở long; short kèm SL/TP thì dùng box ⊟ Short
    body.sl = prNum("pr-sl");
    body.tp = prNum("pr-tp");
  }
  return body;
}

// Qty vị thế luyện tập (âm = short); lệnh "mở" = cùng chiều hoặc đang không có vị thế
function prPosQty() { return (PR.snap && PR.snap.position && PR.snap.position.qty) || 0; }
function prOpens(side) { return side === "buy" ? prPosQty() >= 0 : prPosQty() <= 0; }
function prSlTp(side) { return side === "buy" && prOpens("buy"); }

async function practiceSend(method, url, body, okText) {
  if (!PR.sid) { practiceMsg(PR.ended ? "Phiên đã kết thúc" : "Chưa có phiên luyện tập (bấm Replay)", true); return null; }
  try {
    const s = await sendJSON(method, url, body || {});
    practiceApply(s);
    if (okText) practiceMsg(okText);
    return s;
  } catch (e) { practiceMsg(e.message, true); return null; }
}

function practicePlace(side, type) {
  let body;
  try { body = practiceOrderBody(side, type); } catch (e) { practiceMsg(e.message, true); return; }
  const what = `${side === "buy" ? "MUA" : "BÁN"} ${type} ${body.qty}` + (body.price ? ` @ ${body.price}` : "");
  practiceSend("POST", `/api/practice/${PR.sid}/orders`, body,
               `Đã đặt ${what}` + (type === "market" ? " — khớp ở giá mở nến 1m kế tiếp (bấm → hoặc ▶)" : ""));
}

// B/S: lệnh ngược chiều vị thế -> đóng bớt đúng số ở ô qty (≥ cả vị thế = đóng hết, không đảo chiều);
// cùng chiều/không có vị thế -> mở thêm (S lúc không có vị thế = bán khống)
function practiceKey(side) {
  const q = prPosQty();
  if (prOpens(side)) { practicePlace(side, "market"); return; }
  const qty = prNum("pr-qty");
  if (!(qty > 0) || qty >= Math.abs(q)) practiceSend("POST", `/api/practice/${PR.sid}/close`, {}, "Đã đặt lệnh đóng vị thế");
  else practicePlace(side, "market");
}

function practiceSyncForm() {
  const type = document.getElementById("pr-type").value, side = document.getElementById("pr-side").value;
  document.getElementById("pr-price").disabled = type === "market";
  for (const id of ["pr-sl", "pr-tp"]) document.getElementById(id).disabled = !prSlTp(side);
}

function selectSideTab(tab) {
  document.getElementById("side").dataset.tab = tab;
  for (const b of document.querySelectorAll("#side-tabs button")) b.classList.toggle("active", b.dataset.tab === tab);
  if (tab === "stats") practiceStatsLive(true);
}

function initPractice() {
  const form = document.getElementById("pr-form");
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    practicePlace(document.getElementById("pr-side").value, document.getElementById("pr-type").value);
  });
  for (const id of ["pr-side", "pr-type"]) document.getElementById(id).addEventListener("change", practiceSyncForm);
  practiceSyncForm();
  document.getElementById("pr-close").addEventListener("click", () =>
    practiceSend("POST", `/api/practice/${PR.sid}/close`, {}, "Đã đặt lệnh đóng vị thế"));
  document.getElementById("pr-orders").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-pcancel]");
    if (btn) practiceSend("DELETE", `/api/practice/${PR.sid}/orders/${encodeURIComponent(btn.dataset.pcancel)}`, {}, "Đã huỷ lệnh");
  });
  document.getElementById("side-tabs").addEventListener("click", (ev) => {
    if (ev.target.dataset.tab) selectSideTab(ev.target.dataset.tab);
  });
  document.addEventListener("keydown", (ev) => {
    if (!RP.on || RM.ignoreKey(ev)) return;
    const k = ev.key.toLowerCase();
    if (k === "b") { ev.preventDefault(); practiceKey("buy"); }
    else if (k === "s") { ev.preventDefault(); practiceKey("sell"); }
  });
  initPracticeDrag();
  initPracticeStats();
}
