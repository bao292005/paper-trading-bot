"""Dự báo quantile của LỢI SUẤT LOG TÍCH LUỸ `horizon` ngày tới.

Ba mô hình cùng interface `Forecaster.predict_many(bars_by_symbol, asof, horizon)`:
  NaiveForecaster   : phân phối thực nghiệm của lợi suất horizon-ngày trong 252 ngày trước
  ChronosForecaster : Chronos-Bolt (chỉ dùng giá đóng cửa)
  KronosForecaster  : Kronos (dùng OHLCV, lấy mẫu nhiều lần)

Chống rò rỉ: mọi lớp tự cắt bars <= asof trước khi dùng.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from ..contracts import Forecast
from .cache import PROJECT_ROOT

logger = logging.getLogger("paper_bot")

QUANTILES = (0.1, 0.5, 0.9)
MIN_CONTEXT = 60  # ít hơn số nến này thì không dự báo (trả thiếu mã đó)


def make_forecast(model: str, horizon: int, values) -> Forecast:
    """Tạo Forecast từ 3 giá trị quantile; sắp xếp để q10 <= q50 <= q90 (tránh quantile bị chéo)."""
    q10, q50, q90 = sorted(float(v) for v in values)
    return Forecast(model=model, horizon_days=horizon, quantiles={0.1: q10, 0.5: q50, 0.9: q90})


class Forecaster(ABC):
    @abstractmethod
    def predict_many(self, bars_by_symbol: Dict[str, pd.DataFrame], asof,
                     horizon: int = 5) -> Dict[str, Forecast]:
        """Trả {symbol: Forecast}. Mã thiếu dữ liệu thì KHÔNG có trong kết quả."""

    @staticmethod
    def _cut(bars_by_symbol: Dict[str, pd.DataFrame], asof, min_rows: int) -> Dict[str, pd.DataFrame]:
        """Cắt nến <= asof, bỏ mã quá ít dữ liệu."""
        asof = pd.Timestamp(asof)
        out = {}
        for sym, bars in bars_by_symbol.items():
            cut = bars[bars.index <= asof]
            if len(cut) >= min_rows:
                out[sym] = cut
            else:
                logger.warning(f"{sym}: chỉ có {len(cut)} nến <= {asof.date()}, bỏ qua dự báo")
        return out


class NaiveForecaster(Forecaster):
    """Baseline: quantile thực nghiệm của lợi suất log horizon-ngày trong `lookback` ngày trước."""

    def __init__(self, lookback: int = 252):
        self.lookback = lookback

    def predict_many(self, bars_by_symbol, asof, horizon=5):
        result = {}
        for sym, bars in self._cut(bars_by_symbol, asof, MIN_CONTEXT + horizon).items():
            # mỗi phần tử = log(close_t / close_{t-horizon}), t chạy trong `lookback` ngày cuối
            ret = np.log(bars["close"]).diff(horizon).dropna().iloc[-self.lookback:]
            result[sym] = make_forecast("naive", horizon, np.quantile(ret, QUANTILES))
        return result


class ChronosForecaster(Forecaster):
    """Chronos-Bolt: dự báo quantile của GIÁ đóng cửa, rồi đổi sang lợi suất log so với giá cuối."""

    def __init__(self, model_name: str = "amazon/chronos-bolt-small", max_context: int = 512,
                 batch_size: int = 16):
        # import nặng để trong __init__ cho lazy
        import torch
        from chronos import BaseChronosPipeline

        self._torch = torch
        self.max_context = max_context
        self.batch_size = batch_size
        self.name = "chronos-bolt-small" if "small" in model_name else model_name
        self.pipeline = BaseChronosPipeline.from_pretrained(
            model_name, device_map="cpu", torch_dtype=torch.float32)

    def predict_many(self, bars_by_symbol, asof, horizon=5):
        data = self._cut(bars_by_symbol, asof, MIN_CONTEXT)
        symbols = list(data)
        result = {}
        for i in range(0, len(symbols), self.batch_size):
            chunk = symbols[i:i + self.batch_size]
            contexts = [self._torch.tensor(data[s]["close"].values[-self.max_context:], dtype=self._torch.float32)
                        for s in chunk]
            # quantiles: (batch, horizon, 3) là GIÁ tại từng ngày dự báo
            quantiles, _ = self.pipeline.predict_quantiles(
                contexts, prediction_length=horizon, quantile_levels=list(QUANTILES))
            for s, ctx, q in zip(chunk, contexts, quantiles):
                last_close = float(ctx[-1])
                price_q = q[horizon - 1].numpy()  # giá ở ngày cuối của horizon
                result[s] = make_forecast(self.name, horizon, np.log(price_q / last_close))
        return result


class KronosForecaster(Forecaster):
    """Kronos (vendor/kronos): sinh `n_samples` quỹ đạo OHLCV cho mỗi mã, lấy quantile của
    lợi suất log tích luỹ. KronosPredictor tự TRUNG BÌNH các mẫu nếu sample_count>1 nên ta để
    sample_count=1 và nhân bản mỗi chuỗi n_samples lần trong batch để có mẫu riêng lẻ."""

    def __init__(self, model_name: str = "NeoQuasar/Kronos-small",
                 tokenizer_name: str = "NeoQuasar/Kronos-Tokenizer-base", n_samples: int = 20,
                 max_context: int = 512, device: str = "cpu", max_batch: int = 64, seed: int = 0):
        import sys
        import torch
        from .cache import PROJECT_ROOT

        kronos_dir = str(PROJECT_ROOT / "vendor" / "kronos")
        if kronos_dir not in sys.path:
            sys.path.append(kronos_dir)
        from model import Kronos, KronosPredictor, KronosTokenizer  # từ vendor/kronos

        self._torch = torch
        self.n_samples, self.max_context, self.max_batch, self.seed = n_samples, max_context, max_batch, seed
        self.name = "kronos-small" if "small" in model_name else model_name
        tokenizer = KronosTokenizer.from_pretrained(tokenizer_name)
        model = Kronos.from_pretrained(model_name)
        self.predictor = KronosPredictor(model, tokenizer, device=device, max_context=max_context)

    def predict_many(self, bars_by_symbol, asof, horizon=5):
        data = self._cut(bars_by_symbol, asof, MIN_CONTEXT)
        if not data:
            return {}
        # predict_batch đòi mọi chuỗi cùng độ dài -> dùng độ dài chung (tối đa max_context)
        n = min(self.max_context, min(len(b) for b in data.values()))
        per_chunk = max(1, self.max_batch // self.n_samples)  # số mã mỗi lượt
        symbols = list(data)
        result = {}
        self._torch.manual_seed(self.seed)
        for i in range(0, len(symbols), per_chunk):
            chunk = symbols[i:i + per_chunk]
            dfs, xts, yts = [], [], []
            for s in chunk:
                b = data[s].iloc[-n:]
                future = pd.bdate_range(b.index[-1] + pd.Timedelta(days=1), periods=horizon)
                for _ in range(self.n_samples):  # nhân bản để có mẫu riêng lẻ
                    dfs.append(b[["open", "high", "low", "close", "volume"]])
                    xts.append(pd.Series(b.index))
                    yts.append(pd.Series(future))
            preds = self.predictor.predict_batch(dfs, xts, yts, pred_len=horizon,
                                                 sample_count=1, verbose=False)
            for j, s in enumerate(chunk):
                samples = preds[j * self.n_samples:(j + 1) * self.n_samples]
                final_close = np.array([p["close"].iloc[horizon - 1] for p in samples])
                last_close = float(data[s]["close"].iloc[-1])
                ret = np.log(np.clip(final_close, 1e-6, None) / last_close)
                result[s] = make_forecast(self.name, horizon, np.quantile(ret, QUANTILES))
        return result


# ---------- Dự báo tính sẵn (để backtest không phải chạy lại model) ----------

FORECAST_DIR = PROJECT_ROOT / "data" / "forecasts"
CHRONOS_H5_PATH = FORECAST_DIR / "chronos_h5.parquet"  # cột: date, symbol, q10, q50, q90


def load_forecasts(path: Optional[Path] = None) -> pd.DataFrame:
    """Đọc bảng dự báo tính sẵn. Index (symbol, date), cột q10, q50, q90. Chưa có file -> bảng rỗng."""
    path = Path(path or CHRONOS_H5_PATH)
    if not path.exists():
        return pd.DataFrame(columns=["q10", "q50", "q90"],
                            index=pd.MultiIndex.from_arrays([[], []], names=["symbol", "date"]))
    return pd.read_parquet(path).set_index(["symbol", "date"]).sort_index()


def get_forecast(table: pd.DataFrame, symbol: str, asof, model: str = "chronos-bolt-small",
                 horizon: int = 5) -> Optional[Forecast]:
    """Dự báo của `symbol` tính ĐÚNG tại ngày asof (đã tính chỉ từ dữ liệu <= asof). Không có -> None."""
    key = (symbol, pd.Timestamp(asof).normalize())
    if key not in table.index:
        return None
    row = table.loc[key]
    return make_forecast(model, horizon, [row["q10"], row["q50"], row["q90"]])
