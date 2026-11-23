"""Dựng bảng tín hiệu lịch sử từ cache và đánh giá calibration theo walk-forward.

Chạy:  uv run python -m paper_bot.decision.calib_history [--forecast] [--symbols AAPL MSFT]
Chống rò rỉ / HOLDOUT:
  - chỉ đọc nến <= 2024-12-31 (không chạm 2025+), nên nhãn của tín hiệu cuối 2024 thiếu t+5 -> bị bỏ;
  - tại ngày t chiến lược chỉ thấy nến <= t;
  - train của mỗi cửa sổ chỉ gồm tín hiệu có NGÀY NHÃN (t+5) < ngày bắt đầu test (purge).
"""

import argparse
import logging
import time

import pandas as pd

from ..config import BENCHMARK, UNIVERSE_50
from ..contracts import MarketState
from ..data.cache import load_bars
from ..data.forecast import get_forecast, load_forecasts
from ..data.regime import regime_series
from ..strategies import BollingerBandsStrategy, MovingAverageCrossoverStrategy, RSIStrategy
from .base import wrap_legacy
from .calibration import FEATURE_WINDOW, HORIZON, fit_calibrator, make_label, signal_features

logger = logging.getLogger("paper_bot")

DATA_END = "2024-12-31"   # KHÔNG dùng dữ liệu từ 2025-01-01 (HOLDOUT)
TABLE_START = "2016-01-01"  # 2016-2018 chỉ làm train ban đầu (prereg: giai đoạn khởi động)
EVAL_START = "2019-01-01"
MIN_BARS = 60


def build_signal_table(symbols=None, use_forecast: bool = False, start: str = TABLE_START,
                       end: str = DATA_END) -> pd.DataFrame:
    """Mỗi dòng = 1 tín hiệu của MA/RSI/Bollinger tại 1 ngày, kèm feature và nhãn y."""
    assert pd.Timestamp(end) < pd.Timestamp("2025-01-01"), "không được chạm HOLDOUT"
    symbols = symbols or UNIVERSE_50
    regimes = regime_series(load_bars(BENCHMARK, end=end))
    fc_table = load_forecasts() if use_forecast else None
    strategies = [wrap_legacy(s) for s in
                  (MovingAverageCrossoverStrategy(), RSIStrategy(), BollingerBandsStrategy())]

    rows = []
    for sym in symbols:
        t0 = time.time()
        rows += symbol_rows(sym, load_bars(sym, end=end), regimes, strategies,
                            fc_table, use_forecast, start)
        logger.info(f"{sym}: xong sau {time.time() - t0:.1f}s, tổng {len(rows)} tín hiệu")
    return pd.DataFrame(rows)


def symbol_rows(sym, bars, regimes, strategies, fc_table=None, use_forecast=False, start=None):
    """Các dòng tín hiệu (feature + nhãn) của 1 mã, CHỈ từ `bars` được đưa vào.
    Nhãn cần giá t+5 trong `bars` -> tín hiệu ở 5 phiên cuối bị bỏ (không nhìn ra ngoài bars)."""
    rows = []
    close = bars["close"]
    start = pd.Timestamp(start) if start is not None else None
    for i in range(MIN_BARS, len(bars)):
        date = bars.index[i]
        if start is not None and date < start:
            continue
        window = bars.iloc[max(0, i + 1 - FEATURE_WINDOW):i + 1]  # nến <= date
        state = MarketState(sym, date, window, regime=str(regimes.get(date, "unknown")))
        if use_forecast and fc_table is not None:
            state.forecast = get_forecast(fc_table, sym, date)
        for strat in strategies:
            for sig in strat.generate_from_state(state):
                y = make_label(close, i, sig.action)
                if y is None:
                    continue  # chưa có giá t+5 trong vùng cho phép
                row = signal_features(sig, state, use_forecast)
                row.update(date=date, label_date=bars.index[i + HORIZON], symbol=sym,
                           strategy=sig.strategy, action=sig.action, regime=state.regime, y=y)
                rows.append(row)
    return rows


def walk_forward_windows(start: str = EVAL_START, end: str = DATA_END,
                         train_years: int = 3, test_months: int = 6):
    """Trả list (train_start, test_start, test_end) - test trượt mỗi `test_months` tháng."""
    out = []
    t = pd.Timestamp(start)
    while t <= pd.Timestamp(end):
        out.append((t - pd.DateOffset(years=train_years), t, t + pd.DateOffset(months=test_months)))
        t = t + pd.DateOffset(months=test_months)
    return out


def walk_forward_predict(table: pd.DataFrame, use_forecast: bool = False,
                         method: str = "isotonic") -> pd.DataFrame:
    """Dự đoán out-of-sample cho từng cửa sổ test. Thêm cột p_model, p_base, window."""
    parts = []
    for tr_start, te_start, te_end in walk_forward_windows():
        train = table[(table["date"] >= tr_start) & (table["label_date"] < te_start)]
        test = table[(table["date"] >= te_start) & (table["date"] < te_end)].copy()
        if test.empty or train["y"].nunique() < 2:
            continue
        cal = fit_calibrator(train, method=method, use_forecast=use_forecast)
        test["p_model"] = cal.predict_rows(test)
        test["p_base"] = cal.base_rate  # base rate lấy từ TRAIN (không nhìn test)
        # Base rate THEO HƯỚNG: tỷ lệ thắng của BUY và SELL riêng, cũng chỉ từ train
        by_dir = train.groupby("action")["y"].mean()
        test["p_base_dir"] = test["action"].map(by_dir).fillna(cal.base_rate)
        test["window"] = te_start.strftime("%Y-%m")
        test["n_train"] = len(train)
        parts.append(test)
    return pd.concat(parts, ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forecast", action="store_true", help="dùng forecast Chronos làm feature")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--method", default="isotonic", choices=["isotonic", "sigmoid"])
    ap.add_argument("--out", default="docs/ket-qua/calibration.md")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    from .calib_report import write_report

    table = build_signal_table(args.symbols, use_forecast=args.forecast)
    preds = walk_forward_predict(table, use_forecast=args.forecast, method=args.method)
    write_report(table, preds, args.out, use_forecast=args.forecast, method=args.method)
    logger.info(f"Đã ghi {args.out}")


if __name__ == "__main__":
    main()
