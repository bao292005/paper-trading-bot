// Nút "SL/TP" của vị thế (thật + luyện tập): mở box vị thế trên chart, kéo / nhập SL-TP, "Áp dụng" / "Gỡ SL/TP".
// Thật: XAC NHAN -> POST/DELETE /api/sltp (cổ phiếu = OCO trên sàn, crypto = web server tự canh). Luyện tập: /api/practice/{sid}/sltp.
// SLTP, $sl, sltpRefresh / sltpCells / trạng thái monitor: xem sltp_panel.js (nạp trước file này)

// Vị thế {qty, avg} của mã theo tài khoản; không có -> null
function sltpPos(account, symbol) {
  if (account === "practice") {
    const s = PR.snap;
    return s && s.position && s.symbol === symbol ? { qty: s.position.qty, avg: s.position.avg_price } : null;
  }
  const p = PANEL.positions.find((x) => x.symbol === symbol);
  return p ? { qty: p.qty, avg: p.avg_entry_price } : null;
}

function sltpHasPos(b) { return !!sltpPos(b.account, b.symbol); }

// Mức đang có: thật = GET /api/sltp; luyện tập = lệnh con SL/TP đang chờ
function sltpExisting(account, symbol) {
  if (account === "real") return SLTP.levels[symbol] || null;
  const o = (PR.snap && PR.snap.orders) || [], sl = o.find((x) => x.role === "sl"), tp = o.find((x) => x.role === "tp");
  return sl || tp ? { sl: sl ? sl.price : null, tp: tp ? tp.price : null } : null;
}

// Đổi mã -> chờ biểu đồ tải xong nến của mã mới (tối đa ~8s)
async function sltpWaitChart(symbol, asset) {
  for (let i = 0; i < 80; i++) {
    if (CH.symbol === symbol && CH.asset === asset && D.bars.length) return true;
    await new Promise((r) => setTimeout(r, 100));
  }
  return false;
}

async function sltpOpen(account, symbol, asset) {
  if (account === "real" && (CH.symbol !== symbol || CH.asset !== asset)) {
    selectSymbol(symbol, asset);
    if (!(await sltpWaitChart(symbol, asset))) { posMsg(`Chưa tải được biểu đồ ${symbol}`, true); return; }
    await posSyncReal();  // box SL/TP đang có trên sàn của mã mới
  }
  const pos = sltpPos(account, symbol);
  if (!pos) { posMsg(`Không còn vị thế ${symbol}`, true); return; }
  let b = PB.boxes.find((x) => x.account === account && x.symbol === symbol && x.kind === "position");
  if (!b) {
    const side = pos.qty < 0 ? "short" : "long", t0 = psDefaultT0();
    const lv = SLT.levels(side, pos.avg, pbCurPrice(), PM.atr(D.bars), pbStep(), sltpExisting(account, symbol));
    b = { key: "sltp-" + symbol, kind: "position", sltp: true, symbol, account, side, ...lv, qty: pos.qty, type: "vị thế",
          t0, width: Math.max(20, D.bars.length - pbIndexOf(t0) + 5), dirty: true, ids: {}, orig: { ...lv } };
    PB.boxes.push(b);
  }
  PB.sel = b;
  posChanged(b, true);
  posMsg("Kéo đường SL (đỏ) / TP (xanh) trên chart hoặc nhập số, rồi bấm Áp dụng");
}

// Ô nhập SL/TP khi đang chọn box vị thế (gọi từ posRender)
function sltpRender() {
  const b = PB.sel, on = !!b && b.kind === "position";
  $sl("sltp-edit").hidden = !on;
  if (!on) return;
  $sl("sltp-title").textContent = `${b.symbol} · ${pbSide(b).toUpperCase()} ${fmt.qty(b.qty)} · giá vốn ${pbFmtP(b.entry)}` +
    (b.dirty ? " · chưa áp dụng" : "");
  for (const k of ["sl", "tp"]) if (document.activeElement !== $sl("sltp-" + k)) $sl("sltp-" + k).value = b[k];
  $sl("sltp-note").textContent = b.account === "practice" ? "Luyện tập: áp dụng ngay, không gửi Alpaca."
    : CH.asset === "crypto" ? "Crypto: web server tự canh (giá mid mỗi vài giây), chạm thì BÁN market. Tắt server = không canh."
      : "Cổ phiếu: OCO trên sàn Alpaca (GTC), vẫn chạy khi tắt server.";
}

function sltpDone(b, text) {
  if (b.sltp) pbRemove(b); else { b.orig = { entry: b.entry, sl: b.sl, tp: b.tp }; b.dirty = false; }
  posSync();
  PB.sel = PB.boxes.find((x) => x.kind === "position" && x.symbol === b.symbol && x.account === b.account) || null;
  posRender(); posMsg(text);
}

function sltpCrossConfirm(b, cur, msg) {
  return askConfirm(`Mức SL/TP đã bị vượt qua — ${b.symbol}`, `<p class="err">${esc(msg || `Giá hiện tại ${pbFmtP(cur)} đã vượt qua SL/TP`)}.` +
    " Đặt mức này là lệnh đóng vị thế KHỚP NGAY. Chỉ tiếp tục nếu bạn cố tình làm vậy.</p>");
}

