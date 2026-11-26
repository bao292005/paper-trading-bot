// Visual mode (phần phát lại): đi từng ngày của `replay` (kết quả run_tester), vẽ nến ngày của 1 mã,
// markers vào/ra lệnh, đường SL/TP khi đang giữ vị thế, equity chạy cùng. Phần vẽ nằm ở tester_visual.js.
const RP = { sym: null, days: [], bars: new Map(), i: 0, playing: false, timer: null, speed: "1", markers: [] };

// Nến ngày của server có time = 12:00 UTC của ngày đó
function dayUnix(d) {
  const [y, m, dd] = d.split("-").map(Number);
  return Date.UTC(y, m - 1, dd, 12) / 1000;
}
const unixDay = (t) => new Date(t * 1000).toISOString().slice(0, 10);

function setupReplay(res, symbol, bars) {
  pauseReplay();
  RP.sym = symbol;
  RP.days = res.replay;
  RP.bars = new Map(bars.map((b) => [unixDay(b.time), b]));
  $("v-slider").max = Math.max(0, RP.days.length - 1);
  seekReplay(0);
}

function dayMarkers(day) {
  return day.fills.filter((f) => f.symbol === RP.sym).map((f) => f.side === "buy"
    ? { time: dayUnix(day.date), position: "belowBar", shape: "arrowUp", color: "#26a69a", text: `B ${f.qty}` }
    : { time: dayUnix(day.date), position: "aboveBar", shape: "arrowDown", color: "#ef5350", text: `S ${f.qty} ${f.reason || ""}` });
}

// Đường giá của vị thế đang mở (cuối ngày `day`): giá vào, SL, TP
function dayLines(day) {
  const lv = day.levels.find((l) => l.symbol === RP.sym);
  if (!lv) return [];
  const out = [{ price: lv.entry_price, color: "#9598a1", title: `Vào ${lv.qty}`, dashed: false }];
  if (lv.stop_loss) out.push({ price: lv.stop_loss, color: "#ef5350", title: "SL", dashed: true });
  if (lv.take_profit) out.push({ price: lv.take_profit, color: "#26a69a", title: "TP", dashed: true });
  return out;
}

function dayInfo(day) {
  const mine = day.fills.filter((f) => f.symbol === RP.sym)
    .map((f) => `${f.side === "buy" ? "MUA" : "BÁN"} ${f.qty}@${fmt.num(f.price)}${f.reason ? " (" + f.reason + ")" : ""}`);
  const orders = day.orders.filter((o) => o.symbol === RP.sym).map((o) => `${o.side} ${o.qty}: ${o.reason}`);
  return `Equity ${fmt.usd(day.equity)} · Balance ${fmt.usd(day.balance)}` +
    (mine.length ? " · Khớp: " + mine.join(", ") : "") + (orders.length ? " · Quyết định cuối ngày: " + orders.join("; ") : "");
}

function drawStatus() {
  const day = RP.days[RP.i];
  $("v-slider").value = RP.i;
  $("v-date").textContent = `${day.date} (${RP.i + 1}/${RP.days.length})`;
  $("v-info").textContent = dayInfo(day);
  setVisualLines(dayLines(day));
}

function seekReplay(i) {  // nhảy tới ngày i, vẽ lại từ đầu
  if (!RP.days.length) return;
  RP.i = Math.min(Math.max(0, i), RP.days.length - 1);
  const upto = RP.days.slice(0, RP.i + 1);
  RP.markers = upto.flatMap(dayMarkers);
  const bars = upto.map((d) => RP.bars.get(d.date)).filter(Boolean);
  setVisualData(bars, RP.markers, upto.map((d) => ({ time: dayUnix(d.date), value: d.equity })));
  drawStatus();
}

function advanceReplay() {  // tiến 1 ngày; false nếu đã hết
  if (RP.i >= RP.days.length - 1) return false;
  const day = RP.days[++RP.i], bar = RP.bars.get(day.date);
  RP.markers.push(...dayMarkers(day));
  pushVisual(bar, RP.markers, { time: dayUnix(day.date), value: day.equity });
  drawStatus();
  return true;
}

function pauseReplay() {
  RP.playing = false;
  clearTimeout(RP.timer);
  $("v-play").innerHTML = "&#9654; Play";
}

function loopReplay() {
  if (!RP.playing) return;
  const fast = RP.speed === "max";
  for (let k = 0; k < (fast ? 8 : 1); k++) if (!advanceReplay()) { pauseReplay(); return; }
  RP.timer = setTimeout(loopReplay, fast ? 16 : Math.round(400 / Number(RP.speed)));
}

function togglePlay() {
  if (RP.playing) return pauseReplay();
  if (RP.i >= RP.days.length - 1) seekReplay(0);  // hết rồi thì phát lại từ đầu
  RP.playing = true;
  $("v-play").innerHTML = "&#10074;&#10074; Pause";
  loopReplay();
}
