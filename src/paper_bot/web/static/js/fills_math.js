// "Lệnh của tôi" – hàm thuần (không đụng DOM/chart, test bằng node): snap thời điểm khớp -> nến,
// P/L FIFO, dựng marker, hợp nhất marker, chống lặp toast.
const FM = {};

// Định dạng ngắn cho nhãn trên chart: 0.02, 1722.8 (bỏ số 0 thừa)
FM.q = (x) => String(+Number(x).toFixed(6));
FM.p = (x) => String(+Number(x).toFixed(x >= 100 ? 2 : 4));
FM.p2 = (x) => Number(x).toFixed(x >= 1 ? 2 : 4);  // cho toast / tooltip: luôn 2 số lẻ

// Ngày theo múi giờ của tài sản (cổ phiếu: New York, crypto: UTC) -> 12:00 UTC cùng ngày (quy ước `time` nến 1D)
FM.dayKey = function (sec, asset) {
  const tz = asset === "crypto" ? "UTC" : "America/New_York";
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" })
    .format(new Date(sec * 1000));  // yyyy-mm-dd
  return Date.parse(day + "T12:00:00Z") / 1000;
};

// Thời điểm khớp (unix giây) -> `time` của nến chứa nó: nến cuối cùng có time <= thời điểm (nến đã tải thật sự,
// nên đúng cho mọi khung/giờ mở cửa cổ phiếu; ngoài giờ thì dồn vào nến cuối phiên). Trước nến đầu tiên -> null.
FM.snapTime = function (bars, sec, tf, asset) {
  if (!bars.length) return null;
  const key = tf === "1D" ? FM.dayKey(sec, asset) : sec;
  if (key < bars[0].time) return null;
  let lo = 0, hi = bars.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (bars[mid].time <= key) lo = mid; else hi = mid - 1;
  }
  return bars[lo].time;
};

// P/L của các lệnh ĐÓNG theo FIFO: {id: {pnl, qty}}. longOnly (crypto): bán khi không có lô nào = bỏ qua, không mở short.
// Hạn chế: lệnh đóng một vị thế mở TRƯỚC khoảng thời gian đã tải sẽ không có lô để ghép (stock: bị coi là mở short).
FM.pnlFifo = function (fills, longOnly) {
  const lots = [], out = {};
  const sorted = [...fills].sort((a, b) => a.time - b.time || (a.id < b.id ? -1 : 1));
  for (const f of sorted) {
    const dir = f.side === "buy" ? 1 : -1;
    let left = f.qty, pnl = 0, closed = 0;
    while (left > 1e-12 && lots.length && lots[0].dir !== dir) {
      const lot = lots[0], q = Math.min(left, lot.qty);
      pnl += (f.price - lot.price) * q * lot.dir;
      closed += q; lot.qty -= q; left -= q;
      if (lot.qty <= 1e-12) lots.shift();
    }
    if (closed > 0) out[f.id] = { pnl, qty: closed };
    if (left > 1e-12 && !(longOnly && dir < 0)) lots.push({ qty: left, price: f.price, dir });
  }
  return out;
};

// Marker mũi tên: mua ▲ xanh dưới nến, bán ▼ đỏ trên nến. Text "B 0.02 @1722.8"; lệnh chốt/cắt do TP/SL ghi "TP"/"SL".
FM.markers = function (fills, bars, tf, asset) {
  const pl = FM.pnlFifo(fills, asset === "crypto"), out = [];
  for (const f of fills) {
    const time = FM.snapTime(bars, f.time, tf, asset);
    if (time === null) continue;
    const buy = f.side === "buy";
    const tag = f.exit_kind === "tp" ? "TP" : f.exit_kind === "sl" ? "SL" : buy ? "B" : "S";
    out.push({ time, kind: "myfill", position: buy ? "belowBar" : "aboveBar", shape: buy ? "arrowUp" : "arrowDown",
               color: buy ? "#00e676" : "#ff1744", text: `${tag} ${FM.q(f.qty)} @${FM.p(f.price)}`,
               tooltip: { ...f, pnl: pl[f.id] ? pl[f.id].pnl : null } });
  }
  return out;
};

// Lightweight Charts chỉ cho 1 bộ marker mỗi series -> hợp nhất các danh sách, sắp theo thời gian (ổn định)
FM.merge = (...lists) => [].concat(...lists).sort((a, b) => a.time - b.time);

// Lệnh khớp mới chưa báo: id chưa có trong `seen` và khớp lúc >= minSec (lazy-load kéo về lệnh cũ thì chỉ nhớ id, không báo).
// Mọi id đều được thêm vào `seen` -> không báo lại. Seed lần đầu: minSec = Infinity.
FM.newFills = function (seen, fills, minSec) {
  const fresh = [];
  for (const f of fills) {
    if (seen.has(f.id)) continue;
    seen.add(f.id);
    if (f.time >= minSec) fresh.push(f);
  }
  return fresh;
};

FM.toastText = function (f, symbol) {
  const side = f.side === "buy" ? "MUA" : "BÁN", q = FM.q(f.qty), p = FM.p2(f.price);
  if (f.exit_kind === "tp") return `🎯 TP khớp ${side} ${q} @${p}`;
  if (f.exit_kind === "sl") return `🛑 SL khớp ${side} ${q} @${p}`;
  return `✅ Đã khớp ${side} ${q} ${symbol} @${p}`;
};
