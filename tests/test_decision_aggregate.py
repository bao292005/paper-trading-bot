from datetime import datetime

from paper_bot.contracts import Signal
from paper_bot.decision.aggregate import aggregate, aggregate_explain


def sig(action, conf, strategy="S", prob=None, veto=False):
    return Signal("X", action, 100.0, datetime(2020, 1, 2), f"{strategy} lý do", conf,
                  strategy=strategy, prob=prob, veto=veto)


def test_empty_and_all_vetoed():
    assert aggregate([]) is None
    assert aggregate([sig("BUY", 0.9, veto=True)]) is None


def test_same_direction_merged():
    out = aggregate([sig("BUY", 0.7, "MA"), sig("BUY", 0.9, "RSI")])
    assert out.action == "BUY"
    assert abs(out.confidence - 0.8) < 1e-9 and out.prob is None
    assert out.strategy == "MA+RSI"
    assert "[MA]" in out.reason and "[RSI]" in out.reason
    assert len(out.meta["members"]) == 2


def test_prob_averaged_only_when_all_calibrated():
    out = aggregate([sig("BUY", 0.7, prob=0.6), sig("BUY", 0.9, prob=0.4)])
    assert abs(out.prob - 0.5) < 1e-9 and abs(out.score - 0.5) < 1e-9
    out = aggregate([sig("BUY", 0.7, prob=0.6), sig("BUY", 0.9)])
    assert out.prob is None


def test_conflict_small_gap_stays_out():
    out, why = aggregate_explain([sig("BUY", 0.7), sig("SELL", 0.6)])
    assert out is None and "đứng ngoài" in why


def test_conflict_exact_gap_picks_winner():
    # 0.7 - 0.5 = 0.19999... trong số thực, vẫn phải coi là đủ 0.2
    out = aggregate([sig("BUY", 0.7, "MA"), sig("SELL", 0.5, "RSI")])
    assert out.action == "BUY" and out.strategy == "MA"
    assert "mâu thuẫn" in out.reason


def test_conflict_sum_of_scores():
    # SELL 2 tín hiệu tổng 1.2 > BUY 0.9 (chênh 0.3) -> SELL
    out = aggregate([sig("BUY", 0.9), sig("SELL", 0.6), sig("SELL", 0.6)])
    assert out.action == "SELL" and abs(out.confidence - 0.6) < 1e-9


def test_vetoed_signal_does_not_vote():
    out = aggregate([sig("BUY", 0.9, veto=True), sig("SELL", 0.6)])
    assert out.action == "SELL"
