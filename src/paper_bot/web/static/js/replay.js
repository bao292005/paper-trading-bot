// Bar replay (như TradingView): chọn điểm bắt đầu -> ẩn nến sau con trỏ, step/play, đổi khung vẫn cắt đúng con trỏ.
// Phím: R = chọn điểm bắt đầu, → = step (Shift+→ = cuộn), Space = play/pause, Esc = huỷ chọn.
const RP = { on: false, picking: false, cursor: null, playing: false, speed: 1, timer: null, busy: false, end: false };
const $rp = (id) => document.getElementById(id);

function replayShowBar() {
  $rp("replay-bar").hidden = !(RP.on || RP.picking);
  $rp("replay-pick").hidden = !RP.picking;
  $rp("replay-run").hidden = !RP.on || RP.picking;
  $rp("replay-btn").classList.toggle("active", RP.on || RP.picking);
  document.body.classList.toggle("replay-on", RP.on);
  $rp("rp-play").textContent = RP.playing ? "⏸" : "▶";
  $rp("rp-speed").textContent = RP.speed + "x";
  $rp("rp-time").textContent = RP.cursor ? `${nyDay(RP.cursor)} ${nyClock(RP.cursor)}` : "-";
  $rp("rp-holdout").classList.toggle("hot", !!(RP.cursor && RM.inHoldout(RP.cursor)));  // ≥ 2025: tô đậm cảnh báo
}

// Đường dọc theo chuột khi đang chọn điểm bắt đầu
function replayCrosshair(picking) {
  const v = picking ? { color: "#ff9800", width: 2, style: 0, labelBackgroundColor: "#ff9800" }
                    : { color: "#758696", width: 1, style: 3, labelBackgroundColor: "#4c525e" };
  CH.chart.applyOptions({ crosshair: { vertLine: v, horzLine: { visible: !picking, labelVisible: !picking } } });
}

function replayPick() {
  replayPause();
  RP.picking = true;
  const inp = $rp("rp-date");
  if (!inp.value && D.bars.length) inp.value = nyDay(D.bars[Math.max(0, D.bars.length - 30)].time);
  $rp("rp-tz").textContent = TZ_CHOICES[CH.tz];
  replayCrosshair(true);
  replayShowBar();
}

function replayCancelPick() {
  RP.picking = false;
  replayCrosshair(false);
  replayShowBar();
}

// Bắt đầu tại thời điểm `sec`. steps=1: nến được bấm (bắt đầu lúc sec) hiện đủ, con trỏ = lúc nến đó đóng
async function replayStart(sec, steps) {
  if (RP.on) await practiceEnd(true);  // chọn lại điểm bắt đầu = phiên luyện tập mới
  RP.picking = false; RP.on = true; RP.end = false; RP.cursor = sec;
  replayCrosshair(false);
  pauseLive();
  replayShowBar();
  const b = await replayLoadAll(false, steps);
  if (!b) { replayShowBar(); return; }
  replayShowBar();
  await practiceStart(RP.cursor);
}

function replayStartFromInput() {
  const sec = RM.zonedToUnix($rp("rp-date").value, CH.tz);
  if (sec === null) { setLoadStatus("Ngày giờ không hợp lệ (YYYY-MM-DD hoặc YYYY-MM-DD HH:MM)", true); return; }
  replayStart(sec, 0);
}

async function replayStep() {
  if (!RP.on || RP.busy || RP.end) return false;
  RP.busy = true;
  try {
    const b = await replayStepData();
    if (b && b.end) { RP.end = true; replayPause(); setLoadStatus("Replay: hết dữ liệu"); }
    replayShowBar();
    await practiceAdvance(RP.cursor, 1);
    return !!b && !b.end;
  } catch (e) {
    setLoadStatus(e.message, true);
    replayPause();
    return false;
  } finally {
    RP.busy = false;
  }
}

function replayPause() {
  RP.playing = false;
  clearTimeout(RP.timer);
  if ($rp("replay-bar")) replayShowBar();
}

function replayPlay() {
  if (RP.playing) { replayPause(); return; }
  if (!RP.on || RP.end) return;
  RP.playing = true;
  replayShowBar();
  const tick = async () => {
    if (!RP.playing) return;
    const ok = await replayStep();
    if (ok && RP.playing) RP.timer = setTimeout(tick, RM.delayMs(RP.speed));
  };
  tick();
}

// Thoát replay: kết thúc + lưu phiên luyện tập, về biểu đồ thường + realtime
async function replayExit() {
  if (!RP.on && !RP.picking) return;
  replayPause();
  const wasOn = RP.on;
  RP.on = false; RP.picking = false; RP.cursor = null;
  replayCrosshair(false);
  replayShowBar();
  if (wasOn) { await practiceEnd(false); reload(); resumeLive(); }
}

function replayAfterLoad() {
  if (!RP.on) return;
  practiceRefreshChart();
  refreshLegend();
}

function initReplay() {
  $rp("replay-btn").addEventListener("click", () => (RP.picking ? replayCancelPick() : replayPick()));
  $rp("rp-go").addEventListener("click", replayStartFromInput);
  $rp("rp-date").addEventListener("keydown", (ev) => { if (ev.key === "Enter") replayStartFromInput(); });
  $rp("rp-cancel").addEventListener("click", () => (RP.on ? replayCancelPick() : replayExit()));
  $rp("rp-new").addEventListener("click", replayPick);
  $rp("rp-play").addEventListener("click", replayPlay);
  $rp("rp-step").addEventListener("click", replayStep);
  $rp("rp-speed").addEventListener("click", () => { RP.speed = RM.nextSpeed(RP.speed); replayShowBar(); });
  $rp("rp-exit").addEventListener("click", replayExit);
  CH.chart.subscribeClick((param) => {
    if (RP.picking && param.time) replayStart(param.time, 1);
  });
  document.addEventListener("keydown", (ev) => {
    if (RM.ignoreKey(ev)) return;
    const k = ev.key;
    if (k === "r" || k === "R") { ev.preventDefault(); RP.picking ? replayCancelPick() : replayPick(); }
    else if (k === "Escape" && RP.picking) replayCancelPick();
    else if (!RP.on) return;
    else if (k === "ArrowRight" && !ev.shiftKey) { ev.preventDefault(); replayPause(); replayStep(); }
    else if (k === " ") { ev.preventDefault(); replayPlay(); }
  });
  replayShowBar();
}
