"""Test forecast. Naive chạy offline; Chronos/Kronos bị skip nếu không tải được model."""

import numpy as np
import pandas as pd
import pytest

from paper_bot.data.forecast import ChronosForecaster, KronosForecaster, NaiveForecaster


def make_bars(n=400, seed=0, vol=0.01):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, vol, n)))
    idx = pd.bdate_range("2022-01-03", periods=n, name="date")
    return pd.DataFrame({"open": close * 0.999, "high": close * 1.005, "low": close * 0.995,
                         "close": close, "volume": rng.uniform(1e6, 2e6, n)}, index=idx)


def test_naive_quantile_dung_thu_tu_va_so_voi_tinh_tay():
    bars = make_bars()
    asof = bars.index[-1]
    fc = NaiveForecaster().predict_many({"AAA": bars}, asof, horizon=5)["AAA"]
    q = fc.quantiles
    assert fc.model == "naive" and fc.horizon_days == 5
    assert q[0.1] <= q[0.5] <= q[0.9] and fc.width > 0
    # tính tay: lợi suất log 5 ngày của 252 ngày cuối
    r = np.log(bars["close"]).diff(5).dropna().iloc[-252:]
    assert np.isclose(q[0.1], np.quantile(r, 0.1)) and np.isclose(q[0.9], np.quantile(r, 0.9))


def test_naive_khong_ro_ri_tuong_lai():
    bars = make_bars()
    asof = bars.index[300]
    base = NaiveForecaster().predict_many({"AAA": bars}, asof)["AAA"].quantiles
    broken = bars.copy()
    broken.loc[broken.index > asof, "close"] = 1.0  # phá dữ liệu sau asof
    assert NaiveForecaster().predict_many({"AAA": broken}, asof)["AAA"].quantiles == base


@pytest.fixture(scope="module")
def chronos():
    try:
        return ChronosForecaster()
    except Exception as e:  # không có mạng / chưa tải model
        pytest.skip(f"Không tải được Chronos: {e}")


@pytest.fixture(scope="module")
def kronos():
    try:
        return KronosForecaster(n_samples=4)  # ít mẫu cho test nhanh
    except Exception as e:
        pytest.skip(f"Không tải được Kronos: {e}")


@pytest.mark.parametrize("fixture_name", ["chronos", "kronos"])
def test_model_that_hinh_dang_va_khong_ro_ri(fixture_name, request):
    model = request.getfixturevalue(fixture_name)
    bars = make_bars(300)
    asof = bars.index[250]
    fc = model.predict_many({"AAA": bars, "BBB": make_bars(300, seed=1)}, asof, horizon=5)
    assert set(fc) == {"AAA", "BBB"}
    q = fc["AAA"].quantiles
    assert q[0.1] <= q[0.5] <= q[0.9] and np.isfinite(list(q.values())).all()
    # phá dữ liệu sau asof: dự báo không được đổi
    broken = bars.copy()
    broken.loc[broken.index > asof, ["open", "high", "low", "close"]] = 1.0
    fc2 = model.predict_many({"AAA": broken, "BBB": make_bars(300, seed=1)}, asof, horizon=5)
    assert fc2["AAA"].quantiles == pytest.approx(q)


def test_load_va_get_forecast_tinh_san(tmp_path):
    from paper_bot.data.forecast import get_forecast, load_forecasts
    path = tmp_path / "f.parquet"
    pd.DataFrame({"date": pd.to_datetime(["2024-01-02", "2024-01-03"]), "symbol": ["AAA", "AAA"],
                  "q10": [-0.02, -0.03], "q50": [0.0, 0.01], "q90": [0.03, 0.02]}).to_parquet(path)
    table = load_forecasts(path)
    fc = get_forecast(table, "AAA", "2024-01-03")
    assert fc.quantiles == {0.1: -0.03, 0.5: 0.01, 0.9: 0.02} and fc.horizon_days == 5
    assert get_forecast(table, "AAA", "2024-01-04") is None  # không có thì None, không lấy ngày khác
    assert get_forecast(table, "BBB", "2024-01-03") is None
    assert load_forecasts(tmp_path / "khong_co.parquet").empty


def test_naive_thieu_du_lieu_thi_bo_ma():
    out = NaiveForecaster().predict_many({"AAA": make_bars(30), "BBB": make_bars(400)},
                                         make_bars(400).index[-1])
    assert set(out) == {"BBB"}
