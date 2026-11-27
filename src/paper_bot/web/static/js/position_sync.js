// Box cho lệnh chờ (viền đứt) và vị thế (viền liền) của mã đang xem. Replay: từ tài khoản luyện tập (PR.snap);
// thường: từ GET /api/position/boxes (Alpaca paper). Kéo: luyện tập PATCH ngay khi thả; thật: "Áp dụng" + XAC NHAN.
const PS = { real: null };

// Lệnh MỞ chờ luyện tập (mua = long, bán khống = short; có SL+TP) + vị thế có đủ cặp SL/TP con -> mô tả box
function psPracticeSpecs() {
  const s = PR.snap, out = [];
  if (!s) return out;
  for (const o of s.orders) {
    if (o.role === "entry" && !o.reduce && o.sl && o.tp && o.price !== null) {
      out.push({ key: "pr-" + o.id, kind: "pending", side: o.side === "sell" ? "short" : "long", entry: o.price,
                 sl: o.sl, tp: o.tp, qty: o.qty, type: o.type, t0: o.created, ids: { entry: o.id } });
    }
  }
  const sl = s.orders.find((o) => o.role === "sl"), tp = s.orders.find((o) => o.role === "tp");
  if (s.position && sl && tp) {
    out.push({ key: "pr-pos", kind: "position", side: s.position.qty < 0 ? "short" : "long", entry: s.position.avg_price,
               sl: sl.price, tp: tp.price, qty: s.position.qty, type: "vị thế", t0: sl.created, ids: { sl: sl.id, tp: tp.id } });
  }
  return out;
}

function psDefaultT0() { return D.bars.length ? D.bars[Math.max(0, D.bars.length - 15)].time : 0; }

// Đồng bộ danh sách box với mô tả mới. Box đang kéo / đang sửa dở (dirty) / đang gửi thì giữ nguyên mức.
function posSync() {
  const account = RP.on ? "practice" : "real";
  let specs = [];
  if (RP.on) specs = psPracticeSpecs();
  else if (PS.real && PS.real.symbol === CH.symbol && PS.real.asset === CH.asset) specs = PS.real.boxes;
  for (const b of [...PB.boxes]) {
    if (b.symbol !== CH.symbol || b.account !== account) { pbRemove(b); continue; }  // đổi mã / vào-ra replay
    if (b.kind !== "draft" && !specs.some((x) => x.key === b.key)) pbRemove(b);      // đã khớp xong / huỷ / đóng
  }
  for (const sp of specs) {
    let b = PB.boxes.find((x) => x.key === sp.key);
    const lv = { entry: sp.entry, sl: sp.sl, tp: sp.tp, side: sp.side === "short" ? "short" : "long" };
    if (!PM.valid(lv)) { if (b) pbRemove(b); continue; }
    if (!b) {
      const t0 = sp.t0 || psDefaultT0();
      b = { symbol: CH.symbol, account, t0, width: Math.max(20, D.bars.length - pbIndexOf(t0) + 5), dirty: false };
      PB.boxes.push(b);
    } else if (b.dirty || b.busy || (PD.d && PD.d.b === b)) { b.ids = sp.ids || {}; continue; }
    Object.assign(b, lv, { key: sp.key, kind: sp.kind, qty: sp.qty, type: sp.type, ids: sp.ids || {}, orig: { ...lv } });
  }
  if (typeof posRender === "function") posRender();
  if (typeof pdSyncShortBtn === "function") pdSyncShortBtn();  // đổi mã / vào-ra replay -> bật/tắt nút Short
  pbSchedule();
}

// Thật: hỏi server box của mã đang xem (gọi cùng nhịp làm mới panel 15s)
async function posSyncReal() {
  if (RP.on) return;
  const symbol = CH.symbol, asset = CH.asset;
  try {
    const r = await getJSON("/api/position/boxes", { symbol, asset });
    PS.real = { symbol, asset, boxes: r.boxes || [] };
    if (r.inc) PB.inc = r.inc;  // crypto: bước giá/qty của sàn
  } catch (e) { PS.real = null; }
  posSync();
}

