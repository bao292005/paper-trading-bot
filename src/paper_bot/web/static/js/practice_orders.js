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
  if (side === "buy") {
    body.sl = prNum("pr-sl");
    body.tp = prNum("pr-tp");
  }
  return body;
}

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

// S: có vị thế -> bán market đúng số ở ô qty (tối đa = cả vị thế); không có -> báo không bán khống
function practiceSellKey() {
  const pos = PR.snap && PR.snap.position;
  if (!pos) { practiceMsg("Không có vị thế để bán (không bán khống)", true); return; }
  const qty = prNum("pr-qty");
  if (!(qty > 0) || qty >= pos.qty) practiceSend("POST", `/api/practice/${PR.sid}/close`, {}, "Đã đặt lệnh đóng vị thế");
  else practicePlace("sell", "market");
}

function practiceSyncForm() {
  const type = document.getElementById("pr-type").value, buy = document.getElementById("pr-side").value === "buy";
  document.getElementById("pr-price").disabled = type === "market";
  for (const id of ["pr-sl", "pr-tp"]) document.getElementById(id).disabled = !buy;
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
    if (k === "b") { ev.preventDefault(); practicePlace("buy", "market"); }
    else if (k === "s") { ev.preventDefault(); practiceSellKey(); }
  });
  initPracticeDrag();
  initPracticeStats();
}
