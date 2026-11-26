# Nhật ký Strategy Tester kiểu MT5 (agent j-tester)

## 2026-11-26 — Bước 1: báo cáo kiểu MT5
- File mới: `src/paper_bot/backtest/tester/__init__.py`, `tester/report.py`, `tests/test_tester_report.py`.
- `balance_curve(trades, initial, days)`: balance chỉ đổi ở ngày ĐÓNG lệnh (P/L sau phí cộng dồn);
  equity lấy từ engine (mark-to-market ở close). Giống phân biệt balance/equity của MT5.
- `mt5_report(result, initial_cash) -> dict` (JSON chuẩn, không NaN/inf; không xác định -> `null`):
  net/gross profit/loss, profit factor, expected payoff, recovery factor (= net / equity DD maximal),
  Sharpe, balance & equity drawdown (absolute / maximal $ + % / relative % + $), số trade long/short,
  profit/loss trades, largest/average, chuỗi thắng/thua liên tiếp (theo số lệnh và theo $),
  thời gian giữ trung bình, tổng phí. `report_markdown(rep)` in bảng 2 cột.
- Nhận cả BacktestResult lẫn WalkForwardResult.
- Kế thừa: dùng `metrics.sharpe/daily_returns` (prereg mục 6), `Trade.pnl` của engine.
- Khác MT5 (đã ghi trong docstring): Sharpe năm hoá theo ngày (không theo deal); balance gộp theo ngày;
  lệnh còn mở cuối kỳ (`end`) tính như đóng ở close cuối trừ phí ước tính; không có lệnh short.
- Test: 6 test tính tay (balance curve, maximal vs relative DD, chuỗi thắng/thua, toàn bộ số của
  một bộ 5 trade, trường hợp 0 trade / 0 lệnh lỗ, khớp với run_backtest thật) — pass.

## 2026-11-26 — Bước 2: run_tester (một lần chạy)
- File mới: `tester/run.py`, `tester/strategies.py`, `tester/recorder.py`, `tests/test_tester_run.py`,
  `tests/test_tester_full_params.py`.
- Sửa nhỏ (chỉ thêm): `decision/full_policy.py` thêm `strategy_params=None` (ma_short, ma_long,
  rsi_period, rsi_oversold, rsi_overbought) áp cho cả chiến lược giao dịch và bảng fit calibrator;
  khoá calib_cache chỉ thêm phần tham số khi có (khoá cũ giữ nguyên). None/mặc định -> lệnh y hệt cũ (có test).
- `run_tester(config, bars=None, forecasts=None) -> dict`: config + report MT5 + curve (equity/balance/
  cash/exposure) + trades (giá, qty, P/L, phí, SL/TP lúc vào, lý do vào/ra) + replay theo ngày
  (orders quyết định ở close, fills ở open hôm đó kèm lý do, levels = vị thế mở + SL/TP cuối ngày,
  equity, balance) + per_window (walkforward) + timing.
- Chống rò rỉ / holdout: `to >= 2025-01-01` -> HoldoutError (dùng `walkforward.check_holdout`,
  không có cách bật holdout trong tester). Nến, SPY, forecast bị cắt `<= to` trước khi chạy.
  Test: sửa dữ liệu sau `to` (cả nến, SPY, forecast với FULL) -> kết quả giống hệt.
- B2_goc có tham số: `baselines.make_b2` chỉ đọc và cố định MA 10/30 nên viết `make_b2_params`
  cùng logic (dùng lại `_fast_ma/_fast_rsi`); mặc định cho fill giống hệt make_b2 (có test).
  history_days tự nới = max(100, 1.5*ma_long+20) ngày lịch để MA dài (vd 100) vẫn có tín hiệu.
- mode "single": FULL fit calibrator 1 lần trên 3 năm trước `from` (giống `full_policy.main`), không
  fit lại mỗi 6 tháng như walkforward.
- Hiệu năng thật (cache, 10 mã đầu UNIVERSE_50, 2019–2024, single): B2 **2.0 s** (311 trade);
  FULL 21.7 s (98 trade; chậm do fit calibrator + pipeline từng mã).
- Test: 8 test run + 4 test strategy_params pass; toàn bộ suite 337 pass. (ruff không có trong môi trường.)
- Hạn chế: Trade của engine không lưu SL/TP nên lấy từ ảnh chụp vị thế ngày vào lệnh; lý do vào/ra
  là lý do của lệnh gần nhất trước ngày khớp; nếu mua thêm vào vị thế đang mở thì SL/TP là của lần đầu.

