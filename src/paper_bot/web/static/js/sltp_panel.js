// SL/TP trong panel Vị thế: cột SL / TP / nguồn, dòng trạng thái "Server canh SL/TP" + công tắc bật/tắt.
const SLTP = { levels: {}, status: null, skew: 0 };
const $sl = (id) => document.getElementById(id);

// Mức SL/TP + trạng thái server canh (gọi cùng nhịp làm mới panel). Không bao giờ ném lỗi.
async function sltpRefresh() {
  const [lv, st] = await Promise.all([getJSON("/api/sltp").catch(() => null), getJSON("/api/sltp/status").catch(() => null)]);
  SLTP.levels = lv ? lv.levels : {};
  SLTP.status = st;
  if (st) SLTP.skew = Date.now() / 1000 - st.now;  // lệch đồng hồ trình duyệt - server
  sltpRenderStatus();
}

function sltpRenderStatus() {
  const s = SLTP.status, t = SLT.statusText(s ? { ...s, now: Date.now() / 1000 - SLTP.skew } : null);
  $sl("sltp-status").textContent = t.text;
  $sl("sltp-status").className = t.cls;
  $sl("sltp-toggle").textContent = s && s.enabled ? "Tắt" : "Bật";
}

// 3 ô SL / TP / nguồn cho dòng vị thế thật
function sltpCells(sym) {
  const lv = SLTP.levels[sym];
  if (!lv) return `<td class="num muted">-</td><td class="num muted">-</td><td></td>`;
  return `<td class="num down">${fmt.num(lv.sl)}</td><td class="num up">${fmt.num(lv.tp)}</td>` +
    `<td class="small" title="${esc(SLT.sourceTitle(lv.source))}">${esc(lv.source)}${lv.triggering ? " ⏳" : ""}</td>`;
}

async function sltpToggle() {
  const on = !(SLTP.status && SLTP.status.enabled);
  const msg = on ? "Server sẽ kiểm giá mỗi vài giây và BÁN market khi crypto chạm SL/TP."
    : "Tắt thì SL/TP crypto do server canh sẽ KHÔNG được thực hiện (mức vẫn được giữ).";
  const confirm = await askConfirm(`${on ? "Bật" : "Tắt"} server canh SL/TP crypto`, `<p class="small">${msg}</p>`);
  if (!confirm) return;
  try { await sendJSON("POST", "/api/sltp/monitor", { enabled: on, confirm }); } catch (e) { posMsg(e.message, true); }
  sltpRefresh();
}

function initSltpPanel() {
  $sl("sltp-toggle").addEventListener("click", sltpToggle);
  setInterval(sltpRenderStatus, 1000);  // "kiểm cách đây Xs" chạy theo giây
}
