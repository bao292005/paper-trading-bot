"""Trang 3 - Giám sát bot thật. CHỈ ĐỌC: không có nút nào gửi lệnh. Logic nằm ở ui/core.py."""

import streamlit as st

from paper_bot.ui import core

st.set_page_config(page_title="Giám sát bot", layout="wide")
st.title("Giám sát bot thật")
st.caption("Trang này cho biết gì: tài khoản paper trên Alpaca đang thế nào, bot đã ra tín hiệu / đặt lệnh / "
           "gặp sự kiện gì (đọc từ DB), và kill switch có đang ngắt không. Trang chỉ ĐỌC, không gửi lệnh.")


@st.cache_data(ttl=30, show_spinner="Đang hỏi Alpaca...")
def account():
    return core.fetch_account()


conn = core.open_db()
in_db = core.db_runs(conn) if conn is not None else []
default_idx = next((i for i, r in enumerate(core.LIVE_RUNS) if r in in_db), 0)  # mặc định: run đã có dữ liệu
run_id = st.sidebar.selectbox("run_id", core.LIVE_RUNS, index=default_idx,
                              help="live-paper: bot thật (đặt lệnh paper). live-dry: chạy thử, không gửi lệnh.")

# ---------------------------- kill switch ----------------------------
st.subheader("Kill switch")
try:
    halted, text = core.kill_switch_text(core.read_live_state(run_id))
except ValueError as e:
    halted, text = False, str(e)
    st.error(text)
else:
    (st.error if halted else st.success)(text)
st.caption("Kill switch tự ngắt khi lỗ quá ngưỡng trong ngày hoặc sụt quá sâu từ đỉnh. "
           "Gỡ bằng tay ở trang Điều khiển.")

# ---------------------------- tài khoản Alpaca ----------------------------
st.subheader("Tài khoản Alpaca (paper)")
if st.button("Làm mới"):
    account.clear()
snap, err = account()
if err:
    st.warning(err)
else:
    c1, c2, c3 = st.columns(3)
    c1.metric("Equity", f"{snap['equity']:,.2f}$")
    c2.metric("Tiền mặt", f"{snap['cash']:,.2f}$")
    c3.metric("Số vị thế", len(snap["positions"]))
    st.markdown("**Vị thế đang nắm**")
    if snap["positions"].empty:
        st.info("Không có vị thế.")
    else:
        st.dataframe(snap["positions"], hide_index=True, width="stretch")
    st.markdown("**Lệnh đang chờ**")
    if snap["orders"].empty:
        st.info("Không có lệnh chờ.")
    else:
        st.dataframe(snap["orders"], hide_index=True, width="stretch")

# ---------------------------- từ DB ----------------------------
st.subheader(f"Lịch sử từ DB (run_id = {run_id})")
if conn is None:
    st.info("Chưa có data/trading.db. Chạy bot (`paper-bot once`) ít nhất một lần.")
    st.stop()
if run_id not in in_db:
    st.info(f"DB chưa có dữ liệu cho {run_id}.")
    st.stop()

eq = core.equity_curves(conn, [run_id]).get(run_id)
st.markdown("**Equity theo thời gian**")
if eq is None:
    st.info("Chưa có điểm equity nào.")
else:
    st.line_chart(eq["equity"])

t_sig, t_ord, t_fill, t_evt = st.tabs(["Tín hiệu", "Lệnh", "Khớp lệnh", "Sự kiện"])
with t_sig:
    sig = core.db_table(conn, "signals", run_id)
    st.caption("prob = xác suất đã hiệu chỉnh; veto / veto_reason = luật chặn tín hiệu.")
    if not sig.empty and st.checkbox("Chỉ tín hiệu bị veto"):
        sig = sig[sig["veto"] == 1]
    st.dataframe(sig.drop(columns=["meta", "id"], errors="ignore"), hide_index=True, width="stretch")
with t_ord:
    st.dataframe(core.db_table(conn, "orders", run_id), hide_index=True, width="stretch")
with t_fill:
    st.dataframe(core.db_table(conn, "fills", run_id), hide_index=True, width="stretch")
with t_evt:
    evt = core.db_table(conn, "events", run_id)
    if evt.empty:
        st.info("Chưa có sự kiện.")
    else:
        levels = st.multiselect("Mức (level)", sorted(evt["level"].dropna().unique()))
        kinds = st.multiselect("Loại (kind)", sorted(evt["kind"].dropna().unique()))
        st.dataframe(core.filter_events(evt, levels, kinds).drop(columns=["data"], errors="ignore"),
                     hide_index=True, width="stretch")
