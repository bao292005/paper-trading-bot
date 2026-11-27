// Dữ liệu khi replay: mọi nến/chỉ báo lấy từ /api/replay/bars (server cắt đúng tại con trỏ, không lộ tương lai).
function replayParams(extra) {
  return Object.assign({ symbol: CH.symbol, tf: CH.tf, asset: CH.asset, cursor: String(RP.cursor) }, extra || {});
}

// Nạp lại toàn bộ (vào replay / đổi khung trong replay). steps>0: tiến con trỏ trước rồi mới cắt
async function replayLoadAll(keepRange, steps) {
  const seq = ++D.seq;
  if (keepRange && CH.zoom) CH.zoom.saveRange();
  D.bars = []; D.volume = []; D.ind = {}; D.oldest = null; D.exhausted = false;
  setLoadStatus("Replay: đang tải " + CH.symbol + "…");
  try {
    const b = await getJSON("/api/replay/bars", replayParams(steps ? { steps: String(steps) } : null));
    if (seq !== D.seq) return null;
    RP.cursor = b.cursor;
    D.bars = b.bars; D.volume = b.volume; D.oldest = b.oldest; D.ind = b.ind;
    applyAll();
    if (CH.zoom) { if (keepRange) CH.zoom.restoreRange(D.bars); else CH.zoom.reset(); }
    setLoadStatus(b.bars.length ? (b.fallback ? "Thiếu nến 1m của ngày đang chạy: chỉ hiện ngày đã đóng" : "")
      : "Không có dữ liệu trước điểm này");
    return b;
  } catch (e) {
    if (seq === D.seq) setLoadStatus(e.message, true);
    return null;
  } finally {
    replayAfterLoad();
  }
}

// Kéo về quá khứ trong replay: nến cũ hơn, vẫn không vượt con trỏ
async function replayLoadOlder() {
  if (D.loading || D.exhausted || D.oldest === null) return;
  D.loading = true;
  const seq = D.seq;
  try {
    const b = await getJSON("/api/replay/bars", replayParams({ before: String(D.oldest) }));
    if (seq !== D.seq) return;
    if (!b.bars.length) { D.exhausted = true; return; }
    const range = CH.chart.timeScale().getVisibleLogicalRange();
    D.bars = b.bars.concat(D.bars);
    D.volume = b.volume.concat(D.volume);
    D.oldest = b.oldest;
    for (const k of IND_KEYS) D.ind[k] = (b.ind[k] || []).concat(D.ind[k] || []);
    applyAll();
    if (range) CH.chart.timeScale().setVisibleLogicalRange(ZM.shiftRange(range, b.bars.length));
  } catch (e) {
    setLoadStatus(e.message, true);
  } finally {
    D.loading = false;
  }
}

// Tiến 1 nến khung đang xem: chỉ lấy phần từ nến cuối đang hiện (nến đó có thể còn đang hình thành)
async function replayStepData() {
  const last = D.bars[D.bars.length - 1];
  if (!last) return replayLoadAll(false, 1);
  const seq = D.seq;
  const b = await getJSON("/api/replay/bars", replayParams({ steps: "1", after: String(last.time) }));
  if (seq !== D.seq) return null;  // đã đổi mã/khung trong lúc chờ
  RP.cursor = b.cursor;
  D.bars = RM.mergeByTime(D.bars, b.bars);
  D.volume = RM.mergeByTime(D.volume, b.volume);
  for (const bar of b.bars) CH.candles.update(bar);  // update: đang ở mép phải thì biểu đồ tự theo
  for (const v of b.volume) CH.volume.update(v);
  for (const k of IND_KEYS) {
    D.ind[k] = RM.mergeByTime(D.ind[k] || [], b.ind[k] || []);
    const s = k === "rsi14" ? CH.rsi : CH.ma[k];
    if (s) for (const p of b.ind[k] || []) s.update(p);
  }
  refreshLegend();
  return b;
}
