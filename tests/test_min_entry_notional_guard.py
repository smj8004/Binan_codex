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


class ExitIfLongStrategy(Strategy):
    def on_bar(self, bar: Bar, position: StrategyPosition | None = None):  # type: ignore[override]
        pos = position or StrategyPosition()
        return "exit" if pos.side == "long" else "hold"


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


class CaptureBroker:
    def __init__(self) -> None:
        self.place_calls: list[OrderRequest] = []

    def place_order(self, request: OrderRequest) -> OrderResult:
        self.place_calls.append(request)
        return OrderResult(
            order_id=f"ord-{len(self.place_calls)}",
            status="FILLED",
            filled_qty=float(request.amount),
            avg_price=100.0,
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


def test_min_entry_notional_blocks_small_entry(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "min_entry_notional_block.db")
    try:
        broker = CaptureBroker()
        engine = RuntimeEngine(
            config=RuntimeConfig(
                mode="live",
                symbol="BTC/USDT",
                timeframe="1m",
                fixed_notional_usdt=100.0,
                min_entry_notional_usdt=250.0,
                max_bars=1,
                halt_on_error=True,
                enable_protective_orders=False,
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

        assert result["halted"] is False
        assert len(broker.place_calls) == 0
        events = storage.list_recent_events_for_run(engine.run_id, limit=30)
        blocked = [evt for evt in events if evt.get("event_type") == "entry_notional_below_floor"]
        assert blocked
        payload = blocked[0].get("payload") or {}
        assert float(payload.get("requested_notional", 0.0)) == 100.0
        assert float(payload.get("min_entry_notional", 0.0)) == 250.0
    finally:
        storage.close()


def test_min_entry_notional_allows_reduce_only(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "min_entry_notional_reduce_only.db")
    try:
        broker = CaptureBroker()
        engine = RuntimeEngine(
            config=RuntimeConfig(
                mode="live",
                symbol="ETH/USDT",
                timeframe="1m",
                fixed_notional_usdt=100.0,
                min_entry_notional_usdt=250.0,
                max_bars=1,
                halt_on_error=True,
                enable_protective_orders=False,
                budget_guard_enabled=False,
                binance_env="testnet",
            ),
            strategy=ExitIfLongStrategy(),
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

        assert result["halted"] is False
        assert len(broker.place_calls) == 1
        assert broker.place_calls[0].reduce_only is True
    finally:
        storage.close()
