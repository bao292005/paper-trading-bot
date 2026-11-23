"""Test offline cho cache nến."""

import numpy as np
import pandas as pd

from paper_bot.data.cache import COLUMNS, load_bars, normalize_bars, save_bars


def make_alpaca_like(n=10):
    """Giả dạng df Alpaca: MultiIndex (symbol, timestamp UTC 04:00)."""
    ts = pd.date_range("2024-01-02 05:00", periods=n, freq="D", tz="UTC")
    df = pd.DataFrame({c: np.arange(n, dtype=float) + 1 for c in COLUMNS}, index=ts)
    df["trade_count"] = 5  # cột thừa phải bị bỏ
    df.index = pd.MultiIndex.from_product([["AAA"], ts], names=["symbol", "timestamp"])
    return df


def test_normalize_dinh_dang_chung():
    out = normalize_bars(make_alpaca_like())
    assert list(out.columns) == COLUMNS
    assert out.index.name == "date" and out.index.tz is None
    assert out.index[0] == pd.Timestamp("2024-01-02")
    assert (out.dtypes == float).all()


def test_save_load_va_cat_theo_ngay(tmp_path):
    save_bars("aaa", make_alpaca_like(), bars_dir=tmp_path)
    full = load_bars("AAA", bars_dir=tmp_path)
    assert len(full) == 10
    cut = load_bars("AAA", start="2024-01-04", end="2024-01-06", bars_dir=tmp_path)
    assert list(cut.index) == list(pd.date_range("2024-01-04", "2024-01-06"))


def test_load_thieu_file_tra_rong(tmp_path):
    out = load_bars("ZZZ", bars_dir=tmp_path)
    assert out.empty and list(out.columns) == COLUMNS


def test_normalize_rong():
    assert normalize_bars(pd.DataFrame()).empty
