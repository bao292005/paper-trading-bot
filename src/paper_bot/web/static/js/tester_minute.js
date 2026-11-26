// Visual mode: bấm 1 lệnh trong bảng "Lệnh" -> mở khung nến 5m của ngày vào / ngày ra (qua /api/bars).
const MN = { chart: null, candles: null, lines: [], trade: null, which: "entry", seq: 0 };
const NY_CLOCK = new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });
const nyHm = (sec) => NY_CLOCK.format(new Date(sec * 1000));

function initMinute() {
  if (MN.chart) return;
  const LW = LightweightCharts;
  MN.chart = LW.createChart($("vm-chart"), { ...chartTheme(), timeScale: { borderColor: "#2a2e39", timeVisible: true,
    tickMarkFormatter: (t) => nyHm(t) }, localization: { timeFormatter: nyHm } });
  MN.candles = MN.chart.addSeries(LW.CandlestickSeries, {
    upColor: "#26a69a", downColor: "#ef5350", borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350" });
}

function hideMinute() { $("v-min").hidden = true; }

function minuteLines(t) {  // giá vào/ra, SL, TP của lệnh
  const items = [["Vào", t.entry_price, "#9598a1", false], ["Ra", t.exit_price, "#2962ff", false],
                 ["SL", t.stop_loss, "#ef5350", true], ["TP", t.take_profit, "#26a69a", true]];
  return items.filter((it) => it[1]).map(([title, price, color, dashed]) => MN.candles.createPriceLine({
    price, color, title, lineWidth: 1, axisLabelVisible: true,
    lineStyle: dashed ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid }));
}

async function showMinute(t, which) {
  MN.trade = t;
  MN.which = which;
  const day = which === "entry" ? t.entry_date : t.exit_date, seq = ++MN.seq;
  $("v-min").hidden = false;
  initMinute();
  $("vm-title").textContent = `${t.symbol} 5m · ${day} (${which === "entry" ? "ngày vào" : "ngày ra"})`;
  $("vm-entry").classList.toggle("t-active-day", which === "entry");
  $("vm-exit").classList.toggle("t-active-day", which === "exit");
  $("vm-msg").textContent = "Đang tải…";
  $("vm-msg").className = "small muted";
  try {
    const r = await getJSON("/api/bars", { symbol: t.symbol, tf: "5m", start: day, end: day + " 23:59:59" });
    if (seq !== MN.seq) return;
    MN.candles.setData(r.bars);
    for (const l of MN.lines) MN.candles.removePriceLine(l);
    MN.lines = minuteLines(t);
    MN.chart.timeScale().fitContent();
    $("vm-msg").textContent = r.bars.length ? "" : "Không có nến phút cho ngày này (chưa có cache nến phút).";
  } catch (e) {
    $("vm-msg").textContent = e.message;
    $("vm-msg").className = "small err";
  }
}

function openTradeMinute(t) {  // gọi khi bấm dòng trong bảng lệnh
  showTab("visual");
  showMinute(t, "entry");
}

function initMinuteControls() {
  $("vm-entry").onclick = () => MN.trade && showMinute(MN.trade, "entry");
  $("vm-exit").onclick = () => MN.trade && showMinute(MN.trade, "exit");
  $("vm-close").onclick = hideMinute;
}
