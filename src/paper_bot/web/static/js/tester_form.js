// Panel "Cài đặt" của trang /tester: dựng form, đọc form -> body gửi server (kiểm tra trước ở trình duyệt).
// Mỗi tham số: [khoá, nhãn, mặc định, bước]
const PARAM_DEFS = {
  strategy_params: [["ma_short", "MA ngắn", 10, 1], ["ma_long", "MA dài", 30, 1], ["rsi_period", "RSI period", 14, 1],
                    ["rsi_oversold", "RSI oversold", 30, 1], ["rsi_overbought", "RSI overbought", 70, 1]],
  risk: [["atr_stop_mult", "ATR stop (x)", 2, 0.1], ["take_profit_mult", "Take profit (x ATR)", 3, 0.1],
         ["risk_per_trade", "Risk / lệnh", 0.01, 0.001]],
};
const FULL_ONLY = ["atr_stop_mult", "take_profit_mult"];  // B2_goc không có SL/TP theo ATR
const FORM = { opts: null };

function fieldId(section, key) { return (section === "risk" ? "r-" : "p-") + key; }

function buildForm(opts) {
  FORM.opts = opts;
  for (const [section, box] of [["strategy_params", "f-params"], ["risk", "f-risk"]]) {
    $(box).innerHTML = PARAM_DEFS[section].map(([k, label, v, step]) =>
      `<label>${label} <input id="${fieldId(section, k)}" type="number" value="${v}" step="${step}"></label>`).join("");
  }
  $("s-symbols").innerHTML = opts.universe.map((s) =>
    `<label><input type="checkbox" value="${s}"${opts.universe.indexOf(s) < 5 ? " checked" : ""}> ${s}</label>`).join("");
  $("f-flags").innerHTML = opts.flags.map((f) =>
    `<label class="flag-row"><input type="checkbox" id="f-${f}" checked> ${f.replace("use_", "")}</label>`).join("");
  $("date-note").textContent = "Tối đa " + opts.max_date + ": " + opts.holdout_note;
  applyAssetDefaults(opts);
  $("s-symbols").onchange = onSymbolChange;  // gán (không addEventListener) vì buildForm chạy lại khi đổi asset
  $("s-strategy").onchange = applyStrategyUI;
  $("sym-10").onclick = () => setSymbols(opts.universe.slice(0, 10));
  $("sym-clear").onclick = () => setSymbols([]);
  applyStrategyUI();
  updateSymCount();
}

// Ngày/phí mặc định + ghi chú IS/OOS theo loại tài sản (stock 2019.., crypto 2022..; phí 10 / 25 bps)
function applyAssetDefaults(opts) {
  for (const [id, v] of [["s-from", opts.default_from], ["s-to", opts.default_to]]) {
    $(id).min = opts.min_date;
    $(id).max = opts.max_date;
    $(id).value = v;
  }
  $("s-cost").value = opts.default_cost_bps;
  $("o-periods").textContent = "IS = " + opts.is_period.join("..") + " (để chọn), OOS = " + opts.oos_period.join("..") +
    " (để kiểm tra).";
}

function $(id) { return document.getElementById(id); }
function pickedSymbols() { return [...document.querySelectorAll("#s-symbols input:checked")].map((c) => c.value); }
function updateSymCount() { $("sym-count").textContent = pickedSymbols().length; }

function setSymbols(list) {
  document.querySelectorAll("#s-symbols input").forEach((c) => { c.checked = list.includes(c.value); });
  updateSymCount();
}

function onSymbolChange(e) {
  if (pickedSymbols().length > FORM.opts.max_symbols) {  // giới hạn ≤ 20 mã
    e.target.checked = false;
    $("run-msg").textContent = "Tối đa " + FORM.opts.max_symbols + " mã.";
    $("run-msg").className = "small err";
  }
  updateSymCount();
}

// B2_goc: tắt ô ATR/TP và cờ thành phần; đặt risk_per_trade mặc định của từng chiến lược
function applyStrategyUI() {
  const full = $("s-strategy").value === "FULL";
  for (const k of FULL_ONLY) $(fieldId("risk", k)).disabled = !full;
  document.querySelectorAll("#f-flags input").forEach((c) => { c.disabled = !full; });
  $("flags-card").style.opacity = full ? 1 : 0.5;
  $("risk-note").textContent = full ? "" : "B2_goc chỉ dùng Risk / lệnh (ATR stop, take profit chỉ có ở FULL).";
  $(fieldId("risk", "risk_per_trade")).value = full ? 0.01 : 0.02;
}

function readNumber(id, label) {
  const raw = $(id).value.trim();
  const v = Number(raw);
  if (raw === "" || !isFinite(v)) throw new Error(label + " phải là số");
  return v;
}

// Đọc form -> body; ném Error (tiếng Việt) nếu sai. Server vẫn kiểm tra lại lần nữa.
function readSettings() {
  const o = FORM.opts, full = $("s-strategy").value === "FULL";
  const symbols = pickedSymbols();
  if (!symbols.length) throw new Error("Chọn ít nhất 1 mã.");
  const from = $("s-from").value, to = $("s-to").value;
  if (!from || !to) throw new Error("Nhập đủ Từ ngày / Đến ngày.");
  if (to > o.max_date) throw new Error(o.holdout_note);
  if (from < o.min_date) throw new Error("Từ ngày phải >= " + o.min_date + ".");
  if (from >= to) throw new Error("Từ ngày phải trước Đến ngày.");
  const body = { asset: $("s-asset").value, symbols, strategy: $("s-strategy").value, mode: $("s-mode").value, from, to,
                 fill_model: $("s-fill").value, deposit: readNumber("s-deposit", "Deposit"),
                 cost_bps: readNumber("s-cost", "Phí"), strategy_params: {}, risk: {}, flags: {} };
  for (const section of ["strategy_params", "risk"]) {
    for (const [k, label] of PARAM_DEFS[section]) {
      if (section === "risk" && !full && FULL_ONLY.includes(k)) continue;
      body[section][k] = readNumber(fieldId(section, k), label);
    }
  }
  if (body.strategy_params.ma_short >= body.strategy_params.ma_long) throw new Error("MA ngắn phải nhỏ hơn MA dài.");
  if (full) o.flags.forEach((f) => { body.flags[f] = $("f-" + f).checked; });
  return body;
}
