"""Bar replay: GET /api/replay/bars = nến + chỉ báo CẮT tại con trỏ (không lộ tương lai).

Con trỏ `cursor` = thời điểm (unix giây, làm tròn xuống phút). Chỉ dùng nến 1m BẮT ĐẦU trước con trỏ
(tức đã đóng hẳn). Nến khung lớn đang hình thành chỉ gộp từ các nến 1m đó (vd 10:37 khung 1h -> nến 10:30
gồm 10:30..10:36). Khung 1D: ngày đã đóng cửa lấy từ cache ngày, ngày đang chạy gộp từ nến 1m.
`steps=k`: tiến con trỏ k nến của khung tf (server chỉ dùng THỜI GIAN của nến tương lai để biết nến kế tiếp kết thúc lúc nào).
"""

import time
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request

from ...data.features import rsi_series
from ...data.intraday import TIMEFRAMES, resample_bars
from .market import DEFAULT_DAYS, DOWN_COLOR, UP_COLOR, WARMUP_DAYS, load_df
from .timeutil import TF_MINUTES, check_symbol, index_to_unix, local_tz, unix_seconds

router = APIRouter()
NUM = r"^\d+$"
HORIZON_DAYS = {"1m": 5, "5m": 6, "15m": 8, "1h": 16, "1D": 120}  # tìm nến kế tiếp trong khoảng này


def now_unix() -> int:
    return int(time.time())


def to_local(unix: int, asset: str) -> pd.Timestamp:
    """unix giây -> giờ địa phương của asset (stock: NY, crypto: UTC), không timezone."""
    return pd.Timestamp(int(unix), unit="s", tz="UTC").tz_convert(local_tz(asset)).tz_localize(None)


def to_unix(ts: pd.Timestamp, asset: str) -> int:
    return unix_seconds(pd.Timestamp(ts).tz_localize(local_tz(asset)))


def day_open(day: pd.Timestamp, asset: str) -> pd.Timestamp:
    return day + (pd.Timedelta(0) if asset == "crypto" else pd.Timedelta(hours=9, minutes=30))


def day_close(day: pd.Timestamp, asset: str) -> pd.Timestamp:
    return day + (pd.Timedelta(days=1) if asset == "crypto" else pd.Timedelta(hours=16))


