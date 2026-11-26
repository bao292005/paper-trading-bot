"""Hiệu chỉnh xác suất: confidence gán cứng của bài gốc -> xác suất thắng thật sự (sau phí).

Nhãn y = 1 nếu lợi suất log close(t) -> close(t+5) THEO HƯỚNG tín hiệu > 0.002 (phí 2 chiều).
Mô hình: StandardScaler + LogisticRegression, bọc CalibratedClassifierCV (isotonic/Platt)
-> mọi thứ fit CHỈ trên dữ liệu train được truyền vào.

Bảng dữ liệu lịch sử + đánh giá walk-forward: xem calib_history.py.
"""

from typing import Dict, Optional

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..contracts import REGIMES, MarketState, Signal
from ..data.features import compute_features

COST = 0.002
HORIZON = 5
STRATEGIES = ("MA_Crossover", "RSI", "BollingerBands")  # chiến lược calibrator biết
FEATURE_WINDOW = 260  # số nến cuối đủ cho MA200 + chỉ báo


def dist_ma(close: pd.Series, window: int) -> float:
    """close / MA(window) - 1 ở dòng cuối; thiếu dữ liệu -> NaN. (tự tính, không sửa file P1)"""
    if len(close) < window:
        return float("nan")
    return float(close.iloc[-1] / close.iloc[-window:].mean() - 1)


def feature_names(use_forecast: bool = False):
    """Tên cột feature (phải khớp với signal_features)."""
    names = ["confidence", "is_buy", "rsi_dir", "dist_ma50_dir", "dist_ma200_dir",
             "ret5_dir", "ret20_dir", "vol20"]
    names += [f"strat_{s}" for s in STRATEGIES] + [f"regime_{r}" for r in REGIMES]
    if use_forecast:
        names += ["has_fc", "fc_median_dir", "fc_width"]
    return names


def signal_features(sig: Signal, state: MarketState, use_forecast: bool = False) -> Dict[str, float]:
    """Feature của 1 tín hiệu. Chỉ dùng state.bars (<= asof) nên không rò rỉ.

    Các feature giá được nhân với hướng d (+1 BUY, -1 SELL) để mô hình tuyến tính hiểu
    "thuận/ngược hướng tín hiệu" (vd giá trên MA là tốt cho BUY nhưng xấu cho SELL).
    Thiếu giá trị -> điền 0 (trung tính).
    """
    d = 1.0 if sig.action == "BUY" else -1.0
    bars = state.bars.iloc[-FEATURE_WINDOW:]
    f = compute_features(bars)

    def get(name, default=0.0):
        v = f.get(name, default)
        return default if v is None or not np.isfinite(v) else float(v)

    ma200 = dist_ma(bars["close"], 200)
    row = {
        "confidence": float(sig.confidence),
        "is_buy": float(d > 0),
        "rsi_dir": (get("rsi14", 50.0) - 50.0) / 50.0 * d,
        "dist_ma50_dir": get("dist_ma50") * d,
        "dist_ma200_dir": (0.0 if np.isnan(ma200) else ma200) * d,
        "ret5_dir": get("ret5") * d,
        "ret20_dir": get("ret20") * d,
        "vol20": get("vol20", 0.015),
    }
    for s in STRATEGIES:
        row[f"strat_{s}"] = float(sig.strategy == s)
    for r in REGIMES:
        row[f"regime_{r}"] = float(state.regime == r)
    if use_forecast:
        fc = state.forecast
        # forecast NaN (vd crypto thiếu context) -> coi như không có forecast
        if fc is not None and not (np.isfinite(fc.median) and np.isfinite(fc.width)):
            fc = None
        row["has_fc"] = float(fc is not None)
        row["fc_median_dir"] = fc.median * d if fc is not None else 0.0
        row["fc_width"] = fc.width if fc is not None else 0.0
    return row


def make_label(close: pd.Series, i: int, action: str, horizon: int = HORIZON,
               cost: float = COST) -> Optional[int]:
    """Nhãn cho tín hiệu tại vị trí i. Chưa đủ `horizon` ngày phía sau -> None."""
    if i + horizon >= len(close):
        return None
    d = 1.0 if action == "BUY" else -1.0
    r = float(np.log(close.iloc[i + horizon] / close.iloc[i]))
    return int(d * r > cost)


class Calibrator:
    def __init__(self, model, feature_names, use_forecast: bool, base_rate: float):
        self.model = model
        self.feature_names = list(feature_names)
        self.use_forecast = use_forecast
        self.base_rate = base_rate

    def knows(self, sig: Signal) -> bool:
        return sig.strategy in STRATEGIES

    def predict_rows(self, rows: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(rows[self.feature_names].astype(float))[:, 1]

    def predict_prob(self, sig: Signal, state: MarketState) -> float:
        feats = signal_features(sig, state, self.use_forecast)
        return float(self.predict_rows(pd.DataFrame([feats]))[0])


def fit_calibrator(train_rows: pd.DataFrame, method: str = "isotonic",
                   use_forecast: bool = False, cv: int = 3) -> Calibrator:
    """Fit trên bảng train (cột feature + cột 'y'). method: 'isotonic' hoặc 'sigmoid' (Platt).

    CalibratedClassifierCV chia train thành `cv` phần: fit LR trên phần này, hiệu chỉnh trên
    phần còn lại -> không đụng dữ liệu test.
    """
    names = feature_names(use_forecast)
    X = train_rows[names].astype(float)
    y = train_rows["y"].astype(int)
    base = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    model = CalibratedClassifierCV(base, method=method, cv=cv)
    model.fit(X, y)
    return Calibrator(model, names, use_forecast, float(y.mean()))


# ---------- Đánh giá ----------

def brier(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def reliability_table(p, y, bins: int = 10) -> pd.DataFrame:
    """Chia [0,1] thành `bins` khoảng đều; mỗi khoảng: số mẫu, xác suất dự đoán TB, tần suất thật."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        rows.append({"bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}", "n": int(m.sum()),
                     "p_mean": float(p[m].mean()) if m.any() else float("nan"),
                     "y_rate": float(y[m].mean()) if m.any() else float("nan")})
    return pd.DataFrame(rows)
