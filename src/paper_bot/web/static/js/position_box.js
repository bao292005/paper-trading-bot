// Vẽ box "Long/Short Position" bằng div phủ trên chart (không vẽ vào canvas của thư viện), giống zoombox.js.
// Box: {key, kind: draft|pending|position, account: practice|real, side: long|short, entry, sl, tp, t0, width, qty, type, dirty, geom}
// Giá -> y: CH.candles.priceToCoordinate; thời gian -> x: chỉ số nến (logical) -> timeScale().logicalToCoordinate.
const PB = { boxes: [], layer: null, raf: 0, tool: false, sel: null, inc: { price: 0.01, qty: 0.0001 } };

function pbStep() { return CH.asset === "crypto" ? (PB.inc.price || 0.01) : 0.01; }
function pbQtyStep() { return CH.asset === "crypto" ? (PB.inc.qty || 0.0001) : 1; }
function pbFmtP(p) { return fmt.num(p, Math.max(2, PM.decimals(pbStep()))); }

// Giá hiện tại: replay = giá tại con trỏ (tài khoản luyện tập), thường = close nến cuối
function pbCurPrice() {
  if (RP.on && PR.snap && PR.snap.last_price) return PR.snap.last_price;
  const last = D.bars[D.bars.length - 1];
  return last ? last.close : null;
}

// Chỉ số nến chứa thời điểm t (nến cuối có time ≤ t); trước nến đầu -> 0
function pbIndexOf(t) {
  let lo = 0, hi = D.bars.length - 1, ans = 0;
  while (lo <= hi) {
    const m = (lo + hi) >> 1;
    if (D.bars[m].time <= t) { ans = m; lo = m + 1; } else hi = m - 1;
  }
  return ans;
}

function pbEl(cls, parent) {
  const e = document.createElement("div");
  e.className = cls;
  parent.appendChild(e);
  return e;
}

// Tạo DOM 1 lần cho mỗi box
function pbBuild(b) {
  const root = pbEl("pbox", PB.layer);
  b.el = { root, tp: pbEl("pb-zone pb-tp", root), sl: pbEl("pb-zone pb-sl", root), cur: pbEl("pb-cur", root),
           en: pbEl("pb-entry", root), lt: pbEl("pb-lab pb-lab-tp", root), le: pbEl("pb-lab pb-lab-en", root),
           ls: pbEl("pb-lab pb-lab-sl", root), act: pbEl("pb-actions", root) };
  b.el.act.innerHTML = `<button class="mini primary" data-pb="apply">Áp dụng</button><button class="mini" data-pb="revert">Huỷ thay đổi</button>`;
  b.el.act.addEventListener("click", (ev) => {
    const k = ev.target.dataset && ev.target.dataset.pb;
    if (k === "apply") pbApply(b); else if (k === "revert") pbRevert(b);
  });
}

function pbPlace(e, x, y, w, h) {
  e.style.left = x + "px"; e.style.top = y + "px"; e.style.width = Math.max(0, w) + "px"; e.style.height = Math.max(0, h) + "px";
}

function pbSide(b) { return b.side === "short" ? "short" : "long"; }

function pbLabels(b) {
  const side = pbSide(b), s = PM.stats({ entry: b.entry, sl: b.sl, tp: b.tp, qty: Math.abs(b.qty), asset: CH.asset, side });
  const tag = b.kind === "draft" ? (b.type || "") : b.kind === "pending" ? "chờ " + (b.type || "") : "vị thế";
  b.el.lt.textContent = `TP ${pbFmtP(b.tp)} · +${fmt.num(s.tpPct)}% · +${fmt.usd(s.profit)} sau phí`;
  b.el.ls.textContent = `SL ${pbFmtP(b.sl)} · −${fmt.num(s.slPct)}% · −${fmt.usd(s.loss)} sau phí`;
  let en = `${side.toUpperCase()} · Vào ${pbFmtP(b.entry)} · qty ${fmt.qty(Math.abs(b.qty))} · R:R ${s.rr === null ? "-" : fmt.num(s.rr)} · ${tag}`;
  const cur = pbCurPrice();
  if (b.kind === "position" && cur) {
    const u = PM.unreal(b.entry, b.sl, cur, b.qty, side);
    en += ` · P/L ${u.pl >= 0 ? "+" : "−"}${fmt.usd(Math.abs(u.pl))} (${u.r === null ? "-" : (u.r >= 0 ? "+" : "") + fmt.num(u.r)}R)`;
  }
  b.el.le.textContent = en + (b.dirty ? " · chưa áp dụng" : "");
}

