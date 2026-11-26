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
    monkeypatch.setattr(core, "log_ui_event", boom)  # không ghi DB thật

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


# ---------------------------- trang 2 (Đánh giá) và trang 3 (Giám sát) ----------------------------

@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    """DB tạm: 2 run backtest có equity; live-dry có signal/event/equity. Thay core.open_db trỏ vào DB này."""
    from paper_bot.storage import Store

    path = tmp_path / "t.db"
    for rid, mult in (("bt-abl-FULL", 1.0), ("bt-abl-B0", 1.1)):
        s = Store(path=path, run_id=rid)
        for i, d in enumerate(pd.bdate_range("2024-01-01", periods=5)):
            s.log_equity(d.to_pydatetime(), 100.0 + i * mult - (3 if i == 3 else 0), 50.0, 50.0)
    d = Store(path=path, run_id="live-dry")
    d.log_equity(datetime(2024, 1, 2), 100.0, 100.0, 0.0)
    d.log_event("kill_switch", "ngắt thử", level="WARNING")
    d.log_signal(Signal("AAPL", "BUY", 100.0, datetime(2024, 1, 2), "lý do", 0.6, strategy="s", prob=0.5), regime="bull_calm")
    from paper_bot.dashboard import connect_readonly

    monkeypatch.setattr(core, "open_db", lambda: connect_readonly(path))
    return path


def fake_account():
    return {"equity": 100500.0, "cash": 90000.0,
            "positions": pd.DataFrame([{"symbol": "AAPL", "qty": 3.0, "giá vốn": 190.0, "giá trị": 600.0, "lãi/lỗ": 30.0}]),
            "orders": pd.DataFrame(columns=["symbol", "side", "qty", "type", "status", "id"])}, None


def test_danh_gia_co_du_lieu(fake_db):
    at = run_page("pages/2_Danh_gia.py")
    assert any("Trang này cho biết gì" in c.value for c in at.caption)
    assert any("HOLDOUT".lower() in w.value.lower() or "holdout" in w.value.lower() for w in at.warning)
    assert len(at.tabs) == 4 and len(at.multiselect) == 1
    assert at.multiselect[0].value == ["bt-abl-FULL", "bt-abl-B0"]


def test_danh_gia_bo_bot_run(fake_db):
    at = run_page("pages/2_Danh_gia.py")
    at.multiselect[0].set_value([]).run()
    assert not at.exception, [e.value for e in at.exception]


def test_danh_gia_chua_co_db(monkeypatch):
    monkeypatch.setattr(core, "open_db", lambda path=None: None)
    at = run_page("pages/2_Danh_gia.py")
    assert any("Chưa có run backtest" in i.value for i in at.info)


def test_giam_sat_day_du(fake_db, monkeypatch):
    monkeypatch.setattr(core, "fetch_account", fake_account)
    monkeypatch.setattr(core, "read_live_state", lambda run_id, path=None: {"halted": True, "day": "2026-11-25"})
    at = run_page("pages/3_Giam_sat.py")
    assert any("Trang này cho biết gì" in c.value for c in at.caption)
    assert any("NGẮT" in e.value for e in at.error)  # kill switch đang ngắt
    assert [m.label for m in at.metric] == ["Equity", "Tiền mặt", "Số vị thế"]
    assert len(at.tabs) == 4


def test_giam_sat_loc_su_kien_va_lam_moi(fake_db, monkeypatch):
    monkeypatch.setattr(core, "fetch_account", fake_account)
    at = run_page("pages/3_Giam_sat.py")
    at.multiselect[0].set_value(["WARNING"]).run()
    assert not at.exception, [e.value for e in at.exception]
    at.button[0].click().run()  # nút Làm mới chỉ đọc lại
    assert not at.exception, [e.value for e in at.exception]


def test_giam_sat_khong_co_key_van_chay(fake_db, monkeypatch):
    monkeypatch.setattr(core, "fetch_account", lambda: (None, "Không lấy được tài khoản Alpaca: thiếu key"))
    at = run_page("pages/3_Giam_sat.py")
    assert any("thiếu key" in w.value for w in at.warning)


