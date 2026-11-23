"""P3 - Khớp lệnh: file DUY NHẤT của phần rủi ro nói chuyện với Alpaca.

- AlpacaExecutor.submit: OrderIntent -> lệnh Alpaca (bracket nếu có stop-loss + take-profit)
- KillSwitch: ngắt khi lỗ trong ngày / sụt từ đỉnh vượt ngưỡng
- AlpacaExecutor.reconcile: đối soát broker với DB
"""

import logging
from typing import Optional, Tuple

from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from .contracts import OrderIntent
from .risk import RiskConfig

logger = logging.getLogger("paper_bot")


class KillSwitch:
    """Công tắc ngắt: hàm kiểm tra THUẦN (chỉ so số), không gọi broker."""

    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg

    def check(self, equity: float, day_start_equity: float, peak_equity: float) -> Tuple[bool, str]:
        """(tripped, lý do). Lỗ trong ngày > max_daily_loss_pct hoặc sụt từ đỉnh > max_drawdown_pct."""
        if day_start_equity > 0:
            daily_loss = (day_start_equity - equity) / day_start_equity
            if daily_loss > self.cfg.max_daily_loss_pct:
                return True, f"lỗ trong ngày {daily_loss:.2%} > {self.cfg.max_daily_loss_pct:.2%}"
        if peak_equity > 0:
            drawdown = (peak_equity - equity) / peak_equity
            if drawdown > self.cfg.max_drawdown_pct:
                return True, f"sụt từ đỉnh {drawdown:.2%} > {self.cfg.max_drawdown_pct:.2%}"
        return False, "ok"


