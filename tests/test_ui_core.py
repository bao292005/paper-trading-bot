"""Test ui/core.py: hàm thuần, dữ liệu tổng hợp (không cần mạng, không đọc key)."""

import logging
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from paper_bot.config import FEATURE_FLAGS, SHORT_FLAG_NAMES
from paper_bot.contracts import Forecast, MarketState, NewsItem, Signal
from paper_bot.data.cache import save_bars
from paper_bot.risk import legacy_position_size
from paper_bot.ui import core


def make_bars(n=400, seed=0, end="2024-06-28", base=100.0):
    """Nến tổng hợp: random walk, high/low cách close ±1% nên ATR ≈ 2."""
    idx = pd.bdate_range(end=end, periods=n, name="date")
    close = base * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, 0.01, n)))
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": 1e6}, index=idx)


def make_table(symbol, dates, q=(-0.02, 0.001, 0.025)):
    rows = [{"symbol": symbol, "date": d, "q10": q[0], "q50": q[1], "q90": q[2]} for d in dates]
    return pd.DataFrame(rows).set_index(["symbol", "date"]).sort_index()


# ---------------------------- cờ + dữ liệu ----------------------------

def test_flags_from_selection():
    flags = core.flags_from_selection(["use_regime", "use_bracket"])
    assert set(flags) == set(FEATURE_FLAGS)
    assert flags["use_regime"] and flags["use_bracket"] and not flags["use_forecast"]
    with pytest.raises(ValueError):
        core.flags_from_selection(["use_lạ"])


def test_load_market_data_tmp(tmp_path):
    save_bars("AAA", make_bars(), tmp_path)
    save_bars("SPY", make_bars(seed=1), tmp_path)
    bars, spy, missing = core.load_market_data(["AAA", "ZZZ"], bars_dir=tmp_path)
    assert list(bars) == ["AAA"] and missing == ["ZZZ"] and len(spy) == 400


def test_recent_dates_va_holdout():
    idx = make_bars().index
    assert len(core.recent_dates(idx, 10)) == 10
    assert core.recent_dates(idx, 5, end=idx[100])[-1] == idx[100]
    assert core.is_holdout("2025-01-01") and not core.is_holdout("2024-12-31")


def test_price_with_ma_khong_nhin_tuong_lai():
    bars = make_bars(n=600)
    asof = bars.index[580]
    out = core.price_with_ma(bars, asof)
    assert out.index[-1] == asof and len(out) == 252
    assert out["ma200"].notna().all()  # MA tính trên toàn lịch sử <= asof nên không khuyết
    assert out["ma10"].iloc[-1] == pytest.approx(bars["close"].iloc[571:581].mean())  # không lẫn nến sau asof


def test_ref_width_chi_dung_den_asof():
    d = pd.bdate_range("2024-01-01", periods=10)
    t = pd.concat([make_table("A", d[:5], (-0.01, 0, 0.01)), make_table("A", d[5:], (-0.5, 0, 0.5))])
    assert core.ref_width_at(t, d[4]) == pytest.approx(0.02)  # không thấy phần rộng 1.0 ở sau
    assert core.ref_width_at(None, d[4]) is None


def test_get_forecasts_bang_tinh_san_va_forecaster():
    bars = {"A": make_bars(), "B": make_bars(seed=2)}
    asof = bars["A"].index[-1]
    flags = core.flags_from_selection(FEATURE_FLAGS)

    class Fake:
        def predict_many(self, b, asof, horizon=5):
            return {s: Forecast("fake", horizon, {0.1: -0.1, 0.5: 0.0, 0.9: 0.1}) for s in b}

    fcs = core.get_forecasts(bars, asof, flags, make_table("A", [asof]), Fake())
    assert fcs["A"].model == "chronos-bolt-small" and fcs["B"].model == "fake"
    assert core.get_forecasts(bars, asof, flags, make_table("A", [asof])).keys() == {"A"}
    off = {**flags, "use_forecast": False}
    assert core.get_forecasts(bars, asof, off, make_table("A", [asof]), Fake()) == {}


