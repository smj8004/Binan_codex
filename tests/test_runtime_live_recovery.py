from __future__ import annotations

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
        return "long" if pos.side in {"flat", "short"} else "hold"


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


class ReduceOnlyRejectedBroker:
    def __init__(self) -> None:
        self.place_calls: list[OrderRequest] = []
        self.snapshot_calls = 0

    def place_order(self, request: OrderRequest) -> OrderResult:
        self.place_calls.append(request)
        if request.reduce_only:
            raise RuntimeError(
                'create_order failed: binance {"code":-2022,"msg":"ReduceOnly Order is rejected."}'
                " (endpoint=https://testnet.binancefuture.com/fapi/v1/order)"
            )
        return OrderResult(
            order_id=f"ord-{len(self.place_calls)}",
            status="FILLED",
            filled_qty=float(request.amount),
            avg_price=100.0,
            fee=0.0,
            client_order_id=request.client_order_id,
        )

    def get_position_snapshot(self, *, symbol: str) -> dict[str, float]:  # noqa: ARG002
        self.snapshot_calls += 1
        if self.snapshot_calls <= 1:
            return {"qty": -1.0, "entry_price": 100.0}
        return {"qty": 0.0, "entry_price": 0.0}

    def get_balance(self) -> dict[str, float]:
        return {"USDT": 10_000.0}


class AlgoLimitRecoveryBroker:
    def __init__(self) -> None:
        self.place_calls: list[OrderRequest] = []
        self.cleanup_calls = 0
        self._protective_attempts = 0
        self._qty = 0.0
        self._entry = 0.0

    def place_order(self, request: OrderRequest) -> OrderResult:
        self.place_calls.append(request)
        order_type = str(request.order_type).upper()
        if order_type == "MARKET":
            self._qty = float(request.amount) if str(request.side).upper() == "BUY" else -float(request.amount)
            self._entry = 100.0
            return OrderResult(
                order_id=f"ord-{len(self.place_calls)}",
                status="FILLED",
                filled_qty=float(request.amount),
                avg_price=100.0,
                fee=0.0,
                client_order_id=request.client_order_id,
            )
        if order_type in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            self._protective_attempts += 1
            if self._protective_attempts == 1:
                raise RuntimeError(
                    'create_order failed: binance {"code":-4045,"msg":"Reach max stop order limit."}'
                    " (endpoint=https://testnet.binancefuture.com/fapi/v1/algoOrder)"
                )
            return OrderResult(
                order_id=f"ord-{len(self.place_calls)}",
                status="NEW",
                filled_qty=0.0,
                avg_price=0.0,
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

    def cancel_all_algo_orders(
        self, *, symbol: str, keep_client_order_ids: set[str] | None = None  # noqa: ARG002
    ) -> int:
        self.cleanup_calls += 1
        return 2

    def get_position_snapshot(self, *, symbol: str) -> dict[str, float]:  # noqa: ARG002
        return {"qty": self._qty, "entry_price": self._entry}

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


def test_reduce_only_rejection_recovers_without_halt(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "live_reduce_only_recovery.db")
    try:
        broker = ReduceOnlyRejectedBroker()
        engine = RuntimeEngine(
            config=RuntimeConfig(
                mode="live",
                symbol="BTC/USDT",
                timeframe="1m",
                fixed_notional_usdt=100.0,
                min_entry_notional_usdt=0.0,
                max_bars=1,
                halt_on_error=True,
                enable_protective_orders=False,
                budget_guard_enabled=False,
                binance_env="testnet",
            ),
            strategy=AlwaysLongStrategy(),
            broker=broker,  # type: ignore[arg-type]
            feed=SingleBarFeed(symbol="BTC/USDT"),  # type: ignore[arg-type]
            storage=storage,
            risk_guard=_risk_guard(),
            notifier=Notifier(),
            initial_equity=10_000.0,
        )
        engine.position_qty = -1.0
        engine.position_entry_price = 100.0
        engine.position_entry_ts = "2026-01-01T00:00:00+00:00"

        result = engine.run()

        assert result["halted"] is False
        assert engine.position_qty > 0
        assert any(req.reduce_only for req in broker.place_calls)
        assert any(not req.reduce_only for req in broker.place_calls)
    finally:
        storage.close()


def test_protective_algo_limit_retries_after_cleanup(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "live_algo_limit_recovery.db")
    try:
        broker = AlgoLimitRecoveryBroker()
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
                budget_guard_enabled=False,
                binance_env="testnet",
            ),
            strategy=AlwaysLongStrategy(),
            broker=broker,  # type: ignore[arg-type]
            feed=SingleBarFeed(symbol="ETH/USDT"),  # type: ignore[arg-type]
            storage=storage,
            risk_guard=_risk_guard(),
            notifier=Notifier(),
            initial_equity=10_000.0,
        )

        result = engine.run()

        assert result["halted"] is False
        assert broker.cleanup_calls == 1
        assert len(engine._open_orders) == 2
        protective_calls = [req for req in broker.place_calls if str(req.order_type).upper() in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}]
        assert len(protective_calls) >= 3  # first failure + retry + sibling order
    finally:
        storage.close()