class AlpacaExecutor:
    def __init__(self, trading_client, store, cfg: Optional[RiskConfig] = None):
        self.client = trading_client
        self.store = store
        self.kill_switch = KillSwitch(cfg or RiskConfig())
        self.halted = False  # True sau khi kill switch ngắt: chặn lệnh MUA mới

    # ---- kill switch ----
    def check_kill_switch(self, equity: float, day_start_equity: float, peak_equity: float) -> bool:
        """Kiểm tra; nếu vừa ngắt thì huỷ các lệnh MUA đang chờ và chặn mua mới.
        Giữ nguyên stop-loss/take-profit của vị thế đã nắm, KHÔNG tự bán. Trả True nếu đang bị ngắt."""
        tripped, why = self.kill_switch.check(equity, day_start_equity, peak_equity)
        if tripped and not self.halted:
            self.halted = True
            cancelled, kept = self._cancel_pending_buys()
            self.store.log_event(
                "kill_switch",
                f"NGẮT: {why}. Đã huỷ {len(cancelled)} lệnh MUA chờ, giữ nguyên {kept} lệnh còn lại "
                f"(stop-loss/take-profit bảo vệ vị thế), không tự bán, chặn lệnh mua mới",
                level="CRITICAL", data={"equity": equity, "day_start": day_start_equity,
                                        "peak": peak_equity, "cancelled": cancelled, "kept": kept})
            logger.critical(f"KILL SWITCH: {why}")
        return self.halted

    def _cancel_pending_buys(self) -> Tuple[list, int]:
        """Huỷ lệnh MUA đang chờ (kể cả bracket cha chưa khớp -> lệnh con cũng mất theo).
        Lệnh bán (stop-loss/take-profit của vị thế đã nắm) được giữ. Trả (danh sách id đã huỷ, số lệnh giữ)."""
        open_orders = self.client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        cancelled, kept = [], 0
        for o in open_orders:
            if str(getattr(o.side, "value", o.side)).lower() != "buy":
                kept += 1
                continue
            try:
                self.client.cancel_order_by_id(str(o.id))
                cancelled.append(str(o.id))
            except APIError as e:  # vd lệnh vừa khớp xong: ghi lại, không dừng
                kept += 1
                self.store.log_event("cancel_error", f"{o.symbol} {o.id}: {e}", level="ERROR")
        return cancelled, kept

    def resume(self) -> None:
        """Gỡ chặn bằng tay (hoặc đầu ngày mới sau khi người dùng xem lại)."""
        self.halted = False
        self.store.log_event("kill_switch", "Đã gỡ chặn (resume)")

    # ---- đặt lệnh ----
    def _build_request(self, intent: OrderIntent):
        side = OrderSide(intent.side.lower())
        is_bracket = intent.side.lower() == "buy" and intent.stop_loss and intent.take_profit
        kwargs = dict(symbol=intent.symbol, qty=intent.qty, side=side,
                      time_in_force=TimeInForce.GTC if is_bracket else TimeInForce.DAY)
        if is_bracket:
            kwargs.update(order_class=OrderClass.BRACKET,
                          take_profit=TakeProfitRequest(limit_price=round(intent.take_profit, 2)),
                          stop_loss=StopLossRequest(stop_price=round(intent.stop_loss, 2)))
        if intent.order_type == "limit":
            return LimitOrderRequest(limit_price=round(intent.limit_price, 2), **kwargs)
        return MarketOrderRequest(**kwargs)

    def submit(self, intent: OrderIntent) -> Optional[str]:
        """Gửi lệnh, ghi vào bảng orders. Trả id lệnh của broker, hoặc None nếu bị chặn/lỗi."""
        if intent.qty <= 0:
            return None
        if intent.side.lower() == "buy" and self.halted:
            self.store.log_event("order_blocked", f"Kill switch đang ngắt: chặn MUA {intent.symbol}",
                                 level="WARNING")
            self.store.log_order(intent, status="blocked")
            return None
        try:
            order = self.client.submit_order(self._build_request(intent))
        except APIError as e:
            logger.error(f"Đặt lệnh thất bại {intent.symbol}: {e}")
            self.store.log_order(intent, status="rejected")
            self.store.log_event("order_error", f"{intent.symbol}: {e}", level="ERROR")
            return None
        broker_id = str(order.id)
        self.store.log_order(intent, broker_order_id=broker_id, status="submitted")
        logger.info(f"Đã đặt lệnh {intent.side} {intent.qty} {intent.symbol} ({intent.order_type}), id={broker_id}")
        return broker_id

    # ---- đối soát ----
    def reconcile(self, store=None) -> list:
        """So broker với DB (cùng run_id), trả danh sách chênh lệch [{kind, ...}] và ghi event.
        - unknown_order: broker đang chờ lệnh mà DB không có
        - missing_order: DB ghi 'submitted' nhưng broker không còn chờ và cũng không có fill
        - position_mismatch: vị thế broker khác vị thế suy từ bảng fills (cần fills được ghi đầy đủ)"""
        store = store or self.store
        run, q = store.run_id, store.conn.execute
        diffs = []

        # Lệnh chờ trên broker (nested=True: stop/TP con gộp vào lệnh cha, không bị tính là lệnh lạ)
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)
        open_ids = {str(o.id) for o in self.client.get_orders(filter=request)}
        db_ids = {r[0] for r in q("SELECT broker_order_id FROM orders WHERE run_id=? AND broker_order_id!=''", (run,))}
        pending = {r[0]: r[1] for r in q("SELECT broker_order_id, symbol FROM orders WHERE run_id=? "
                                         "AND status='submitted' AND broker_order_id!=''", (run,))}
        filled_ids = {r[0] for r in q("SELECT order_id FROM fills WHERE run_id=?", (run,))}

        for oid in sorted(open_ids - db_ids):
            diffs.append({"kind": "unknown_order", "order_id": oid})
        for oid, symbol in pending.items():
            if oid not in open_ids and oid not in filled_ids:
                diffs.append({"kind": "missing_order", "order_id": oid, "symbol": symbol})

        # Vị thế: broker vs tổng mua - bán trong bảng fills
        expected: dict = {}
        for symbol, side, qty in q("SELECT symbol, side, qty FROM fills WHERE run_id=?", (run,)):
            expected[symbol] = expected.get(symbol, 0.0) + (qty if side.lower() == "buy" else -qty)
        actual = {p.symbol: float(p.qty) for p in self.client.get_all_positions()}
        for symbol in sorted(set(expected) | set(actual)):
            exp, act = expected.get(symbol, 0.0), actual.get(symbol, 0.0)
            if abs(exp - act) > 1e-6:
                diffs.append({"kind": "position_mismatch", "symbol": symbol, "db_qty": exp, "broker_qty": act})

        store.log_event("reconcile", f"Đối soát: {len(diffs)} chênh lệch",
                        level="WARNING" if diffs else "INFO", data={"diffs": diffs})
        return diffs
