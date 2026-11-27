// Realtime qua WebSocket /ws/live: nến cuối nhảy theo từng giao dịch (server gộp ≤ 4 tin/giây).
// - Badge: "Realtime ● (độ trễ Xs)" xanh; vàng khi stream Alpaca đang thử lại (kèm lý do); đỏ khi mất kết nối.
// - Dự phòng: WS im lặng > 20s khi thị trường mở (crypto: luôn mở) -> poll /api/bars mỗi 10s; WS có tin lại thì ngừng.
// - WS rớt: tự nối lại (1 -> 15s). Tab ẩn rồi hiện lại: nối lại ngay + tải bù nến thiếu.
// - Đang replay: bỏ qua mọi tin realtime, dừng poll.
const LIVE = {
  ws: null, timer: null, delay: 1000, closing: false, open: false, reconnectAt: 0,
  market: "", stream: "", reason: "", retryUntil: 0,
  lastMsgAt: 0, lastTs: 0, lag: 0, pollTimer: null, ticker: null,
};
const LIVE_STALE_MS = 20000, LIVE_POLL_MS = 10000, LIVE_MAX_DELAY = 15000;
const TF_SECONDS = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1D": 86400 };

function replayOn() { return typeof RP !== "undefined" && RP.on; }
function marketOpen() { return CH.asset === "crypto" || LIVE.market === "open"; }

function setLiveBadge(text, level) {
  const el = document.getElementById("live-status");
  el.textContent = "Realtime " + text;
  el.className = "badge " + level;  // ok | warn | bad
}

// Vẽ lại badge theo trạng thái hiện tại (gọi mỗi giây và khi có tin trạng thái)
function renderLiveBadge(now) {
  const secs = (ms) => Math.max(0, Math.ceil(ms / 1000));
  if (replayOn()) return setLiveBadge("tạm dừng (replay)", "warn");
  if (!LIVE.open) return setLiveBadge("● mất kết nối, thử lại sau " + secs(LIVE.reconnectAt - now) + "s", "bad");
  const left = secs(LIVE.retryUntil - now);
  if (LIVE.stream === "limit") return setLiveBadge("● Alpaca đang giữ kết nối cũ, thử lại sau " + left + "s", "warn");
  if (LIVE.stream === "reconnecting" || LIVE.stream === "error") {
    return setLiveBadge("● đang thử lại sau " + left + "s: " + (LIVE.reason || "stream lỗi"), "warn");
  }
  if (!marketOpen()) return setLiveBadge("● thị trường đóng cửa", "warn");
  if (LIVE.pollTimer) return setLiveBadge("○ dự phòng REST (10s/lần), chờ stream…", "warn");
  if (LIVE.lastTs) return setLiveBadge("● (độ trễ " + LIVE.lag.toFixed(1) + "s)", "ok");
  if (LIVE.stream === "connected") return setLiveBadge("● (chờ giao dịch…)", "ok");
  setLiveBadge("● đang kết nối stream…", "warn");
}

function showServerStatus(msg) {
  LIVE.market = msg.market; LIVE.stream = msg.stream; LIVE.reason = msg.reason || msg.message || "";
  LIVE.retryUntil = Date.now() + (msg.retry_in || 0) * 1000;
  // server gửi trạng thái mỗi 10s: stream "connected" = WS + stream Alpaca còn sống dù chưa có trade -> không cần poll
  if (msg.stream === "connected") { LIVE.lastMsgAt = Date.now(); stopPoll(); }
  renderLiveBadge(Date.now());
}

// Ghi 1 nến (cùng time -> thay nến cuối, time mới -> thêm) + volume + MA/RSI điểm cuối + legend
function updateLastBar(m) {
  const last = D.bars[D.bars.length - 1];
  if (last && m.time < last.time) return false;
  const bar = { time: m.time, open: m.open, high: m.high, low: m.low, close: m.close };
  const vol = { time: m.time, value: m.volume, color: m.close >= m.open ? "#26a69a80" : "#ef535080" };
  if (last && last.time === m.time) { D.bars[D.bars.length - 1] = bar; D.volume[D.volume.length - 1] = vol; }
  else { D.bars.push(bar); D.volume.push(vol); }
  CH.candles.update(bar);
  CH.volume.update(vol);
  const vals = IL.liveValues(D.bars);
  for (const k of IND_KEYS) {
    if (!D.ind[k]) D.ind[k] = [];
    const p = IL.upsertLast(D.ind[k], m.time, vals[k]);
    const series = k === "rsi14" ? CH.rsi : CH.ma[k];
    if (p && series) series.update(p);
  }
  refreshLegend();
  return true;
}

