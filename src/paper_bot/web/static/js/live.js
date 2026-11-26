// Realtime qua WebSocket /ws/live: cập nhật nến cuối; mất kết nối thì hiện trạng thái và tự nối lại.
const LIVE = { ws: null, timer: null, delay: 1000, closing: false, market: "", stream: "" };

function setLiveBadge(text, level) {
  const el = document.getElementById("live-status");
  el.textContent = "Realtime: " + text;
  el.className = "badge " + level;  // ok | warn | bad
}

function showServerStatus(msg) {
  LIVE.market = msg.market; LIVE.stream = msg.stream;
  if (msg.stream === "reconnecting" || msg.stream === "error") {
    setLiveBadge(msg.message || "stream lỗi, đang nối lại", "bad");
  } else if (msg.market === "closed") {
    setLiveBadge("thị trường đóng cửa", "warn");
  } else if (msg.stream === "connected") {
    setLiveBadge("đang chạy", "ok");
  } else {
    setLiveBadge("đang kết nối stream…", "warn");
  }
}

// Nến đang chạy từ server: thay nến cuối hoặc thêm nến mới
function applyLiveBar(m) {
  if (m.symbol !== CH.symbol || m.tf !== CH.tf) return;
  const last = D.bars[D.bars.length - 1];
  if (last && m.time < last.time) return;
  const bar = { time: m.time, open: m.open, high: m.high, low: m.low, close: m.close };
  const vol = { time: m.time, value: m.volume,
                color: m.close >= m.open ? "#26a69a80" : "#ef535080" };
  if (last && last.time === m.time) { D.bars[D.bars.length - 1] = bar; D.volume[D.volume.length - 1] = vol; }
  else { D.bars.push(bar); D.volume.push(vol); }
  CH.candles.update(bar);
  CH.volume.update(vol);
  refreshLegend();
}

function connectLive() {
  clearTimeout(LIVE.timer);
  if (LIVE.ws) { LIVE.closing = true; LIVE.ws.close(); }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const q = new URLSearchParams({ symbol: CH.symbol, tf: CH.tf });
  const ws = new WebSocket(`${proto}://${location.host}/ws/live?${q}`);
  LIVE.ws = ws; LIVE.closing = false;
  setLiveBadge("đang kết nối…", "warn");
  ws.onopen = () => { LIVE.delay = 1000; };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "status") showServerStatus(msg);
    else if (msg.type === "bar") applyLiveBar(msg);
  };
  ws.onclose = () => {
    if (LIVE.ws !== ws || LIVE.closing) return;  // đóng chủ động khi đổi mã/khung
    setLiveBadge("mất kết nối, thử lại sau " + LIVE.delay / 1000 + "s", "bad");
    LIVE.timer = setTimeout(connectLive, LIVE.delay);
    LIVE.delay = Math.min(LIVE.delay * 2, 30000);
  };
}

// Đổi mã/khung: gửi yêu cầu trên cùng kết nối (server hỗ trợ), nếu chưa mở thì nối lại
function switchLive() {
  if (LIVE.ws && LIVE.ws.readyState === WebSocket.OPEN) {
    LIVE.ws.send(JSON.stringify({ symbol: CH.symbol, tf: CH.tf }));
  } else {
    connectLive();
  }
}