## 2026-11-26 — Bước 3: fill model "minute"
- `engine.py` (chỉ thêm): `run_backtest(..., fill_model="daily_ohlc"|"minute", minute_loader=None)`;
  `BacktestResult.fill_stats` = {fill_model, ambiguous, minute_sl, minute_tp, fallback}.
  Ngày mơ hồ = open nằm giữa SL/TP và low<=SL, high>=TP. Chỉ ngày đó mới tải nến 1m (lazy,
  `data.intraday.load_intraday`); `first_hit_in_minutes`: duyệt từng phút, cùng phút -> SL trước,
  gap trong phút -> khớp ở open phút. Thiếu dữ liệu/lỗi tải -> fallback quy tắc cũ (SL trước) + warning.
  `minute` với `end >= 2025-01-01` -> ValueError. Mặc định giữ nguyên kết quả cũ (toàn bộ test cũ pass).
- `tester/fillcompare.py`: `compare_fill_models(config)` chạy 2 lần, trả % ngày mơ hồ, chỉ số, trade đổi kết cục.
- Test `tests/test_tester_fill_model.py` (8, nến phút tổng hợp).
- So sánh thật (FULL, 10 mã, 2019–2024): với SL/TP mặc định (2/3 ATR) và (1/1.5) có **0 ngày mơ hồ**;
  phải ép SL/TP hẹp (0.5/0.75 ATR) mới có **1 ngày** (0.007% số mã-ngày, ~1% số lệnh thoát SL/TP):
  GOOGL 2020-10-19, daily ghi stop_loss −152$, minute thấy TP chạm trước -> +93$. Net −1735 -> −1482,
  Sharpe −0.44 -> −0.38. Kết luận: với cấu hình hiện tại giả định "SL trước" gần như không làm lệch kết quả.

## 2026-11-26 — Bước 4: optimize
- `run_tester(..., detail=False)`: bỏ curve/trades/replay (nhanh hơn cho optimize).
- `tester/optimize.py`: `run_optimize(config, grid, criterion, is_period, oos_period, n_jobs, progress,
  heatmap_params)`. Tên tham số không tiền tố -> strategy_params, `risk.x`/`flags.x` -> mục tương ứng.
  >500 tổ hợp -> ValueError; holdout/khoá lạ kiểm tra trước khi chạy; tổ hợp lỗi (vd ma_short>=ma_long)
  thành dòng `error`, xếp cuối. Tiêu chí sharpe|net_profit|profit_factor|recovery_factor. Mỗi dòng có
  metric IS + OOS, is_rank, oos_rank; `best_is` kèm `oos_rank`; heatmap [y][x] cho IS và OOS (còn tham
  số khác thì lấy max); `note` cảnh báo overfit. ProcessPoolExecutor (mặc định CPU−1), n_jobs=1 chạy tuần tự.
- Test `tests/test_tester_optimize.py` (4: giới hạn/ghép tham số, khớp chạy tay + JSON + heatmap,
  song song == tuần tự, tổ hợp lỗi + holdout + tiêu chí sai).
- Demo thật: `docs/ket-qua/tester-optimize-demo.md` (B2, 9 tổ hợp, 10.7 s). IS Sharpe 0.24–0.38 sát nhau,
  hạng IS/OOS tương quan yếu (ρ≈0.52, n=9); không có bằng chứng nên đổi mặc định 10/30.

## 2026-11-26 — Bước 5: CLI
- `scripts/tester.py run|optimize <cấu hình.json> [--out tiền-tố] [--jobs N]` -> `<tiền-tố>.json` + `.md`
  (run: bảng MT5 + fill_stats; optimize: bảng tổ hợp, best IS + hạng OOS, 2 heatmap, cảnh báo overfit).
- Test `tests/test_tester_cli.py` (2, thay run_tester/run_optimize bằng bản offline). Chạy thật B2 10 mã
  2019–2024: khớp số ở bước 2 (311 trade, net +5882).
- `tester/__init__.py` xuất thêm `run_optimize`, `compare_fill_models` cho agent web.
- Toàn bộ suite (chạy lại sau khi tiếp tục): **373 passed**. ruff không có trong môi trường nên chưa lint.
