"""Trang 1 - Pipeline P1 -> P2 -> P3 (tương tác). Mọi logic nằm ở ui/core.py; file này chỉ vẽ."""

import pandas as pd
import streamlit as st

from paper_bot.config import FEATURE_FLAGS, UNIVERSE_50
from paper_bot.ui import charts, core

st.set_page_config(page_title="Pipeline P1→P2→P3", layout="wide")
st.title("Pipeline P1 → P2 → P3")
st.caption("Trang này cho biết gì: với một mã và một ngày, bot nhìn thấy gì (P1 dữ liệu + dự báo), "
           "quyết định gì (P2) và sẽ đặt lệnh thế nào (P3). Đây là MÔ PHỎNG, không gửi lệnh nào.")


# ---------------------------- cache cho phần nặng ----------------------------
@st.cache_data(show_spinner=False)
def load_data(symbols: tuple):
    return core.load_market_data(symbols)


@st.cache_data(show_spinner=False)
def forecast_table():
    return core.load_forecast_table()


@st.cache_data(show_spinner="Đang lấy tin...")
def load_news(symbols: tuple, asof: str):
    return core.fetch_news(list(symbols), asof)


@st.cache_resource(show_spinner="Đang tải Chronos (lần đầu hơi lâu)...")
def chronos():
    from paper_bot.data.forecast import ChronosForecaster

    return ChronosForecaster()


@st.cache_resource(show_spinner="Đang nạp / fit calibrator (dữ liệu tới 2024-12-31)...")
def calibrator(symbols: tuple, use_forecast: bool):
    return core.get_calibrator(symbols, {"use_calibration": True, "use_forecast": use_forecast})


# ---------------------------- sidebar ----------------------------
sb = st.sidebar
sb.header("Lựa chọn")
symbols = sb.multiselect("Mã cổ phiếu", UNIVERSE_50, default=core.DEFAULT_UI_SYMBOLS)
if not symbols:
    st.info("Chọn ít nhất 1 mã ở thanh bên trái.")
    st.stop()

bars, spy, missing = load_data(tuple(symbols))
if spy.empty:
    st.error("Chưa có cache nến SPY (data/bars/SPY.parquet). Chạy bước nạp dữ liệu của P1 trước.")
    st.stop()
if missing:
    st.warning(f"Chưa có cache cho: {', '.join(missing)}")
symbols = [s for s in symbols if s in bars]
if not symbols:
    st.stop()

dates = core.available_dates(spy)
picked = sb.date_input("Ngày asof (quyết định ở giá đóng cửa)", value=dates[-1].date(),
                       min_value=dates[0].date(), max_value=dates[-1].date())
asof = dates[dates <= pd.Timestamp(picked)][-1]
sb.caption(f"Phiên dùng: {asof.date()}")
if core.is_holdout(asof):
    st.warning("Ngày này thuộc HOLDOUT (≥ 2025-01-01). Xem thì được; calibrator vẫn chỉ fit tới 2024-12-31.")

sb.subheader("Bật / tắt thành phần")
flags = core.flags_from_selection(
    [f for f in FEATURE_FLAGS if sb.checkbox(core.FLAG_LABELS[f], value=True, key=f)])
use_chronos = sb.checkbox("Chạy Chronos thật khi bảng forecast tính sẵn thiếu ngày này", value=False)
use_news = sb.checkbox("Lấy tin từ Alpaca (cần key trong .env)", value=False)

sb.subheader("Giả định cho P3")
equity = sb.number_input("Vốn giả định ($)", min_value=1000.0, value=100_000.0, step=1000.0)
with sb.expander("Vị thế giả định (số cổ đang nắm)"):
    positions = {s: float(st.number_input(s, min_value=0, value=0, step=1, key=f"pos_{s}")) for s in symbols}
mode = sb.radio("Chế độ", ["Một ngày", "Quét N phiên"])

# ---------------------------- dùng chung cho cả hai chế độ ----------------------------
table = forecast_table()
forecaster = chronos() if (use_chronos and flags["use_forecast"]) else None
cal, cal_note = (calibrator(tuple(symbols), flags["use_forecast"]) if flags["use_calibration"]
                 else (None, "đã tắt"))
pipe = core.build_pipeline(flags, cal)
st.caption(f"Calibrator: {cal_note}")

# ---------------------------- các khối hiển thị ----------------------------
FEATURE_HELP = {"atr14": "Biên độ dao động trung bình 14 phiên ($)", "vol20": "Độ biến động lợi suất 20 phiên",
                "rsi14": "RSI 14: <30 quá bán, >70 quá mua", "ret5": "Lợi suất log 5 phiên",
                "ret20": "Lợi suất log 20 phiên", "dist_ma50": "Giá cách MA50 bao nhiêu %"}


def pct(x: float) -> str:
    return f"{x:+.2%}"


