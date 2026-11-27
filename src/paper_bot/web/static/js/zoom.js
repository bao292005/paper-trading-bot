// Zoom cho biểu đồ: nút +/−/reset/hiện tại/fit, phím tắt, box zoom (Shift+kéo), trục giá, nhớ vùng khi đổi khung.
// attachZoom(chart, mainSeries, containerEl, { bar, active }): bar = div chứa nút có thuộc tính data-z.
// Phần tính toán nằm ở zoom_math.js, trục giá ở pricescale.js, box ở zoombox.js.
const ZOOM_STEP = 0.78;  // mỗi lần phóng ~22%

function attachZoom(chart, series, el, opts) {
  opts = opts || {};
  const ts = chart.timeScale();
  const q = (k) => (opts.bar ? opts.bar.querySelector(`[data-z="${k}"]`) : null);
  const ps = attachPriceScale(chart, series, { mode: q("mode"), auto: q("auto") });
  const count = () => series.data().length;
  const view = () => ts.getVisibleLogicalRange();
  const setView = (r) => { if (r) ts.setVisibleLogicalRange(r); };
  const isActive = opts.active || (() => el.offsetParent !== null);  // chart đang hiện trên trang
  let saved = null;

  function zoomBy(factor) {
    const r = view();
    if (!r) return;
    const last = count() - 1;  // thấy nến cuối thì phóng quanh nến cuối, không thì quanh tâm
    setView(ZM.zoomRange(r, factor, last >= r.from && last <= r.to ? last : undefined));
  }
  const api = {
    zoomIn: () => zoomBy(ZOOM_STEP),
    zoomOut: () => zoomBy(1 / ZOOM_STEP),
    scroll: (frac) => { const r = view(); if (r) setView(ZM.scrollRange(r, frac)); },
    toStart: () => { const r = view(); if (r) setView(ZM.alignStart(r)); },  // nến đầu đã tải
    toLatest: () => ts.scrollToRealTime(),
    fitAll: () => ts.fitContent(),
    reset() { ps.setAuto(true); setView(ZM.lastNRange(count())); },  // ~150 nến cuối + bỏ giá thủ công
    setMode: ps.setMode, setAuto: ps.setAuto,
    saveRange() {  // nhớ khoảng THỜI GIAN đang xem (trước khi đổi khung)
      const tr = ts.getVisibleRange(), r = view();
      saved = tr && r ? { from: tr.from, to: tr.to, atEdge: ZM.atRightEdge(r, count() - 1) } : null;
      return saved;
    },
    restoreRange(bars) {  // sau khi nạp dữ liệu khung mới: cùng khoảng thời gian, không đủ dữ liệu thì ~150 nến
      const lg = ZM.timeRangeToLogical(bars || series.data(), saved);
      saved = null;
      if (lg) { ps.setAuto(true); setView(lg); } else api.reset();
    },
  };

  const box = attachZoomBox(chart, series, el, {
    priceOn: () => ps.getMode() === "normal",
    apply(sel) { setView(sel.logical); if (sel.price) ps.setManual(sel.price); },
  });

  const actions = { in: api.zoomIn, out: api.zoomOut, reset: api.reset, latest: api.toLatest, fit: api.fitAll };
  if (opts.bar) {
    opts.bar.addEventListener("click", (ev) => {
      const b = ev.target.closest && ev.target.closest("[data-z]");
      if (b && actions[b.dataset.z]) actions[b.dataset.z]();
    });
  }

  const keys = {
    "+": api.zoomIn, "=": api.zoomIn, "-": api.zoomOut, "0": api.reset, Home: api.toStart, End: api.toLatest,
    ArrowLeft: () => api.scroll(-0.1), ArrowRight: () => api.scroll(0.1),
    l: ps.toggleLog, a: () => ps.setAuto(!series.priceScale().options().autoScale),
    Escape: box.cancel,
  };
  document.addEventListener("keydown", (ev) => {
    const t = ev.target, tag = t && t.tagName;
    if (ev.ctrlKey || ev.metaKey || ev.altKey || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (t && t.isContentEditable) return;
    if (ev.key === "ArrowRight" && !ev.shiftKey && opts.rightKeyTaken && opts.rightKeyTaken()) return;  // replay: → = step
    const fn = keys[ev.key.length === 1 ? ev.key.toLowerCase() : ev.key];
    if (fn && isActive() && (fn() !== false || ev.key !== "Escape")) ev.preventDefault();
  });

  // Double-click trên trục giá -> auto-scale lại; kéo/cuộn trục giá làm thư viện tự tắt auto -> cập nhật nút
  el.addEventListener("dblclick", (ev) => {
    const x = ev.clientX - el.getBoundingClientRect().left;
    if (x > el.clientWidth - chart.priceScale("right").width()) ps.setAuto(true);
  });
  for (const name of ["mouseup", "wheel"]) el.addEventListener(name, () => setTimeout(ps.syncUi, 0));
  return api;
}
