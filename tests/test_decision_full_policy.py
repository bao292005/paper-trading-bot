"""Test full_policy trên dữ liệu tổng hợp nhỏ (offline, không đọc cache)."""

import numpy as np
import pandas as pd
import pytest

from paper_bot.backtest.engine import run_backtest
from paper_bot.backtest.walkforward import slice_bars
from paper_bot.decision.full_policy import make_full_policy_factory
from paper_bot.risk import legacy_position_size

DAYS = pd.bdate_range("2019-01-01", "2021-06-30")
TRAIN_END = pd.Timestamp("2020-06-30")
TEST_START, TEST_END = pd.Timestamp("2020-07-01"), pd.Timestamp("2021-06-30")
CUT = pd.Timestamp("2021-01-04")  # dữ liệu từ ngày này trở đi bị sửa trong test rò rỉ


def fake_bars(seed, n=len(DAYS), vol=0.02):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, vol, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.01,
                         "low": np.minimum(open_, close) * 0.99, "close": close,
                         "volume": 1e6}, index=DAYS[:n])


def make_world():
    bars = {s: fake_bars(i) for i, s in enumerate(["AAA", "BBB", "CCC", "DDD"])}
    spy = fake_bars(99, vol=0.01)
    rng = np.random.default_rng(7)
    idx = pd.MultiIndex.from_product([list(bars), DAYS], names=["symbol", "date"])
    q50 = rng.normal(0, 0.01, len(idx))
    fc = pd.DataFrame({"q10": q50 - 0.03, "q50": q50, "q90": q50 + 0.03}, index=idx)
    return bars, spy, fc


def run(bars, spy, fc, flags=None, min_score=0.0):
    factory = make_full_policy_factory(flags or {}, forecasts=fc, spy_bars=spy, min_score=min_score)
    policy = factory(slice_bars(bars, DAYS[0], TRAIN_END))
    log = []

    def rec(asof, view, pf):
        out = policy(asof, view, pf)
        log.append((asof, [(o.symbol, o.side, o.qty, o.stop_loss, o.take_profit) for o in out]))
        return out

    res = run_backtest(bars, TEST_START, TEST_END, rec)
    return log, res


def test_flag_la_bi_tu_choi():
    with pytest.raises(ValueError):
        make_full_policy_factory({"use_regim": False})


def test_khong_ro_ri_tuong_lai():
    bars, spy, fc = make_world()
    log1, _ = run(bars, spy, fc)

    # Sửa mọi dữ liệu từ CUT trở đi: giá, SPY, forecast
    bars2 = {s: df.copy() for s, df in bars.items()}
    for df in bars2.values():
        df.loc[df.index >= CUT, ["open", "high", "low", "close"]] *= 1.7
    spy2 = spy.copy()
    spy2.loc[spy2.index >= CUT, ["open", "high", "low", "close"]] *= 0.5
    fc2 = fc.copy()
    fc2.loc[fc2.index.get_level_values("date") >= CUT, ["q10", "q50", "q90"]] -= 0.5
    log2, _ = run(bars2, spy2, fc2)

    before1 = [x for x in log1 if x[0] < CUT]
    before2 = [x for x in log2 if x[0] < CUT]
    assert sum(len(o) for _, o in before1) > 0  # có lệnh thật để so sánh
    assert before1 == before2
    assert log1 != log2  # dữ liệu sửa có tác dụng sau CUT


def test_tat_bracket_va_atr_sizing():
    bars, spy, fc = make_world()
    log, res = run(bars, spy, fc, flags={"use_bracket": False, "use_atr_sizing": False})
    buys = [o for _, orders in log for o in orders if o[1] == "buy"]
    assert buys
    assert all(sl is None and tp is None for *_, sl, tp in buys)
    # Bài gốc: min(2% tiền mặt, 1000$)/giá -> mỗi lệnh mua không quá ~1000$
    first_day, first = next((d, o) for d, o in log if any(x[1] == "buy" for x in o))
    sym, _, qty, *_ = next(x for x in first if x[1] == "buy")
    price = float(bars[sym].at[first_day, "close"])
    assert qty == legacy_position_size(100_000.0, price)
    assert not any(t.exit_reason in ("stop_loss", "take_profit") for t in res.trades)


def test_full_co_bracket():
    bars, spy, fc = make_world()
    log, _ = run(bars, spy, fc)
    buys = [o for _, orders in log for o in orders if o[1] == "buy"]
    assert buys and all(sl is not None and tp is not None and sl < tp for *_, sl, tp in buys)


class LowCalibrator:
    """Gán prob thấp cho mọi tín hiệu -> mọi BUY phải bị ngưỡng chặn, SELL thì không."""
    def knows(self, sig):
        return True

    def predict_prob(self, sig, state):
        return 0.1


def _policy_with(calibrator, bars, spy):
    train = slice_bars(bars, DAYS[0], TRAIN_END)
    train_end = max(df.index[-1] for df in train.values())
    cache = {(train_end, False, "isotonic"): calibrator}
    factory = make_full_policy_factory({"use_forecast": False, "use_regime": False},
                                       spy_bars=spy, calib_cache=cache)
    return factory(train)


def test_nguong_chi_chan_buy_sell_thoat_luon_chay():
    from paper_bot.backtest.engine import Portfolio, Position, View
    bars, spy, _ = make_world()
    policy = _policy_with(LowCalibrator(), bars, spy)
    pf = Portfolio(cash=100_000.0, equity=200_000.0,
                   positions={s: Position(10, 100.0, DAYS[0]) for s in bars})
    out = [o for d in DAYS[(DAYS >= TEST_START)] for o in policy(d, View(bars, d), pf)]
    assert any(o.side == "sell" for o in out)  # prob 0.1 < 0.5 nhưng vẫn thoát
    pf.positions = {}
    out = [o for d in DAYS[(DAYS >= TEST_START)] for o in policy(d, View(bars, d), pf)]
    assert out == []  # không có vị thế: SELL bị bỏ, BUY bị ngưỡng chặn


def test_calib_cache_dung_lai():
    bars, spy, _ = make_world()
    cache = {}
    train = slice_bars(bars, DAYS[0], TRAIN_END)
    for flags in ({"use_forecast": False}, {"use_forecast": False, "use_bracket": False}):
        make_full_policy_factory(flags, spy_bars=spy, calib_cache=cache)(train)
    assert len(cache) == 1


def test_tat_forecast_khong_doc_bang():
    bars, spy, _ = make_world()
    # use_forecast=False: bảng forecast không được dùng -> truyền bảng "độc" cũng không đổi kết quả
    bad = pd.DataFrame({"q10": [-9.0], "q50": [-9.0], "q90": [9.0]},
                       index=pd.MultiIndex.from_tuples([("AAA", TEST_START)], names=["symbol", "date"]))
    log_a, _ = run(bars, spy, None, flags={"use_forecast": False})
    log_b, _ = run(bars, spy, bad, flags={"use_forecast": False})
    assert log_a == log_b