def test_giam_sat_run_live_paper_chua_co_du_lieu(fake_db, monkeypatch):
    monkeypatch.setattr(core, "fetch_account", fake_account)
    at = run_page("pages/3_Giam_sat.py")
    at.sidebar.selectbox[0].set_value("live-paper").run()
    assert not at.exception and any("chưa có dữ liệu cho live-paper" in i.value.lower() for i in at.info)


def test_giam_sat_chua_co_db(monkeypatch):
    monkeypatch.setattr(core, "fetch_account", fake_account)
    monkeypatch.setattr(core, "open_db", lambda path=None: None)
    at = run_page("pages/3_Giam_sat.py")
    assert any("Chưa có data/trading.db" in i.value for i in at.info)


def test_danh_gia_nhan_tieng_viet(fake_db):
    at = run_page("pages/2_Danh_gia.py")
    assert any("FULL (bản đầy đủ" in o for o in at.multiselect[0].options)


# ---------------------------- trang 4 (Điều khiển): an toàn ----------------------------

class Ctl:
    """Ghi lại mọi lệnh subprocess + event; `order` cho biết event được ghi TRƯỚC khi chạy lệnh."""

    def __init__(self):
        self.calls, self.events, self.order = [], [], []
        self.result = (0, "stdout giả", "stderr giả")
        self.raise_timeout = False


@pytest.fixture
def ctl(monkeypatch):
    c = Ctl()

    def run(cmd, **kw):
        c.calls.append(cmd)
        c.order.append("run")
        assert kw.get("shell") is False and kw.get("timeout") == 600 and isinstance(cmd, list)
        if c.raise_timeout:
            raise subprocess.TimeoutExpired(cmd, 600)
        return subprocess.CompletedProcess(cmd, c.result[0], c.result[1], c.result[2])

    def log(run_id, kind, message, db_path=None):
        c.events.append((run_id, kind, message))
        c.order.append("event")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(core, "log_ui_event", log)
    return c


def real_calls(c):
    return [x for x in c.calls if core.is_real_order_command(x)]


def test_dieu_khien_trang_mo(ctl):
    at = run_page("pages/4_Dieu_khien.py")
    assert any("Trang này cho biết gì" in x.value for x in at.caption)
    assert at.button(key="run_real").disabled and at.button(key="run_ks").disabled
    assert not at.button(key="run_dry").disabled
    assert not any("loop" in b.label.lower() or "liên tục" in b.label.lower() for b in at.button)  # không có nút loop
    assert any("paper-bot loop" in c.value for c in at.code) and "--dry-run" in at.code[0].value
    assert ctl.calls == []  # mở trang không chạy gì


def test_dieu_khien_dry_run(ctl):
    at = run_page("pages/4_Dieu_khien.py")
    at.button(key="run_dry").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert ctl.calls == [core.build_once_command(core.DEFAULT_UI_SYMBOLS, dry_run=True)]
    assert any("stdout giả" in c.value for c in at.code) and any("stderr giả" in c.value for c in at.code)
    assert ctl.events == []  # dry-run không ghi event ui_real_run


def test_dieu_khien_thieu_xac_nhan_khong_chay_that(ctl):
    at = run_page("pages/4_Dieu_khien.py")
    for ack, word in [(False, ""), (True, ""), (False, "XAC NHAN"), (True, "xac nhan"), (True, " XAC NHAN"),
                      (True, "XAC NHAN "), (True, "XAC  NHAN")]:
        at.checkbox(key="ack_real").set_value(ack)
        at.text_input(key="confirm_real").set_value(word).run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.button(key="run_real").disabled, (ack, word)  # AppTest không cho bấm nút disabled
    assert ctl.calls == [] and ctl.events == []


