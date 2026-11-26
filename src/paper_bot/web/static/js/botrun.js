// Nút "Chạy bot 1 vòng": dry-run chạy ngay; chạy THẬT (gửi lệnh paper) phải gõ XAC NHAN.
// Toàn bộ chốt chặn nằm ở server (tái dùng ui.core), ở đây chỉ gửi yêu cầu và hiện kết quả.
let _botRunning = false;

function botSymbols() {
  return $("bot-symbols").value.toUpperCase().split(/[\s,;]+/).filter(Boolean);
}

function showBotOutput(text, bad) {
  const el = $("bot-output");
  el.hidden = false;
  el.textContent = text;
  el.className = bad ? "err" : "";
}

async function runBot(dry) {
  if (_botRunning) return;
  const symbols = botSymbols();
  if (!symbols.length) { showBotOutput("Nhập ít nhất 1 mã", true); return; }
  const body = { symbols, dry_run: dry };
  if (!dry) {
    const typed = await askConfirm("Chạy bot THẬT 1 vòng",
      summaryTable([["Mã", symbols.join(", ")], ["Chế độ", "THẬT (gửi lệnh lên tài khoản paper)"], ["Engine", "full"]]) +
      `<p class="err small">Bot có thể gửi lệnh mua/bán thật lên tài khoản paper.</p>`);
    if (typed === null) return;
    body.confirm = typed;
  }
  _botRunning = true;
  setBotButtons(true);
  showBotOutput(dry ? "Đang chạy thử (dry-run)…" : "Đang chạy bot…");
  try {
    const r = await sendJSON("POST", "/api/bot/run", body);
    const head = r.timed_out ? "[HẾT GIỜ]" : `[mã thoát ${r.returncode}]`;
    showBotOutput(`${head} ${r.dry_run ? "dry-run" : "THẬT"}\n${r.stdout}${r.stderr ? "\n--- stderr ---\n" + r.stderr : ""}`,
                  r.timed_out || r.returncode !== 0);
    if (!dry) refreshPanel();
  } catch (e) {
    showBotOutput("Lỗi: " + e.message, true);
  } finally {
    _botRunning = false;
    setBotButtons(false);
  }
}

function setBotButtons(disabled) {
  $("bot-dry").disabled = disabled;
  $("bot-real").disabled = disabled;
}

function initBotUI() {
  $("bot-dry").addEventListener("click", () => runBot(true));
  $("bot-real").addEventListener("click", () => runBot(false));
}
