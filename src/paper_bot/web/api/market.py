"""API dữ liệu thị trường: /api/symbols, /api/bars, /api/indicators."""

from typing import Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request

from ...config import BENCHMARK, CRYPTO_UNIVERSE, DEFAULT_SYMBOLS, UNIVERSE_50
from ...data.cache import BARS_DIR
from ...data.features import rsi_series
from ...data.intraday import TIMEFRAMES
from .timeutil import check_asset, check_symbol, index_to_unix, local_tz, parse_time

router = APIRouter()

# Khoảng mặc định (ngày lịch) khi không truyền start; WARMUP = đoạn nạp thêm để MA200/RSI có giá trị từ đầu.
DEFAULT_DAYS = {"1m": 5, "5m": 20, "15m": 45, "1h": 150, "1D": 730}
WARMUP_DAYS = {"1m": 5, "5m": 8, "15m": 15, "1h": 60, "1D": 450}
UP_COLOR, DOWN_COLOR = "#26a69a80", "#ef535080"


def resolve_range(tf: str, start, end, before, asset: str = "stock"):
    """-> (start, end, before_unix). `before` (unix giây) = chỉ lấy nến có time < before (dùng khi kéo về quá khứ).
    start/end hiểu theo giờ địa phương của asset (stock: NY, crypto: UTC)."""
    if tf not in TIMEFRAMES:
        raise HTTPException(400, f"tf phải thuộc {list(TIMEFRAMES)}")
    before_ts = parse_time(before, asset)
    end_ts = parse_time(end, asset) or before_ts or pd.Timestamp.now(tz=local_tz(asset)).tz_localize(None)
    start_ts = parse_time(start, asset) or (end_ts - pd.Timedelta(days=DEFAULT_DAYS[tf])).normalize()
    return start_ts, end_ts, (int(before) if before else None)


def load_df(request: Request, symbol: str, tf: str, start, end, asset: str = "stock") -> pd.DataFrame:
    try:
        if asset == "crypto":  # chỉ truyền asset khi là crypto: hàm load_intraday giả của test cũ không có tham số này
            return request.app.state.load_intraday(symbol, tf, start, end, asset="crypto")
        return request.app.state.load_intraday(symbol, tf, start, end)
    except RuntimeError as e:  # thiếu key Alpaca khi cần tải
        raise HTTPException(503, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


def _within(times: List[int], start_unix: int, before: Optional[int]) -> List[bool]:
    return [t >= start_unix and (before is None or t < before) for t in times]


@router.get("/api/symbols")
def symbols(request: Request, asset: str = "stock"):
    """Danh sách mã gợi ý: mã mặc định + universe + mã đã có cache ngày. Crypto: đúng 5 mã whitelist."""
    if check_asset(asset) == "crypto":
        return {"symbols": list(CRYPTO_UNIVERSE), "default": CRYPTO_UNIVERSE[0], "timeframes": list(TIMEFRAMES),
                "asset": "crypto"}
    cache_dir = getattr(request.app.state, "bars_dir", None) or BARS_DIR
    cached = [p.stem for p in cache_dir.glob("*.parquet")] if cache_dir.exists() else []
    names = sorted(set(DEFAULT_SYMBOLS) | set(UNIVERSE_50) | {BENCHMARK} | set(cached))
    return {"symbols": names, "default": DEFAULT_SYMBOLS[0], "timeframes": list(TIMEFRAMES), "asset": "stock"}


@router.get("/api/bars")
def bars(request: Request, symbol: str, tf: str = "1D", start: Optional[str] = None,
         end: Optional[str] = None, before: Optional[str] = Query(None, pattern=r"^\d+$"),
         asset: str = "stock"):
    """Nến + volume cho lightweight-charts. time = unix giây UTC (xem timeutil)."""
    symbol = check_symbol(symbol, asset)
    start_ts, end_ts, before_unix = resolve_range(tf, start, end, before, asset)
    df = load_df(request, symbol, tf, start_ts, end_ts, asset)
    times = index_to_unix(df.index, tf, asset)
    keep = [i for i, ok in enumerate(_within(times, 0, before_unix)) if ok]
    out_bars, out_vol = [], []
    for i in keep:
        r = df.iloc[i]
        out_bars.append({"time": times[i], "open": r.open, "high": r.high, "low": r.low, "close": r.close})
        out_vol.append({"time": times[i], "value": r.volume,
                        "color": UP_COLOR if r.close >= r.open else DOWN_COLOR})
    return {"symbol": symbol, "tf": tf, "asset": asset, "bars": out_bars, "volume": out_vol,
            "oldest": out_bars[0]["time"] if out_bars else None}


@router.get("/api/indicators")
def indicators(request: Request, symbol: str, tf: str = "1D", start: Optional[str] = None,
               end: Optional[str] = None, before: Optional[str] = Query(None, pattern=r"^\d+$"),
               asset: str = "stock"):
    """MA10/30/200 + RSI14 (tính trên đoạn có nạp thêm warmup, rồi cắt về đúng khoảng yêu cầu)."""
    symbol = check_symbol(symbol, asset)
    start_ts, end_ts, before_unix = resolve_range(tf, start, end, before, asset)
    df = load_df(request, symbol, tf, start_ts - pd.Timedelta(days=WARMUP_DAYS[tf]), end_ts, asset)
    cols: Dict[str, pd.Series] = {f"ma{w}": df["close"].rolling(w).mean() for w in (10, 30, 200)}
    cols["rsi14"] = rsi_series(df, 14).replace([float("inf"), float("-inf")], float("nan"))
    times = index_to_unix(df.index, tf, asset)
    first = index_to_unix(pd.DatetimeIndex([start_ts]), tf, asset)[0]
    ok = _within(times, first, before_unix)
    return {name: [{"time": t, "value": float(v)} for t, v, k in zip(times, s, ok) if k and pd.notna(v)]
            for name, s in cols.items()}
