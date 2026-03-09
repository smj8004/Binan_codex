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


class BalanceOnlyBroker:
    def __init__(self, *, available: float, total: float) -> None:
        self.available = float(available)
        self.total = float(total)

    def place_order(self, request: OrderRequest) -> OrderResult:  # noqa: ARG002
        return OrderResult(order_id="noop", status="CANCELED", filled_qty=0.0, avg_price=0.0)

    def get_balance(self) -> dict[str, float]:
        return {"USDT": self.total}

    def get_account_budget_snapshot(self, *, quote_asset: str = "USDT") -> dict[str, float | str]:
        return {
            "asset": str(quote_asset).upper(),
            "available_balance": self.available,
            "total_balance": self.total,
            "account_available_usdt": self.available,
            "account_total_usdt": self.total,
            "source": "test.balance_only",
        }


def _risk_guard() -> RiskGuard:
    return RiskGuard(
        max_order_notional=1_000_000.0,
        max_position_notional=1_000_000.0,
        max_daily_loss=1_000_000.0,
        max_drawdown_pct=0.99,
        max_atr_pct=1.0,
        account_allocation_pct=0.2,
        risk_per_trade_pct=0.0,
        daily_loss_limit_pct=0.99,
        consec_loss_limit=100,
        quiet_hours=None,
        capital_limit_usdt=None,
    )


def test_runtime_status_payload_includes_account_balance_and_budget_cap(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "status_account_balance.db")
    try:
        broker = BalanceOnlyBroker(available=4978.1021, total=5001.3173)
        cfg = RuntimeConfig(
            mode="live",
            symbol="BTC/USDT",
            timeframe="1m",
            fixed_notional_usdt=100.0,
            max_bars=1,
            halt_on_error=True,
            enable_protective_orders=False,
            budget_guard_enabled=True,
            binance_env="testnet",
        )
        engine = RuntimeEngine(
            config=cfg,
            strategy=HoldStrategy(),
            broker=broker,  # type: ignore[arg-type]
            feed=OneBarFeed(symbol=cfg.symbol),  # type: ignore[arg-type]
            storage=storage,
            risk_guard=_risk_guard(),
            budget_guard=AccountBudgetGuard(broker=broker),  # type: ignore[arg-type]
            notifier=Notifier(),
            initial_equity=10_000.0,
        )

        result = engine.run()
        assert result["halted"] is False

        summary = storage.get_run_status(engine.run_id)
        risk_state_raw = summary.get("risk_state", {})
        if isinstance(risk_state_raw, dict) and cfg.symbol in risk_state_raw and isinstance(risk_state_raw[cfg.symbol], dict):
            risk_state = risk_state_raw[cfg.symbol]
        else:
            risk_state = risk_state_raw

        assert float(risk_state.get("account_total_usdt", 0.0)) == 5001.3173
        assert float(risk_state.get("account_available_usdt", 0.0)) == 4978.1021
        assert float(risk_state.get("budget_cap_usdt", 0.0)) == 2000.0
        assert float(risk_state.get("budget_usdt", 0.0)) == 2000.0
    finally:
        storage.close()


def test_runtime_status_payload_uses_auto_budget_cap_from_available_balance(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "status_account_balance_auto.db")
    try:
        broker = BalanceOnlyBroker(available=4978.1021, total=5001.3173)
        cfg = RuntimeConfig(
            mode="live",
            symbol="BTC/USDT",
            timeframe="1m",
            fixed_notional_usdt=100.0,
            max_bars=1,
            halt_on_error=True,
            enable_protective_orders=False,
            budget_guard_enabled=True,
            budget_usdt_mode="auto",
            binance_env="testnet",
        )
        engine = RuntimeEngine(
            config=cfg,
            strategy=HoldStrategy(),
            broker=broker,  # type: ignore[arg-type]
            feed=OneBarFeed(symbol=cfg.symbol),  # type: ignore[arg-type]
            storage=storage,
            risk_guard=_risk_guard(),
            budget_guard=AccountBudgetGuard(broker=broker),  # type: ignore[arg-type]
            notifier=Notifier(),
            initial_equity=10_000.0,
        )

        result = engine.run()
        assert result["halted"] is False

        summary = storage.get_run_status(engine.run_id)
        risk_state_raw = summary.get("risk_state", {})
        if isinstance(risk_state_raw, dict) and cfg.symbol in risk_state_raw and isinstance(risk_state_raw[cfg.symbol], dict):
            risk_state = risk_state_raw[cfg.symbol]
        else:
            risk_state = risk_state_raw

        assert float(risk_state.get("budget_cap_usdt", 0.0)) == 4978.1021
        assert float(risk_state.get("budget_cap_remaining_usdt", 0.0)) == 4978.1021
        assert str(risk_state.get("budget_cap_source", "")) == "auto_available_usdt"
    finally:
        storage.close()
