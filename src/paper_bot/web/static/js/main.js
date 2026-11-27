// Khởi động trang "/": dựng biểu đồ, nối ô tìm mã, nút khung giờ, công tắc chỉ báo, chọn run.
async function reload(keepRange) {  // keepRange: đổi khung thì giữ khoảng thời gian đang xem
  await loadAll(keepRange);
  refreshLegend();
  updatePriceLines();
  posSync();      // đổi mã/khung: bỏ box của mã cũ
  posSyncReal();  // thường: hỏi box của mã mới (replay: không làm gì)
}

const CRYPTO_SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD"];  // whitelist, khớp server

// Chuẩn hoá mã theo loại tài sản; mã không hợp lệ trả null. Crypto: "btc" -> "BTC/USD", chỉ nhận whitelist.
function normalizeSymbol(raw, asset) {
  let sym = String(raw || "").trim().toUpperCase();
  if (asset === "crypto") {
    if (/^[A-Z]{2,5}$/.test(sym)) sym += "/USD";
    return CRYPTO_SYMBOLS.includes(sym) ? sym : null;
  }
  return /^[A-Z]{1,6}$/.test(sym) ? sym : null;
}

function selectSymbol(raw, asset) {
  // Bấm vào vị thế/lệnh crypto từ panel: tự chuyển sang chế độ crypto
  const kind = asset || (CRYPTO_SYMBOLS.includes(String(raw).toUpperCase()) ? "crypto" : CH.asset);
  if (kind !== CH.asset) { changeAsset(kind, raw); return; }
  const sym = normalizeSymbol(raw, CH.asset);
  if (sym === null || sym === CH.symbol) {
    document.getElementById("symbol-input").value = CH.symbol;
    return;
  }
  replayExit();  // tài khoản luyện tập gắn với 1 mã: đổi mã = kết thúc replay (phiên được lưu)
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
  reload(true);
  switchLive();
}

async function loadSymbols() {
  try {
    const { symbols } = await getJSON("/api/symbols", { asset: CH.asset });
    document.getElementById("symbols-list").innerHTML = symbols.map((s) => `<option value="${esc(s)}">`).join("");
  } catch (e) { /* gợi ý mã là phụ, bỏ qua lỗi */ }
}

// Đổi Stock <-> Crypto: mỗi loại có danh sách mã riêng, form lệnh/bot riêng (múi giờ hiển thị giữ theo #tz-select)
function changeAsset(asset, wantedSymbol) {
  replayExit();
  CH.asset = asset;
  document.getElementById("asset-select").value = asset;
  const sym = normalizeSymbol(wantedSymbol, asset) || (asset === "crypto" ? CRYPTO_SYMBOLS[0] : "AAPL");
  CH.symbol = sym;
  document.getElementById("symbol-input").value = sym;
  loadSymbols();
  reload();
  switchLive();
  syncOrderAsset();
  syncBotAsset();
}

function bindToolbar() {
  const input = document.getElementById("symbol-input");
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") selectSymbol(input.value); });
  input.addEventListener("change", () => selectSymbol(input.value));
  document.getElementById("asset-select").addEventListener("change", (ev) => changeAsset(ev.target.value));
  document.getElementById("tf-buttons").addEventListener("click", (ev) => {
    if (ev.target.dataset.tf) selectTf(ev.target.dataset.tf);
  });
  for (const k of ["ma10", "ma30", "ma200"]) {
    document.getElementById("chk-" + k).addEventListener("change", (ev) => setSeriesVisible(CH.ma[k], ev.target.checked));
  }
  document.getElementById("chk-vol").addEventListener("change", (ev) => setSeriesVisible(CH.volume, ev.target.checked));
  document.getElementById("chk-rsi").addEventListener("change", (ev) => setRsiVisible(ev.target.checked, D.ind.rsi14));
  document.getElementById("run-select").addEventListener("change", (ev) => { D.runId = ev.target.value; loadMarkers(); });
  // Ô chọn múi giờ hiển thị (mặc định giờ VN, nhớ trong localStorage)
  const tzSel = document.getElementById("tz-select");
  tzSel.innerHTML = Object.entries(TZ_CHOICES).map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join("");
  tzSel.value = CH.tz;
  tzSel.addEventListener("change", (ev) => { setChartTz(ev.target.value); refreshLegend(); });
}

window.addEventListener("DOMContentLoaded", () => {
  CH.symbol = document.getElementById("symbol-input").value.trim().toUpperCase() || "AAPL";
  initChart(document.getElementById("chart"));
  CH.zoom = attachZoom(CH.chart, CH.candles, document.getElementById("chart"), { bar: document.getElementById("zoombar"),
                                                                         rightKeyTaken: () => RP.on });
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
  initReplay();
  initPractice();
  initPositionBox();
  initPositionDrag();
  initPositionOrders();
  initSltp();
});
