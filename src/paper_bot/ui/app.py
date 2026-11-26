"""Trang chủ UI. Chạy:  uv run streamlit run src/paper_bot/ui/app.py
Các trang con nằm trong thư mục pages/ (Streamlit tự hiện ở thanh bên)."""

from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Paper Trading Bot", layout="wide")
st.title("Paper Trading Bot")
st.caption("Trang này cho biết gì: bot gồm những khối nào, chúng nối với nhau ra sao, và nên mở trang nào để xem gì.")

st.subheader("Luồng dữ liệu")
st.code(
    "P1 Dữ liệu + dự báo ──MarketState──▶ P2 Quyết định ──Signal──▶ P3 Rủi ro / đặt lệnh\n"
    "  nến, regime, features,                chiến lược → veto →          cỡ lệnh theo ATR, stop-loss,\n"
    "  forecast, tin tức                     hiệu chỉnh xác suất → lọc    take-profit, kiểm tra tương quan\n"
    "                                                                          │\n"
    "                                                                   OrderIntent / Fill\n"
    "                                                                          ▼\n"
    "                                    P4 Đánh giá (backtest, báo cáo) ◀── DB (SQLite: signals, orders,\n"
    "                                                                          fills, equity, events)",
    language=None)
st.markdown("""
- **P1** gom nến ngày, regime thị trường (từ SPY), chỉ báo, dự báo lợi suất 5 phiên và tin tức → `MarketState`.
- **P2** cho các chiến lược nói ý kiến, luật veto có thể chặn, calibrator đổi điểm thô thành xác suất → `Signal`.
- **P3** biến `Signal` thành lệnh cụ thể (bao nhiêu cổ, stop-loss, take-profit) nếu qua các luật rủi ro.
- **DB** lưu mọi tín hiệu, lệnh, khớp lệnh, equity, sự kiện. **P4** đọc DB để đánh giá.
""")
st.info("Đây là paper trading (tiền giả trên tài khoản paper của Alpaca). Dữ liệu từ 2025-01-01 là holdout, "
        "chưa được dùng để tối ưu bất cứ thứ gì.")

st.subheader("Các trang")
PAGES = [
    ("pages/1_Pipeline.py", "1. Pipeline", "Chạy thử P1 → P2 → P3 cho một mã, một ngày (hoặc quét nhiều phiên)."),
    ("pages/2_Danh_gia.py", "2. Đánh giá", "Equity, drawdown, ablation, calibration của các lần backtest (P4)."),
    ("pages/3_Giam_sat.py", "3. Giám sát", "Tài khoản Alpaca, tín hiệu/lệnh/sự kiện của bot thật, kill switch."),
    ("pages/4_Dieu_khien.py", "4. Điều khiển", "Chạy thử (dry-run) hoặc chạy một vòng thật, có xác nhận."),
]
here = Path(__file__).parent
for path, label, desc in PAGES:
    col_a, col_b = st.columns([1, 4])
    if (here / path).exists():
        col_a.page_link(path, label=label)
    else:
        col_a.write(f"{label} (sắp có)")
    col_b.write(desc)
