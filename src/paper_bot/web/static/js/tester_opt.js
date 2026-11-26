// Tab Optimization (phần form + chạy): chọn 1-3 tham số, nhập giá trị, đếm tổ hợp, chạy job tối ưu.
// Phần hiển thị kết quả nằm ở tester_opt_view.js.
const OPT_SLOTS = [1, 2, 3];

function splitNums(t, sep) {
  const parts = t.split(sep).map((x) => x.trim());
  if (parts.some((x) => x === "") || !parts.every((x) => isFinite(Number(x)))) throw new Error("Giá trị phải là số: " + t);
  return parts.map(Number);
}

// "5,10,15" -> danh sách; "5:1:10" (đầu:bước:cuối) -> dãy
function parseValues(text) {
  const t = text.trim();
  if (!t) return [];
  if (!t.includes(":")) return [...new Set(splitNums(t, ","))];
  const p = splitNums(t, ":");
  if (p.length !== 3 || p[1] <= 0 || p[2] < p[0]) throw new Error("Dạng dãy: đầu:bước:cuối (bước > 0, cuối >= đầu)");
  const n = Math.floor((p[2] - p[0]) / p[1] + 1e-9) + 1;
  if (n > TESTER.opts.max_combos) throw new Error("Dãy quá dài (" + n + " giá trị)");
  return Array.from({ length: n }, (_, k) => Math.round((p[0] + k * p[1]) * 1e6) / 1e6);
}

// Đọc 3 ô chọn -> grid {tên: [giá trị]}; ném Error tiếng Việt nếu sai
function readGrid() {
  const grid = {};
  for (const i of OPT_SLOTS) {
    const name = $("o-p" + i).value;
    if (!name) continue;
    if (name in grid) throw new Error("Tham số '" + name + "' bị chọn trùng.");
    const vals = parseValues($("o-v" + i).value);
    if (!vals.length) throw new Error("Nhập giá trị cho " + name + ".");
    const spec = TESTER.opts.params[name];
    for (const v of vals) {
      if (v < spec.min || v > spec.max) throw new Error(name + " phải trong " + spec.min + ".." + spec.max + ".");
      if (spec.int && !Number.isInteger(v)) throw new Error(name + " phải là số nguyên.");
    }
    grid[name] = vals;
  }
  if (!Object.keys(grid).length) throw new Error("Chọn ít nhất 1 tham số.");
  return grid;
}

function updateOptCount() {
  const box = $("o-count"), btn = $("btn-opt"), max = TESTER.opts.max_combos;
  try {
    const n = Object.values(readGrid()).reduce((a, v) => a * v.length, 1);
    box.textContent = n + " tổ hợp" + (n > max ? " > giới hạn " + max + ", giảm bớt giá trị." : " (mỗi tổ hợp chạy IS + OOS)");
    box.className = "small " + (n > max ? "err" : "ok");
    btn.disabled = n > max;
  } catch (e) {
    box.textContent = e.message;
    box.className = "small muted";
    btn.disabled = true;
  }
}

// B2_goc không có ATR stop / take profit nên bỏ khỏi danh sách tham số
function fillOptParams() {
  const full = $("s-strategy").value === "FULL";
  const names = Object.keys(TESTER.opts.params).filter((n) => full || !FULL_ONLY.includes(n.replace("risk.", "")));
  for (const i of OPT_SLOTS) {
    const sel = $("o-p" + i), old = sel.value;
    sel.innerHTML = '<option value="">(không dùng)</option>' + names.map((n) => `<option>${n}</option>`).join("");
    sel.value = names.includes(old) ? old : "";
  }
  updateOptCount();
}

function setOptMsg(text, cls) {
  $("o-msg").textContent = text;
  $("o-msg").className = "small " + (cls || "");
}

async function startOptimize() {
  let body, grid;
  try { body = readSettings(); grid = readGrid(); } catch (e) { setOptMsg(e.message, "err"); return; }
  delete body.from;  // tối ưu luôn dùng IS 2019-2022 và OOS 2023-2024 cố định ở server
  delete body.to;
  const names = Object.keys(grid);
  Object.assign(body, { grid, criterion: $("o-crit").value, heatmap_params: names.length >= 2 ? names.slice(0, 2) : null });
  $("btn-opt").disabled = true;
  setOptMsg("Đang tối ưu…");
  showProgress("o-bar", null);
  try {
    const job = await runJob("/api/tester/optimize", body, (j) => {
      showProgress("o-bar", j);
      setOptMsg("Đã chạy " + j.progress.done + "/" + j.progress.total + " tổ hợp…");
    });
    setOptMsg("Xong sau " + job.elapsed_s + " giây.", "ok");
    renderOptimize(job.result);
  } catch (e) {
    setOptMsg(e.message, "err");
  } finally {
    $("o-bar").hidden = true;
    updateOptCount();
  }
}

// Bấm 1 dòng xếp hạng: chạy đầy đủ run_tester với tham số đó (theo ngày trong form) rồi mở tab Báo cáo
async function runCombo(params) {
  let body;
  try { body = readSettings(); } catch (e) { setOptMsg(e.message, "err"); return; }
  for (const [name, v] of Object.entries(params)) {
    if (name.startsWith("risk.")) body.risk[name.slice(5)] = v;
    else body.strategy_params[name] = v;
  }
  setOptMsg("Đang chạy đầy đủ tổ hợp " + JSON.stringify(params) + "…");
  try {
    const job = await runJob("/api/tester/run", body, null);
    setOptMsg("Đã chạy xong tổ hợp, xem tab Báo cáo.", "ok");
    setResult(job);
  } catch (e) {
    setOptMsg(e.message, "err");
  }
}

function initOptimization() {
  $("o-crit").innerHTML = TESTER.opts.criteria.map((c) => `<option>${c}</option>`).join("");
  for (const i of OPT_SLOTS) {
    $("o-p" + i).onchange = updateOptCount;
    $("o-v" + i).oninput = updateOptCount;
  }
  $("s-strategy").addEventListener("change", fillOptParams);
  $("btn-opt").onclick = startOptimize;
  fillOptParams();
}
