"""Bước 1 ghép bot: config (engine, flags, dry_run, llm) + CLI. Offline."""

import logging
from datetime import datetime

import pytest

from paper_bot import _SHORT_FLAGS, apply_args, build_parser
from paper_bot.bot import DryRunOrderManager, TradingBot
from paper_bot.config import FEATURE_FLAGS, SHORT_FLAG_NAMES, TradingConfig
from paper_bot.decision.full_policy import DEFAULT_FLAGS
from paper_bot.strategies import TradingSignal


def cfg():
    return TradingConfig(api_key="x", api_secret="y")


def test_defaults():
    c = cfg()
    assert c.engine == "full" and c.dry_run is False and c.use_llm is False
    assert all(c.flags.values()) and c.disabled() == []


def test_flag_names_match_full_policy():
    # tên cờ phải khớp đúng full_policy để truyền thẳng vào make_full_policy_factory
    assert set(FEATURE_FLAGS) == set(DEFAULT_FLAGS)
    assert list(SHORT_FLAG_NAMES) == _SHORT_FLAGS


def test_disable_short_and_full_names():
    c = cfg()
    c.disable(["bracket", "use_regime"])
    assert c.flags["use_bracket"] is False and c.flags["use_regime"] is False
    assert sorted(c.disabled()) == ["use_bracket", "use_regime"]
    with pytest.raises(ValueError):
        c.disable(["abc"])


def test_flags_not_shared_between_configs():
    a, b = cfg(), cfg()
    a.disable(["forecast"])
    assert b.flags["use_forecast"] is True


def test_cli_parse_and_apply():
    args = build_parser().parse_args(
        ["once", "--dry-run", "--engine", "legacy", "--disable", "bracket", "regime",
         "--symbols", "aapl", "msft", "--llm"])
    c = cfg()
    apply_args(c, args)
    assert c.engine == "legacy" and c.dry_run and c.use_llm
    assert c.symbols == ["AAPL", "MSFT"]
    assert c.flags["use_bracket"] is False and c.flags["use_regime"] is False
    assert c.flags["use_forecast"] is True


def test_cli_defaults_and_old_commands():
    p = build_parser()
    a = p.parse_args(["loop", "--interval", "5"])
    assert a.mode == "loop" and a.interval == 5 and a.engine == "full"
    assert a.dry_run is False and a.disable == [] and a.llm is False
    assert p.parse_args(["report"]).mode == "report"


def test_cli_rejects_unknown_component():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["once", "--disable", "abc"])


def test_legacy_dry_run_never_sends():
    # bot legacy với DryRunOrderManager: có lệnh dự kiến nhưng không có client nào được gọi
    class FakePortfolio:
        def get_positions(self):
            return {}

        def get_open_order_symbols(self):
            return set()

        def calculate_position_size(self, price):
            return 5

    bot = TradingBot.__new__(TradingBot)
    bot.config = cfg()
    bot.config.dry_run = True
    bot.logger = logging.getLogger("test")
    bot.portfolio_manager = FakePortfolio()
    bot.order_manager = DryRunOrderManager(bot.logger)
    s = TradingSignal("AAA", "BUY", 100.0, datetime.now(), "test", 0.9)
    bot.execute_signal_group("AAA", "BUY", [s])
    assert bot.order_manager.orders == [("AAA", 5, "buy")]
    assert not hasattr(bot, "trading_client")  # không có client thật nào để gửi