async function sltpApply(b) {
  if (b.busy) return;
  const cur = pbCurPrice(), side = pbSide(b), chk = SLT.check(side, b.sl, b.tp, cur);
  if (chk.err) { posMsg(chk.err, true); return; }
  if (b.account === "practice") {
    if (chk.cross) { posMsg(`Giá hiện tại ${pbFmtP(cur)} đã vượt qua mức SL/TP`, true); return; }
    b.busy = true;
    const ok = await practiceSend("POST", `/api/practice/${PR.sid}/sltp`, { sl: b.sl, tp: b.tp });
    b.busy = false;
    if (ok) sltpDone(b, `Đã gắn SL ${pbFmtP(b.sl)} / TP ${pbFmtP(b.tp)} (luyện tập)`);
    return;
  }
  const ex = SLTP.levels[b.symbol] || {}, crypto = CH.asset === "crypto", was = (v) => (v ? pbFmtP(v) + " → " : "");
  const rows = [["Mã", b.symbol], ["Vị thế", `${side.toUpperCase()} ${fmt.qty(b.qty)} @ ${pbFmtP(b.entry)}`],
    ["SL", was(ex.sl) + pbFmtP(b.sl)], ["TP", was(ex.tp) + pbFmtP(b.tp)], ["Giá hiện tại", cur ? pbFmtP(cur) : "-"],
    ["Nơi đặt", crypto ? "web server tự canh (live_state.json)" : `OCO trên sàn Alpaca, GTC, qty ${fmt.qty(Math.abs(b.qty))}`]];
  const note = crypto ? `<p class="small err">Crypto: chỉ được canh khi web server đang chạy và công tắc "Server canh SL/TP" BẬT.</p>` : "";
  const confirm = await askConfirm(`SL/TP vị thế ${b.symbol}`, summaryTable(rows) + note);
  if (!confirm) return;
  let allow = false;
  if (chk.cross && !(allow = !!(await sltpCrossConfirm(b, cur)))) return;
  const body = { asset: CH.asset, symbol: b.symbol, sl: b.sl, tp: b.tp, confirm };
  b.busy = true;
  try {
    let r;
    try { r = await sendJSON("POST", "/api/sltp", allow ? { ...body, allow_cross: true } : body); } catch (e) {
      // giá ở server khác giá trên chart: server báo đã bị vượt qua -> hỏi thêm 1 lần rồi gửi lại
      if (e.status !== 409 || allow || !e.message.includes("allow_cross") || !(await sltpCrossConfirm(b, cur, e.message))) throw e;
      r = await sendJSON("POST", "/api/sltp", { ...body, allow_cross: true });
    }
    sltpDone(b, `Đã áp dụng SL/TP ${b.symbol} (${r.mode})`);
  } catch (e) { posMsg(e.message, true); }
  b.busy = false;
  refreshPanel();
}

async function sltpRemove() {
  const b = PB.sel;
  if (!b || b.kind !== "position" || b.busy) return;
  if (b.account === "practice") {
    if (await practiceSend("POST", `/api/practice/${PR.sid}/sltp`, { sl: null, tp: null })) { pbRemove(b); posSync(); posMsg("Đã gỡ SL/TP luyện tập"); }
    return;
  }
  const what = CH.asset === "crypto" ? "xoá SL/TP server đang canh" : "huỷ OCO / chân SL-TP trên sàn";
  const confirm = await askConfirm(`Gỡ SL/TP ${b.symbol}`, summaryTable([["Mã", b.symbol], ["Việc", what]]) +
    `<p class="small err">Sau khi gỡ, vị thế KHÔNG còn cắt lỗ / chốt lời tự động.</p>`);
  if (!confirm) return;
  try {
    await sendJSON("DELETE", "/api/sltp", { asset: CH.asset, symbol: b.symbol, confirm });
    pbRemove(b); posRender(); posMsg(`Đã gỡ SL/TP ${b.symbol}`);
  } catch (e) { posMsg(e.message, true); }
  refreshPanel();
}

function initSltp() {
  for (const k of ["sl", "tp"]) {
    $sl("sltp-" + k).addEventListener("input", () => {
      const b = PB.sel, v = Number($sl("sltp-" + k).value);
      if (!b || b.kind !== "position" || !(v > 0)) return;
      b[k] = v;
      posChanged(b);
    });
  }
  $sl("sltp-apply").addEventListener("click", () => { if (PB.sel && PB.sel.kind === "position") sltpApply(PB.sel); });
  $sl("sltp-remove").addEventListener("click", sltpRemove);
  $sl("sltp-close").addEventListener("click", () => { const b = PB.sel; if (b && b.sltp) pbRemove(b); PB.sel = null; posRender(); pbSchedule(); });
  $sl("pr-position").addEventListener("click", (ev) => {
    if (ev.target.closest("button[data-psltp]") && PR.snap) sltpOpen("practice", PR.snap.symbol, CH.asset);
  });
  initSltpPanel();
}
