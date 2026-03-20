from __future__ import annotations

from datetime import datetime, timezone
import json

import pandas as pd

from trader.data.binance_live import BinanceLiveFeed, LiveBar
from trader.notify import Notifier
from trader.risk.guards import RiskGuard
from trader.runtime import RuntimeConfig, RuntimeEngine
from trader.storage import SQLiteStorage
from trader.strategy.base import Bar, Strategy, StrategyPosition


class HoldStrategy(Strategy):
    def on_bar(self, bar: Bar, position: StrategyPosition | None = None):  # type: ignore[override]
        return "hold"


class PassiveBroker:
    def get_balance(self) -> dict[str, float]:
        return {"USDT": 10_000.0}


class NoBarFeed:
    def __init__(self) -> None:
        self._callback = None

    def set_event_callback(self, callback):  # noqa: ANN001
        self._callback = callback

    def iter_closed_bars(self, *, max_bars: int | None = None):  # noqa: ARG002
        if callable(self._callback):
            self._callback("ws_worker_reconnect", {"attempt": 1, "delay_sec": 1.0})
            self._callback("ws_receive_timeout", {"timeout_sec": 90.0})
        if False:
            yield None
        return

    def get_health_snapshot(self) -> dict[str, object]:
        return {"mode": "websocket", "emitted_bar_count": 0}

    def close(self) -> None:
        return None


class EventThenBarFeed:
    def __init__(self, *, symbol: str) -> None:
        self.symbol = symbol
        self._callback = None

    def set_event_callback(self, callback):  # noqa: ANN001
        self._callback = callback

    def iter_closed_bars(self, *, max_bars: int | None = None):  # noqa: ARG002
        if callable(self._callback):
            self._callback("ws_worker_reconnect", {"attempt": 1, "delay_sec": 1.0})
        yield LiveBar(
            timestamp=pd.Timestamp(datetime(2026, 1, 1, tzinfo=timezone.utc)),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1.0,
            symbol=self.symbol,
        )

    def get_health_snapshot(self) -> dict[str, object]:
        return {"mode": "websocket", "emitted_bar_count": 1}

    def close(self) -> None:
        return None


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


def test_zero_bar_session_records_feed_health(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "zero_bar.db")
    try:
        engine = RuntimeEngine(
            config=RuntimeConfig(
                mode="paper",
                symbol="BTC/USDT",
                timeframe="1m",
                max_bars=1,
                enable_protective_orders=False,
            ),
            strategy=HoldStrategy(),
            broker=PassiveBroker(),  # type: ignore[arg-type]
            feed=NoBarFeed(),  # type: ignore[arg-type]
            storage=storage,
            risk_guard=_risk_guard(),
            notifier=Notifier(),
            initial_equity=10_000.0,
        )

        result = engine.run()

        assert result["processed_bars"] == 0
        assert result["feed_event_count"] == 2
        events = storage.list_recent_events_for_run(engine.run_id, limit=50)
        event_types = [str(row["event_type"]) for row in events]
        assert "zero_bar_session_detected" in event_types
        runtime_state = storage.load_runtime_state(engine.run_id)
        assert runtime_state is not None
        risk_state = runtime_state["risk_state"]["BTC/USDT"]
        assert int(risk_state["feed_event_count"]) == 2
        assert str(risk_state["last_feed_event_type"]) == "ws_receive_timeout"
    finally:
        storage.close()


def test_first_bar_processed_event_records_feed_context(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "first_bar.db")
    try:
        engine = RuntimeEngine(
            config=RuntimeConfig(
                mode="paper",
                symbol="ETH/USDT",
                timeframe="1m",
                max_bars=1,
                enable_protective_orders=False,
            ),
            strategy=HoldStrategy(),
            broker=PassiveBroker(),  # type: ignore[arg-type]
            feed=EventThenBarFeed(symbol="ETH/USDT"),  # type: ignore[arg-type]
            storage=storage,
            risk_guard=_risk_guard(),
            notifier=Notifier(),
            initial_equity=10_000.0,
        )

        result = engine.run()

        assert result["processed_bars"] == 1
        assert result["feed_event_count"] == 1
        assert result["first_bar_delay_sec"] is not None
        events = storage.list_recent_events_for_run(engine.run_id, limit=50)
        event_types = [str(row["event_type"]) for row in events]
        assert "first_bar_processed" in event_types
        runtime_state = storage.load_runtime_state(engine.run_id)
        assert runtime_state is not None
        risk_state = runtime_state["risk_state"]["ETH/USDT"]
        assert int(risk_state["feed_event_count"]) == 1
        assert float(risk_state["first_bar_delay_sec"]) >= 0.0
    finally:
        storage.close()


class ReconnectingMessageSource:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    def __call__(self):
        self.calls += 1

        async def _gen():
            if self.calls == 1:
                raise ConnectionError("forced reconnect for test")
                yield ""  # pragma: no cover
            yield json.dumps(self.payload)

        return _gen()


def test_websocket_reconnect_then_resumed_ingestion_emits_payload_milestones() -> None:
    payload = {
        "e": "kline",
        "E": 1_700_000_000_000,
        "s": "BTCUSDT",
        "k": {
            "t": 1_700_000_000_000,
            "T": 1_700_000_059_999,
            "s": "BTCUSDT",
            "i": "1m",
            "x": True,
            "o": "100.0",
            "c": "101.0",
            "h": "102.0",
            "l": "99.0",
            "v": "5.0",
        },
    }
    source = ReconnectingMessageSource(payload)
    events: list[tuple[str, dict[str, object]]] = []
    feed = BinanceLiveFeed(
        symbol="BTC/USDT",
        timeframe="1m",
        mode="websocket",
        ws_max_retries=2,
        ws_backoff_base_sec=0.01,
        ws_backoff_max_sec=0.01,
        ws_receive_timeout_sec=1.0,
        ws_worker_message_source=source,
    )
    feed.set_event_callback(lambda event_type, detail: events.append((str(event_type), dict(detail))))
    try:
        bars = list(feed.iter_closed_bars(max_bars=1))
        event_types = [event_type for event_type, _ in events]
        assert len(bars) == 1
        assert "binance_live_feed_initialized" in event_types
        assert "websocket_worker_start_called" in event_types
        assert "ws_worker_started" in event_types
        assert "ws_worker_reconnect" in event_types
        assert "first_market_payload_received" in event_types
        assert "first_ws_payload_received" in event_types
        assert "first_kline_payload_received" in event_types
        assert "first_closed_kline_received" in event_types
        health = feed.get_health_snapshot()
        assert int(health["ws_payload_count"]) >= 1
        assert int(health["ws_closed_kline_count"]) >= 1
    finally:
        feed.close()