def show_p1(state):
    s = core.state_summary(state)
    c1, c2, c3 = st.columns(3)
    c1.metric("Giá đóng cửa", f"{s['close']:.2f}")
    c2.metric("Regime thị trường (tính từ SPY)", s["regime"])
    c3.metric("Số nến đưa vào MarketState", s["n_bars"])
    candles = st.radio("Kiểu biểu đồ", ["Nến", "Đường"], horizontal=True, key=f"kind_{state.symbol}") == "Nến"
    st.altair_chart(charts.price_chart(core.price_with_ma(bars[state.symbol], state.asof), candles),
                    width="stretch")
    st.caption("1 năm tới ngày asof, kèm MA10 (cam), MA30 (xanh), MA200 (tím).")
    fan = core.fan_chart_frame(state)
    if fan is None:
        st.info("Không có forecast (cờ forecast tắt, hoặc bảng tính sẵn thiếu ngày này — "
                "tick 'Chạy Chronos thật' ở thanh bên nếu muốn tính).")
    else:
        cols = st.columns(4)
        cols[0].metric("Forecast: mô hình", s["forecast_model"])
        cols[1].metric("q10 (kịch bản xấu)", pct(s["q10"]))
        cols[2].metric("q50 (trung vị)", pct(s["q50"]))
        cols[3].metric("q90 (kịch bản tốt)", pct(s["q90"]))
        st.altair_chart(charts.fan_chart(state.bars, fan), width="stretch")
        st.caption(f"Lợi suất {core.HORIZON} phiên tới (độ rộng q90−q10 = {pct(s['width'])}). "
                   "Đường giữa/dải được nội suy tuyến tính từ điểm cuối nên chỉ để minh hoạ.")
    st.markdown("**Features**")
    st.dataframe(pd.DataFrame([{"chỉ báo": k, "giá trị": v, "ý nghĩa": FEATURE_HELP.get(k, "")}
                               for k, v in state.features.items()]), hide_index=True)
    st.markdown("**Tin gần đây** (≤ asof)")
    if not use_news:
        st.caption("Chưa bật 'Lấy tin' ở thanh bên.")
    for n in state.news[:10]:
        st.write(f"- `{n.created_at:%m-%d %H:%M}` {n.headline}")
    if use_news and not state.news:
        st.caption("Không có tin nào trong 7 ngày trước asof.")


def show_p2(state):
    d = core.decide(pipe, state)
    if d.signals:
        st.dataframe(core.signal_rows(d.signals), hide_index=True)
    else:
        st.caption("Không chiến lược nào lên tiếng ở ngày này.")
    st.write(f"**Ghi chú gộp:** {d.note or '-'}")
    (st.success if d.final else st.info)(d.verdict)
    return d


def show_p3(state, d, cash):
    st.caption("MÔ PHỎNG — không gửi lệnh nào lên Alpaca.")
    plan = core.plan_order(d.final, state, flags, equity, cash, positions, bars,
                           ref_width=core.ref_width_at(table, state.asof))
    if plan.intent is None:
        st.warning(f"Không ra lệnh: {plan.rejected}")
    else:
        i = plan.intent
        cols = st.columns(4)
        cols[0].metric("Lệnh", f"{i.side.upper()} {i.qty} cổ")
        cols[1].metric("Số tiền", f"${plan.notional:,.0f}", f"{plan.pct_equity:.1%} vốn", delta_color="off")
        cols[2].metric("Stop-loss", f"{i.stop_loss:.2f}" if i.stop_loss else "không có")
        cols[3].metric("Take-profit", f"{i.take_profit:.2f}" if i.take_profit else "không có")
        if plan.risk_amount is not None:
            st.caption(f"Nếu chạm stop-loss sẽ mất khoảng ${plan.risk_amount:,.0f} "
                       f"({plan.risk_amount / equity:.2%} vốn). Giá hiện tại {plan.price:.2f}.")
        st.caption(f"Lý do: {i.reason}")
    if plan.corr_ok is None:
        st.caption("Kiểm tra tương quan: không cần (không phải BUY, chưa nắm mã nào khác, hoặc cờ đã tắt).")
    else:
        (st.success if plan.corr_ok else st.error)(f"Kiểm tra tương quan: {plan.corr_msg}")


def run_one_day():
    fcs = core.get_forecasts(core.cut_upto(bars, asof), asof, flags, table, forecaster)
    news = []
    if use_news:
        news, err = load_news(tuple(symbols), str(asof.date()))
        if err:
            st.warning(err)
    held_value = sum(q * float(bars[s][bars[s].index <= asof]["close"].iloc[-1])
                     for s, q in positions.items() if q > 0)
    cash = max(0.0, equity - held_value)
    st.caption(f"Vốn ${equity:,.0f}, tiền mặt giả định ${cash:,.0f} (vốn trừ giá trị vị thế giả định).")
    for tab, sym in zip(st.tabs(symbols), symbols):
        with tab:
            if asof not in bars[sym].index:
                st.info(f"{sym} không có phiên {asof.date()}.")
                continue
            state = core.build_state(sym, asof, bars, spy, fcs, news)
            st.subheader("P1 — Dữ liệu & dự báo")
            show_p1(state)
            st.subheader("P2 — Quyết định")
            d = show_p2(state)
            st.subheader("P3 — Lệnh dự kiến")
            show_p3(state, d, cash)


def run_scan():
    n = st.slider("Số phiên quét ngược (tính tới ngày asof)", 10, 250, 60)
    sig = (tuple(symbols), str(asof.date()), n, tuple(flags.items()), forecaster is not None)
    if st.button("Quét"):
        with st.spinner("Đang quét..."):
            df = core.scan_sessions(symbols, core.recent_dates(dates, n, end=asof), bars, spy, flags, pipe,
                                    table, forecaster)
        st.session_state["scan"] = (sig, df)
    saved = st.session_state.get("scan")
    if not saved or saved[0] != sig:
        st.info("Bấm 'Quét' để tìm các ngày có tín hiệu (đổi lựa chọn thì phải quét lại).")
        return
    df = saved[1]
    if df.empty:
        st.info("Không có tín hiệu nào trong khoảng này — thử tăng số phiên hoặc đổi mã.")
        return
    st.caption(f"{len(df)} dòng có tín hiệu; final = '-' nghĩa là bị veto hoặc không đạt ngưỡng.")
    st.dataframe(df, hide_index=True)


if mode == "Một ngày":
    run_one_day()
else:
    run_scan()
