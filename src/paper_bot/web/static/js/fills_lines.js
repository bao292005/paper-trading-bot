// "Lệnh của tôi" – đường giá trên chart (hàm thuần): giá vốn vị thế (nét liền) + lệnh chờ (nét đứt).
// Cái nào đã có box Long/Short thì không vẽ trùng.
const FL = {};
FL.TYPE = { limit: "LMT", stop: "STP", stop_limit: "STP-LMT", market: "MKT", trailing_stop: "TRL" };
FL.near = (a, b) => Math.abs(a - b) <= 1e-6 * Math.max(1, Math.abs(a));
FL.orderPrice = (o) => (o.limit_price !== null && o.limit_price !== undefined) ? o.limit_price : o.stop_price;

// positions/orders: dữ liệu /api/positions, /api/orders; boxes: PB.boxes (box nháp bỏ qua).
// Trả [{price, color, title, dashed}] cho setPriceLines.
FL.items = function (symbol, positions, orders, boxes) {
  const mine = (boxes || []).filter((b) => b.symbol === symbol && b.kind !== "draft" && (b.account === "real" || b.sltp));
  const ids = new Set(), prices = [];
  for (const b of mine) {
    for (const v of Object.values(b.ids || {})) ids.add(v);
    const keys = b.kind === "position" ? ["sl", "tp"] : ["entry", "sl", "tp"];  // giá vào của box vị thế = giá vốn, không phải lệnh
    for (const src of [b, b.orig || {}]) for (const k of keys) if (typeof src[k] === "number") prices.push(src[k]);
  }
  const items = [], pos = positions.find((p) => p.symbol === symbol && p.qty !== 0);
  if (pos && !mine.some((b) => b.kind === "position")) {
    items.push({ price: pos.avg_entry_price, color: "#2196f3", title: `Vị thế ${FM.q(pos.qty)} @${FM.p(pos.avg_entry_price)}` });
  }
  for (const o of orders) {
    const price = FL.orderPrice(o);
    if (o.symbol !== symbol || price === null || price === undefined) continue;
    if (ids.has(o.id) || prices.some((x) => FL.near(x, price))) continue;  // box đã vẽ mức này
    const exit = pos && o.side === (pos.qty < 0 ? "buy" : "sell");  // lệnh đóng vị thế: long = bán, short = mua
    const color = exit ? (o.type === "limit" ? "#26a69a" : "#ef5350") : "#ff9800";
    const type = FL.TYPE[o.type] || String(o.type || "").toUpperCase();
    items.push({ price, color, dashed: true,
                 title: `${String(o.side).toUpperCase()} ${type}${o.qty === null || o.qty === undefined ? "" : " " + FM.q(o.qty)}` });
  }
  return items;
};
