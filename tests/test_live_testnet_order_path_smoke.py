from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from trader.broker.base import OrderRequest, OrderResult
from trader.data.binance_live import LiveBar
from trader.notify import Notifier
from trader.risk.guards import RiskGuard
from trader.runtime import AccountBudgetGuard, RuntimeConfig, RuntimeEngine
from trader.storage import SQLiteStorage
from trader.strategy.base import Bar, Strategy, StrategyPosition


class AlwaysLongStrategy(Strategy):
    def on_bar(self, bar: Bar, position: StrategyPosition | None = None):  # type: ignore[override]
        pos = position or StrategyPosition()
        return "long" if pos.side == "flat" else "hold"


class DummyFeed:
    def set_event_callback(self, callback):  # noqa: ANN001
        return None

    def iter_closed_bars(self, *, max_bars: int | None = None):  # noqa: ARG002
        if False:
            yield None
        return

    def close(self) -> None:
        return None


class SharedBudgetBroker:
    def __init__(self, *, available_balance: float) -> None:
        self.available_balance = float(available_balance)
        self.place_calls: list[OrderRequest] = []

    def place_order(self, request: OrderRequest) -> OrderResult:
        self.place_calls.append(request)
        idx = len(self.place_calls)
        return OrderResult(
            order_id=f"oid-{idx}",
            status="FILLED",
            filled_qty=float(request.amount),
            avg_price=100.0,
            fee=0.0,
            client_order_id=request.client_order_id,
        )

    def get_balance(self) -> dict[str, float]:
        return {"USDT": self.available_balance}

    def get_account_budget_snapshot(self, *, quote_asset: str = "USDT") -> dict[str, float | str]:
        return {
            "asset": str(quote_asset).upper(),
            "available_balance": self.available_balance,
            "total_balance": self.available_balance,
            "source": "test.shared",
        }


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


def _bar(symbol: str) -> LiveBar:
    return LiveBar(
        timestamp=pd.Timestamp(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1.0,
        symbol=symbol,
    )


def test_multisymbol_shared_budget_guard_blocks_second_entry_same_bar(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "live_testnet_smoke.db")
    try:
        broker = SharedBudgetBroker(available_balance=150.0)
        shared_guard = AccountBudgetGuard(broker=broker)  # type: ignore[arg-type]

        cfg1 = RuntimeConfig(
            mode="live",
            symbol="BTC/USDT",
            timeframe="1m",
            fixed_notional_usdt=100.0,
            min_entry_notional_usdt=0.0,
            max_bars=1,
            enable_protective_orders=False,
            budget_guard_enabled=True,
            binance_env="testnet",
        )
        cfg2 = RuntimeConfig(
            mode="live",
            symbol="ETH/USDT",
            timeframe="1m",
            fixed_notional_usdt=100.0,
            min_entry_notional_usdt=0.0,
            max_bars=1,
            enable_protective_orders=False,
            budget_guard_enabled=True,
            binance_env="testnet",
        )
        engine1 = RuntimeEngine(
            config=cfg1,
            strategy=AlwaysLongStrategy(),
            broker=broker,  # type: ignore[arg-type]
            feed=DummyFeed(),  # type: ignore[arg-type]
            storage=storage,
            risk_guard=_risk_guard(),
            budget_guard=shared_guard,
            notifier=Notifier(),
            initial_equity=10_000.0,
        )
        engine2 = RuntimeEngine(
            config=cfg2,
            strategy=AlwaysLongStrategy(),
            broker=broker,  # type: ignore[arg-type]
            feed=DummyFeed(),  # type: ignore[arg-type]
            storage=storage,
            risk_guard=_risk_guard(),
            budget_guard=shared_guard,
            notifier=Notifier(),
            initial_equity=10_000.0,
        )

        engine1.start_session()
        engine2.start_session()
        try:
            assert engine1.process_bar(_bar("BTC/USDT")) is True
            assert engine2.process_bar(_bar("ETH/USDT")) is True
        finally:
            engine1.finish_session()
            engine2.finish_session()

        assert len(broker.place_calls) == 1
        assert engine1.position_qty > 0
        assert engine2.position_qty == 0
        events2 = storage.list_recent_events_for_run(engine2.run_id, limit=50)
        assert any(
            evt.get("event_type") == "insufficient_budget"
            and str((evt.get("payload") or {}).get("symbol", "")) == "ETH/USDT"
            for evt in events2
        )
    finally:
        storage.close()
