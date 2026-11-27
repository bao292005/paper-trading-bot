// Công cụ "⊞ Long" (phím P) / "⊟ Short" (Shift+P): bấm lên chart tại giá vào -> box nháp. Kéo: đường TP / SL,
// đường giữa = entry (SL/TP giữ nguyên), Alt + kéo thân = dời cả box, mép phải = độ rộng.
// Long: SL < entry < TP; Short: TP < entry < SL (SL đỏ ở trên).
const PD = { d: null, hit: 5, eat: false, cur: false };

function pdLocal(ev) {
  const r = PB.layer.getBoundingClientRect();
  return { x: ev.clientX - r.left, y: ev.clientY - r.top };
}

// Vùng giá chính (bên trái trục giá, không lấn pane RSI) — giống zoombox.js
function pdInArea(p) {
  const el = PB.layer.parentElement;
  let h = el.clientHeight;
  try { h = CH.chart.paneSize(0).height; } catch (e) { /* bản cũ */ }
  return p.x >= 0 && p.y >= 0 && p.x <= el.clientWidth - CH.chart.priceScale("right").width() && p.y <= h;
}

// Phần box dưới con trỏ -> {b, part}; part null = trong box nhưng không trúng chỗ kéo. Không trúng box -> null.
// Vị thế đã khớp: entry = giá khớp nên không kéo entry/thân.
function pdHit(p, alt) {
  for (let i = PB.boxes.length - 1; i >= 0; i--) {
    const b = PB.boxes[i], g = b.geom;
    if (!g || b.busy) continue;
    if (p.x < g.x0 - 3 || p.x > g.x1 + PD.hit || p.y < g.top - PD.hit || p.y > g.bot + PD.hit) continue;
    const near = (yy) => Math.abs(p.y - yy) <= PD.hit, fixed = b.kind === "position" || b.fixedEntry;
    if (near(g.yt)) return { b, part: "tp" };
    if (near(g.ys)) return { b, part: "sl" };
    if (near(g.ye) && !fixed) return { b, part: "entry" };
    if (Math.abs(p.x - g.x1) <= PD.hit) return { b, part: "width" };
    return { b, part: alt && !fixed ? "body" : null };
  }
  return null;
}

function pdCursor(part) { return part === "width" ? "ew-resize" : part === "body" ? "move" : part ? "ns-resize" : ""; }

// Short được phép? Luyện tập: stock + crypto; thật (Alpaca): chỉ cổ phiếu (Alpaca không cho bán khống crypto)
function pdShortOk() { return RP.on || CH.asset !== "crypto"; }

function pdSyncShortBtn() {
  const btn = document.getElementById("pos-short-btn"), ok = pdShortOk();
  btn.disabled = !ok;
  btn.parentElement.title = ok ? "Short Position (Shift+P): bán khống, SL trên – TP dưới"
    : "Alpaca không cho bán khống crypto (chỉ short crypto trong Bar Replay)";
  if (!ok && PB.tool === "short") pdToolOn(false);
}

// side: false (tắt) | "long" | "short". PB.tool = chiều box sẽ tạo khi bấm lên chart
function pdToolOn(side) {
  if (side === "short" && !pdShortOk()) side = false;
  PB.tool = side || false;
  document.getElementById("pos-long-btn").classList.toggle("active", PB.tool === "long");
  document.getElementById("pos-short-btn").classList.toggle("active", PB.tool === "short");
  document.getElementById("chart").style.cursor = PB.tool ? "crosshair" : "";
}

// Box nháp tại điểm bấm: entry = giá con trỏ, SL/TP theo ATR14 của các nến tới nến được bấm
function pdCreate(p) {
  const price = CH.candles.coordinateToPrice(p.y), lg = CH.chart.timeScale().coordinateToLogical(p.x);
  if (price === null || !(price > 0) || lg === null || !D.bars.length) return;
  const i = Math.max(0, Math.min(D.bars.length - 1, Math.round(lg))), side = PB.tool === "short" ? "short" : "long";
  const lv = { ...PM.defaults(price, PM.atr(D.bars.slice(0, i + 1)), pbStep(), side), side };
  if (!PM.valid(lv)) return;
  for (const x of PB.boxes.filter((x) => x.kind === "draft")) pbRemove(x);
  const b = { key: "draft", kind: "draft", symbol: CH.symbol, account: RP.on ? "practice" : "real", ...lv, t0: D.bars[i].time,
              width: 20, qty: 0, type: null, dirty: false };
  PB.boxes.push(b);
  PB.sel = b;
  pdToolOn(false);
  posChanged(b);
}

