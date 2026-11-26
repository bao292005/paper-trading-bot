// Nạp dữ liệu cho biểu đồ: nến + chỉ báo + markers; kéo về quá khứ thì tải thêm.
const D = { bars: [], volume: [], ind: {}, oldest: null, exhausted: false, loading: false, seq: 0, runId: "" };
const IND_KEYS = ["ma10", "ma30", "ma200", "rsi14"];

function setLoadStatus(text, bad) {
  const el = document.getElementById("load-status");
  el.textContent = text || "";
  el.className = bad ? "err" : "muted";
}

function applyIndicators() {
  for (const k of ["ma10", "ma30", "ma200"]) CH.ma[k].setData(D.ind[k] || []);
  if (CH.rsi) CH.rsi.setData(D.ind.rsi14 || []);
}

function applyAll() {
  CH.candles.setData(D.bars);
  CH.volume.setData(D.volume);
  applyIndicators();
}

// Tải mới hoàn toàn khi đổi mã / khung. keepRange = true (đổi khung): giữ cùng khoảng THỜI GIAN đang xem;
// false (lần đầu / đổi mã): hiện ~150 nến gần nhất
async function loadAll(keepRange) {
  const seq = ++D.seq;
  if (keepRange && CH.zoom) CH.zoom.saveRange();
  D.bars = []; D.volume = []; D.ind = {}; D.oldest = null; D.exhausted = false;
  const params = { symbol: CH.symbol, tf: CH.tf, asset: CH.asset };
  setLoadStatus("Đang tải " + CH.symbol + "…");
  try {
    const [b, ind] = await Promise.all([getJSON("/api/bars", params), getJSON("/api/indicators", params)]);
    if (seq !== D.seq) return;  // người dùng đã đổi mã/khung trong lúc chờ
    D.bars = b.bars; D.volume = b.volume; D.oldest = b.oldest; D.ind = ind;
    applyAll();
    if (CH.zoom) { if (keepRange) CH.zoom.restoreRange(D.bars); else CH.zoom.reset(); }
    setLoadStatus(b.bars.length ? "" : "Không có dữ liệu");
  } catch (e) {
    if (seq === D.seq) setLoadStatus(e.message, true);
  }
  loadMarkers();
}

// Kéo sát mép trái -> tải đoạn cũ hơn và nối vào đầu, giữ nguyên vị trí đang xem
async function loadOlder() {
  if (D.loading || D.exhausted || D.oldest === null) return;
  D.loading = true;
  const seq = D.seq;
  const params = { symbol: CH.symbol, tf: CH.tf, asset: CH.asset, before: String(D.oldest) };
  setLoadStatus("Đang tải dữ liệu cũ hơn…");
  try {
    const [b, ind] = await Promise.all([getJSON("/api/bars", params), getJSON("/api/indicators", params)]);
    if (seq !== D.seq) return;
    if (!b.bars.length) { D.exhausted = true; setLoadStatus("Đã hết dữ liệu cũ"); return; }
    const range = CH.chart.timeScale().getVisibleLogicalRange();
    D.bars = b.bars.concat(D.bars);
    D.volume = b.volume.concat(D.volume);
    D.oldest = b.oldest;
    for (const k of IND_KEYS) D.ind[k] = (ind[k] || []).concat(D.ind[k] || []);
    applyAll();
    if (range) {  // setData làm lệch chỉ số logic: dịch lại đúng số nến vừa thêm
      CH.chart.timeScale().setVisibleLogicalRange(ZM.shiftRange(range, b.bars.length));
    }
    setLoadStatus("");
  } catch (e) {
    setLoadStatus(e.message, true);
  } finally {
    D.loading = false;
  }
}

async function loadMarkers() {
  if (!D.runId) { setMarkers([]); return; }
  try {
    const m = await getJSON("/api/markers", { symbol: CH.symbol, run_id: D.runId, tf: CH.tf, asset: CH.asset });
    setMarkers([...m.signals, ...m.fills]);
  } catch (e) {
    setMarkers([]);
    setLoadStatus("Markers: " + e.message, true);
  }
}

async function loadRuns() {
  try {
    const { runs } = await getJSON("/api/runs");
    const sel = document.getElementById("run-select");
    for (const r of runs) sel.add(new Option(r, r));
  } catch (e) { /* không có DB thì bỏ qua */ }
}

function watchOlderLoading() {
  CH.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (range && range.from < 20) loadOlder();
  });
}