// Lệnh luyện tập bị box "sở hữu" -> practice_drag.js không kéo đường giá đó (tránh 2 kiểu kéo giành nhau)
function pbOwnsOid(oid) {
  return PB.boxes.some((b) => b.account === "practice" && b.ids && Object.values(b.ids).includes(oid));
}

// Thả chuột sau khi kéo: nháp thì thôi; luyện tập -> PATCH ngay; thật -> chờ bấm "Áp dụng"
async function posDragEnd(b, lv) {
  if (b.kind === "draft") return;
  if (b.account === "real") { b.dirty = posMoved(b); pbSchedule(); return; }
  const url = (oid) => `/api/practice/${PR.sid}/orders/${encodeURIComponent(oid)}`;
  b.busy = true;
  let ok = true;
  if (b.kind === "pending") {
    const body = {};
    for (const [k, f] of [["entry", "price"], ["sl", "sl"], ["tp", "tp"]]) if (b[k] !== lv[k]) body[f] = b[k];
    ok = !!(await practiceSend("PATCH", url(b.ids.entry), body, "Đã sửa lệnh chờ"));
  } else {
    if (ok && b.sl !== lv.sl) ok = !!(await practiceSend("PATCH", url(b.ids.sl), { price: b.sl }, `Đã sửa SL -> ${pbFmtP(b.sl)}`));
    if (ok && b.tp !== lv.tp) ok = !!(await practiceSend("PATCH", url(b.ids.tp), { price: b.tp }, `Đã sửa TP -> ${pbFmtP(b.tp)}`));
  }
  b.busy = false;
  if (!ok) pbRevert(b);  // server từ chối -> box về chỗ cũ
  posSync();
}

function pbRevert(b) {
  if (b.orig) Object.assign(b, b.orig);
  b.dirty = false;
  pbSchedule();
}

// Thật: XAC NHAN rồi gửi. Lệnh chờ -> huỷ + đặt lại bracket; vị thế -> sửa chân SL/TP (crypto: live_state)
async function pbApply(b) {
  if (b.account !== "real" || !b.dirty || b.busy) return;
  const pending = b.kind === "pending", s = PM.stats({ ...b, qty: Math.abs(b.qty) }), SIDE = pbSide(b).toUpperCase();
  const rows = [["Mã", CH.symbol], ["Việc", pending ? "huỷ lệnh chờ cũ + đặt lại" : "sửa SL/TP của vị thế"]];
  for (const k of ["entry", "sl", "tp"]) if (b[k] !== b.orig[k]) rows.push([k.toUpperCase(), `${pbFmtP(b.orig[k])} → ${pbFmtP(b[k])}`]);
  rows.push(["Qty", fmt.qty(b.qty)], ["R:R", s.rr === null ? "-" : fmt.num(s.rr)], ["Rủi ro (sau phí)", fmt.usd(s.loss)]);
  const note = CH.asset === "crypto" ? `<p class="small err">SL/TP crypto chỉ hoạt động khi bot loop --asset crypto đang chạy</p>` : "";
  const confirm = await askConfirm(pending ? `Sửa lệnh chờ ${SIDE}` : `Sửa SL/TP vị thế ${SIDE}`, summaryTable(rows) + note);
  if (!confirm) return;
  const base = { symbol: CH.symbol, asset: CH.asset, side: pbSide(b), entry: b.entry, sl: b.sl, tp: b.tp, confirm };
  b.busy = true;
  try {
    if (pending) await sendJSON("POST", "/api/position/repost", { ...base, old_order_id: b.ids.entry, type: b.type, qty: b.qty });
    else await sendJSON("POST", "/api/position/legs", { ...base, sl_id: b.ids.sl || null, tp_id: b.ids.tp || null });
    b.orig = { entry: b.entry, sl: b.sl, tp: b.tp };
    b.dirty = false;
    posMsg("Đã áp dụng thay đổi");
  } catch (e) { posMsg(e.message, true); pbRevert(b); }
  b.busy = false;
  refreshPanel();
}
