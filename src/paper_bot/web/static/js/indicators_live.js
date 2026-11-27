// Chỉ báo của NẾN CUỐI, tính ngay trên client mỗi tick (không gọi lại API). Hàm thuần, test bằng node.
// Công thức giống backend (web/api/market.py):
//   - MA n  = trung bình cộng n giá đóng cửa gần nhất (pandas rolling(n).mean());
//   - RSI14 = kiểu SMA như data/features.rsi_series: trung bình 14 mức tăng / 14 mức giảm gần nhất.
//     loss = 0: gain > 0 -> 100, gain = 0 -> không có giá trị (backend bỏ điểm NaN).
// Chỉ cần ~201 nến cuối -> mỗi tick O(200), không phụ thuộc số nến đã tải.
const IL = {
  MA_WINDOWS: { ma10: 10, ma30: 30, ma200: 200 },
  RSI_WINDOW: 14,

  // MA n tại nến cuối của mảng closes; thiếu nến -> null
  maLast(closes, n) {
    if (closes.length < n) return null;
    let sum = 0;
    for (let i = closes.length - n; i < closes.length; i++) sum += closes[i];
    return sum / n;
  },

  // RSI n (SMA) tại nến cuối; cần n+1 giá đóng cửa
  rsiLast(closes, n) {
    if (closes.length < n + 1) return null;
    let gain = 0, loss = 0;
    for (let i = closes.length - n; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1];
      if (d > 0) gain += d; else loss -= d;
    }
    gain /= n; loss /= n;
    if (loss === 0) return gain > 0 ? 100 : null;
    return 100 - 100 / (1 + gain / loss);
  },

  // Giá trị cả 4 chỉ báo cho nến cuối của bars [{close,...}]
  liveValues(bars) {
    const closes = bars.slice(-201).map((b) => b.close);
    const out = {};
    for (const [k, n] of Object.entries(IL.MA_WINDOWS)) out[k] = IL.maLast(closes, n);
    out.rsi14 = IL.rsiLast(closes, IL.RSI_WINDOW);
    return out;
  },

  // Ghi điểm {time, value} vào cuối mảng: cùng time -> thay, time mới -> thêm.
  // Trả về điểm đã ghi (để series.update), hoặc null nếu không ghi (value null / time cũ hơn điểm cuối).
  upsertLast(arr, time, value) {
    if (value === null || value === undefined || !isFinite(value)) return null;
    const last = arr[arr.length - 1];
    if (last && last.time > time) return null;
    const p = { time, value };
    if (last && last.time === time) arr[arr.length - 1] = p; else arr.push(p);
    return p;
  },
};
