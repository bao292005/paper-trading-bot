// Biểu đồ Lightweight Charts v5: nến + volume + MA (pane 0), RSI (pane 1), markers, price line.
const NY_TZ = "America/New_York";
const MA_COLORS = { ma10: "#f7c948", ma30: "#2196f3", ma200: "#e040fb" };
const CH = {
  chart: null, candles: null, volume: null, ma: {}, rsi: null, markerApi: null,
  lines: [], markerByTime: new Map(), symbol: "AAPL", tf: "1D", asset: "stock",
};

// Múi giờ HIỂN THỊ (chỉ đổi nhãn trục/crosshair, không đổi dữ liệu). Người dùng chọn ở ô #tz-select.
const TZ_CHOICES = { "Asia/Ho_Chi_Minh": "Giờ VN (UTC+7)", "UTC": "UTC", "America/New_York": "New York" };
const TZ_KEY = "ptb.chartTz";
const TZ_DEFAULT = "Asia/Ho_Chi_Minh";
let _fmtClock, _fmtDay;
CH.tz = TZ_DEFAULT;

function savedTz() {
  try { const t = localStorage.getItem(TZ_KEY); if (t in TZ_CHOICES) return t; } catch (e) { /* localStorage bị chặn */ }
  return TZ_DEFAULT;
}

// Đặt múi giờ hiển thị; nhận tên múi giờ trong TZ_CHOICES (giá trị lạ -> mặc định giờ VN)
function setChartTz(tz) {
  if (!(tz in TZ_CHOICES)) tz = TZ_DEFAULT;
  CH.tz = tz;
  _fmtClock = new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false });
  _fmtDay = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" });
  try { localStorage.setItem(TZ_KEY, tz); } catch (e) { /* bỏ qua */ }
  // Ép biểu đồ vẽ lại nhãn thời gian theo múi giờ mới
  if (CH.chart) CH.chart.applyOptions({ localization: { timeFormatter: nyLabel }, timeScale: { tickMarkFormatter: tickFormatter } });
}
setChartTz(savedTz());

// `time` từ server là unix giây UTC; hiển thị theo múi giờ đang chọn (CH.tz). Tên hàm giữ "ny" cho tương thích.
function nyClock(sec) { return _fmtClock.format(new Date(sec * 1000)); }
function nyDay(sec) { return _fmtDay.format(new Date(sec * 1000)); }
function nyLabel(sec) { return CH.tf === "1D" ? nyDay(sec) : nyDay(sec) + " " + nyClock(sec); }

function tickFormatter(sec, type) {
  const day = nyDay(sec);  // yyyy-mm-dd
  if (type === 0) return day.slice(0, 4);
  if (type === 1) return day.slice(0, 7);
  if (type === 2) return day.slice(5);
  return nyClock(sec);
}

function initChart(container) {
  const LW = LightweightCharts;
  CH.chart = LW.createChart(container, {
    autoSize: true,
    layout: { background: { type: LW.ColorType.Solid, color: "#131722" }, textColor: "#d1d4dc",
              panes: { separatorColor: "#2a2e39" } },
    grid: { vertLines: { color: "#1e222d" }, horzLines: { color: "#1e222d" } },
    crosshair: { mode: LW.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#2a2e39" },
    // Zoom: cuộn chuột neo theo vị trí con trỏ, kéo trục giá/thời gian để co giãn, không quán tính khi kéo bằng chuột
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: { time: true, price: true },
                   axisDoubleClickReset: { time: true, price: true } },
    kineticScroll: { mouse: false, touch: true },
    timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: false, tickMarkFormatter: tickFormatter,
                 shiftVisibleRangeOnNewBar: true, rightOffset: ZM.PAD },  // đang ở mép phải thì theo nến mới; xem quá khứ thì giữ nguyên
    localization: { timeFormatter: nyLabel },
  });
  CH.candles = CH.chart.addSeries(LW.CandlestickSeries, {
    upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
    wickUpColor: "#26a69a", wickDownColor: "#ef5350",
  });
  CH.candles.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.22 } });
  CH.volume = CH.chart.addSeries(LW.HistogramSeries, {
    priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false,
  });
  CH.chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  for (const [name, color] of Object.entries(MA_COLORS)) {
    CH.ma[name] = CH.chart.addSeries(LW.LineSeries, {
      color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
  }
  CH.markerApi = LW.createSeriesMarkers(CH.candles, []);
  return CH.chart;
}

// RSI nằm ở pane riêng: bật = tạo series, tắt = xoá series (pane trống tự biến mất)
function setRsiVisible(on, data) {
  const LW = LightweightCharts;
  if (!on && CH.rsi) { CH.chart.removeSeries(CH.rsi); CH.rsi = null; return; }
  if (on && !CH.rsi) {
    CH.rsi = CH.chart.addSeries(LW.LineSeries, {
      color: "#b39ddb", lineWidth: 1, priceLineVisible: false, title: "RSI14",
      autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
    }, 1);
    for (const v of [30, 70]) {
      CH.rsi.createPriceLine({ price: v, color: "#4a4e59", lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: false });
    }
    try { CH.chart.panes()[1].setHeight(130); } catch (e) { /* bản cũ không có setHeight */ }
    if (data) CH.rsi.setData(data);
  }
}

function setSeriesVisible(series, on) { series.applyOptions({ visible: on }); }

// Gộp tín hiệu + lệnh khớp thành markers (đã sắp theo time); giữ tooltip để hiện khi rê chuột
function setMarkers(items) {
  const sorted = [...items].sort((a, b) => a.time - b.time);
  CH.markerByTime = new Map();
  for (const m of sorted) {
    if (!CH.markerByTime.has(m.time)) CH.markerByTime.set(m.time, []);
    CH.markerByTime.get(m.time).push(m);
  }
  CH.markerApi.setMarkers(sorted.map((m) => ({
    time: m.time, position: m.position, shape: m.shape, color: m.color, text: m.text,
  })));
}

// Price line: giá vào, SL, TP, lệnh chờ của mã đang xem. items = [{price, color, title, dashed}]
function setPriceLines(items) {
  for (const l of CH.lines) CH.candles.removePriceLine(l);
  CH.lines = items.map((it) => CH.candles.createPriceLine({
    price: it.price, color: it.color, title: it.title, lineWidth: 1,
    lineStyle: it.dashed ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid,
    axisLabelVisible: true,
  }));
}
