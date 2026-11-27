// Panel "Long Position": rủi ro (% vốn hoặc $) -> qty, loại lệnh (gợi ý theo giá hiện tại), đặt lệnh từ box nháp.
// Luyện tập: gửi ngay (không XAC NHAN). Thật: modal XAC NHAN -> POST /api/position (bracket gtc / crypto + live_state).
const $pos = (id) => document.getElementById(id);

function posReal() { return !RP.on; }

// Vốn + tiền mặt để tính qty: replay = tài khoản luyện tập, thường = Alpaca paper
function posFunds() {
  if (RP.on) return PR.snap ? { equity: PR.snap.equity, cash: PR.snap.cash } : null;
  return PANEL.account ? { equity: PANEL.account.equity, cash: PANEL.account.cash } : null;
}

function posRiskUsd() {
  const v = Number($pos("pos-risk").value), f = posFunds();
  if (!(v > 0)) return 0;
  if ($pos("pos-risk-unit").value === "usd") return v;
  return f && f.equity > 0 ? f.equity * v / 100 : 0;
}

// Crypto thật không có lệnh stop (chỉ market/limit gtc)
function posFixType(t) { return t === "stop" && posReal() && CH.asset === "crypto" ? "market" : t; }

// Box nháp đổi mức -> gợi ý lại loại lệnh (nếu chưa chọn tay) + qty (nếu đang tự tính)
function posChanged(b, selectOnly) {
  if (!selectOnly && b.kind === "draft") {
    if (!b.typeManual) b.type = posFixType(PM.suggestType(b.entry, pbCurPrice()));
    if ($pos("pos-auto").checked) {
      const f = posFunds();
      const r = PM.riskQty({ risk: posRiskUsd(), entry: b.entry, sl: b.sl, asset: CH.asset, step: pbQtyStep(), cash: f ? f.cash : 0 });
      b.qty = r.qty; b.capped = r.capped;
    }
  }
  if (!selectOnly && b.kind !== "draft" && b.account === "real") b.dirty = posMoved(b);
  if (b === PB.sel) posRender();
  pbSchedule();
}

function posMoved(b) { return !!b.orig && (b.entry !== b.orig.entry || b.sl !== b.orig.sl || b.tp !== b.orig.tp); }

function posRender() {
  const b = PB.sel, draft = !!b && b.kind === "draft";
  $pos("pos-empty").hidden = draft;
  $pos("pos-form").hidden = !draft;
  if (!draft) return;
  $pos("pos-type").value = b.type;
  $pos("pos-type").querySelector('option[value="stop"]').disabled = posReal() && CH.asset === "crypto";
  if ($pos("pos-auto").checked) $pos("pos-qty").value = b.qty;
  const s = PM.stats(b), cap = b.capped === "cash" ? " · bị chặn theo tiền mặt" : b.capped === "max" ? " · bị chặn theo trần sàn" : "";
  $pos("pos-info").textContent = `${posReal() ? "Alpaca paper" : "Luyện tập"} · R:R ${s.rr === null ? "-" : fmt.num(s.rr)} · ` +
    `rủi ro ~${fmt.usd(s.loss)} · lời ~${fmt.usd(s.profit)} (sau phí)${cap}`;
  $pos("pos-crypto-note").hidden = !(posReal() && CH.asset === "crypto");
}

function posMsg(text, bad) { $pos("pos-msg").textContent = text || ""; $pos("pos-msg").className = "small " + (bad ? "err" : "muted"); }

function posDiscard() {
  for (const x of PB.boxes.filter((x) => x.kind === "draft")) pbRemove(x);
  posRender(); pbSchedule();
}

// Body chung cho lệnh vào từ box nháp; sai -> ném lỗi tiếng Việt
function posOrderBody(b) {
  if (!PM.valid(b)) throw new Error("Cần SL < giá vào < TP");
  const qty = Number(b.qty);
  if (!(qty > 0)) throw new Error("Số lượng phải > 0 (tăng rủi ro hoặc nhập qty)");
  if (CH.asset === "stock" && !Number.isInteger(qty)) throw new Error("Cổ phiếu: qty phải là số nguyên");
  return { symbol: CH.symbol, asset: CH.asset, type: b.type, qty, entry: b.entry, sl: b.sl, tp: b.tp };
}

async function posSubmit() {
  const b = PB.sel;
  if (!b || b.kind !== "draft") return;
  let body;
  try { body = posOrderBody(b); } catch (e) { posMsg(e.message, true); return; }
  if (!posReal()) {
    const pb = { side: "buy", type: body.type, qty: body.qty, sl: body.sl, tp: body.tp };
    if (body.type !== "market") pb.price = body.entry;
    const ok = await practiceSend("POST", `/api/practice/${PR.sid}/orders`, pb, `Đã đặt long ${body.type} ${body.qty}`);
    if (ok) { posDiscard(); posMsg("Đã đặt lệnh luyện tập"); }
    return;
  }
  const s = PM.stats(b);
  const html = summaryTable([["Mã", body.symbol], ["Loại", body.type + (CH.asset === "stock" ? " (bracket, GTC)" : " (GTC)")],
    ["Giá vào", pbFmtP(body.entry)], ["SL", pbFmtP(body.sl)], ["TP", pbFmtP(body.tp)], ["Qty", fmt.qty(body.qty)],
    ["R:R", s.rr === null ? "-" : fmt.num(s.rr)], ["Rủi ro (sau phí)", fmt.usd(s.loss)]]) +
    (CH.asset === "crypto" ? `<p class="small err">SL/TP crypto chỉ hoạt động khi bot loop --asset crypto đang chạy</p>` : "");
  const confirm = await askConfirm("Đặt lệnh LONG (Alpaca paper)", html);
  if (!confirm) return;
  try {
    const r = await sendJSON("POST", "/api/position", { ...body, confirm });
    posDiscard(); posMsg(`Đã gửi lệnh ${r.id || ""} (${r.status || ""})`);
    refreshPanel();
  } catch (e) { posMsg(e.message, true); }
}

function initPositionOrders() {
  const redo = () => { if (PB.sel && PB.sel.kind === "draft") posChanged(PB.sel); };
  for (const id of ["pos-risk", "pos-risk-unit"]) $pos(id).addEventListener("input", redo);
  $pos("pos-risk-unit").addEventListener("change", redo);
  $pos("pos-auto").addEventListener("change", redo);
  $pos("pos-qty").addEventListener("input", () => {
    const b = PB.sel;
    if (!b || b.kind !== "draft") return;
    $pos("pos-auto").checked = false;  // sửa tay qty -> tắt tự tính
    b.qty = Number($pos("pos-qty").value) || 0; b.capped = null;
    posRender(); pbSchedule();
  });
  $pos("pos-type").addEventListener("change", () => {
    const b = PB.sel;
    if (b && b.kind === "draft") { b.type = $pos("pos-type").value; b.typeManual = true; posRender(); pbSchedule(); }
  });
  $pos("pos-submit").addEventListener("click", posSubmit);
  $pos("pos-discard").addEventListener("click", posDiscard);
  posRender();
}
