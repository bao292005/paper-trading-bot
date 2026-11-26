"""AppTest chạy từng trang UI: không exception. Hoàn toàn offline, dữ liệu tổng hợp.
Chốt an toàn: mọi subprocess và TradingClient đều bị chặn (gọi tới là test fail)."""

import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from paper_bot.contracts import Signal
from paper_bot.ui import core

UI = Path(__file__).resolve().parents[1] / "src" / "paper_bot" / "ui"
ASOF = "2024-06-28"


def make_bars(n=500, seed=0):
    idx = pd.bdate_range(end=ASOF, periods=n, name="date")
    close = 100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, 0.01, n)))
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": 1e6}, index=idx)


class StubPipe:
    """Pipeline giả luôn ra tín hiệu BUY để chạy tới tận khối P3."""
    last_signals, last_note = [], ""

    def decide(self, state):
        s = Signal(state.symbol, "BUY", float(state.bars["close"].iloc[-1]), datetime(2024, 6, 28),
                   "lý do thử", 0.8, strategy="stub", prob=0.7)
        self.last_signals, self.last_note = [s], "một tín hiệu BUY"
        return s


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    st.cache_data.clear()
    st.cache_resource.clear()

    def boom(*a, **k):
        raise AssertionError("UI test không được chạy subprocess / gọi broker")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr("alpaca.trading.client.TradingClient.__init__", boom)

    spy = make_bars(seed=99)
    monkeypatch.setattr(core, "load_market_data",
                        lambda symbols, bars_dir=None: ({s: make_bars(seed=i) for i, s in enumerate(symbols)},
                                                        spy, []))
    table = pd.DataFrame([{"symbol": "AAPL", "date": pd.Timestamp(ASOF), "q10": -0.02, "q50": 0.003, "q90": 0.03}])
    monkeypatch.setattr(core, "load_forecast_table", lambda: table.set_index(["symbol", "date"]))
    monkeypatch.setattr(core, "get_calibrator", lambda symbols, flags, **kw: (None, "calibrator giả"))
    monkeypatch.setattr(core, "fetch_news", lambda symbols, asof, source=None: ([], None))


def run_page(name, timeout=60):
    at = AppTest.from_file(str(UI / name), default_timeout=timeout).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def sidebar_checkbox(at, label_start):
    return next(c for c in at.sidebar.checkbox if c.label.startswith(label_start))


def test_trang_chu():
    at = run_page("app.py")
    assert at.title[0].value == "Paper Trading Bot"
    assert any("Trang này cho biết gì" in c.value for c in at.caption)


def test_pipeline_mac_dinh():
    at = run_page("pages/1_Pipeline.py")
    assert any("Trang này cho biết gì" in c.value for c in at.caption)
    assert len(at.tabs) == 3  # AAPL NVDA JPM
    assert [s.value for s in at.subheader][:3] == ["P1 — Dữ liệu & dự báo", "P2 — Quyết định", "P3 — Lệnh dự kiến"]
    assert not at.error


def test_pipeline_ra_lenh_du_kien(monkeypatch):
    monkeypatch.setattr(core, "build_pipeline", lambda flags, calibrator=None: StubPipe())
    at = run_page("pages/1_Pipeline.py")
    metrics = [m.label for m in at.metric]
    assert "Lệnh" in metrics and "Stop-loss" in metrics and "Take-profit" in metrics
    assert any("MÔ PHỎNG" in c.value for c in at.caption)


def test_pipeline_tat_het_co_va_vi_the_gia_dinh(monkeypatch):
    monkeypatch.setattr(core, "build_pipeline", lambda flags, calibrator=None: StubPipe())
    at = run_page("pages/1_Pipeline.py")
    for key in ("use_regime", "use_calibration", "use_forecast", "use_atr_sizing", "use_bracket", "use_corr_limit"):
        at.sidebar.checkbox(key=key).uncheck()
    at.sidebar.number_input(key="pos_NVDA").set_value(10)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Không ra lệnh" in w.value for w in at.warning)  # NVDA đã có vị thế -> không mua trùng


def test_pipeline_bat_tin_va_chronos_khong_goi_that():
    at = run_page("pages/1_Pipeline.py")
    sidebar_checkbox(at, "Lấy tin").check()
    at.run()  # fetch_news đã bị thay bằng bản giả
    assert not at.exception, [e.value for e in at.exception]


def test_pipeline_quet_n_phien(monkeypatch):
    monkeypatch.setattr(core, "build_pipeline", lambda flags, calibrator=None: StubPipe())
    at = run_page("pages/1_Pipeline.py")
    at.sidebar.radio[0].set_value("Quét N phiên").run()
    assert not at.exception and any("Bấm 'Quét'" in i.value for i in at.info)
    at.button[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.dataframe) == 1 and len(at.dataframe[0].value) == 3 * 60  # StubPipe nói BUY mọi ngày


def test_pipeline_quet_khong_co_tin_hieu():
    at = run_page("pages/1_Pipeline.py")
    at.sidebar.radio[0].set_value("Quét N phiên").run()
    at.button[0].click().run()
    assert not at.exception, [e.value for e in at.exception]


def test_pipeline_khong_chon_ma():
    at = run_page("pages/1_Pipeline.py")
    at.sidebar.multiselect[0].set_value([]).run()
    assert not at.exception and any("ít nhất 1 mã" in i.value for i in at.info)


def test_pipeline_thieu_cache_spy(monkeypatch):
    monkeypatch.setattr(core, "load_market_data",
                        lambda symbols, bars_dir=None: ({}, pd.DataFrame(), list(symbols)))
    at = run_page("pages/1_Pipeline.py")
    assert any("SPY" in e.value for e in at.error)


def test_pipeline_ngay_holdout_co_canh_bao(monkeypatch):
    idx = pd.bdate_range(end="2025-03-14", periods=500, name="date")
    close = pd.Series(np.linspace(100, 120, 500), index=idx)
    bars = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": 1e6})
    monkeypatch.setattr(core, "load_market_data",
                        lambda symbols, bars_dir=None: ({s: bars for s in symbols}, bars, []))
    at = run_page("pages/1_Pipeline.py")
    assert any("HOLDOUT" in w.value for w in at.warning)
