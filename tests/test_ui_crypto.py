"""Trang Streamlit 'Điều khiển' ở chế độ crypto: lệnh ghép đúng `--asset crypto`, chỉ dry-run, không gửi lệnh thật.
Offline: subprocess bị thay bằng bản giả ghi lại lệnh."""

import subprocess
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from paper_bot.config import CRYPTO_UNIVERSE
from paper_bot.ui import core

PAGE = str(Path(__file__).resolve().parents[1] / "src" / "paper_bot" / "ui" / "pages" / "4_Dieu_khien.py")


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "stdout giả", "stderr giả")

    def boom(*a, **k):
        raise AssertionError("không được ghi event / gọi broker")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(core, "log_ui_event", boom)
    monkeypatch.setattr("alpaca.trading.client.TradingClient.__init__", boom)
    return seen


def open_crypto():
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    at.radio(key="asset").set_value("crypto").run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_loop_command_text_crypto():
    text = core.loop_command_text(CRYPTO_UNIVERSE, asset="crypto", interval=60)
    assert "--asset crypto" in text and "paper-bot loop" in text and "--interval 60" in text
    assert "--asset" not in core.loop_command_text(["AAPL"])  # cổ phiếu giữ nguyên
    with pytest.raises(ValueError):
        core.loop_command_text(["AAPL"], asset="crypto")


def test_trang_crypto_hien_lenh_dry_run(calls):
    at = open_crypto()
    assert at.multiselect(key="symbols_crypto").value == CRYPTO_UNIVERSE
    assert [r.options for r in at.radio if r.key == "engine_crypto"] == [["full"]]  # crypto chỉ engine full
    assert "--asset" in at.code[0].value and "crypto" in at.code[0].value and "--dry-run" in at.code[0].value
    assert calls == []  # mở trang không chạy gì


def test_crypto_dry_run_chay_dung_lenh(calls):
    at = open_crypto()
    at.button(key="run_dry").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert calls == [core.build_once_command(CRYPTO_UNIVERSE, dry_run=True, asset="crypto")]
    assert all("--dry-run" in c for c in calls)


def test_crypto_chay_that_van_can_xac_nhan(calls):
    at = open_crypto()
    assert at.button(key="run_real").disabled
    assert calls == []
