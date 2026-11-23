"""Ghi kết quả đánh giá calibration ra markdown (docs/ket-qua/calibration.md)."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .calibration import brier, reliability_table


def md_table(df: pd.DataFrame, fmt: str = "{:.4f}") -> str:
    """DataFrame -> bảng markdown (không cần thư viện tabulate)."""
    def cell(v):
        if isinstance(v, (float, np.floating)):
            return "—" if np.isnan(v) else fmt.format(v)
        return str(v)

    lines = ["| " + " | ".join(map(str, df.columns)) + " |",
             "|" + "---|" * len(df.columns)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(cell(v) for v in r.values) + " |")
    return "\n".join(lines)


def summary(preds: pd.DataFrame) -> dict:
    y = preds["y"]
    raw = preds["confidence"].clip(0, 1)
    b_model, b_base, b_raw = brier(preds["p_model"], y), brier(preds["p_base"], y), brier(raw, y)
    b_dir = brier(preds["p_base_dir"], y)
    return {"n": len(preds), "y_rate": float(y.mean()), "b_model": b_model, "b_base": b_base,
            "b_raw": b_raw, "b_dir": b_dir, "skill": 1 - b_model / b_base,
            "skill_dir": 1 - b_model / b_dir}


def write_report(table: pd.DataFrame, preds: pd.DataFrame, out: str, use_forecast: bool,
                 method: str) -> None:
    s = summary(preds)
    per_window = preds.groupby("window").apply(lambda g: pd.Series({
        "n_train": int(g["n_train"].iloc[0]), "n_test": len(g), "y_rate": g["y"].mean(),
        "brier_model": brier(g["p_model"], g["y"]), "brier_base": brier(g["p_base"], g["y"]),
        "brier_base_dir": brier(g["p_base_dir"], g["y"])}),
        include_groups=False).reset_index()
    per_strat = preds.groupby(["strategy", "action"]).apply(lambda g: pd.Series({
        "n": len(g), "conf_tb": g["confidence"].mean(), "y_rate": g["y"].mean(),
        "p_model_tb": g["p_model"].mean(), "brier_model": brier(g["p_model"], g["y"]),
        "brier_base": brier(g["p_base"], g["y"]), "brier_base_dir": brier(g["p_base_dir"], g["y"])}),
        include_groups=False).reset_index()
    # cột đếm: ép về int để in không có phần thập phân
    per_window[["n_train", "n_test"]] = per_window[["n_train", "n_test"]].astype(int)
    per_strat["n"] = per_strat["n"].astype(int)
    rel_model = reliability_table(preds["p_model"], preds["y"])
    rel_raw = reliability_table(preds["confidence"].clip(0, 1), preds["y"])

    pass_mask = preds["p_model"] >= 0.5
    thr = pd.DataFrame([
        {"tập": "mọi tín hiệu (bài gốc: conf ≥ 0.5 gần như luôn đạt)", "n": len(preds),
         "tỷ lệ thắng": preds["y"].mean()},
        {"tập": "p_model ≥ 0.5", "n": int(pass_mask.sum()),
         "tỷ lệ thắng": preds.loc[pass_mask, "y"].mean() if pass_mask.any() else float("nan")},
    ])

    def verdict(b_other, name):
        return (f"Mô hình có Brier THẤP hơn {name}" if s["b_model"] < b_other
                else f"Mô hình KHÔNG tốt hơn {name} (Brier cao hơn hoặc bằng)")

    text = f"""# Kết quả hiệu chỉnh xác suất (P2)

Sinh tự động bởi `uv run python -m paper_bot.decision.calib_history` ngày {date.today()}.
File này bị GHI ĐÈ mỗi lần chạy. Kết luận viết tay ở [calibration-ketluan.md](calibration-ketluan.md).

## Thiết lập
- Tín hiệu: MA_Crossover(10/30), RSI(14), BollingerBands(20,2) của bài gốc, chạy mỗi ngày trên {table['symbol'].nunique()} mã.
- Nhãn: y=1 nếu lợi suất log close(t)→close(t+5) theo hướng tín hiệu > 0.002 (phí 2 chiều).
- Feature: confidence thô, chiến lược (one-hot), BUY/SELL, regime (one-hot), RSI, khoảng cách MA50/MA200, lợi suất 5/20 ngày, vol20
  (các feature giá nhân với hướng tín hiệu). Forecast: **{"CÓ" if use_forecast else "KHÔNG"}** (bật bằng `--forecast`).
- Mô hình: StandardScaler + LogisticRegression, hiệu chỉnh `{method}` (CalibratedClassifierCV, cv=3) CHỈ trên train của từng cửa sổ.
- Walk-forward: train 3 năm trước, test 6 tháng, trượt 6 tháng, test 2019-01 → 2024-12. Train chỉ gồm tín hiệu có ngày nhãn (t+5) < ngày bắt đầu test.
- Dữ liệu chỉ tới 2024-12-31 (không chạm HOLDOUT). Bảng tín hiệu từ 2016 (2016–2018 chỉ làm train cho cửa sổ đầu).
- Base rate: tỷ lệ y=1 trên TRAIN của cùng cửa sổ (không nhìn test).
- Base rate theo hướng: tỷ lệ y=1 của BUY và của SELL riêng, cũng lấy từ TRAIN.

## Tổng hợp (out-of-sample, {s['n']} tín hiệu)
| Dự đoán | Brier |
|---|---|
| Mô hình hiệu chỉnh | {s['b_model']:.4f} |
| Base rate (train) | {s['b_base']:.4f} |
| Base rate theo hướng BUY/SELL (train) | {s['b_dir']:.4f} |
| Confidence thô của bài gốc (coi như xác suất) | {s['b_raw']:.4f} |

Tỷ lệ thắng thực tế (test): {s['y_rate']:.3f}. Brier skill score (> 0 là tốt hơn):
so với base rate {s['skill']:+.4f}; so với base rate theo hướng {s['skill_dir']:+.4f}.
- **{verdict(s['b_base'], 'base rate')}.**
- **{verdict(s['b_dir'], 'base rate theo hướng')}.**

## Theo cửa sổ walk-forward
{md_table(per_window)}

## Theo chiến lược / hướng
{md_table(per_strat)}

## Reliability – mô hình hiệu chỉnh (10 bin)
{md_table(rel_model)}

## Reliability – confidence thô của bài gốc
{md_table(rel_raw)}

## Ngưỡng 0.5
{md_table(thr)}
"""
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
