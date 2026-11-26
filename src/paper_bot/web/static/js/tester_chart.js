// Tab "Đồ thị": Balance (xanh dương) + Equity (xanh lục) ở pane trên, Drawdown % ở pane dưới.
// time = chuỗi "YYYY-MM-DD" (Lightweight Charts hiểu là ngày).
const TC = { chart: null, balance: null, equity: null, dd: null };

function chartTheme() {
  const LW = LightweightCharts;
  return {
    autoSize: true,
    layout: { background: { type: LW.ColorType.Solid, color: "#131722" }, textColor: "#d1d4dc",
              panes: { separatorColor: "#2a2e39" } },
    grid: { vertLines: { color: "#1e222d" }, horzLines: { color: "#1e222d" } },
    rightPriceScale: { borderColor: "#2a2e39" },
    timeScale: { borderColor: "#2a2e39" },
  };
}

function initTesterChart() {
  const LW = LightweightCharts;
  TC.chart = LW.createChart($("bal-chart"), chartTheme());
  const line = (color, pane = 0) => TC.chart.addSeries(LW.LineSeries, { color, lineWidth: 2, priceLineVisible: false }, pane);
  TC.balance = line("#2962ff");
  TC.equity = line("#26a69a");
  TC.dd = TC.chart.addSeries(LW.AreaSeries, {
    lineColor: "#ef5350", topColor: "rgba(239,83,80,0.05)", bottomColor: "rgba(239,83,80,0.35)",
    lineWidth: 1, priceLineVisible: false, priceFormat: { type: "custom", formatter: (v) => v.toFixed(1) + "%" },
  }, 1);
  try { TC.chart.panes()[1].setHeight(140); } catch (e) { /* bản cũ không có setHeight */ }
}

// Drawdown % = equity / đỉnh equity trước đó - 1 (số âm)
function drawdownSeries(curve) {
  let peak = 0;
  return curve.map((p) => {
    peak = Math.max(peak, p.equity);
    return { time: p.date, value: peak > 0 ? 100 * (p.equity / peak - 1) : 0 };
  });
}

function renderTesterChart(curve) {
  if (!TC.chart) initTesterChart();
  TC.balance.setData(curve.map((p) => ({ time: p.date, value: p.balance })));
  TC.equity.setData(curve.map((p) => ({ time: p.date, value: p.equity })));
  TC.dd.setData(drawdownSeries(curve));
  TC.chart.timeScale().fitContent();
}
