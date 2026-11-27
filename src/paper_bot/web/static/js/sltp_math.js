// Hàm thuần cho nút "SL/TP" của vị thế (test bằng node; cần position_math.js nạp trước).
// side = "long" | "short". Long: SL < giá hiện tại < TP; short: TP < giá hiện tại < SL.
const SLT = {
  // Kiểm tra mức: err = sai thứ tự (chặn hẳn); cross = giá hiện tại đã vượt qua mức (đặt là khớp NGAY -> hỏi thêm)
  check(side, sl, tp, cur) {
    const long = side !== "short";
    if (!(sl > 0) || !(tp > 0)) return { err: "SL và TP phải là số > 0", cross: false };
    if (long ? !(sl < tp) : !(tp < sl)) return { err: long ? "Long: cần SL < TP" : "Short: cần TP < SL", cross: false };
    const cross = cur > 0 && !(long ? sl < cur && cur < tp : tp < cur && cur < sl);
    return { err: null, cross };
  },

  // Mức ban đầu của box: entry = giá vốn TB; SL/TP = mức đang có, thiếu thì ±2×ATR / 3×ATR quanh giá vốn
  // (giá đã chạy xa khiến mức mặc định bị vượt qua -> tính quanh giá hiện tại). ex = {sl, tp} | null
  levels(side, entry, cur, atr, step, ex) {
    let base = PM.defaults(entry, atr, step, side);
    if (cur > 0 && SLT.check(side, base.sl, base.tp, cur).cross) base = PM.defaults(cur, atr, step, side);
    const has = (v) => v !== null && v !== undefined && v > 0;
    return { entry: PM.snap(entry, step), sl: has(ex && ex.sl) ? ex.sl : base.sl, tp: has(ex && ex.tp) ? ex.tp : base.tp };
  },

  // "12s" / "3 phút" / "2 giờ"
  ago(sec) {
    if (!(sec >= 0)) return "-";
    if (sec < 90) return Math.round(sec) + "s";
    return sec < 5400 ? Math.round(sec / 60) + " phút" : Math.round(sec / 3600) + " giờ";
  },

  // Dòng trạng thái server canh SL/TP crypto. st = GET /api/sltp/status (now, last_check: unix giây)
  statusText(st) {
    if (!st) return { text: "Server canh SL/TP: không đọc được trạng thái", cls: "err" };
    const n = (st.entries || []).length, cnt = n ? ` · ${n} mã` : "";
    if (!st.enabled) return { text: `Server canh SL/TP ○ TẮT${cnt}`, cls: "err" };
    if (!st.running) return { text: `Server canh SL/TP ○ chưa chạy${cnt}`, cls: "err" };
    const age = st.last_check ? st.now - st.last_check : null;
    const err = st.last_error && st.last_error_at && st.now - st.last_error_at < 60 ? ` · lỗi: ${st.last_error}` : "";
    const stale = age === null || age > Math.max(30, 10 * (st.interval || 3));
    return { text: `Server canh SL/TP ● (kiểm cách đây ${age === null ? "-" : SLT.ago(age)})${cnt}${err}`,
             cls: stale || err ? "err" : "up" };
  },

  // Nhãn nguồn SL/TP ở panel Vị thế
  sourceTitle(src) {
    return { "sàn": "OCO/bracket trên sàn Alpaca (chạy cả khi tắt server)", server: "web server tự canh mỗi vài giây",
             bot: "LiveBot (bot loop crypto) tự quản" }[src] || "";
  },
};