def minute_rows(request: Request, symbol: str, asset: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Nến 1m có thời điểm bắt đầu trong [start, end) (giờ địa phương)."""
    if end <= start:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = load_df(request, symbol, "1m", start, end - pd.Timedelta(minutes=1), asset)
    return df[(df.index >= start) & (df.index < end)]


def frame_at(request, symbol, tf, asset, t_local, start_local):
    """Nến khung tf từ start_local tới con trỏ t_local (nến cuối có thể đang hình thành). -> (df, fallback)."""
    if tf != "1D":
        return resample_bars(minute_rows(request, symbol, asset, start_local, t_local), tf, asset), False
    daily = load_df(request, symbol, "1D", start_local.normalize(), t_local, asset)
    today = t_local.normalize()
    done = daily[[day_close(d, asset) <= t_local for d in daily.index]]
    if day_close(today, asset) <= t_local or t_local <= day_open(today, asset):
        return done, False
    m = minute_rows(request, symbol, asset, day_open(today, asset), t_local)
    if m.empty:  # không có nến 1m của ngày đang chạy (dữ liệu quá cũ) -> chỉ hiện ngày đã đóng
        return done, bool(today in daily.index)
    row = pd.DataFrame({"open": [m["open"].iloc[0]], "high": [m["high"].max()], "low": [m["low"].min()],
                        "close": [m["close"].iloc[-1]], "volume": [m["volume"].sum()]}, index=pd.DatetimeIndex([today]))
    return pd.concat([done, row]), False


def _bucket(index: pd.DatetimeIndex, tf: str, asset: str) -> pd.DatetimeIndex:
    """Mốc bắt đầu nến khung tf chứa từng nến 1m (cổ phiếu: mốc 09:30 mỗi ngày)."""
    step = pd.Timedelta(minutes=TF_MINUTES[tf])
    base = index.normalize() + (pd.Timedelta(0) if asset == "crypto" else pd.Timedelta(hours=9, minutes=30))
    return base + ((index - base) // step) * step


def next_cursor(request, symbol, tf, asset, t: int, steps: int) -> Optional[int]:
    """Con trỏ sau khi đi thêm `steps` nến khung tf (= lúc nến thứ steps kết thúc). Hết dữ liệu -> None."""
    t_local = to_local(t, asset)
    horizon = t_local + pd.Timedelta(days=HORIZON_DAYS[tf] + steps * (2 if tf == "1D" else 0))
    if tf == "1D":
        daily = load_df(request, symbol, "1D", t_local.normalize(), horizon, asset)
        ends = [day_close(d, asset) for d in daily.index if day_close(d, asset) > t_local]
        return to_unix(ends[min(steps, len(ends)) - 1], asset) if ends else None
    m = minute_rows(request, symbol, asset, t_local, horizon)
    if m.empty:
        return None
    labels = pd.Series(m.index, index=_bucket(m.index, tf, asset))
    last_per_bucket = labels.groupby(level=0).max().sort_index()
    pick = last_per_bucket.iloc[min(steps, len(last_per_bucket)) - 1]
    return to_unix(pick + pd.Timedelta(minutes=1), asset)


def indicator_cols(df: pd.DataFrame) -> dict:
    cols = {f"ma{w}": df["close"].rolling(w).mean() for w in (10, 30, 200)}
    cols["rsi14"] = rsi_series(df, 14).replace([float("inf"), float("-inf")], float("nan"))
    return cols


@router.get("/api/replay/bars")
def replay_bars(request: Request, symbol: str, cursor: str = Query(..., pattern=NUM), tf: str = "1D",
                asset: str = "stock", after: Optional[str] = Query(None, pattern=NUM),
                before: Optional[str] = Query(None, pattern=NUM), steps: int = Query(0, ge=0, le=50)):
    """Nến <= con trỏ. after=<time>: chỉ trả nến có time >= after (cập nhật khi step); before=<time>: nến cũ hơn (kéo trái)."""
    symbol = check_symbol(symbol, asset)
    if tf not in TIMEFRAMES:
        raise HTTPException(400, f"tf phải thuộc {list(TIMEFRAMES)}")
    t = min(int(cursor), now_unix()) // 60 * 60
    end = False
    if steps:
        nxt = next_cursor(request, symbol, tf, asset, t, steps)
        end = nxt is None or nxt <= t
        t = t if end else min(nxt, now_unix() // 60 * 60)
    t_local = to_local(t, asset)
    cut_local = to_local(min(int(before), t), asset) if before else t_local
    show_from = to_local(int(after), asset) if after else cut_local - pd.Timedelta(days=DEFAULT_DAYS[tf])
    df, fallback = frame_at(request, symbol, tf, asset, cut_local,
                            (show_from - pd.Timedelta(days=WARMUP_DAYS[tf])).normalize())
    times = index_to_unix(df.index, tf, asset)
    first = int(after) if after else index_to_unix(pd.DatetimeIndex([show_from]), tf, asset)[0]
    keep = [i for i, x in enumerate(times) if x >= first and (before is None or x < int(before))]
    cols = indicator_cols(df)
    out_bars, out_vol = [], []
    for i in keep:
        r = df.iloc[i]
        out_bars.append({"time": times[i], "open": r.open, "high": r.high, "low": r.low, "close": r.close})
        out_vol.append({"time": times[i], "value": r.volume, "color": UP_COLOR if r.close >= r.open else DOWN_COLOR})
    ind = {k: [{"time": times[i], "value": float(s.iloc[i])} for i in keep if pd.notna(s.iloc[i])] for k, s in cols.items()}
    return {"symbol": symbol, "tf": tf, "asset": asset, "cursor": t, "end": end, "fallback": fallback,
            "bars": out_bars, "volume": out_vol, "ind": ind, "oldest": out_bars[0]["time"] if out_bars else None}
