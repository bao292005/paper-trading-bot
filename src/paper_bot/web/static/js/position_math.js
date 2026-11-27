// Hàm thuần cho công cụ "Long/Short Position": ATR, mức mặc định, phí, làm tròn giá, qty theo rủi ro, R:R, loại lệnh,
// ràng buộc kéo. Không đụng DOM/chart (test bằng node). side = "long" (SL < entry < TP) | "short" (TP < entry < SL).
const PM = {
  FEE_BPS: { stock: 10, crypto: 25 },  // phí ước tính mỗi chiều (giống cost_bps của backtest)
  MAX_QTY: 10000,                      // trần qty cổ phiếu (order_rules.MAX_QTY)
  MAX_NOTIONAL: 200000,                // trần giá trị lệnh crypto (execution.CRYPTO_MAX_NOTIONAL)
  MARKET_TOL: 0.0005,                  // entry lệch giá hiện tại < 0.05% -> coi là market

  // Số chữ số thập phân của bước (0.01 -> 2, 0.0001 -> 4)
  decimals(step) {
    if (!(step > 0)) return 2;
    const s = String(step);
    if (s.includes("e-")) return Number(s.split("e-")[1]);
    return s.includes(".") ? s.split(".")[1].length : 0;
  },

  // Làm tròn về bội số gần nhất của step (bỏ sai số số thực)
  snap(p, step) {
    step = step > 0 ? step : 0.01;
    return Number((Math.round(p / step) * step).toFixed(PM.decimals(step)));
  },

  // Làm tròn XUỐNG theo step (qty không được vượt)
  floorStep(q, step) {
    step = step > 0 ? step : 1;
    return Number((Math.floor(Number((q / step).toFixed(6))) * step).toFixed(PM.decimals(step)));
  },

  // ATR14 đơn giản: trung bình true range của n nến cuối. Thiếu dữ liệu -> null
  atr(bars, n = 14) {
    if (!bars || bars.length < n + 1) return null;
    let sum = 0;
    for (let i = bars.length - n; i < bars.length; i++) {
      const b = bars[i], pc = bars[i - 1].close;
      sum += Math.max(b.high - b.low, Math.abs(b.high - pc), Math.abs(b.low - pc));
    }
    return sum / n;
  },

  // Chiều: long = +1, short = −1. Mức nằm DƯỚI / TRÊN entry: long (sl, tp), short (tp, sl)
  dir(side) { return side === "short" ? -1 : 1; },
  lohi(side) { return side === "short" ? ["tp", "sl"] : ["sl", "tp"]; },

  // Mức mặc định: long SL = entry − 2×ATR, TP = entry + 3×ATR; short ngược lại. Không có ATR thì ±2% / ±3%
  defaults(entry, atr, step, side = "long") {
    const d = PM.dir(side), ok = atr > 0 && entry - (d > 0 ? 2 : 3) * atr > 0;
    const sl = ok ? entry - d * 2 * atr : entry * (1 - d * 0.02), tp = ok ? entry + d * 3 * atr : entry * (1 + d * 0.03);
    return { entry: PM.snap(entry, step), sl: PM.snap(sl, step), tp: PM.snap(tp, step) };
  },

  feeRate(asset) { return (PM.FEE_BPS[asset] ?? PM.FEE_BPS.stock) / 1e4; },

  // Phí ước tính mỗi đơn vị cho 1 vòng vào tại entry rồi ra tại exit
  feeRound(entry, exit, asset) { return (entry + exit) * PM.feeRate(asset); },

  // qty = rủi_ro$ / (|entry − SL| + phí mỗi đơn vị); chặn theo tiền mặt (long) / ký quỹ 1x (short) và trần sàn.
  // o = {risk, entry, sl, asset, step, cash, side}. Trả {qty, capped: null | "cash" | "max"}
  riskQty(o) {
    const crypto = o.asset === "crypto", step = crypto ? (o.step > 0 ? o.step : 0.0001) : 1;
    const per = PM.dir(o.side) * (o.entry - o.sl) + PM.feeRound(o.entry, o.sl, o.asset);
    if (!(o.risk > 0) || !(o.entry > 0) || !(per > 0)) return { qty: 0, capped: null };
    let qty = o.risk / per, capped = null;
    const byCash = o.cash > 0 ? o.cash / (o.entry * (1 + PM.feeRate(o.asset))) : Infinity;
    const byMax = crypto ? PM.MAX_NOTIONAL / o.entry : PM.MAX_QTY;
    if (byCash < qty) { qty = byCash; capped = "cash"; }
    if (byMax < qty) { qty = byMax; capped = "max"; }
    return { qty: Math.max(0, PM.floorStep(qty, step)), capped };
  },

  // Thông tin hiện trên box (đã trừ phí ước tính 2 chiều). loss là số dương.
  // Short: R:R = (entry − TP) / (SL − entry), % tính theo chiều có lợi.
  stats(o) {
    const { entry, sl, tp, asset } = o, qty = o.qty || 0, d = PM.dir(o.side);
    const risk = d * (entry - sl), rr = risk > 0 ? d * (tp - entry) / risk : null;
    return {
      rr: rr === null ? null : Number(rr.toFixed(2)),
      tpPct: d * (tp / entry - 1) * 100, slPct: d * (1 - sl / entry) * 100,
      profit: qty * (d * (tp - entry) - PM.feeRound(entry, tp, asset)),
      loss: qty * (risk + PM.feeRound(entry, sl, asset)),
    };
  },

  // Đề xuất loại lệnh: ≈ giá hiện tại -> market; giá "tốt hơn" (long thấp hơn, short cao hơn) -> limit; còn lại stop
  suggestType(entry, cur, side = "long") {
    if (!(cur > 0)) return "limit";
    if (Math.abs(entry - cur) / cur < PM.MARKET_TOL) return "market";
    return PM.dir(side) * (entry - cur) < 0 ? "limit" : "stop";
  },

  // Ràng buộc khi kéo. lv = {entry, sl, tp, side} lúc bắt đầu kéo; part = tp | sl | entry | body;
  // price = giá tại con trỏ; grab = giá lúc nhấn chuột (chỉ dùng cho body).
  // Luôn giữ mức dưới < entry < mức trên (long SL < entry < TP, short TP < entry < SL), mức dưới > 0.
  // free = vị thế đã khớp: SL/TP chỉ cần giữ mức dưới < mức trên (được kéo SL qua giá vốn để khoá lời)
  drag(lv, part, price, step, grab, free) {
    step = step > 0 ? step : 0.01;
    const [lo, hi] = PM.lohi(lv.side);
    const p = PM.snap(price, step), out = { entry: lv.entry, sl: lv.sl, tp: lv.tp };
    if (lv.side) out.side = lv.side;
    const hiMin = free ? lv[lo] : lv.entry, loMax = free ? lv[hi] : lv.entry;
    if (part === hi) out[hi] = Math.max(p, PM.snap(hiMin + step, step));
    else if (part === lo) out[lo] = Math.max(step, Math.min(p, PM.snap(loMax - step, step)));
    else if (part === "entry") out.entry = Math.min(Math.max(p, PM.snap(lv[lo] + step, step)), PM.snap(lv[hi] - step, step));
    else if (part === "body") {
      const d = Math.max(PM.snap(p - PM.snap(grab, step), step), PM.snap(step - lv[lo], step));
      out.entry = PM.snap(lv.entry + d, step); out.sl = PM.snap(lv.sl + d, step); out.tp = PM.snap(lv.tp + d, step);
    }
    return out;
  },

  // P/L chưa chốt của vị thế: $ và bội số R (R = |entry − SL|). qty có thể âm (short) -> dùng trị tuyệt đối
  unreal(entry, sl, cur, qty, side = "long") {
    const d = PM.dir(side), risk = d * (entry - sl), r = risk > 0 ? d * (cur - entry) / risk : null;
    return { pl: Math.abs(qty) * d * (cur - entry), r: r === null ? null : Number(r.toFixed(2)) };
  },

  // Mức hợp lệ theo chiều (lv.side, mặc định long)? free = vị thế: chỉ cần mức dưới < mức trên
  valid(lv, free) {
    const [lo, hi] = PM.lohi(lv.side);
    return lv[lo] > 0 && (free ? lv[lo] < lv[hi] : lv[lo] < lv.entry && lv.entry < lv[hi]);
  },
};
