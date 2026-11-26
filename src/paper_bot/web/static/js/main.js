// Khởi động trang "/": dựng biểu đồ, nối ô tìm mã, nút khung giờ, công tắc chỉ báo, chọn run.
async function reload() {
  await loadAll();
  refreshLegend();
  updatePriceLines();
}

function selectSymbol(raw) {
  const sym = String(raw || "").trim().toUpperCase();
  if (!/^[A-Z]{1,6}$/.test(sym) || sym === CH.symbol) {
    document.getElementById("symbol-input").value = CH.symbol;
    return;
  }
  CH.symbol = sym;
  document.getElementById("symbol-input").value = sym;
  reload();
  switchLive();
  syncOrderSymbol(sym);
}

function selectTf(tf) {
  if (tf === CH.tf) return;
  CH.tf = tf;
  for (const b of document.querySelectorAll("#tf-buttons button")) b.classList.toggle("active", b.dataset.tf === tf);
  reload();
  switchLive();
}

async function loadSymbols() {
  try {
    const { symbols } = await getJSON("/api/symbols");
    document.getElementById("symbols-list").innerHTML = symbols.map((s) => `<option value="${esc(s)}">`).join("");
  } catch (e) { /* gợi ý mã là phụ, bỏ qua lỗi */ }
}

function bindToolbar() {
  const input = document.getElementById("symbol-input");
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") selectSymbol(input.value); });
  input.addEventListener("change", () => selectSymbol(input.value));
  document.getElementById("tf-buttons").addEventListener("click", (ev) => {
    if (ev.target.dataset.tf) selectTf(ev.target.dataset.tf);
  });
  for (const k of ["ma10", "ma30", "ma200"]) {
    document.getElementById("chk-" + k).addEventListener("change", (ev) => setSeriesVisible(CH.ma[k], ev.target.checked));
  }
  document.getElementById("chk-vol").addEventListener("change", (ev) => setSeriesVisible(CH.volume, ev.target.checked));
  document.getElementById("chk-rsi").addEventListener("change", (ev) => setRsiVisible(ev.target.checked, D.ind.rsi14));
  document.getElementById("run-select").addEventListener("change", (ev) => { D.runId = ev.target.value; loadMarkers(); });
}

window.addEventListener("DOMContentLoaded", () => {
  CH.symbol = document.getElementById("symbol-input").value.trim().toUpperCase() || "AAPL";
  initChart(document.getElementById("chart"));
  setRsiVisible(document.getElementById("chk-rsi").checked);
  initLegend();
  watchOlderLoading();
  bindToolbar();
  loadSymbols();
  loadRuns();
  reload();
  connectLive();
  initPanel();
  initOrderUI();
  initBotUI();
});
