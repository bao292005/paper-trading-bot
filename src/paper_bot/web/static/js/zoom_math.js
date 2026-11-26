// Tính toán thuần cho zoom biểu đồ (không đụng DOM/chart) để test bằng node.
// "range" = vùng nhìn theo chỉ số logic {from, to}; chỉ số nến thứ i = i.
const ZM = {
  MIN_BARS: 8, MAX_BARS: 200000, DEFAULT_BARS: 150, PAD: 5, MIN_PX: 5,
  MODES: { normal: 0, log: 1, percent: 2 },  // khớp LightweightCharts.PriceScaleMode

  // factor < 1: phóng to, > 1: thu nhỏ; điểm `anchor` (chỉ số logic) giữ nguyên vị trí tương đối
  zoomRange(range, factor, anchor) {
    const w = range.to - range.from;
    const a = anchor === undefined ? range.from + w / 2 : anchor;
    const nw = Math.min(Math.max(w * factor, ZM.MIN_BARS), ZM.MAX_BARS);
    const k = w > 0 ? (a - range.from) / w : 0.5;  // anchor nằm ở k% chiều rộng
    return { from: a - k * nw, to: a + (1 - k) * nw };
  },

  // Cuộn ngang: frac = 0.1 là 10% chiều rộng vùng nhìn (âm = sang trái)
  scrollRange(range, frac) {
    const d = (range.to - range.from) * frac;
    return { from: range.from + d, to: range.to + d };
  },

  // Hiện `count` nến cuối của dữ liệu n nến, chừa pad ô trống bên phải
  lastNRange(n, count, pad) {
    const c = count === undefined ? ZM.DEFAULT_BARS : count;
    return { from: Math.max(n - c, 0) - 0.5, to: n - 1 + (pad === undefined ? ZM.PAD : pad) };
  },

  // Đưa nến đầu tiên về sát mép trái, giữ nguyên độ rộng
  alignStart(range) {
    const w = range.to - range.from;
    return { from: -0.5, to: -0.5 + w };
  },

  // Sau khi thêm n nến vào ĐẦU dữ liệu, mọi chỉ số logic cũ lệch +n: dịch lại để vùng nhìn không nhảy
  shiftRange(range, n) { return { from: range.from + n, to: range.to + n }; },

  // Đang ở mép phải (thấy nến cuối)? lastIndex = chỉ số nến cuối
  atRightEdge(range, lastIndex) { return !!range && range.to >= lastIndex - 0.5; },

  // Toạ độ chọn (px) -> vùng logic + khoảng giá. p1, p2 = {x, y}; toLogical(x), toPrice(y) lấy từ chart.
  // Quá nhỏ (< MIN_PX theo một trong hai chiều) hoặc không đổi được toạ độ -> null.
  // priceOn = false: chỉ lấy khoảng thời gian (chế độ Log/% không đặt giá tay được).
  boxSelection(p1, p2, toLogical, toPrice, priceOn) {
    if (Math.abs(p1.x - p2.x) < ZM.MIN_PX || Math.abs(p1.y - p2.y) < ZM.MIN_PX) return null;
    const l1 = toLogical(Math.min(p1.x, p2.x)), l2 = toLogical(Math.max(p1.x, p2.x));
    if (l1 === null || l2 === null || l1 === undefined || l2 === undefined) return null;
    const out = { logical: { from: l1, to: Math.max(l2, l1 + 3) }, price: null };
    if (priceOn) {
      const a = toPrice(p1.y), b = toPrice(p2.y);
      if (a !== null && b !== null && a !== b) out.price = { from: Math.min(a, b), to: Math.max(a, b) };
    }
    return out;
  },

  // Chỉ số nến đầu tiên có time >= t (tìm nhị phân); bars sắp theo time tăng dần
  lowerBound(bars, t) {
    let lo = 0, hi = bars.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (bars[m].time < t) lo = m + 1; else hi = m; }
    return lo;
  },

  // Khung thời gian đổi: đổi khoảng THỜI GIAN đã lưu tr = {from, to, atEdge} sang vùng logic trên bars mới.
  // Trả null khi dữ liệu mới chưa phủ tới from, hoặc quá ít nến (< 10) -> caller lùi về ~150 nến.
  timeRangeToLogical(bars, tr, pad) {
    if (!tr || !bars.length || tr.from < bars[0].time) return null;
    const lo = ZM.lowerBound(bars, tr.from);
    const hi = ZM.lowerBound(bars, tr.to + 1) - 1;  // nến cuối có time <= to
    if (hi - lo + 1 < 10) return null;
    const p = pad === undefined ? ZM.PAD : pad;
    return { from: lo - 0.5, to: tr.atEdge ? bars.length - 1 + p : hi + 0.5 };
  },
};
