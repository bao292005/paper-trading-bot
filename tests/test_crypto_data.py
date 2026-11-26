"""Test offline cho phần dữ liệu crypto: profile, cache, nến ngày, intraday (client giả)."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from paper_bot.config import CRYPTO_UNIVERSE, TradingConfig, asset_profile
from paper_bot.data.cache import (BARS_DIR, COLUMNS, CRYPTO_BARS_DIR, bars_path, load_bars,
                                  load_crypto_bars, missing_days, normalize_bars, save_bars)
from paper_bot.data.intraday import load_intraday, resample_bars
from paper_bot.data.sources import fetch_crypto_daily_bars


# ---------- profile ----------
def test_profile_dung_prereg():
    c = asset_profile("crypto")
    assert c["universe"] == CRYPTO_UNIVERSE and c["benchmark"] == "BTC/USD"
    assert (c["periods_per_year"], c["cost_bps"], c["fractional"], c["qty_decimals"]) == (365, 25.0, True, 6)
    assert (c["max_pair_corr"], c["max_positions"], c["max_position_pct"]) == (0.9, 5, 0.2)
    assert (c["dev_start"], c["dev_end"], c["holdout_start"]) == ("2022-01-01", "2024-12-31", "2025-01-01")
    assert c["market_hours"] is False and c["train_years"] is None


def test_profile_stock_giu_nhu_cu_va_ban_sao():
    s = asset_profile("stock")
    assert (s["periods_per_year"], s["cost_bps"], s["fractional"]) == (252, 10.0, False)
    assert (s["max_pair_corr"], s["max_positions"], s["max_position_pct"]) == (0.8, 10, 0.10)
    assert s["benchmark"] == "SPY" and len(s["universe"]) == 50
    s["universe"].append("X")  # sửa bản sao không ảnh hưởng lần gọi sau
    assert len(asset_profile("stock")["universe"]) == 50
    with pytest.raises(ValueError):
        asset_profile("forex")


def test_tradingconfig_asset_mac_dinh_stock():
    cfg = TradingConfig(api_key="k", api_secret="s")
    assert cfg.asset == "stock" and cfg.profile()["benchmark"] == "SPY"
    assert TradingConfig(api_key="k", api_secret="s", asset="crypto").profile()["periods_per_year"] == 365


# ---------- cache nến ngày ----------
def test_duong_dan_cache():
    assert bars_path("BTC/USD", asset="crypto") == CRYPTO_BARS_DIR / "BTC-USD.parquet"
    assert bars_path("aapl") == BARS_DIR / "AAPL.parquet"  # cổ phiếu không đổi


def test_normalize_crypto_lay_ngay_utc():
    ts = pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC")  # nến chốt 00:00 UTC
    df = pd.DataFrame({c: [1.0, 2.0, 3.0] for c in COLUMNS}, index=ts)
    out = normalize_bars(df, tz="UTC")
    assert list(out.index) == list(pd.date_range("2024-01-01", periods=3, name="date"))
    assert out.index.tz is None
    # Cổ phiếu (mặc định NY) sẽ lùi 1 ngày -> lý do crypto phải dùng UTC
    assert normalize_bars(df).index[0] == pd.Timestamp("2023-12-31")


def test_save_load_crypto_khong_noi_suy(tmp_path):
    idx = pd.DatetimeIndex(["2024-01-01", "2024-01-02", "2024-01-05"], name="date")  # thiếu 03, 04
    df = pd.DataFrame({c: [1.0, 2.0, 3.0] for c in COLUMNS}, index=idx)
    path = save_bars("ETH/USD", df, bars_dir=tmp_path, asset="crypto")
    assert path.name == "ETH-USD.parquet"
    out = load_crypto_bars("ETH/USD", end="2024-01-04", bars_dir=tmp_path)
    assert len(out) == 2
    full = load_bars("ETH/USD", bars_dir=tmp_path, asset="crypto")
    assert len(full) == 3 and missing_days(full) == 2
    assert missing_days(normalize_bars(None)) == 0


class FakeCryptoClient:
    """Giả CryptoHistoricalDataClient: trả df MultiIndex (symbol, timestamp UTC)."""

    def __init__(self, raw: dict):
        self.raw, self.calls = raw, []  # raw: {symbol: df index UTC}

    def get_crypto_bars(self, request):
        self.calls.append(request)
        syms = request.symbol_or_symbols
        syms = [syms] if isinstance(syms, str) else syms
        start = pd.Timestamp(request.start).tz_localize("UTC") if request.start.tzinfo is None \
            else pd.Timestamp(request.start)
        end = pd.Timestamp(request.end).tz_localize("UTC") if request.end.tzinfo is None \
            else pd.Timestamp(request.end)
        parts = []
        for s in syms:
            df = self.raw.get(s)
            if df is None:
                continue
            df = df[(df.index >= start) & (df.index <= end)].copy()
            df.index = pd.MultiIndex.from_product([[s], df.index], names=["symbol", "timestamp"])
            parts.append(df)
        return SimpleNamespace(df=pd.concat(parts) if parts else pd.DataFrame())


def daily_raw(start, n):
    ts = pd.date_range(start, periods=n, freq="D", tz="UTC")
    v = np.arange(n, dtype=float) + 1
    return pd.DataFrame({"open": v, "high": v, "low": v, "close": v, "volume": v}, index=ts)


def test_fetch_crypto_daily_bars_ma_thieu_tra_rong():
    client = FakeCryptoClient({"BTC/USD": daily_raw("2024-01-01", 5)})
    out = fetch_crypto_daily_bars(client, ["BTC/USD", "AVAX/USD"], pd.Timestamp("2024-01-01"),
                                  pd.Timestamp("2024-01-10"))
    assert len(out["BTC/USD"]) == 5 and out["BTC/USD"].index[0] == pd.Timestamp("2024-01-01")
    assert out["AVAX/USD"].empty


# ---------- intraday crypto ----------
def minute_raw(start, n):
    ts = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    i = np.arange(n, dtype=float)
    return pd.DataFrame({"open": i, "high": i + 0.5, "low": i - 0.5, "close": i + 0.1,
                         "volume": np.ones(n)}, index=ts)


def test_resample_crypto_moc_gio_tron():
    idx = pd.date_range("2024-03-09 22:00", periods=240, freq="1min", name="time")  # thứ 7, qua nửa đêm
    df = pd.DataFrame({c: np.ones(240) for c in COLUMNS}, index=idx)
    h = resample_bars(df, "1h", asset="crypto")
    assert list(h.index.strftime("%H:%M")) == ["22:00", "23:00", "00:00", "01:00"]
    assert (h.volume == 60).all()


def test_load_intraday_crypto_24_7_va_cache(tmp_path):
    raw = minute_raw("2024-03-09 00:00", 2 * 24 * 60)  # thứ 7 + chủ nhật, cả ngày
    client = FakeCryptoClient({"BTC/USD": raw})
    kw = dict(client=client, minute_dir=tmp_path, now="2024-06-10 12:00")
    df = load_intraday("btc/usd", "1m", "2024-03-09", "2024-03-10", **kw)
    assert len(df) == 2 * 24 * 60 and df.index.tz is None  # không lọc phiên, không bỏ cuối tuần
    assert df.index[0] == pd.Timestamp("2024-03-09 00:00")  # giờ UTC
    assert (tmp_path / "BTC-USD" / "2024-03.parquet").exists()
    n = len(client.calls)
    h = load_intraday("BTC/USD", "1h", "2024-03-09", "2024-03-10", **kw)
    assert len(client.calls) == n and len(h) == 48  # tháng đã qua: không tải lại


def test_load_intraday_crypto_thang_hien_tai(tmp_path):
    client = FakeCryptoClient({"ETH/USD": minute_raw("2024-03-09 00:00", 24 * 60)})
    kw = dict(client=client, minute_dir=tmp_path, asset="crypto")
    a = load_intraday("ETH/USD", "1m", "2024-03-09", "2024-03-09", now="2024-03-09 10:00", **kw)
    assert a.index[-1] == pd.Timestamp("2024-03-09 10:00")
    load_intraday("ETH/USD", "1m", "2024-03-09", "2024-03-09", now="2024-03-09 10:00:30", **kw)
    assert len(client.calls) == 1  # đã đủ tới phút hiện tại -> không gọi lại
    b = load_intraday("ETH/USD", "1m", "2024-03-09", "2024-03-09", now="2024-03-09 12:00", **kw)
    assert b.index[-1] == pd.Timestamp("2024-03-09 12:00") and b.index.is_unique


def test_load_intraday_crypto_1d(tmp_path):
    idx = pd.date_range("2024-01-01", periods=10, name="date")
    save_bars("SOL/USD", pd.DataFrame({c: np.ones(10) for c in COLUMNS}, index=idx),
              bars_dir=tmp_path, asset="crypto")
    out = load_intraday("SOL/USD", "1D", "2024-01-06", "2024-01-07", bars_dir=tmp_path)
    assert len(out) == 2  # gồm cả thứ 7, chủ nhật