def test_dieu_khien_chay_that_khi_du_hai_xac_nhan(ctl):
    at = run_page("pages/4_Dieu_khien.py")
    at.checkbox(key="ack_real").check()
    at.text_input(key="confirm_real").set_value("XAC NHAN").run()
    assert not at.button(key="run_real").disabled
    at.button(key="run_real").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(real_calls(ctl)) == 1 and "--dry-run" not in ctl.calls[0] and ctl.calls[0][3] == "once"
    assert ctl.order == ["event", "run"]  # ghi event trước, rồi mới chạy
    run_id, kind, msg = ctl.events[0]
    assert (run_id, kind) == ("live-paper", "ui_real_run") and "AAPL" in msg


def test_dieu_khien_input_la_bi_chan(ctl):
    for bad in ["aapl", "AAPL;", "$(rm)", "A B1", "../x", "TOOLONGSYM"]:
        at = run_page("pages/4_Dieu_khien.py")
        at.text_input(key="extra").set_value(bad).run()
        assert not at.exception, [e.value for e in at.exception]
        assert any("không hợp lệ" in e.value for e in at.error), bad
        assert len(at.button) == 0  # không tạo được lệnh thì không có nút nào
    at = run_page("pages/4_Dieu_khien.py")
    at.multiselect(key="symbols").set_value([]).run()  # không chọn mã nào
    assert any("không hợp lệ" in e.value for e in at.error)
    assert ctl.calls == []


def test_dieu_khien_engine_legacy_va_disable(ctl):
    at = run_page("pages/4_Dieu_khien.py")
    at.multiselect(key="disable").set_value(["bracket", "regime"]).run()
    at.button(key="run_dry").click().run()
    assert ctl.calls[-1][ctl.calls[-1].index("--disable"):][:3] == ["--disable", "bracket", "regime"]
    at.radio(key="engine").set_value("legacy").run()  # legacy: bỏ qua --disable
    assert at.multiselect(key="disable").disabled
    at.button(key="run_dry").click().run()
    assert "--engine" in ctl.calls[-1] and "legacy" in ctl.calls[-1] and "--disable" not in ctl.calls[-1]
    assert not at.exception, [e.value for e in at.exception]


def test_dieu_khien_ma_them_hop_le_va_llm(ctl):
    at = run_page("pages/4_Dieu_khien.py")
    at.text_input(key="extra").set_value("MSFT").run()
    at.checkbox(key="llm").check().run()
    at.button(key="run_dry").click().run()
    cmd = ctl.calls[-1]
    assert "MSFT" in cmd and "--llm" in cmd and "--dry-run" in cmd


def test_dieu_khien_ket_qua_loi_va_timeout(ctl):
    at = run_page("pages/4_Dieu_khien.py")
    ctl.result = (2, "", "boom")
    at.button(key="run_dry").click().run()
    assert any("exit code 2" in e.value for e in at.error)
    ctl.raise_timeout = True
    at.button(key="run_dry").click().run()
    assert not at.exception and any("Quá 600" in e.value for e in at.error)


def test_dieu_khien_go_kill_switch_can_xac_nhan(ctl):
    at = run_page("pages/4_Dieu_khien.py")
    assert at.button(key="run_ks").disabled and ctl.calls == []
    at.checkbox(key="ack_ks").check().run()
    assert at.button(key="run_ks").disabled  # mới tick, chưa gõ chữ
    at.checkbox(key="ack_ks").check()
    at.text_input(key="confirm_ks").set_value("XAC NHAN").run()
    assert not at.button(key="run_ks").disabled
    at.button(key="run_ks").click().run()
    assert ctl.calls == [core.BASE_CMD + ["report", "--reset-kill-switch"]]  # mặc định run live-paper
    at.radio(key="ks_run").set_value("live-dry").run()
    at.button(key="run_ks").click().run()
    assert ctl.calls[-1] == core.BASE_CMD + ["report", "--reset-kill-switch", "--dry-run"]
    assert not at.exception, [e.value for e in at.exception]
    assert ctl.events == [] and real_calls(ctl) == []  # gỡ kill switch không phải lệnh đặt lệnh
