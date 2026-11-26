"""Demo P2: MarketState (P1) -> DecisionPipeline -> Signal, in từng bước để thấy vì sao ra quyết định.

Chạy:  uv run python scripts/demo_p2.py [MÃ ...] [--days 60]
Quét ngược N phiên gần nhất trong cache, in các ngày có tín hiệu. Chỉ đọc, không đặt lệnh.
Cấu hình giống LiveBot mặc định: RegimeSwitch + veto + calibrator, ngưỡng 0.5 chỉ áp cho BUY.
"""

import argparse

import pandas as pd

from paper_bot.data.cache import load_bars
from paper_bot.data.forecast import ChronosForecaster
from paper_bot.data.market_state import build_market_state
from paper_bot.decision.pipeline import DecisionPipeline
from paper_bot.decision.regime_switch import RegimeSwitchStrategy
from paper_bot.live import load_or_fit_calibrator

MIN_BUY_SCORE = 0.5


def fmt(x):
    return "-" if x is None else f"{x:.2f}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="*", default=["AAPL", "NVDA", "JPM"])
    p.add_argument("--days", type=int, default=60, help="số phiên quét ngược")
    args = p.parse_args()

    spy = load_bars("SPY")
    bars = {s: load_bars(s) for s in args.symbols}
    days = spy.index[-args.days:]

    print("Nạp calibrator (fit trên dữ liệu tới 2024-12-31)...")
    cal, note = load_or_fit_calibrator(list(bars), pd.Timestamp.today().normalize(), True)
    print(f"  -> {note}\n")
    pipe = DecisionPipeline([RegimeSwitchStrategy()], calibrator=cal, min_score=0.0)
    chronos = ChronosForecaster()

    print(f"Quét {len(days)} phiên {days[0].date()} -> {days[-1].date()} cho {args.symbols}\n")
    n_days = 0
    for asof in days:
        cut = {s: b[b.index <= asof] for s, b in bars.items()}
        fcs = chronos.predict_many(cut, asof, horizon=5)
        for s in args.symbols:
            state = build_market_state(s, asof, bars[s], spy, forecast=fcs.get(s))
            final = pipe.decide(state)
            if not pipe.last_signals:
                continue  # ngày thường: không chiến lược nào lên tiếng
            n_days += 1
            # ---- INPUT tóm tắt ----
            f = state.features
            print(f"=== {asof.date()}  {s} ===")
            fc = f"forecast q50={state.forecast.median:+.2%}" if state.forecast else "forecast -"
            print(f"INPUT  regime={state.regime}  close={state.bars.close.iloc[-1]:.2f}  "
                  f"rsi14={f.get('rsi14', float('nan')):.1f}  {fc}")
            # ---- Các tín hiệu thô sau veto + calibrate ----
            print("  tín hiệu:")
            for sig in pipe.last_signals:
                v = f"  VETO: {sig.veto_reason}" if sig.veto else ""
                print(f"    [{sig.strategy:<13}] {sig.action:<4} confidence={fmt(sig.confidence)} "
                      f"prob={fmt(sig.prob)}{v}")
                print(f"        lý do: {sig.reason}")
            print(f"  gộp   : {pipe.last_note}")
            # ---- Ngưỡng (chỉ BUY) giống LiveBot ----
            if final is not None and final.action == "BUY" and final.score < MIN_BUY_SCORE:
                print(f"OUTPUT -> KHÔNG có Signal (BUY score {final.score:.2f} < {MIN_BUY_SCORE})\n")
            elif final is None:
                print("OUTPUT -> KHÔNG có Signal\n")
            else:
                print(f"OUTPUT -> Signal {final.action} {s} @ {final.price:.2f}  score={final.score:.2f}  "
                      f"strategy={final.strategy}\n")
    if n_days == 0:
        print("Không có tín hiệu nào trong khoảng này - thử --days lớn hơn hoặc mã khác.")


if __name__ == "__main__":
    main()
