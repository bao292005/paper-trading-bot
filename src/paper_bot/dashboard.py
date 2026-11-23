"""Dashboard Streamlit (P4): xem bot thật và các lần backtest từ data/trading.db.

Chạy:  uv run streamlit run src/paper_bot/dashboard.py
Các hàm load_* là hàm thuần (test được, không cần Streamlit). Giao diện chỉ dựng trong main().
DB được mở CHỈ ĐỌC: dashboard không bao giờ ghi hay đặt lệnh.
"""

import sqlite3
from pathlib import Path
from typing import Dict, List

import pandas as pd

from paper_bot.backtest.report import load_equity, report_from_store

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "trading.db"
RESULTS_DIR = ROOT / "docs" / "ket-qua"
TABLES = ("signals", "orders", "fills", "equity", "events")


def connect_readonly(path=DB_PATH):
    """Mở DB chỉ đọc. Chưa có file -> None."""
    path = Path(path)
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)


def list_runs(conn) -> List[str]:
    """Mọi run_id xuất hiện trong DB (live, paper-..., bt-...)."""
    q = " UNION ".join(f"SELECT DISTINCT run_id FROM {t}" for t in TABLES)
    return sorted(r[0] for r in conn.execute(q) if r[0] is not None)


def load_table(conn, table: str, run_id: str, limit: int = 5000) -> pd.DataFrame:
    """Đọc một bảng theo run_id, mới nhất trước."""
    if table not in TABLES:
        raise ValueError(f"Bảng không hợp lệ: {table}")
    order = {"signals": "asof", "orders": "asof", "fills": "filled_at",
             "equity": "ts", "events": "ts"}[table]
    return pd.read_sql_query(
        f"SELECT * FROM {table} WHERE run_id = ? ORDER BY {order} DESC LIMIT ?",
        conn, params=(run_id, limit))


def load_equity_many(conn, run_ids: List[str]) -> Dict[str, pd.DataFrame]:
    """Equity + drawdown của nhiều run để so sánh trên cùng biểu đồ."""
    return {r: load_equity(conn, r) for r in run_ids}


def summary_table(conn, run_ids: List[str]) -> pd.DataFrame:
    """Bảng report_from_store của nhiều run (bỏ run không có equity)."""
    rows = [x for x in (report_from_store(conn, r) for r in run_ids) if x]
    return pd.DataFrame(rows).set_index("run_id") if rows else pd.DataFrame()


def load_result_docs(results_dir=RESULTS_DIR) -> Dict[str, str]:
    """Các báo cáo kết quả docs/ket-qua/*.md: tên file -> nội dung."""
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(Path(results_dir).glob("*.md"))}


# ---------------------------- Giao diện ----------------------------

def main():
    import streamlit as st

    st.set_page_config(page_title="Paper Trading Bot", layout="wide")
    st.title("Paper Trading Bot — Dashboard")
    conn = connect_readonly()
    page = st.sidebar.radio("Trang", ["Equity & drawdown", "Lệnh / fill", "Tín hiệu",
                                      "Sự kiện", "Kết quả (docs)"])

    if page == "Kết quả (docs)":
        docs = load_result_docs()
        if not docs:
            st.info("Chưa có báo cáo trong docs/ket-qua/.")
        for name, text in docs.items():
            with st.expander(name, expanded=True):
                st.markdown(text)
        return

    if conn is None:
        st.warning(f"Chưa có DB {DB_PATH}. Chạy bot hoặc `uv run python scripts/run_backtest.py` trước.")
        return
    runs = list_runs(conn)
    if not runs:
        st.info("DB chưa có dữ liệu.")
        return

    if page == "Equity & drawdown":
        default = [r for r in runs if r.startswith("bt-B")][:3] or runs[:1]
        chosen = st.sidebar.multiselect("run_id", runs, default=default)
        if not chosen:
            return
        st.subheader("Tóm tắt")
        st.dataframe(summary_table(conn, chosen))
        curves = load_equity_many(conn, chosen)
        st.subheader("Equity")
        st.line_chart(pd.DataFrame({r: c["equity"] for r, c in curves.items()}))
        st.subheader("Drawdown")
        st.area_chart(pd.DataFrame({r: c["drawdown"] for r, c in curves.items()}))
        return

    run_id = st.sidebar.selectbox("run_id", runs)
    if page == "Lệnh / fill":
        st.subheader("Lệnh (orders)")
        st.dataframe(load_table(conn, "orders", run_id))
        st.subheader("Khớp lệnh (fills)")
        st.dataframe(load_table(conn, "fills", run_id))
    elif page == "Tín hiệu":
        df = load_table(conn, "signals", run_id)
        only_veto = st.sidebar.checkbox("Chỉ tín hiệu bị veto")
        if only_veto and not df.empty:
            df = df[df["veto"] == 1]
        st.caption("reason = lý do của chiến lược; prob = xác suất đã hiệu chỉnh; "
                   "veto/veto_reason = luật chặn tín hiệu.")
        st.dataframe(df[[c for c in ["asof", "symbol", "strategy", "action", "price", "confidence",
                                     "prob", "veto", "veto_reason", "reason", "regime"]
                         if c in df.columns]])
    elif page == "Sự kiện":
        df = load_table(conn, "events", run_id)
        kinds = sorted(df["kind"].dropna().unique()) if not df.empty else []
        chosen = st.sidebar.multiselect("Loại sự kiện (vd kill_switch, reconcile)", kinds)
        if chosen:
            df = df[df["kind"].isin(chosen)]
        st.dataframe(df)


if __name__ == "__main__":
    main()
