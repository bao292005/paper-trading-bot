"""Bước 3 ghép bot (phần thuần, không cần broker): nến đã đóng, calibrator cache, live_state.json, report."""

import pickle
import time
from datetime import datetime

import pandas as pd

from paper_bot import build_parser, live
from paper_bot.config import TradingConfig
from paper_bot.storage import Store

TODAY = pd.Timestamp("2026-11-25")


def test_last_closed_day():
    # phiên đang mở hoặc trước 16:00 -> chỉ tới hôm qua; đóng cửa sau 16:00 -> hôm nay
    assert live.last_closed_day(True, pd.Timestamp("2026-11-25 11:00")) == pd.Timestamp("2026-11-24")
    assert live.last_closed_day(False, pd.Timestamp("2026-11-25 08:00")) == pd.Timestamp("2026-11-24")
    assert live.last_closed_day(False, pd.Timestamp("2026-11-25 16:30")) == pd.Timestamp("2026-11-25")


def test_calibrator_never_trains_on_holdout():
    assert TradingConfig(api_key="x", api_secret="y").calib_end == "2024-12-31"
    assert live.calib_train_end(TODAY) == pd.Timestamp("2024-12-31")
    assert live.calib_train_end(pd.Timestamp("2024-06-10")) == pd.Timestamp("2024-06-09")
    # sau holdout: đổi sang "yesterday"
    assert live.calib_train_end(TODAY, "yesterday") == pd.Timestamp("2026-11-24")


def test_calibrator_refit_when_calib_end_changes(tmp_path):
    path, fit = tmp_path / "cal.pkl", CountFit()
    live.load_or_fit_calibrator(["AAA"], TODAY, True, path=path, fit_fn=fit)
    live.load_or_fit_calibrator(["AAA"], TODAY, True, path=path, fit_fn=fit, calib_end="yesterday")
    assert fit.calls == [pd.Timestamp("2024-12-31"), pd.Timestamp("2026-11-24")]
    assert pickle.loads(path.read_bytes())["calib_end"] == "yesterday"


def test_reset_kill_switch(tmp_path):
    path, store = tmp_path / "s.json", Store(tmp_path / "t.db", run_id="live-paper")
    live.save_state(path, "live-paper", {"halted": True, "peak_equity": 5.0})
    live.save_state(path, "live-dry", {"halted": True})
    msg = live.reset_kill_switch("live-paper", state_path=path, store=store)
    assert "Đã gỡ" in msg
    assert live.load_state(path, "live-paper") == {"halted": False, "peak_equity": 5.0}
    assert live.load_state(path, "live-dry")["halted"] is True  # run_id khác không đụng tới
    assert store.conn.execute("SELECT level FROM events WHERE kind='kill_switch'").fetchall() == [("WARNING",)]
    assert "không đổi" in live.reset_kill_switch("live-paper", state_path=path, store=store)


def test_cli_reset_flag():
    assert build_parser().parse_args(["report", "--reset-kill-switch"]).reset_kill_switch is True
    assert build_parser().parse_args(["once"]).reset_kill_switch is False


class CountFit:
    def __init__(self, result="CAL", error=None, sleep=0.0):
        self.calls, self.result, self.error, self.sleep = [], result, error, sleep

    def __call__(self, symbols, end, use_fc_feat):
        self.calls.append(end)
        time.sleep(self.sleep)
        if self.error:
            raise self.error
        return self.result


def save_pkl(path, fit_date, use_forecast=True):
    with open(path, "wb") as f:
        pickle.dump({"fit_date": fit_date, "use_forecast": use_forecast, "calibrator": "OLD"}, f)


def test_calibrator_fit_saves_then_reuses(tmp_path):
    path, fit = tmp_path / "m" / "cal.pkl", CountFit()
    cal, _ = live.load_or_fit_calibrator(["AAA"], TODAY, True, path=path, fit_fn=fit)
    assert cal == "CAL" and fit.calls == [pd.Timestamp("2024-12-31")]
    saved = pickle.loads(path.read_bytes())
    assert saved["fit_date"] == "2026-11-25" and saved["train_end"] == "2024-12-31"
    cal2, _ = live.load_or_fit_calibrator(["AAA"], TODAY + pd.Timedelta(days=7), True, path=path, fit_fn=fit)
    assert cal2 == "CAL" and len(fit.calls) == 1  # còn mới (≤ 7 ngày) -> không fit lại


def test_calibrator_refit_when_stale_or_flag_changed(tmp_path):
    path, fit = tmp_path / "cal.pkl", CountFit(result="NEW")
    save_pkl(path, "2026-11-10")  # 15 ngày
    assert live.load_or_fit_calibrator(["AAA"], TODAY, True, path=path, fit_fn=fit)[0] == "NEW"
    save_pkl(path, "2026-11-24", use_forecast=False)  # mới nhưng khác use_forecast
    assert live.load_or_fit_calibrator(["AAA"], TODAY, True, path=path, fit_fn=fit)[0] == "NEW"
    assert len(fit.calls) == 2


def test_calibrator_error_or_timeout_gives_none(tmp_path):
    path = tmp_path / "cal.pkl"
    cal, note = live.load_or_fit_calibrator(["AAA"], TODAY, True, path=path,
                                            fit_fn=CountFit(error=ValueError("hỏng")))
    assert cal is None and "lỗi" in note and not path.exists()
    cal, note = live.load_or_fit_calibrator(["AAA"], TODAY, True, path=path,
                                            fit_fn=CountFit(sleep=1.0), timeout_s=0.05)
    assert cal is None and "quá" in note
    cal, _ = live.load_or_fit_calibrator(["AAA"], TODAY, True, path=path, fit_fn=CountFit(result=None))
    assert cal is None


def test_state_file_per_run_id(tmp_path):
    path = tmp_path / "live_state.json"
    assert live.load_state(path, "live-paper") == {}
    live.save_state(path, "live-paper", {"halted": True, "peak_equity": 1.0})
    live.save_state(path, "live-dry", {"halted": False})
    assert live.load_state(path, "live-paper")["halted"] is True
    assert live.load_state(path, "live-dry") == {"halted": False}


def test_live_report(tmp_path):
    store = Store(tmp_path / "t.db", run_id="live-paper")
    assert live.live_report(store) == {}
    store.log_equity(datetime(2026, 11, 24, 16), 100000.0, 50000.0, 0.5)
    store.log_equity(datetime(2026, 11, 25, 16), 101000.0, 50000.0, 0.5)
    rep = live.live_report(store)
    assert rep["run_id"] == "live-paper" and abs(rep["total_return"] - 0.01) < 1e-9
