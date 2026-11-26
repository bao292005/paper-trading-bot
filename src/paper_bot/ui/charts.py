"""Biểu đồ altair cho UI (altair có sẵn cùng streamlit, không cần plotly).
Mọi hàm nhận DataFrame có index tên "date" và trả về alt.Chart; không gọi streamlit nên test được."""

import altair as alt
import pandas as pd

MA_COLUMNS = ["ma10", "ma30", "ma200"]
MA_COLORS = ["#f5a623", "#4a90e2", "#9013fe"]
UP, DOWN = "#26a69a", "#ef5350"


def _ma_lines(d: pd.DataFrame) -> alt.Chart:
    long = d.melt(id_vars="date", value_vars=MA_COLUMNS, var_name="Đường MA", value_name="giá").dropna()
    return alt.Chart(long).mark_line(strokeWidth=1.5).encode(
        x="date:T", y=alt.Y("giá:Q", scale=alt.Scale(zero=False)),
        color=alt.Color("Đường MA:N", scale=alt.Scale(domain=MA_COLUMNS, range=MA_COLORS)))


def price_chart(df: pd.DataFrame, candles: bool = True) -> alt.Chart:
    """Nến (hoặc đường giá đóng cửa) 1 năm kèm MA10/MA30/MA200. df: cột open/high/low/close/ma10/ma30/ma200."""
    d = df.reset_index()
    y = alt.Y("low:Q", scale=alt.Scale(zero=False), title="Giá")
    if candles:
        color = alt.condition("datum.open <= datum.close", alt.value(UP), alt.value(DOWN))
        base = alt.Chart(d).encode(x=alt.X("date:T", title=None), color=color)
        body = (base.mark_rule().encode(y=y, y2="high:Q") +
                base.mark_bar(size=3).encode(y="open:Q", y2="close:Q"))
    else:
        body = alt.Chart(d).mark_line(color="#555").encode(
            x=alt.X("date:T", title=None), y=alt.Y("close:Q", scale=alt.Scale(zero=False), title="Giá"))
    return (body + _ma_lines(d)).properties(height=320)


def fan_chart(bars: pd.DataFrame, fan: pd.DataFrame, history: int = 30) -> alt.Chart:
    """Fan chart: `history` phiên giá đóng cửa gần nhất + dải q10-q90 và đường q50 cho các phiên tới.
    fan: bảng từ core.fan_chart_frame (cột q10, q50, q90)."""
    hist = bars["close"].iloc[-history:].rename("close").reset_index()
    f = fan.reset_index()
    x = alt.X("date:T", title=None)
    band = alt.Chart(f).mark_area(opacity=0.25, color="#4a90e2").encode(
        x=x, y=alt.Y("q10:Q", scale=alt.Scale(zero=False), title="Giá"), y2="q90:Q")
    mid = alt.Chart(f).mark_line(color="#4a90e2", strokeDash=[5, 3]).encode(x=x, y="q50:Q")
    past = alt.Chart(hist).mark_line(color="#555").encode(x=x, y="close:Q")
    return (band + mid + past).properties(height=260)
