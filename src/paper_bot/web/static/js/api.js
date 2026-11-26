// Gọi API: ném lỗi kèm nội dung `detail` của server để hiển thị cho người dùng.
async function checkResponse(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* không phải JSON */ }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return res.json();
}

async function getJSON(url, params) {
  const qs = params ? "?" + new URLSearchParams(params).toString() : "";
  return checkResponse(await fetch(url + qs));
}

// POST/DELETE: server bắt buộc header X-Requested-With và Content-Type JSON (chống trang lạ gọi)
async function sendJSON(method, url, body) {
  return checkResponse(await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", "X-Requested-With": "paper-web" },
    body: JSON.stringify(body),
  }));
}

// Định dạng số/tiền cho panel
const fmt = {
  num: (v, d = 2) => (v === null || v === undefined || isNaN(v)) ? "-" :
    Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }),
  usd: (v) => (v === null || v === undefined || isNaN(v)) ? "-" : "$" + fmt.num(v, 2),
  // Số lượng: cổ phiếu là số nguyên, crypto là số lẻ (tối đa 6 chữ số thập phân, bỏ số 0 thừa)
  qty: (v) => (v === null || v === undefined || isNaN(v)) ? "-" :
    Number(v).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 6 }),
  cls: (v) => v > 0 ? "up" : v < 0 ? "down" : "",
};

// Thoát HTML để chèn text từ server vào innerHTML an toàn
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