def test_build_state_cat_tuong_lai_va_loc_tin():
    bars, spy = {"A": make_bars()}, make_bars(seed=3)
    asof = bars["A"].index[200]
    news = [NewsItem("tin A", datetime(2023, 1, 1), symbols=["A"]), NewsItem("tin B", datetime(2023, 1, 1), symbols=["B"]),
            NewsItem("tin A tương lai", datetime(2024, 1, 1), symbols=["A"])]  # sau asof -> bị loại
    fc = Forecast("x", 5, {0.1: -0.02, 0.5: 0.0, 0.9: 0.02})
    st = core.build_state("A", asof, bars, spy, {"A": fc}, news)
    assert st.bars.index[-1] == asof and st.forecast is fc
    assert [n.headline for n in st.news] == ["tin A"]
    s = core.state_summary(st)
    assert s["regime"] == st.regime and s["q50"] == 0.0


def test_fan_chart_frame():
    bars, spy = {"A": make_bars()}, make_bars(seed=3)
    fc = Forecast("x", 5, {0.1: -0.02, 0.5: 0.001, 0.9: 0.03})
    st = core.build_state("A", bars["A"].index[-1], bars, spy, {"A": fc})
    fan = core.fan_chart_frame(st)
    assert len(fan) == 6 and (fan["q10"] <= fan["q50"]).all() and (fan["q50"] <= fan["q90"]).all()
    assert fan["q50"].iloc[0] == pytest.approx(st.bars["close"].iloc[-1])
    st.forecast = None
    assert core.fan_chart_frame(st) is None


# ---------------------------- ghép lệnh có whitelist ----------------------------

@pytest.mark.parametrize("bad", [["aapl"], ["AAPL;rm"], ["AAPL\n"], ["A B"], ["../x"], [""], [], "AAPL",
                                 ["AAPL", 5], ["TOOLONGX"], ["$(id)"]])
def test_symbols_la_bi_chan(bad):
    with pytest.raises(ValueError):
        core.validate_symbols(bad)


