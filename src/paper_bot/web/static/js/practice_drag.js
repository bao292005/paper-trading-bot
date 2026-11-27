// Kéo đường giá SL/TP/limit của tài khoản luyện tập (price line của thư viện không kéo được -> tự bắt chuột).
// Nhấn chuột trong ±5px quanh một đường kéo được -> kéo theo trục giá -> thả = PATCH lệnh. Lỗi -> vẽ lại như cũ.
const PR_DRAG = { idx: -1, price: null, hitPx: 5 };

function prRoundPrice(p) { return Math.round(p * (p < 10 ? 10000 : 100)) / (p < 10 ? 10000 : 100); }

// Chỉ số đường kéo được gần toạ độ y nhất (trong ±hitPx), không có -> -1
function prHitLine(y) {
  let best = -1, dist = PR_DRAG.hitPx + 0.01;
  PR.lineItems.forEach((it, i) => {
    if (!it.oid || (typeof pbOwnsOid === "function" && pbOwnsOid(it.oid))) return;  // đã có box -> kéo bằng box
    const c = CH.candles.priceToCoordinate(it.price);
    if (c !== null && Math.abs(c - y) < dist) { best = i; dist = Math.abs(c - y); }
  });
  return best;
}

function prLocalY(el, ev) { return ev.clientY - el.getBoundingClientRect().top; }

function initPracticeDrag() {
  const el = document.getElementById("chart");
  const down = (ev) => {
    if (!RP.on || RP.picking || ev.shiftKey || ev.button !== 0) return;
    const i = prHitLine(prLocalY(el, ev));
    if (i < 0) return;
    ev.stopPropagation(); ev.preventDefault();  // không cho biểu đồ kéo/cuộn
    PR_DRAG.idx = i; PR_DRAG.price = PR.lineItems[i].price;
  };
  el.addEventListener("pointerdown", down, true);
  el.addEventListener("mousedown", down, true);

  document.addEventListener("mousemove", (ev) => {
    if (PR_DRAG.idx < 0) {  // rê chuột: đổi con trỏ khi ở gần đường kéo được
      if (RP.on && ev.target && el.contains(ev.target)) el.style.cursor = prHitLine(prLocalY(el, ev)) >= 0 ? "ns-resize" : "";
      return;
    }
    const p = CH.candles.coordinateToPrice(prLocalY(el, ev));
    if (p === null || !(p > 0)) return;
    PR_DRAG.price = prRoundPrice(p);
    const it = PR.lineItems[PR_DRAG.idx];
    if (CH.lines[PR_DRAG.idx]) CH.lines[PR_DRAG.idx].applyOptions({ price: PR_DRAG.price, title: `${it.title} → ${fmt.num(PR_DRAG.price)}` });
  }, true);

  document.addEventListener("mouseup", async () => {
    if (PR_DRAG.idx < 0) return;
    const it = PR.lineItems[PR_DRAG.idx], price = PR_DRAG.price;
    PR_DRAG.idx = -1;
    if (!it || price === it.price) { practiceRefreshChart(); return; }
    const ok = await practiceSend("PATCH", `/api/practice/${PR.sid}/orders/${encodeURIComponent(it.oid)}`,
                                  { [it.field]: price }, `Đã sửa ${it.title} -> ${fmt.num(price)}`);
    if (!ok) practiceRefreshChart();  // server từ chối -> trả đường về chỗ cũ
  }, true);
}
