"""Trang 4 - Điều khiển bot. Lệnh chỉ được ghép từ lựa chọn có whitelist (ui/core.py), chạy bằng subprocess
(list args, không shell). KHÔNG có nút chạy loop. Chạy THẬT cần tick + gõ XAC NHAN."""

import shlex

import streamlit as st

from paper_bot.config import ENGINES, SHORT_FLAG_NAMES, UNIVERSE_50
from paper_bot.ui import core

st.set_page_config(page_title="Điều khiển bot", layout="wide")
st.title("Điều khiển bot")
st.caption("Trang này cho biết gì: chạy thử bot một vòng (dry-run, không gửi lệnh), chạy một vòng THẬT trên tài khoản paper, "
           "hoặc gỡ kill switch. Mỗi hành động nguy hiểm đều cần bạn xác nhận.")
st.warning("Chạy THẬT sẽ gửi lệnh lên tài khoản paper của Alpaca (tiền giả, nhưng lệnh là thật). "
           "Cấu hình mặc định chưa được chứng minh có lãi (xem trang Đánh giá). Nên chạy thử dry-run trước.")


def show_result(res: dict):
    """Hiện kết quả của run_command."""
    if res["timed_out"]:
        st.error(f"Quá {core.RUN_TIMEOUT} giây nên đã dừng lệnh.")
    elif res["returncode"] == 0:
        st.success("Lệnh chạy xong (exit code 0).")
    else:
        st.error(f"Lệnh lỗi (exit code {res['returncode']}).")
    st.markdown("**stdout**")
    st.code(res["stdout"] or "(trống)", language=None)
    st.markdown("**stderr** (bot ghi log ở đây nên thường không rỗng)")
    st.code(res["stderr"] or "(trống)", language=None)


# ---------------------------- lựa chọn ----------------------------
st.subheader("Chọn tham số")
c1, c2 = st.columns(2)
picked = c1.multiselect("Mã cổ phiếu", UNIVERSE_50, default=core.DEFAULT_UI_SYMBOLS, key="symbols")
extra = c1.text_input("Mã khác (chữ HOA, cách nhau bằng dấu cách)", key="extra",
                      help="Chỉ nhận chữ HOA A-Z, tối đa 6 ký tự mỗi mã.")
engine = c2.radio("Engine", ENGINES, key="engine", horizontal=True,
                  help="full = pipeline mới; legacy = bot gốc của bài (MA + RSI).")
disable = c2.multiselect("Tắt thành phần (--disable)", SHORT_FLAG_NAMES, key="disable",
                         disabled=engine == "legacy", help="Chỉ dùng được với engine full.")
llm = c2.checkbox("Thêm LLM agent (--llm, cần LLM_API_KEY)", key="llm")
interval = c2.number_input("Chu kỳ loop (phút) - chỉ để tạo câu lệnh terminal", min_value=1, max_value=1440,
                           value=30, step=1, key="interval")

symbols = picked + [s for s in extra.split() if s not in picked]
try:
    dry_cmd = core.build_once_command(symbols, engine, disable if engine == "full" else (), llm, dry_run=True)
except ValueError as e:
    st.error(f"Tham số không hợp lệ, chưa tạo được lệnh: {e}")
    st.stop()

# ---------------------------- chạy thử ----------------------------
st.subheader("1. Chạy thử (dry-run)")
st.caption("Chạy 1 vòng, ghi DB (run_id = live-dry) nhưng KHÔNG gửi hay huỷ lệnh nào. Timeout 10 phút.")
st.code(shlex.join(dry_cmd), language="bash")
if st.button("Chạy thử (dry-run)", key="run_dry"):
    with st.spinner("Đang chạy dry-run..."):
        show_result(core.run_command(dry_cmd, allow_real=False))

# ---------------------------- chạy thật ----------------------------
st.subheader("2. Chạy THẬT (đặt lệnh paper)")
st.error("Lệnh sẽ được gửi lên tài khoản paper Alpaca. Sự kiện `ui_real_run` được ghi vào DB (run_id = live-paper).")
ack = st.checkbox("Tôi hiểu lệnh sẽ được gửi lên tài khoản paper", key="ack_real")
word = st.text_input(f"Gõ đúng chữ {core.CONFIRM_WORD} để mở nút", key="confirm_real")
ok = core.confirmed(ack, word)
if st.button("Chạy THẬT", key="run_real", disabled=not ok, type="primary"):
    try:
        real_cmd = core.build_once_command(symbols, engine, disable if engine == "full" else (), llm,
                                           dry_run=False, ack=ack, confirm_text=word)
        core.log_ui_event(core.real_run_id(False), "ui_real_run", f"UI gửi lệnh thật: {shlex.join(real_cmd)}")
        with st.spinner("Đang chạy THẬT..."):
            show_result(core.run_command(real_cmd, allow_real=ok))
    except PermissionError as e:
        st.error(str(e))

# ---------------------------- kill switch ----------------------------
st.subheader("3. Gỡ kill switch")
st.caption("Chỉ gỡ sau khi đã xem lại vì sao bot bị ngắt (trang Giám sát). Gỡ xong bot được mua lại.")
ks_run = st.radio("Run cần gỡ", core.LIVE_RUNS, key="ks_run", horizontal=True)
ks_ack = st.checkbox("Tôi đã xem lại và muốn gỡ kill switch", key="ack_ks")
ks_word = st.text_input(f"Gõ đúng chữ {core.CONFIRM_WORD}", key="confirm_ks")
ks_ok = core.confirmed(ks_ack, ks_word)
if st.button("Gỡ kill switch", key="run_ks", disabled=not ks_ok):
    try:
        ks_cmd = core.build_reset_kill_switch_command(ks_run == "live-dry", ks_ack, ks_word)
        with st.spinner("Đang gỡ..."):
            show_result(core.run_command(ks_cmd))
    except PermissionError as e:
        st.error(str(e))

# ---------------------------- loop: chỉ hiện câu lệnh ----------------------------
st.subheader("4. Chạy liên tục (loop)")
st.info("UI KHÔNG chạy loop. Muốn chạy liên tục, chép câu lệnh này vào terminal (Ctrl+C để dừng). "
        "Bỏ `--dry-run` nếu muốn đặt lệnh thật.")
st.code(core.loop_command_text(symbols, engine, disable if engine == "full" else (), llm, True, int(interval)),
        language="bash")
