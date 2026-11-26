// Visual mode (phần vẽ): biểu đồ nến ngày + markers + SL/TP + equity nhỏ. Nến lấy qua /api/bars (tf=1D).
const VC = { chart: null, candles: null, markers: null, lines: [], eqChart: null, eq: null, loadSeq: 0 };

function initVisual() {
  if (VC.chart) return;
  const LW = LightweightCharts;
  VC.chart = LW.createChart($("v-chart"), { ...chartTheme(), timeScale: { borderColor: "#2a2e39", timeVisible: false } });
  VC.candles = VC.chart.addSeries(LW.CandlestickSeries, {
    upColor: "#26a69a", downColor: "#ef5350", borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350" });
  VC.markers = LW.createSeriesMarkers(VC.candles, []);
  VC.eqChart = LW.createChart($("v-eq"), { ...chartTheme(), timeScale: { borderColor: "#2a2e39", timeVisible: false } });
  VC.eq = VC.eqChart.addSeries(LW.LineSeries, { color: "#26a69a", lineWidth: 2, priceLineVisible: false, title: "Equity" });
}

function setVisualData(bars, markers, eqPoints) {  // vẽ lại toàn bộ (khi nhảy/đổi mã)
  VC.candles.setData(bars);
  VC.markers.setMarkers(markers);
  VC.eq.setData(eqPoints);
  const n = bars.length;
  VC.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 120), to: n + 8 });
  VC.eqChart.timeScale().fitContent();
}

function pushVisual(bar, markers, eqPoint) {  // thêm 1 ngày (khi phát)
  if (bar) VC.candles.update(bar);
  VC.markers.setMarkers(markers);
  VC.eq.update(eqPoint);
  VC.eqChart.timeScale().fitContent();
}

function setVisualLines(items) {  // đường giá vào / SL / TP
  for (const l of VC.lines) VC.candles.removePriceLine(l);
  VC.lines = items.map((it) => VC.candles.createPriceLine({
    price: it.price, color: it.color, title: it.title, lineWidth: 1, axisLabelVisible: true,
    lineStyle: it.dashed ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid }));
}

// Tải nến ngày của mã trong đúng khoảng của run (không vượt `to`, nên không đụng dữ liệu holdout)
async function loadVisualSymbol(symbol) {
  const res = TESTER.result, seq = ++VC.loadSeq;
  initVisual();
  $("v-info").textContent = "Đang tải nến " + symbol + "…";
  try {
    const r = await getJSON("/api/bars", { symbol, tf: "1D", start: res.config.from, end: res.config.to });
    if (seq !== VC.loadSeq) return;  // người dùng đã đổi mã trong lúc chờ
    setupReplay(res, symbol, r.bars);
  } catch (e) {
    $("v-info").textContent = "Không tải được nến: " + e.message;
  }
}

function onVisualResult(res) {  // có kết quả run mới: nạp danh sách mã, hiện mã đầu
  pauseReplay();
  $("v-symbol").innerHTML = res.config.symbols.map((s) => `<option>${s}</option>`).join("");
  hideMinute();
  if ($("v-symbol").value) loadVisualSymbol($("v-symbol").value);
}

function initVisualControls() {
  TESTER.onResult.push(onVisualResult);
  TESTER.onShow.visual = () => {  // chart tạo khi pane còn ẩn: về đúng kích thước khi hiện
    if (VC.chart && RP.days.length) seekReplay(RP.i);
  };
  $("v-symbol").onchange = (e) => loadVisualSymbol(e.target.value);
  $("v-play").onclick = togglePlay;
  $("v-step").onclick = () => { pauseReplay(); advanceReplay(); };
  $("v-speed").onchange = (e) => { RP.speed = e.target.value; };
  $("v-slider").oninput = (e) => { pauseReplay(); seekReplay(Number(e.target.value)); };
}
