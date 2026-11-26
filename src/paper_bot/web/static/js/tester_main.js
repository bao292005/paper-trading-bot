// Nối các phần của trang /tester: tab, nút Start, lưu DB, CSV.
function showTab(name) {
  document.querySelectorAll("#t-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".t-pane").forEach((p) => { p.hidden = p.id !== "pane-" + name; });
  if (TESTER.onShow[name]) TESTER.onShow[name]();
}

function setMsg(text, cls) {
  $("run-msg").textContent = text;
  $("run-msg").className = "small " + (cls || "");
}

// Có kết quả run mới (từ nút Start hoặc từ tab Optimization): vẽ lại mọi tab
function setResult(job, switchTo = "report") {
  const res = job.result;
  TESTER.result = res;
  TESTER.jobId = job.id;
  renderReport(res);
  renderTesterChart(res.curve);
  setTrades(res.trades, openTradeMinute);
  for (const fn of TESTER.onResult) fn(res);
  showTab(switchTo);
}

async function startRun() {
  let body;
  try { body = readSettings(); } catch (e) { setMsg(e.message, "err"); return; }  // chặn ngay ở trình duyệt
  $("btn-start").disabled = true;
  setMsg("Đang chạy… (B2 vài giây, FULL có thể lâu hơn)");
  showProgress("run-bar", null);
  try {
    const job = await runJob("/api/tester/run", body, (j) => showProgress("run-bar", j));
    setMsg("Xong sau " + job.elapsed_s + " giây.", "ok");
    setResult(job);
  } catch (e) {
    setMsg(e.message, "err");
  } finally {
    $("btn-start").disabled = false;
    $("run-bar").hidden = true;
  }
}

async function saveToDb() {
  const msg = $("save-msg");
  const name = $("save-name").value.trim();
  if (!TESTER.jobId) { msg.textContent = "Chưa có kết quả để lưu."; msg.className = "small err"; return; }
  if (!/^[a-z0-9-]{1,40}$/.test(name)) { msg.textContent = "Tên chỉ gồm a-z, 0-9, dấu '-'."; msg.className = "small err"; return; }
  try {
    const r = await sendJSON("POST", "/api/tester/save", { job_id: TESTER.jobId, name });
    msg.textContent = "Đã lưu: " + r.run_id;
    msg.className = "small ok";
  } catch (e) {
    msg.textContent = e.message;
    msg.className = "small err";
  }
}

async function initTester() {
  document.querySelectorAll("#t-tabs button").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
  $("btn-start").onclick = startRun;
  $("btn-csv").onclick = exportTradesCsv;
  $("btn-save").onclick = saveToDb;
  initVisualControls();
  initMinuteControls();
  TESTER.onShow.chart =() => { if (TC.chart) TC.chart.timeScale().fitContent(); };
  $("s-asset").onchange = changeAsset;
  try {
    TESTER.opts = await getJSON("/api/tester/options", { asset: $("s-asset").value });
    buildForm(TESTER.opts);
    initOptimization();
  } catch (e) {
    setMsg("Không tải được danh sách mã: " + e.message, "err");
  }
}

// Đổi Stock/Crypto: tải lại danh sách mã, ngày, phí mặc định (kết quả cũ vẫn giữ nguyên asset của nó)
async function changeAsset() {
  try {
    TESTER.opts = await getJSON("/api/tester/options", { asset: $("s-asset").value });
    buildForm(TESTER.opts);
    setMsg("Đã đổi sang " + TESTER.opts.asset + ": mã, ngày, phí mặc định theo loại tài sản.");
  } catch (e) {
    setMsg("Không tải được danh sách mã: " + e.message, "err");
  }
}

initTester();
