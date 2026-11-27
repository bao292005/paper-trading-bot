// "Lệnh của tôi": lệnh đã khớp của tài khoản Alpaca (mũi tên trên chart) + toast khi có lệnh khớp mới.
// Tính toán nằm ở fills_math.js / fills_lines.js (hàm thuần); file này chỉ nối với chart, API và DOM.
const MF_KEY = "ptb.myFills";
const TOAST_MS = 6000;
const MF = { on: true, fills: [], seen: new Set(), key: "", seedAt: 0, seq: 0, run: [] };
try { MF.on = localStorage.getItem(MF_KEY) !== "0"; } catch (e) { /* localStorage bị chặn: mặc định bật */ }

function mfActive() { return MF.on && !(typeof RP !== "undefined" && RP.on); }  // replay: chỉ hiện lệnh luyện tập

// Vẽ lại markers = markers của bot (D.runId) + mũi tên lệnh của tôi, hợp nhất thành 1 danh sách
function mfRender() {
  if (typeof RP !== "undefined" && RP.on) return;  // replay tự đặt markers
  const mine = mfActive() ? FM.markers(MF.fills, D.bars, CH.tf, CH.asset) : [];
  setMarkers(FM.merge(MF.run, mine));
}

function mfToast(text) {
  const box = document.getElementById("toast-box");
  if (!box) return;
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = text;
  box.appendChild(el);
  setTimeout(() => el.remove(), TOAST_MS);
}

// Hỏi /api/account/fills cho mã đang xem, từ nến cũ nhất đã tải (kéo về quá khứ -> since lùi theo)
async function mfRefresh() {
  if (!mfActive()) return;
  const key = CH.symbol + "|" + CH.asset;
  if (key !== MF.key) { MF.key = key; MF.fills = []; MF.seen = new Set(); MF.seedAt = null; }
  const seq = ++MF.seq;
  const params = { symbol: CH.symbol, asset: CH.asset };
  if (D.oldest !== null && D.oldest !== undefined) params.since = String(D.oldest);
  try {
    const r = await getJSON("/api/account/fills", params);
    if (seq !== MF.seq || key !== MF.key || !mfActive()) return;  // đổi mã / tắt công tắc trong lúc chờ
    const first = MF.seedAt === null;
    if (first) MF.seedAt = Date.now() / 1000;
    // lần đầu chỉ nhớ id (không báo lệnh cũ); sau đó chỉ báo lệnh khớp sau lúc trang bắt đầu theo dõi
    for (const f of FM.newFills(MF.seen, r.fills, first ? Infinity : MF.seedAt - 60)) mfToast(FM.toastText(f, CH.symbol));
    MF.fills = r.fills;
    mfRender();
  } catch (e) { /* thiếu key / lỗi mạng: không có lệnh thì thôi, không làm phiền */ }
}

function mfSet(on) {
  MF.on = on;
  try { localStorage.setItem(MF_KEY, on ? "1" : "0"); } catch (e) { /* bỏ qua */ }
  if (!on) { MF.key = ""; MF.fills = []; }
  mfRender();
  if (typeof updatePriceLines === "function") updatePriceLines();
  if (on) mfRefresh();
}

function initFills() {
  const chk = document.getElementById("chk-myfills");
  chk.checked = MF.on;
  chk.addEventListener("change", (ev) => mfSet(ev.target.checked));
}