// Vẽ 1 box theo toạ độ hiện tại của chart. Ngoài màn hình thì ẩn.
function pbDrawBox(b) {
  if (!b.el) pbBuild(b);
  const ts = CH.chart.timeScale(), y = (p) => CH.candles.priceToCoordinate(p);
  const i0 = pbIndexOf(b.t0), xa = ts.logicalToCoordinate(i0), xb = ts.logicalToCoordinate(i0 + b.width);
  const yt = y(b.tp), ye = y(b.entry), ys = y(b.sl);
  const ok = [xa, xb, yt, ye, ys].every((v) => v !== null && isFinite(v));
  b.el.root.hidden = !ok;
  if (!ok) { b.geom = null; return; }
  const w = Math.min(xb, PB.layer.clientWidth - CH.chart.priceScale("right").width()) - xa;  // không lấn trục giá
  // long: TP trên, SL dưới; short: SL (đỏ) trên, TP (xanh) dưới. top/bot = mép trên/dưới của box
  const top = Math.min(yt, ys), bot = Math.max(yt, ys), short = pbSide(b) === "short";
  b.geom = { x0: xa, x1: xa + w, yt, ye, ys, top, bot };
  b.el.root.className = `pbox ${b.kind}${short ? " short" : ""}${b.dirty ? " dirty" : ""}${PB.sel === b ? " sel" : ""}`;
  pbPlace(b.el.tp, xa, Math.min(yt, ye), w, Math.abs(ye - yt));
  pbPlace(b.el.sl, xa, Math.min(ys, ye), w, Math.abs(ys - ye));
  pbPlace(b.el.en, xa, ye, w, 0);
  const cur = pbCurPrice(), yc = cur ? y(cur) : null;
  b.el.cur.hidden = !(b.kind === "position" && yc !== null);
  if (!b.el.cur.hidden) {
    pbPlace(b.el.cur, xa, Math.min(ye, yc), w, Math.abs(yc - ye));
    b.el.cur.classList.toggle("loss", PM.dir(pbSide(b)) * (cur - b.entry) < 0);
  }
  pbLabels(b);
  const lx = Math.max(xa, xa + w - 4), at = (yy) => (yy === top ? yy - 16 : yy + 2);  // nhãn mép trên đặt phía trên
  for (const [e, yy] of [[b.el.lt, at(yt)], [b.el.le, ye - 8], [b.el.ls, at(ys)]]) { e.style.left = lx + "px"; e.style.top = yy + "px"; }
  b.el.act.hidden = !b.dirty;
  b.el.act.style.left = Math.max(0, xa) + "px"; b.el.act.style.top = (bot + 4) + "px";
}

function pbDraw() {
  PB.raf = 0;
  if (!PB.layer) return;
  for (const b of PB.boxes) pbDrawBox(b);
}

// Gộp nhiều sự kiện (zoom, cuộn, resize, đổi trục) thành 1 lần vẽ mỗi khung hình
function pbSchedule() { if (!PB.raf) PB.raf = requestAnimationFrame(pbDraw); }

function pbRemove(b) {
  if (b.el) b.el.root.remove();
  PB.boxes = PB.boxes.filter((x) => x !== b);
  if (PB.sel === b) PB.sel = null;
}

function pbClearAll() { for (const b of [...PB.boxes]) pbRemove(b); }

function initPositionBox() {
  const el = document.getElementById("chart");
  PB.layer = pbEl("pbox-layer", el);
  CH.chart.timeScale().subscribeVisibleLogicalRangeChange(pbSchedule);
  CH.chart.subscribeCrosshairMove(pbSchedule);
  new ResizeObserver(pbSchedule).observe(el);
  for (const name of ["wheel", "mousemove", "mouseup"]) el.addEventListener(name, pbSchedule, { passive: true });
  const mode = document.getElementById("z-mode");
  if (mode) mode.addEventListener("change", () => setTimeout(pbSchedule, 0));  // đổi trục Log/%
  document.addEventListener("keydown", () => setTimeout(pbSchedule, 0));      // phím zoom/L/A
}
