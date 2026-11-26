// Đặt lệnh paper thủ công + huỷ lệnh chờ. Mọi thao tác ghi đều qua hộp xác nhận (gõ XAC NHAN).
// Server kiểm tra lại toàn bộ; ở đây chỉ gom dữ liệu, tóm tắt, và hiển thị lỗi.
const $ = (id) => document.getElementById(id);

function ordMsg(text, level) {
  const el = $("ord-msg");
  el.textContent = text || "";
  el.className = "small " + (level === "bad" ? "err" : level === "ok" ? "up" : "muted");
}

const numOrNull = (id) => ($(id).value.trim() === "" ? null : Number($(id).value));

function readCryptoOrderForm() {
  // Crypto: qty lẻ, KHÔNG SL/TP (bot tự quản), tif gtc do server đặt. Mã nhập "btc" -> "BTC/USD".
  const type = $("ord-type").value, sym = normalizeSymbol($("ord-symbol").value, "crypto");
  if (sym === null) throw new Error("Mã crypto phải thuộc: " + CRYPTO_SYMBOLS.join(", "));
  const body = { asset: "crypto", symbol: sym, side: $("ord-side").value, type, qty: Number($("ord-qty").value),
    limit_price: type === "market" ? null : numOrNull("ord-limit"),
    stop_price: type === "stop_limit" ? numOrNull("ord-stop") : null };
  if (!(body.qty > 0)) throw new Error("Số lượng phải > 0");
  const last = D.bars[D.bars.length - 1];
  if (type === "market" && sym === CH.symbol && last) body.ref_price = last.close;  // để server kiểm tra trần 200k$
  return body;
}

function readOrderForm() {
  if (CH.asset === "crypto") return readCryptoOrderForm();
  const isLimit = $("ord-type").value === "limit";
  const body = {
    symbol: $("ord-symbol").value.trim(), side: $("ord-side").value, type: $("ord-type").value,
    qty: Number($("ord-qty").value), limit_price: isLimit ? numOrNull("ord-limit") : null,
    stop_loss: numOrNull("ord-sl"), take_profit: numOrNull("ord-tp"),
  };
  // Lệnh market có SL/TP cần giá tham chiếu để kiểm tra SL/TP hợp lý: lấy giá nến cuối đang xem
  if (!isLimit && (body.stop_loss !== null || body.take_profit !== null)) {
    const last = D.bars[D.bars.length - 1];
    if (body.symbol !== CH.symbol || !last) throw new Error("Lệnh market kèm SL/TP: hãy mở biểu đồ của mã này để lấy giá tham chiếu");
    body.ref_price = last.close;
  }
  return body;
}

// Giá ước tính để tính giá trị lệnh: limit -> giá limit; market -> giá nến cuối nếu đang xem đúng mã
function estimatePrice(body) {
  if (body.limit_price) return body.limit_price;
  const last = D.bars[D.bars.length - 1];
  return body.symbol === CH.symbol && last ? last.close : null;
}

function orderSummary(b) {
  const px = estimatePrice(b);
  const rows = [["Mã", b.symbol], ["Hướng", b.side === "buy" ? "MUA" : "BÁN"], ["Loại", b.type],
    ["Số lượng", String(b.qty)], ["Giá limit", b.limit_price === null ? "-" : fmt.usd(b.limit_price)]];
  if (b.asset === "crypto") {
    rows.push(["Giá stop", b.stop_price == null ? "-" : fmt.usd(b.stop_price)], ["Hiệu lực", "GTC"],
      ["SL/TP", "do bot tự quản"]);
  } else {
    rows.push(["Stop loss", b.stop_loss === null ? "-" : fmt.usd(b.stop_loss)],
      ["Take profit", b.take_profit === null ? "-" : fmt.usd(b.take_profit)]);
  }
  rows.push(["Giá trị ước tính", px === null ? "không ước tính được" : fmt.usd(px * b.qty)]);
  return summaryTable(rows) + `<p class="muted small">Tài khoản PAPER (tiền giả) của Alpaca.</p>`;
}

async function submitOrder(ev) {
  ev.preventDefault();
  let body;
  try { body = readOrderForm(); } catch (e) { ordMsg(e.message, "bad"); return; }
  const typed = await askConfirm("Xác nhận đặt lệnh", orderSummary(body));
  if (typed === null) { ordMsg("Đã huỷ, chưa gửi lệnh."); return; }
  ordMsg("Đang gửi…");
  try {
    const r = await sendJSON("POST", "/api/orders", { ...body, confirm: typed });
    ordMsg(`Đã gửi lệnh ${r.symbol} (${r.status || "?"}) id ${r.id}`, "ok");
    refreshPanel();
  } catch (e) { ordMsg("Lỗi: " + e.message, "bad"); }
}

async function cancelOrder(id) {
  const o = PANEL.orders.find((x) => x.id === id);
  const rows = o ? [["Mã", o.symbol], ["Hướng", o.side], ["Loại", o.type], ["Số lượng", String(o.qty)]] : [["Order id", id]];
  const typed = await askConfirm("Xác nhận huỷ lệnh chờ", summaryTable(rows));
  if (typed === null) return;
  try {
    await sendJSON("DELETE", "/api/orders/" + encodeURIComponent(id), { confirm: typed });
    ordMsg("Đã gửi yêu cầu huỷ lệnh.", "ok");
    refreshPanel();
  } catch (e) { ordMsg("Lỗi huỷ lệnh: " + e.message, "bad"); }
}

function syncOrderSymbol(sym) { $("ord-symbol").value = sym; }

// Đổi form theo loại tài sản: crypto = qty lẻ, không SL/TP (ghi chú), thêm stop_limit
function syncOrderAsset() {
  const crypto = CH.asset === "crypto";
  syncOrderSymbol(CH.symbol);
  $("ord-symbol").maxLength = crypto ? 8 : 5;
  $("ord-qty").step = crypto ? "any" : "1";
  $("ord-qty").min = crypto ? "0" : "1";
  $("ord-qty").max = crypto ? "" : "10000";
  if (crypto && Number($("ord-qty").value) >= 1) $("ord-qty").value = "0.01";
  if (!crypto && !Number.isInteger(Number($("ord-qty").value))) $("ord-qty").value = "1";
  for (const id of ["ord-sl", "ord-tp"]) {
    $(id).disabled = crypto;
    if (crypto) $(id).value = "";
    $(id).closest("label").hidden = crypto;
  }
  $("ord-stop").closest("label").hidden = !crypto;
  $("ord-type").querySelector('option[value="stop_limit"]').hidden = !crypto;
  $("ord-type").querySelector('option[value="stop_limit"]').disabled = !crypto;
  if (!crypto && $("ord-type").value === "stop_limit") $("ord-type").value = "market";
  $("ord-note").hidden = !crypto;
  $("ord-type").dispatchEvent(new Event("change"));
}

function initOrderUI() {
  syncOrderSymbol(CH.symbol);
  $("order-form").addEventListener("submit", submitOrder);
  $("ord-type").addEventListener("change", () => {
    const t = $("ord-type").value;
    $("ord-limit").disabled = t === "market";
    $("ord-stop").disabled = t !== "stop_limit";
    if (t === "market") $("ord-limit").value = "";
    if (t !== "stop_limit") $("ord-stop").value = "";
  });
  syncOrderAsset();
  $("ord-type").dispatchEvent(new Event("change"));
  $("ord-symbol").addEventListener("input", (ev) => { ev.target.value = ev.target.value.toUpperCase(); });
}
