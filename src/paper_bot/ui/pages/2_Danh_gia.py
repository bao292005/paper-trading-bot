"""Trang 2 - Đánh giá (P4): backtest, ablation, calibration, forecast. Mọi logic nằm ở ui/core.py."""

import pandas as pd
import streamlit as st

from paper_bot.ui import core

st.set_page_config(page_title="Đánh giá (P4)", layout="wide")
st.title("Đánh giá (P4)")
st.caption("Trang này cho biết gì: các cấu hình bot (bản đầy đủ, bỏ từng thành phần, và các mốc so sánh B0/B1/B2) "
           "lãi lỗ ra sao trong backtest, và các thành phần dự báo / hiệu chỉnh xác suất có thật sự giúp ích không.")
st.warning("Đây là giai đoạn PHÁT TRIỂN (2019-01 → 2024-12). Holdout (từ 2025-01-01) CHƯA chạy, "
           "nên chưa có kết luận chính thức. Con số ở đây được phép dùng để chỉnh tham số.")


def show_doc_tables(name: str, intro: str = ""):
    """Hiện mọi bảng markdown của docs/ket-qua/<name>, mỗi bảng một khối có tiêu đề."""
    text = core.read_result_doc(name)
    if not text:
        st.info(f"Chưa có docs/ket-qua/{name}.")
        return
    if intro:
        st.caption(intro)
    tables = core.markdown_tables(text)
    for heading, df in tables:
        st.markdown(f"**{heading or name}**")
        st.dataframe(df, hide_index=True, width="stretch")
    if not tables:
        st.markdown(text)
    with st.expander(f"Xem nguyên văn {name}"):
        st.markdown(text)


tab_eq, tab_abl, tab_cal, tab_fc = st.tabs(["Equity & drawdown", "Ablation", "Calibration", "Forecast"])

with tab_eq:
    conn = core.open_db()
    runs = core.backtest_runs(core.db_runs(conn)) if conn is not None else []
    if not runs:
        st.info("Chưa có run backtest (bt-*) trong data/trading.db. Chạy `uv run python scripts/run_backtest.py` trước.")
    else:
        st.caption("Equity = tổng giá trị tài khoản theo ngày; drawdown = mức sụt so với đỉnh gần nhất. "
                   "Chọn nhiều run để đặt cạnh nhau.")
        default = [r for r in ("bt-abl-FULL", "bt-abl-B0", "bt-abl-B1", "bt-abl-B2") if r in runs] or runs[:3]
        chosen = st.multiselect("Run backtest", runs, default=default, format_func=core.run_label)
        if chosen:
            st.dataframe(core.run_summary(conn, chosen), width="stretch")
            curves = core.equity_curves(conn, chosen)
            if curves:
                st.markdown("**Equity ($)**")
                st.line_chart(pd.DataFrame({r: c["equity"] for r, c in curves.items()}))
                st.markdown("**Drawdown**")
                st.area_chart(pd.DataFrame({r: c["drawdown"] for r, c in curves.items()}))
            else:
                st.info("Các run đã chọn chưa có dữ liệu equity.")

with tab_abl:
    st.caption("Ablation = bỏ từng thành phần khỏi bản FULL rồi xem Sharpe đổi thế nào. "
               "Thành phần 'có giá trị' khi bỏ nó làm Sharpe giảm ≥ 0,1.")
    show_doc_tables("ablation-dev.md")

with tab_cal:
    st.caption("Calibration: xác suất bot đưa ra có khớp tần suất thắng thật không (Brier càng thấp càng tốt).")
    show_doc_tables("calibration.md")
    with st.expander("Kết luận viết tay về calibration"):
        st.markdown(core.read_result_doc("calibration-ketluan.md") or "Chưa có file kết luận.")

with tab_fc:
    st.caption("Forecast (Chronos): dự báo lợi suất 5 phiên có chính xác không, khoảng q10–q90 có phủ đủ không.")
    show_doc_tables("forecast-eval.md")
