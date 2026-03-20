from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pandas as pd

from trader.broker.base import OrderRequest
from trader.broker.paper import PaperBroker, PaperPosition
from trader.notify import Notifier
from trader.risk.guards import RiskGuard
from trader.runtime import RuntimeConfig, RuntimeEngine, RuntimeOrchestrator
from trader.storage import SQLiteStorage
from trader.strategy.base import Bar, Strategy, StrategyPosition


class FlatToLongStrategy(Strategy):
    def on_bar(self, bar: Bar, position: StrategyPosition | None = None):  # type: ignore[override]
        pos = position or StrategyPosition()
        return "long" if pos.side == "flat" else "hold"


@dataclass
class FakeFeed:
    symbol: str
    closes: list[float]

    def set_event_callback(self, callback):  # noqa: ANN001
        return None

    def iter_closed_bars(self, *, max_bars: int | None = None, history_limit: int = 500):  # noqa: ARG002
        emitted = 0
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for idx, close in enumerate(self.closes):
            if max_bars is not None and emitted >= max_bars:
                return
            ts = pd.Timestamp(start + timedelta(minutes=idx))
            yield type(
                "BarLike",
                (),
                {
                    "timestamp": ts,
                    "open": float(close),
                    "high": float(close) * 1.001,
                    "low": float(close) * 0.999,
                    "close": float(close),
                    "volume": 1.0,
                    "symbol": self.symbol,
                    "is_backfill": False,
                },
            )()
            emitted += 1

    def close(self) -> None:
        return None


def _make_risk_guard() -> RiskGuard:
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


def test_poll_filled_orders_symbol_scoped() -> None:
    broker = PaperBroker(starting_cash=10_000.0, slippage_bps=0.0, taker_fee_bps=0.0, maker_fee_bps=0.0)
    broker.positions["BTC/USDT"] = PaperPosition(qty=1.0, avg_entry_price=100.0)
    broker.positions["ETH/USDT"] = PaperPosition(qty=1.0, avg_entry_price=50.0)
    broker.update_market_price("BTC/USDT", 100.0)
    broker.update_market_price("ETH/USDT", 50.0)

    btc_stop = OrderRequest(
        symbol="BTC/USDT",
        side="SELL",
        amount=1.0,
        order_type="STOP_MARKET",
        stop_price=99.0,
        reduce_only=True,
    )
    eth_stop = OrderRequest(
        symbol="ETH/USDT",
        side="SELL",
        amount=1.0,
        order_type="STOP_MARKET",
        stop_price=49.0,
        reduce_only=True,
    )
    assert broker.place_order(btc_stop).status == "NEW"
    assert broker.place_order(eth_stop).status == "NEW"

    broker.update_market_price("BTC/USDT", 98.0)
    broker.update_market_price("ETH/USDT", 48.0)

    btc_updates = broker.poll_filled_orders("BTC/USDT")
    assert len(btc_updates) == 1
    assert btc_updates[0][0].symbol == "BTC/USDT"

    eth_updates = broker.poll_filled_orders("ETH/USDT")
    assert len(eth_updates) == 1
    assert eth_updates[0][0].symbol == "ETH/USDT"
    assert broker.poll_filled_orders() == []


def test_multisymbol_entry_price_protective_and_bnb_bars(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "multi_sync.db")
    try:
        broker = PaperBroker(starting_cash=10_000.0, slippage_bps=0.0, taker_fee_bps=0.0, maker_fee_bps=0.0)
        risk_guard = _make_risk_guard()
        run_id = uuid4().hex
        symbols = {
            "BTC/USDT": [67_000.0, 67_010.0, 67_020.0, 67_030.0, 67_040.0],
            "ETH/USDT": [1_950.0, 1_951.0, 1_952.0, 1_953.0, 1_954.0],
            "BNB/USDT": [510.0, 511.0, 512.0, 513.0, 514.0],
        }

        feeds = {sym: FakeFeed(symbol=sym, closes=closes) for sym, closes in symbols.items()}
        engines: dict[str, RuntimeEngine] = {}
        for sym in symbols:
            cfg = RuntimeConfig(
                mode="paper",
                symbol=sym,
                timeframe="1m",
                fixed_notional_usdt=100.0,
                min_entry_notional_usdt=0.0,
                max_bars=5,
                halt_on_error=True,
                enable_protective_orders=True,
                protective_stop_loss_pct=0.01,
                protective_take_profit_pct=0.02,
                require_protective_orders=True,
                protective_missing_policy="halt",
                consec_loss_limit=20,
                binance_env="testnet",
            )
            engines[sym] = RuntimeEngine(
                config=cfg,
                strategy=FlatToLongStrategy(),
                broker=broker,
                feed=feeds[sym],  # type: ignore[arg-type]
                storage=storage,
                risk_guard=risk_guard,
                notifier=Notifier(),
                initial_equity=10_000.0,
                run_id=run_id,
            )

        orchestrator = RuntimeOrchestrator(
            engines=engines,
            feeds=feeds,  # type: ignore[arg-type]
            max_bars=5,
            account_risk_guard=risk_guard,
            account_initial_equity=10_000.0,
        )
        result = orchestrator.run()
        events = storage.list_recent_events_for_run(run_id, limit=200)
        event_types = [str(row["event_type"]) for row in events]

        assert result["halted"] is False
        assert event_types.count("feed_worker_thread_created") == len(symbols)
        assert event_types.count("feed_worker_thread_started") == len(symbols)
        assert event_types.count("feed_worker_entered") == len(symbols)
        assert event_types.count("feed_worker_entered_iter_closed_bars") == len(symbols)
        assert event_types.count("feed_worker_completed") == len(symbols)
        symbol_results = result["symbols"]
        for sym, closes in symbols.items():
            sym_result = symbol_results[sym]
            assert int(sym_result["processed_bars"]) > 0
            engine = engines[sym]
            assert engine.position_qty > 0
            entry = float(engine.position_entry_price)
            lo = min(closes)
            hi = max(closes)
            assert lo * 0.5 <= entry <= hi * 1.5
            protective = broker.get_open_orders(symbol=sym)
            assert len(protective) >= 2
            assert all(bool(v.get("reduce_only", False)) for v in protective.values())
    finally:
        storage.close()