function pdDown(ev) {
  if (ev.type === "mousedown" && PD.eat) { PD.eat = false; ev.stopPropagation(); ev.preventDefault(); return; }
  if (ev.button !== 0 || ev.shiftKey || PD.d || RP.picking) return;
  const p = pdLocal(ev);
  if (!pdInArea(p)) return;
  const eat = () => { ev.stopPropagation(); ev.preventDefault(); PD.eat = ev.type === "pointerdown"; };
  if (PB.tool) { eat(); pdCreate(p); return; }
  const h = pdHit(p, ev.altKey);
  if (!h) return;
  if (PB.sel !== h.b) { PB.sel = h.b; posChanged(h.b, true); }
  if (!h.part) { pbSchedule(); return; }  // bấm trong box (không trúng cạnh): chỉ chọn, chart vẫn kéo được
  eat();
  const b = h.b;
  PD.d = { b, part: h.part, lv: { entry: b.entry, sl: b.sl, tp: b.tp, side: b.side || "long" }, grab: CH.candles.coordinateToPrice(p.y), moved: false };
  CH.chart.applyOptions({ handleScroll: false, handleScale: false });  // kéo box thì chart đứng yên
  document.getElementById("chart").style.cursor = pdCursor(h.part);
}

function pdMove(ev) {
  const p = pdLocal(ev);
  if (!PD.d) {  // rê chuột: đổi con trỏ khi ở trên cạnh kéo được
    if (PB.tool || !ev.target || !PB.layer.parentElement.contains(ev.target)) return;
    const h = pdInArea(p) ? pdHit(p, ev.altKey) : null, c = h ? pdCursor(h.part) : "";
    if (c || PD.cur) document.getElementById("chart").style.cursor = c;
    PD.cur = !!c;
    return;
  }
  const d = PD.d, b = d.b;
  if (d.part === "width") {
    const lg = CH.chart.timeScale().coordinateToLogical(p.x);
    if (lg !== null) b.width = Math.max(3, Math.round(lg - pbIndexOf(b.t0)));
  } else {
    const price = CH.candles.coordinateToPrice(p.y);
    if (price === null || d.grab === null) return;
    Object.assign(b, PM.drag(d.lv, d.part, price, pbStep(), d.grab, b.kind === "position"));
    d.moved = b.entry !== d.lv.entry || b.sl !== d.lv.sl || b.tp !== d.lv.tp;
  }
  posChanged(b);
  pbSchedule();
}

function pdUp() {
  if (!PD.d) return;
  const d = PD.d;
  PD.d = null;
  CH.chart.applyOptions({ handleScroll: true, handleScale: true });
  document.getElementById("chart").style.cursor = "";
  if (d.moved) posDragEnd(d.b, d.lv);
}

function initPositionDrag() {
  const el = document.getElementById("chart");
  el.addEventListener("pointerdown", pdDown, true);  // capture: chạy trước handler của chart
  el.addEventListener("mousedown", pdDown, true);
  document.addEventListener("mousemove", pdMove, true);
  document.addEventListener("mouseup", pdUp, true);
  const toggle = (side) => pdToolOn(PB.tool === side ? false : side);
  document.getElementById("pos-long-btn").addEventListener("click", () => toggle("long"));
  document.getElementById("pos-short-btn").addEventListener("click", () => toggle("short"));
  pdSyncShortBtn();
  document.addEventListener("keydown", (ev) => {
    if (RM.ignoreKey(ev)) return;
    const k = ev.key.toLowerCase();
    if (k === "p") { ev.preventDefault(); toggle(ev.shiftKey ? "short" : "long"); }  // P = Long, Shift+P = Short
    else if (k === "escape" && PB.tool) pdToolOn(false);
    else if ((k === "delete" || k === "backspace") && PB.sel && PB.sel.kind === "draft") { posDiscard(); }
  });
}
