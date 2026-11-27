"""MA_noSLTP vs MA_RR1: cùng điểm vào (MA crossover), khác cách thoát lệnh."""

import numpy as np
import pandas as pd
import pytest

from paper_bot.backtest.tester.run import run_tester
from paper_bot.backtest.tester.strategies import make_factory


def _bars(closes):
    idx = pd.bdate_range("2021-01-01", periods=len(closes))
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6},
                        index=idx.rename("date"))


def _series():
    # giảm -> tăng mạnh (cắt lên) -> giảm (cắt xuống)
    down = list(np.linspace(120, 100, 60))
    up = list(np.linspace(100, 140, 60))
    down2 = list(np.linspace(140, 110, 60))
    return down + up + down2


def _run(strategy, **risk):
    bars = {"AAA": _bars(_series())}
    cfg = {"symbols": ["AAA"], "strategy": strategy, "from": "2021-03-01", "to": "2021-09-15",
           "deposit": 100_000, "cost_bps": 0, "risk": risk}
    return run_tester(cfg, bars=bars, forecasts=pd.DataFrame())


def test_cung_diem_vao_khac_cach_thoat():
    a, b = _run("MA_noSLTP"), _run("MA_RR1")
    ta, tb = a["trades"], b["trades"]
    assert ta and tb
    assert ta[0]["entry_date"] == tb[0]["entry_date"]  # cùng 1 chỉ báo -> cùng ngày vào lệnh đầu
    assert ta[0]["exit_reason"] in ("signal", "end")
    assert tb[0]["exit_reason"] in ("stop_loss", "take_profit", "end")


def test_rr1_sl_tp_doi_xung():
    pol = make_factory("MA_RR1", ["AAA"], risk={"atr_stop_mult": 2.0})(None)
    bars = _bars(_series())

    class View:
        def bars(self, s):
            return cut

    class Pf:
        cash, positions = 100_000.0, {}

    for i in range(40, len(bars)):
        cut = bars.iloc[: i + 1]
        orders = pol(cut.index[-1], View(), Pf())
        if orders:
            o = orders[0]
            price = float(cut["close"].iloc[-1])
            assert o.side == "buy" and o.stop_loss < price < o.take_profit
            assert abs((o.take_profit - price) - (price - o.stop_loss)) < 1e-9  # RR 1:1
            break
    else:
        pytest.fail("không có lệnh vào")


def test_risk_la_bi_tu_choi():
    with pytest.raises(ValueError):
        make_factory("MA_noSLTP", ["AAA"], risk={"take_profit_mult": 3})
