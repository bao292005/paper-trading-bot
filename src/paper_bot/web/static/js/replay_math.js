// Hàm thuần cho bar replay (không đụng DOM) -> test được bằng node.
const RM = {
  SPEEDS: [0.5, 1, 3, 10],  // nến / giây

  // Độ lệch (giây) của múi giờ tz so với UTC tại thời điểm sec (vd giờ VN = +25200)
  tzOffset(sec, tz) {
    const f = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit",
      day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const p = {};
    for (const x of f.formatToParts(new Date(sec * 1000))) p[x.type] = x.value;
    const wall = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute, +p.second) / 1000;
    return wall - sec;
  },

  // "YYYY-MM-DD" hoặc "YYYY-MM-DD HH:MM" (hoặc có "T") hiểu theo múi giờ tz -> unix giây. Sai định dạng -> null
  zonedToUnix(text, tz) {
    const m = String(text || "").trim().match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?$/);
    if (!m) return null;
    const [y, mo, d, h, mi] = [+m[1], +m[2], +m[3], +(m[4] || 0), +(m[5] || 0)];
    if (mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59) return null;
    const wall = Date.UTC(y, mo - 1, d, h, mi) / 1000;
    let t = wall - RM.tzOffset(wall, tz);
    t = wall - RM.tzOffset(t, tz);  // chỉnh lại quanh lúc đổi giờ mùa hè
    return t;
  },

  // Gộp mảng điểm {time,...} mới vào cũ: điểm cùng time thay thế, time mới hơn nối đuôi (cả hai đã sắp tăng dần)
  mergeByTime(old, upd) {
    if (!upd || !upd.length) return old.slice();
    const first = upd[0].time;
    return old.filter((x) => x.time < first).concat(upd);
  },

  // Tốc độ kế tiếp trong danh sách (vòng lại)
  nextSpeed(cur) {
    const i = RM.SPEEDS.indexOf(cur);
    return RM.SPEEDS[(i + 1) % RM.SPEEDS.length];
  },

  delayMs(speed) { return Math.round(1000 / (speed > 0 ? speed : 1)); },

  // Phím tắt không chạy khi đang gõ trong ô nhập / có Ctrl/Meta/Alt
  ignoreKey(ev) {
    const t = ev.target, tag = t && t.tagName;
    return !!(ev.ctrlKey || ev.metaKey || ev.altKey || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
              (t && t.isContentEditable));
  },

  // Thời điểm t (unix) -> `time` của nến chứa nó trong mảng times (tăng dần). 1D: nến 12:00 UTC cùng ngày UTC
  // (cổ phiếu khớp 13:30-21:00 UTC nên ngày UTC = ngày NY). Không có nến chứa -> null
  snapTime(times, t, tf) {
    if (tf === "1D") {
      const day = Math.floor(t / 86400) * 86400 + 43200;
      return times.includes(day) ? day : null;
    }
    let lo = 0, hi = times.length - 1, ans = null;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (times[mid] <= t) { ans = times[mid]; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  },

  // Dữ liệu >= 2025 nằm trong giai đoạn holdout của bot
  inHoldout(sec) { return sec >= Date.UTC(2025, 0, 1) / 1000; },
};
