from datetime import datetime, timezone

import pandas as pd

from trader.broker.base import OrderRequest, OrderResult
from trader.data.binance_live import LiveBar
from trader.notify import Notifier
from trader.risk.guards import RiskGuard
from trader.runtime import RuntimeConfig, RuntimeEngine
from trader.storage import SQLiteStorage
from trader.strategy.base import Bar, Strategy, StrategyPosition


class AlwaysLongStrategy(Strategy):
    def on_bar(self, bar: Bar, position: StrategyPosition | None = None):  # type: ignore[override]
        pos = position or StrategyPosition()
        return "long" if pos.side == "flat" else "hold"


class HoldStrategy(Strategy):
    def on_bar(self, bar: Bar, position: StrategyPosition | None = None):  # type: ignore[override]
        return "hold"


class OneBarFeed:
    def __init__(self, *, symbol: str, close: float = 100.0) -> None:
        self.symbol = symbol
        self.close = float(close)

    def set_event_callback(self, callback):  # noqa: ANN001
        return None

    def iter_closed_bars(self, *, max_bars: int | None = None):  # noqa: ARG002
        ts = pd.Timestamp(datetime(2026, 1, 1, tzinfo=timezone.utc))
        yield LiveBar(
            timestamp=ts,
            open=self.close,
            high=self.close * 1.001,
            low=self.close * 0.999,
            close=self.close,
            volume=1.0,
            symbol=self.symbol,
        )

    def close(self) -> None:
        return None


class ProtectiveFailureBroker:
    def __init__(self) -> None:
        self.place_calls: list[OrderRequest] = []

    def place_order(self, request: OrderRequest) -> OrderResult:
        self.place_calls.append(request)
        order_type = str(request.order_type).upper()
        if not request.reduce_only and order_type == "MARKET":
            return OrderResult(
                order_id=f"ord-{len(self.place_calls)}",
                status="FILLED",
                filled_qty=float(request.amount),
                avg_price=100.0,
                fee=0.0,
                client_order_id=request.client_order_id,
            )
        if request.reduce_only and order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            return OrderResult(
                order_id=f"ord-{len(self.place_calls)}",
                status="REJECTED",
                filled_qty=0.0,
                avg_price=0.0,
                fee=0.0,
                message="protective create failed",
                client_order_id=request.client_order_id,
            )
        if request.reduce_only and order_type == "MARKET":
            return OrderResult(
                order_id=f"ord-{len(self.place_calls)}",
                status="FILLED",
                filled_qty=float(request.amount),
                avg_price=100.0,
                fee=0.0,
                client_order_id=request.client_order_id,
            )
        return OrderResult(
            order_id=f"ord-{len(self.place_calls)}",
            status="REJECTED",
            filled_qty=0.0,
            avg_price=0.0,
            fee=0.0,
            client_order_id=request.client_order_id,
        )

    def get_balance(self) -> dict[str, float]:
        return {"USDT": 10_000.0}


def _risk_guard() -> RiskGuard:
    return RiskGuard(
        max_order_notional=1_000_000.0,
        max_position_notional=1_000_000.0,
        max_daily_loss=1_000_000.0,
        max_drawdown_pct=0.99,
        max_atr_pct=1.0,
        account_allocation_pct=1.0,
        risk_per_trade_pct=0.0,
        daily_loss_limit_pct=0.99,
        consec_loss_limit=100,
        quiet_hours=None,
        capital_limit_usdt=None,
    )


def test_entry_protective_failure_triggers_emergency_close_and_halt(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "protective_entry_fail.db")
    try:
        broker = ProtectiveFailureBroker()
        engine = RuntimeEngine(
            config=RuntimeConfig(
                mode="live",
                symbol="BTC/USDT",
                timeframe="1m",
                fixed_notional_usdt=100.0,
                min_entry_notional_usdt=0.0,
                max_bars=1,
                halt_on_error=True,
                enable_protective_orders=True,
                protective_stop_loss_pct=0.01,
                protective_take_profit_pct=0.02,
                require_protective_orders=True,
                protective_integrity_retries=2,
                budget_guard_enabled=False,
                binance_env="testnet",
            ),
            strategy=AlwaysLongStrategy(),
            broker=broker,  # type: ignore[arg-type]
            feed=OneBarFeed(symbol="BTC/USDT"),  # type: ignore[arg-type]
            storage=storage,
            risk_guard=_risk_guard(),
            notifier=Notifier(),
            initial_equity=10_000.0,
        )

        result = engine.run()

        assert result["halted"] is True
        assert result["halt_reason"] == "protective_order_failed_emergency_close"
        assert engine.position_qty == 0.0
        emergency_calls = [
            req for req in broker.place_calls if req.reduce_only and str(req.order_type).upper() == "MARKET"
        ]
        assert len(emergency_calls) >= 1

        summary = storage.get_run_status(engine.run_id)
        risk_state = summary.get("risk_state", {})
        if isinstance(risk_state, dict) and isinstance(risk_state.get("BTC/USDT"), dict):
            risk_state = risk_state["BTC/USDT"]
        assert int(risk_state.get("protective_fail_count", 0)) == 1
    finally:
        storage.close()


def test_missing_protective_orders_retry_then_emergency_close_and_halt(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "protective_retry_fail.db")
    try:
        broker = ProtectiveFailureBroker()
        engine = RuntimeEngine(
            config=RuntimeConfig(
                mode="live",
                symbol="ETH/USDT",
                timeframe="1m",
                fixed_notional_usdt=100.0,
                min_entry_notional_usdt=0.0,
                max_bars=1,
                halt_on_error=True,
                enable_protective_orders=True,
                protective_stop_loss_pct=0.01,
                protective_take_profit_pct=0.02,
                require_protective_orders=True,
                protective_integrity_retries=2,
                budget_guard_enabled=False,
                binance_env="testnet",
            ),
            strategy=HoldStrategy(),
            broker=broker,  # type: ignore[arg-type]
            feed=OneBarFeed(symbol="ETH/USDT"),  # type: ignore[arg-type]
            storage=storage,
            risk_guard=_risk_guard(),
            notifier=Notifier(),
            initial_equity=10_000.0,
        )
        engine.position_qty = 1.0
        engine.position_entry_price = 100.0
        engine.position_entry_ts = "2026-01-01T00:00:00+00:00"

        result = engine.run()

        assert result["halted"] is True
        assert result["halt_reason"] == "protective_order_failed_emergency_close"
        assert engine.position_qty == 0.0
        protective_attempts = [
            req for req in broker.place_calls if req.reduce_only and str(req.order_type).upper() in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
        ]
        assert len(protective_attempts) == 4
        emergency_calls = [
            req for req in broker.place_calls if req.reduce_only and str(req.order_type).upper() == "MARKET"
        ]
        assert len(emergency_calls) == 1

        events = storage.list_recent_events_for_run(engine.run_id, limit=100)
        recreate_events = [evt for evt in events if evt.get("event_type") == "protective_orders_recreate_attempt"]
        assert len(recreate_events) == 2
    finally:
        storage.close()