def test_symbols_hop_le_bo_trung():
    assert core.validate_symbols(["AAPL", "NVDA", "AAPL"]) == ["AAPL", "NVDA"]
    with pytest.raises(ValueError):
        core.validate_symbols([f"{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(51)])


def test_disable_whitelist():
    assert core.validate_disable(["bracket", "regime", "bracket"]) == ["bracket", "regime"]
    for bad in (["use_bracket"], ["bracket; ls"], ["--llm"], [""]):
        with pytest.raises(ValueError):
            core.validate_disable(bad)
    assert set(SHORT_FLAG_NAMES) == {"regime", "calibration", "forecast", "atr_sizing", "bracket", "corr_limit"}


def test_lenh_dry_run_mac_dinh():
    cmd = core.build_once_command(["AAPL", "NVDA"], disable=["bracket"], llm=True)
    assert cmd == ["uv", "run", "paper-bot", "once", "--symbols", "AAPL", "NVDA", "--engine", "full",
                   "--disable", "bracket", "--llm", "--dry-run"]
    assert all(isinstance(c, str) for c in cmd) and not core.is_real_order_command(cmd)


def test_lenh_that_can_du_2_xac_nhan():
    real = dict(symbols=["AAPL"], dry_run=False)
    for ack, text in [(False, "XAC NHAN"), (True, ""), (True, "xac nhan"), (True, "XAC NHAN "),
                      (True, "XAC NHAN\n"), ("yes", "XAC NHAN"), (1, "XAC NHAN"), (None, None)]:
        with pytest.raises(PermissionError):
            core.build_once_command(**real, ack=ack, confirm_text=text)
    cmd = core.build_once_command(**real, ack=True, confirm_text="XAC NHAN")
    assert "--dry-run" not in cmd and core.is_real_order_command(cmd)


def test_lenh_engine_la_va_legacy_khong_disable():
    with pytest.raises(ValueError):
        core.build_once_command(["AAPL"], engine="evil")
    with pytest.raises(ValueError):
        core.build_once_command(["AAPL"], engine="legacy", disable=["bracket"])
    assert "legacy" in core.build_once_command(["AAPL"], engine="legacy")


def test_lenh_go_ky_kill_switch_can_xac_nhan():
    with pytest.raises(PermissionError):
        core.build_reset_kill_switch_command()
    with pytest.raises(PermissionError):
        core.build_reset_kill_switch_command(ack=True, confirm_text="nope")
    cmd = core.build_reset_kill_switch_command(ack=True, confirm_text="XAC NHAN")
    assert cmd == ["uv", "run", "paper-bot", "report", "--reset-kill-switch"]
    assert core.build_reset_kill_switch_command(True, True, "XAC NHAN")[-1] == "--dry-run"


def test_loop_text_chi_de_hien_thi():
    text = core.loop_command_text(["AAPL"], interval=15)
    assert text.startswith("uv run paper-bot loop") and "--interval 15" in text and "--dry-run" in text
    assert "--dry-run" not in core.loop_command_text(["AAPL"], dry_run=False)
    for bad in (0, 1441, "5", True, 2.5):
        with pytest.raises(ValueError):
            core.validate_interval(bad)


# ---------------------------- P2: quyết định ----------------------------

ALL_ON = core.flags_from_selection(FEATURE_FLAGS)


def sig(action="BUY", conf=0.8, prob=None, veto=False, why="", symbol="AAA", price=100.0):
    return Signal(symbol, action, price, datetime(2024, 6, 28), "lý do thử", conf,
                  strategy="stub", prob=prob, veto=veto, veto_reason=why)


class StubPipe:
    """Giả DecisionPipeline: trả sẵn một danh sách tín hiệu; gộp = tín hiệu đầu không bị veto."""

    def __init__(self, signals, note="ghi chú gộp"):
        self.signals, self.last_note, self.last_signals = signals, note, []

    def decide(self, state):
        self.last_signals = list(self.signals(state) if callable(self.signals) else self.signals)
        ok = [s for s in self.last_signals if not s.veto]
        return ok[0] if ok else None


def state_of(symbol="AAA", n=60, seed=0):
    bars = make_bars(n, seed)
    return MarketState(symbol, bars.index[-1], bars)


def test_build_pipeline_theo_co():
    names = lambda f: [s.name for s in core.build_pipeline(f).strategies]
    assert len(names(ALL_ON)) == 1  # chỉ RegimeSwitch
    assert len(names({**ALL_ON, "use_regime": False})) == 2  # MA + RSI như bài gốc
    assert core.build_pipeline({**ALL_ON, "use_calibration": False}, calibrator=object()).calibrator is None
    assert core.build_pipeline(ALL_ON).min_score == 0.0


def test_decide_nguong_chi_ap_cho_buy():
    st = state_of()
    d = core.decide(StubPipe([sig("BUY", conf=0.3)]), st)
    assert d.final is None and "BUY" in d.verdict and "0.5" in d.verdict
    d = core.decide(StubPipe([sig("SELL", conf=0.3)]), st)  # SELL không bị ngưỡng chặn
    assert d.final is not None and d.final.action == "SELL" and d.verdict.startswith("Signal SELL")
    d = core.decide(StubPipe([sig("BUY", conf=0.3, prob=0.9)]), st)  # score ưu tiên prob
    assert d.final is not None
    assert core.decide(StubPipe([sig("BUY", conf=0.6)]), st, min_buy=0.7).final is None


def test_decide_khong_co_tin_hieu_va_veto():
    st = state_of()
    d = core.decide(StubPipe([]), st)
    assert d.final is None and not d.signals and "không chiến lược nào" in d.verdict
    d = core.decide(StubPipe([sig(veto=True, why="chống xu hướng")], note="mọi tín hiệu bị veto"), st)
    assert d.final is None and d.note in d.verdict and len(d.signals) == 1


def test_signal_rows():
    df = core.signal_rows([sig(veto=True, why="vì sao đó", prob=0.6)])
    assert list(df.columns) == ["strategy", "action", "confidence", "prob", "veto", "veto_reason", "reason"]
    assert bool(df["veto"].iloc[0]) and df["veto_reason"].iloc[0] == "vì sao đó"
    assert core.signal_rows([]).empty


def test_scan_sessions_chi_giu_ngay_co_tin_hieu():
    bars = {"AAA": make_bars(300, 0), "BBB": make_bars(300, 1)}
    spy = make_bars(300, 2)
    dates = spy.index[-10:]
    pipe = StubPipe(lambda st: [sig("BUY", 0.8, symbol=st.symbol)] if st.asof.day % 2 == 0 else [])
    df = core.scan_sessions(["AAA", "BBB"], dates, bars, spy, ALL_ON, pipe)
    even = [d for d in dates if d.day % 2 == 0]
    assert len(df) == 2 * len(even) and set(df["final"]) == {"BUY"}
    assert core.scan_sessions(["AAA"], dates, bars, spy, ALL_ON, StubPipe([])).empty


def test_get_calibrator_khong_cham_holdout(tmp_path):
    assert core.get_calibrator(["AAA"], {**ALL_ON, "use_calibration": False})[0] is None
    seen = {}

    def fake_fit(symbols, end, use_fc_feat):
        seen["end"], seen["symbols"] = end, symbols
        return {"giả": 1}

    cal, note = core.get_calibrator(["AAA"], ALL_ON, today="2026-11-26", path=tmp_path / "c.pkl", fit_fn=fake_fit)
    assert cal == {"giả": 1} and "fit" in note
    assert seen["end"] <= pd.Timestamp("2024-12-31") and "AAA" in seen["symbols"]


# ---------------------------- P3: lệnh dự kiến ----------------------------

def plan(signal, state=None, flags=ALL_ON, equity=100_000.0, cash=100_000.0, positions=None, all_bars=None, **kw):
    state = state or state_of()
    return core.plan_order(signal, state, flags, equity, cash, positions or {}, all_bars or {}, **kw)


def test_plan_buy_co_so_co_sl_tp():
    p = plan(sig())
    i = p.intent
    assert i.side == "buy" and i.qty > 0 and i.stop_loss < p.price < i.take_profit
    assert p.notional == pytest.approx(i.qty * p.price) and p.pct_equity <= 0.10 + 1e-9
    assert p.risk_amount == pytest.approx(i.qty * (p.price - i.stop_loss)) and not p.rejected


def test_plan_ly_do_tu_choi():
    assert "veto" in plan(sig(veto=True, why="xấu")).rejected
    assert "chưa có vị thế" in plan(sig("SELL")).rejected
    assert "đã có vị thế" in plan(sig(), positions={"AAA": 5}).rejected
    assert "cỡ lệnh = 0" in plan(sig(), cash=1.0).rejected
    assert plan(None).intent is None and "Không có Signal" in plan(None).rejected


def test_plan_sell_dong_vi_the():
    p = plan(sig("SELL"), positions={"AAA": 7})
    assert p.intent.side == "sell" and p.intent.qty == 7 and p.risk_amount is None


def test_plan_tuong_quan():
    st = state_of(n=100)
    twin = {"AAA": st.bars, "BBB": st.bars}  # giống hệt nhau -> tương quan 1.0
    p = plan(sig(), st, positions={"BBB": 10}, all_bars=twin)
    assert p.intent is None and p.corr_ok is False and "tương quan" in p.rejected
    off = plan(sig(), st, flags={**ALL_ON, "use_corr_limit": False}, positions={"BBB": 10}, all_bars=twin)
    assert off.intent is not None and off.corr_ok is None
    other = {"AAA": st.bars, "BBB": make_bars(100, seed=9)}
    assert plan(sig(), st, positions={"BBB": 10}, all_bars=other).corr_ok is True


def test_plan_co_legacy_sizing_va_bracket():
    st = state_of()
    p = plan(sig(), st, flags={**ALL_ON, "use_bracket": False, "use_atr_sizing": False})
    assert p.intent.stop_loss is None and p.intent.take_profit is None and p.risk_amount is None
    assert p.intent.qty == legacy_position_size(100_000.0, p.price)


def test_plan_khong_doi_muc_log():
    logger = logging.getLogger("paper_bot")
    before, handlers = logger.level, list(logger.handlers)
    plan(sig(veto=True, why="x"))
    assert logger.level == before and logger.handlers == handlers


# ---------------------------- Đánh giá + giám sát (đọc DB / file) ----------------------------

@pytest.fixture
def db(tmp_path):
    """DB tạm có run bt-x (2 điểm equity) và live-dry (events, signal)."""
    from paper_bot.storage import Store

    s = Store(path=tmp_path / "t.db", run_id="bt-x")
    s.log_equity(datetime(2024, 1, 1), 100.0, 100.0, 0.0)
    s.log_equity(datetime(2024, 1, 2), 90.0, 50.0, 40.0)
    d = Store(path=tmp_path / "t.db", run_id="live-dry")
    d.log_event("kill_switch", "ngắt", level="WARNING")
    d.log_event("reconcile", "ok")
    d.log_signal(sig(), regime="bull_calm")
    return tmp_path / "t.db"


def test_open_db_thieu_file_va_chi_doc(tmp_path, db):
    assert core.open_db(tmp_path / "khong_co.db") is None
    conn = core.open_db(db)
    assert core.db_runs(conn) == ["bt-x", "live-dry"] and core.backtest_runs(core.db_runs(conn)) == ["bt-x"]
    with pytest.raises(Exception):  # mở ro nên ghi sẽ lỗi
        conn.execute("DELETE FROM equity")


def test_equity_va_summary(db):
    conn = core.open_db(db)
    curves = core.equity_curves(conn, ["bt-x", "live-dry"])  # live-dry không có equity -> bị bỏ
    assert list(curves) == ["bt-x"] and curves["bt-x"]["drawdown"].iloc[-1] == pytest.approx(-0.1)
    assert core.run_summary(conn, ["bt-x"]).loc["bt-x", "total_return"] == pytest.approx(-0.1)
    assert core.db_table(conn, "signals", "live-dry")["symbol"].tolist() == ["AAA"]
    with pytest.raises(ValueError):
        core.db_table(conn, "sqlite_master", "x")


def test_filter_events(db):
    evt = core.db_table(core.open_db(db), "events", "live-dry")
    assert len(core.filter_events(evt)) == 2
    assert core.filter_events(evt, levels=["WARNING"])["kind"].tolist() == ["kill_switch"]
    assert core.filter_events(evt, kinds=["reconcile"])["level"].tolist() == ["INFO"]
    assert core.filter_events(evt, levels=["ERROR"]).empty
    assert core.filter_events(evt.iloc[0:0], levels=["INFO"]).empty


def test_live_state_va_kill_switch_text(tmp_path):
    path = tmp_path / "live_state.json"
    assert core.read_live_state("live-dry", path) == {}
    assert core.kill_switch_text({})[0] is False
    path.write_text('{"live-dry": {"halted": true, "day": "2026-11-25", "peak_equity": 1000}}')
    halted, text = core.kill_switch_text(core.read_live_state("live-dry", path))
    assert halted is True and "NGẮT" in text and "2026-11-25" in text
    assert core.read_live_state("live-paper", path) == {}  # run khác -> rỗng
    path.write_text("{hỏng")
    with pytest.raises(ValueError, match="hỏng"):
        core.read_live_state("live-dry", path)


MD = """# Tiêu đề
## Bảng một
| cấu hình | sharpe | cagr | kết luận |
|---|---|---|---|
| FULL | 0.12 | 0.6% |  |
| B0 | 0.92 | 17.1% | ok |

Chữ ở giữa.

## Bảng hai
| a | b |
|---|---|
| 1 | x |
"""


def test_markdown_tables():
    (h1, t1), (h2, t2) = core.markdown_tables(MD)
    assert h1 == "Bảng một" and h2 == "Bảng hai"
    assert list(t1.columns) == ["cấu hình", "sharpe", "cagr", "kết luận"]
    assert t1["sharpe"].tolist() == [0.12, 0.92] and t1["cagr"].tolist() == ["0.6%", "17.1%"]
    assert t2["a"].tolist() == [1.0] and t2["b"].tolist() == ["x"]
    assert core.markdown_tables("không có bảng nào") == []
    assert core.markdown_tables("| a |\n|---|\n") == []  # thiếu dòng dữ liệu


def test_doc_va_ablation(tmp_path):
    (tmp_path / "ablation-dev.md").write_text(MD, encoding="utf-8")
    assert core.ablation_table(tmp_path).loc["FULL", "sharpe"] == 0.12
    assert core.ablation_table(tmp_path / "khong_co").empty
    assert core.read_result_doc("nope.md", tmp_path) == ""
    for bad in ["../x.md", "a/b.md", "x.txt"]:
        with pytest.raises(ValueError):
            core.read_result_doc(bad, tmp_path)


def test_doc_that_cua_du_an():
    t = core.ablation_table()
    if not t.empty:  # file thật có thể bị ghi đè nhưng phải còn dòng FULL
        assert "FULL" in t.index and "sharpe" in t.columns


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeClient:
    """Client giả: chỉ có hàm ĐỌC. Có ai gọi submit_order là lỗi."""
    calls = []

    def get_account(self):
        self.calls.append("account")
        return _Obj(equity="100500.5", cash="90000")

    def get_all_positions(self):
        return [_Obj(symbol="AAPL", qty="3", avg_entry_price="190", market_value="600", unrealized_pl="30")]

    def get_orders(self, filter=None):
        return [_Obj(symbol="NVDA", side="buy", qty="2", order_type="market", status="new", id="abc")]

    def submit_order(self, *a, **k):
        raise AssertionError("giám sát không được đặt lệnh")


def test_account_snapshot():
    s = core.account_snapshot(FakeClient())
    assert s["equity"] == 100500.5 and s["cash"] == 90000.0
    assert s["positions"].loc[0, "symbol"] == "AAPL" and s["positions"].loc[0, "lãi/lỗ"] == 30.0
    assert s["orders"].loc[0, "symbol"] == "NVDA" and s["orders"].loc[0, "status"] == "new"


def test_fetch_account_loi_thi_tra_thong_bao(monkeypatch):
    def thieu_key(*a, **k):
        raise SystemExit("thiếu key")

    monkeypatch.setattr("paper_bot.config.TradingConfig.from_env", thieu_key)
    snap, err = core.fetch_account()
    assert snap is None and "thiếu key" in err


# ---------------------------- chạy lệnh: chốt chặn ----------------------------

@pytest.fixture
def fake_run(monkeypatch):
    """Thay subprocess.run bằng bản ghi lại (KHÔNG chạy gì thật)."""
    import subprocess

    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "xong", "log")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


DRY = core.build_once_command(["AAPL"], dry_run=True)
REAL = core.BASE_CMD + ["once", "--symbols", "AAPL", "--engine", "full"]  # không --dry-run


def test_run_command_dry_chay_duoc(fake_run):
    res = core.run_command(DRY)
    cmd, kw = fake_run[0]
    assert cmd == DRY and isinstance(cmd, list) and kw["shell"] is False and kw["timeout"] == 600
    assert res == {"returncode": 0, "stdout": "xong", "stderr": "log", "timed_out": False}


def test_run_command_that_can_allow_real_dung_true(fake_run):
    assert core.is_real_order_command(REAL)
    for bad in (False, "yes", 1, None):
        with pytest.raises(PermissionError):
            core.run_command(REAL, allow_real=bad)
    assert fake_run == []  # chưa lần nào chạm subprocess
    core.run_command(REAL, allow_real=True)
    assert len(fake_run) == 1


@pytest.mark.parametrize("bad", [
    "uv run paper-bot once --dry-run",  # chuỗi, không phải list
    ["rm", "-rf", "/"],
    ["uv", "run", "paper-bot"],
    core.BASE_CMD + ["loop", "--dry-run"],  # UI không chạy loop
    core.BASE_CMD + ["loop"],
    ["uv", "run", "python", "-c", "x", "once"],
    core.BASE_CMD + ["once", 5],
    [],
])
def test_run_command_chan_lenh_la(fake_run, bad):
    with pytest.raises(PermissionError):
        core.run_command(bad, allow_real=True)
    assert fake_run == []


def test_run_command_reset_kill_switch_la_report(fake_run):
    cmd = core.build_reset_kill_switch_command(True, True, core.CONFIRM_WORD)
    core.run_command(cmd)
    assert fake_run[0][0] == core.BASE_CMD + ["report", "--reset-kill-switch", "--dry-run"]


def test_run_command_timeout(monkeypatch):
    import subprocess

    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 600, output=b"mot phan", stderr=None)

    monkeypatch.setattr(subprocess, "run", run)
    res = core.run_command(DRY)
    assert res["timed_out"] is True and res["returncode"] is None and res["stdout"] == "mot phan" and res["stderr"] == ""


def test_log_ui_event_va_nhan_run(tmp_path):
    core.log_ui_event("live-paper", "ui_real_run", "thử", db_path=tmp_path / "e.db")
    evt = core.db_table(core.open_db(tmp_path / "e.db"), "events", "live-paper")
    assert evt["kind"].tolist() == ["ui_real_run"] and evt["level"].tolist() == ["WARNING"]
    assert core.real_run_id(False) == "live-paper" and core.real_run_id(True) == "live-dry"
    assert "bỏ forecast" in core.run_label("bt-abl-no-forecast") and "[bt-abl-no-forecast]" in core.run_label("bt-abl-no-forecast")
    assert core.run_label("bt-lạ") == "bt-lạ"