// Tin "bar" từ WS: nến đang chạy của mã/khung đang xem
function applyLiveBar(m) {
  if (replayOn()) return;  // replay: không nhận nến realtime
  if (m.symbol !== CH.symbol || m.tf !== CH.tf || (m.asset || "stock") !== CH.asset) return;
  LIVE.lastMsgAt = Date.now();
  // độ trễ đo lúc NHẬN trade mới (ts đổi); tin bar không kèm trade mới giữ số cũ (crypto có khi cả phút không có trade)
  if (m.ts && m.ts !== LIVE.lastTs) { LIVE.lastTs = m.ts; LIVE.lag = Math.max(0, Date.now() / 1000 - m.ts); }
  stopPoll();  // WS hoạt động lại: ngừng dự phòng
  updateLastBar(m);
}

// ---------- dự phòng REST ----------
// Lấy vài nến cuối qua /api/bars rồi ghi vào biểu đồ (cũng dùng để tải bù khi tab hiện lại)
async function pollOnce() {
  if (replayOn()) { stopPoll(); return; }
  const last = D.bars[D.bars.length - 1];
  if (!last) return;
  const seq = D.seq, start = last.time - 3 * (TF_SECONDS[CH.tf] || 60);
  try {
    const b = await getJSON("/api/bars", { symbol: CH.symbol, tf: CH.tf, asset: CH.asset, start: String(start) });
    if (seq !== D.seq || replayOn()) return;  // đã đổi mã/khung hoặc vào replay
    const cur = D.bars[D.bars.length - 1];
    if (b.bars.length && cur && b.bars[0].time > cur.time) { loadAll(true); return; }  // hổng lớn: tải lại hẳn
    b.bars.forEach((x, i) => updateLastBar({ ...x, volume: b.volume[i] ? b.volume[i].value : 0 }));
  } catch (e) { /* lỗi mạng: lần sau thử lại */ }
}

function startPoll() {
  if (LIVE.pollTimer || replayOn()) return;
  LIVE.pollTimer = setInterval(pollOnce, LIVE_POLL_MS);
  pollOnce();
}

function stopPoll() {
  if (LIVE.pollTimer) { clearInterval(LIVE.pollTimer); LIVE.pollTimer = null; }
}

// Mỗi giây: cập nhật độ trễ / đếm ngược; WS im lặng quá lâu khi thị trường mở -> bật poll
function liveTick() {
  const now = Date.now();
  if (!replayOn() && marketOpen() && now - LIVE.lastMsgAt > LIVE_STALE_MS) startPoll();
  renderLiveBadge(now);
}

function initLiveOnce() {
  if (LIVE.ticker) return;
  LIVE.ticker = setInterval(liveTick, 1000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || replayOn()) return;
    if (!LIVE.ws || LIVE.ws.readyState > 1) { LIVE.delay = 1000; connectLive(); }  // CLOSING/CLOSED: nối ngay
    pollOnce();  // tải bù các nến bị thiếu lúc tab ẩn
  });
}

// ---------- WebSocket ----------
// Replay: tạm dừng realtime (đóng WS, dừng poll), thoát replay thì nối lại
function pauseLive() {
  clearTimeout(LIVE.timer);
  stopPoll();
  if (LIVE.ws) { LIVE.closing = true; LIVE.ws.close(); LIVE.ws = null; }
  setLiveBadge("tạm dừng (replay)", "warn");
}

function resumeLive() { connectLive(); }

function resetLiveData() { LIVE.lastTs = 0; LIVE.lastMsgAt = Date.now(); stopPoll(); }

function connectLive() {
  if (replayOn()) return;  // đang replay: không nối realtime
  initLiveOnce();
  clearTimeout(LIVE.timer);
  if (LIVE.ws) { LIVE.closing = true; LIVE.ws.close(); }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const q = new URLSearchParams({ symbol: CH.symbol, tf: CH.tf, asset: CH.asset });
  const ws = new WebSocket(`${proto}://${location.host}/ws/live?${q}`);
  LIVE.ws = ws; LIVE.closing = false; LIVE.open = true; LIVE.stream = "";
  resetLiveData();
  setLiveBadge("● đang kết nối…", "warn");
  ws.onopen = () => { LIVE.delay = 1000; };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "status") showServerStatus(msg);
    else if (msg.type === "bar") applyLiveBar(msg);
  };
  ws.onclose = () => {
    if (LIVE.ws !== ws || LIVE.closing) return;  // đóng chủ động khi đổi mã/khung
    LIVE.open = false; LIVE.reconnectAt = Date.now() + LIVE.delay;
    renderLiveBadge(Date.now());
    LIVE.timer = setTimeout(connectLive, LIVE.delay);
    LIVE.delay = Math.min(LIVE.delay * 2, LIVE_MAX_DELAY);
  };
}

// Đổi mã/khung: gửi yêu cầu trên cùng kết nối (server hỗ trợ), nếu chưa mở thì nối lại
function switchLive() {
  if (replayOn()) return;
  if (LIVE.ws && LIVE.ws.readyState === WebSocket.OPEN) {
    resetLiveData();
    LIVE.ws.send(JSON.stringify({ symbol: CH.symbol, tf: CH.tf, asset: CH.asset }));
  } else {
    connectLive();
  }
}
