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


class SingleBarFeed:
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


class BudgetSpyBroker:
    def __init__(self, *, available_balance: float) -> None:
        self.available_balance = float(available_balance)
        self.place_calls: list[OrderRequest] = []

    def place_order(self, request: OrderRequest) -> OrderResult:
        self.place_calls.append(request)
        idx = len(self.place_calls)
        order_type = str(request.order_type).upper()
        if order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            return OrderResult(
                order_id=f"oid-{idx}",
                status="NEW",
                filled_qty=0.0,
                avg_price=0.0,
                client_order_id=request.client_order_id,
            )
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
            "source": "test.spy",
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


def test_insufficient_budget_skips_order_submission(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "budget_insufficient.db")
    try:
        broker = BudgetSpyBroker(available_balance=50.0)
        cfg = RuntimeConfig(
            mode="live",
            symbol="BTC/USDT",
            timeframe="1m",
            fixed_notional_usdt=100.0,
            min_entry_notional_usdt=0.0,
            max_bars=1,
            halt_on_error=True,
            enable_protective_orders=False,
            budget_guard_enabled=True,
            binance_env="testnet",
        )
        engine = RuntimeEngine(
            config=cfg,
            strategy=AlwaysLongStrategy(),
            broker=broker,  # type: ignore[arg-type]
            feed=SingleBarFeed(symbol=cfg.symbol),  # type: ignore[arg-type]
            storage=storage,
            risk_guard=_risk_guard(),
            budget_guard=AccountBudgetGuard(broker=broker),  # type: ignore[arg-type]
            notifier=Notifier(),
            initial_equity=10_000.0,
        )

        result = engine.run()

        assert result["halted"] is False
        assert len(broker.place_calls) == 0
        events = storage.list_recent_events_for_run(engine.run_id, limit=30)
        assert any(evt.get("event_type") == "insufficient_budget" for evt in events)
    finally:
        storage.close()


def test_sufficient_budget_submits_entry_and_creates_protective_orders(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "budget_sufficient.db")
    try:
        broker = BudgetSpyBroker(available_balance=1_000.0)
        cfg = RuntimeConfig(
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
            budget_guard_enabled=True,
            binance_env="testnet",
        )
        engine = RuntimeEngine(
            config=cfg,
            strategy=AlwaysLongStrategy(),
            broker=broker,  # type: ignore[arg-type]
            feed=SingleBarFeed(symbol=cfg.symbol),  # type: ignore[arg-type]
            storage=storage,
            risk_guard=_risk_guard(),
            budget_guard=AccountBudgetGuard(broker=broker),  # type: ignore[arg-type]
            notifier=Notifier(),
            initial_equity=10_000.0,
        )

        result = engine.run()

        assert result["halted"] is False
        assert len(broker.place_calls) >= 3
        first = broker.place_calls[0]
        assert first.reduce_only is False
        assert str(first.order_type).upper() == "MARKET"
        assert any(str(req.order_type).upper() == "STOP_MARKET" and req.reduce_only for req in broker.place_calls)
        assert any(str(req.order_type).upper() == "TAKE_PROFIT_MARKET" and req.reduce_only for req in broker.place_calls)
        assert len(engine._open_orders) >= 2
    finally:
        storage.close()


def test_budget_guard_uses_min_of_available_and_budget_cap_remaining() -> None:
    broker = BudgetSpyBroker(available_balance=1_000.0)
    guard = AccountBudgetGuard(broker=broker)  # type: ignore[arg-type]
    bar_ts = pd.Timestamp(datetime(2026, 1, 1, tzinfo=timezone.utc))

    ok, meta = guard.check_and_reserve(
        bar_ts=bar_ts,
        order_notional=150.0,
        reduce_only=False,
        budget_cap_remaining_usdt=100.0,
    )

    assert ok is False
    assert meta.get("reason") == "insufficient_budget"
    assert float(meta.get("effective_available", 0.0)) == 100.0
    assert float(meta.get("budget_cap_remaining_usdt", 0.0)) == 100.0
