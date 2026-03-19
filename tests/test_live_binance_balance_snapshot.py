import time

import time

from trader.broker.base import OrderRequest
from trader.broker.live_binance import LiveBinanceBroker
from trader.storage import SQLiteStorage


class _DirectBalanceOnlyExchange:
    def __init__(self) -> None:
        self.urls = {"api": {}}

    def fetch_balance(self, params=None):  # noqa: ANN001, ARG002
        raise RuntimeError("fetch_balance disabled")

    def fapiPrivateV2GetBalance(self):
        return [
            {
                "asset": "USDT",
                "balance": "250.0",
                "walletBalance": "250.0",
                "availableBalance": "125.5",
            }
        ]


class _OrderParamCaptureExchange:
    def __init__(self) -> None:
        self.urls = {"api": {}}
        self.create_params: dict | None = None
        self.cancel_params: dict | None = None

    def fetch_ticker(self, symbol: str):  # noqa: ANN201, ARG002
        return {"last": 100000.0}

    def create_order(self, *, symbol, type, side, amount, price, params):  # noqa: ANN001, ANN201
        self.create_params = dict(params)
        return {"id": "oid-1", "status": "open", "filled": 0.0, "average": 0.0}

    def cancel_order(self, order_id, *, symbol, params):  # noqa: ANN001
        self.cancel_params = dict(params)
        return {"id": order_id, "symbol": symbol}


class _AlgoCancelFallbackExchange:
    def __init__(self) -> None:
        self.urls = {"api": {}}
        self.deleted_payloads: list[dict] = []

    def cancel_order(self, order_id, *, symbol, params):  # noqa: ANN001, ARG002
        raise RuntimeError("standard cancel unsupported for algo orders")

    def fapiPrivateDeleteAlgoOrder(self, payload):  # noqa: ANN001
        self.deleted_payloads.append(dict(payload))
        return {"success": True}


class _AlgoSweepExchange(_AlgoCancelFallbackExchange):
    def __init__(self) -> None:
        super().__init__()
        self.last_get_payload: dict | None = None

    def fapiPrivateGetOpenAlgoOrders(self, payload):  # noqa: ANN001
        self.last_get_payload = dict(payload)
        return [
            {"algoId": "algo-keep", "clientAlgoId": "keep-me"},
            {"algoId": "algo-drop", "clientAlgoId": "drop-me"},
        ]


class _ExchangeWithOptions:
    def __init__(self) -> None:
        self.urls = {"api": {}}
        self.options: dict[str, object] = {}


class _DirectOrderExchange:
    def __init__(self) -> None:
        self.urls = {"api": {}}
        self.options: dict[str, object] = {}
        self.last_payload: dict | None = None

    def fapiPrivatePostOrder(self, payload):  # noqa: ANN001
        self.last_payload = dict(payload)
        return {"orderId": 12345, "status": "NEW", "executedQty": "0", "avgPrice": "0", "price": "0"}

    def create_order(self, **kwargs):  # noqa: ANN003
        raise AssertionError("create_order should not be used when futures private endpoint exists")


class _PrecisionFallbackExchange:
    def __init__(self) -> None:
        self.urls = {"api": {}}
        self.options: dict[str, object] = {}

    def amount_to_precision(self, symbol, amount):  # noqa: ANN001, ARG002
        raise RuntimeError("markets not loaded")

    def price_to_precision(self, symbol, price):  # noqa: ANN001, ARG002
        raise RuntimeError("markets not loaded")

    def fapiPublicGetExchangeInfo(self):
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    ],
                }
            ]
        }


class _DirectFetchOrderExchange:
    def __init__(self) -> None:
        self.urls = {"api": {}}
        self.options: dict[str, object] = {}

    def fapiPrivateGetOrder(self, payload):  # noqa: ANN001
        return {
            "orderId": payload.get("orderId"),
            "status": "FILLED",
            "executedQty": "0.001",
            "avgPrice": "100000.0",
            "price": "0",
            "commission": "0.0",
        }

    def fetch_order(self, order_id, symbol=None, params=None):  # noqa: ANN001, ARG002
        raise AssertionError("fetch_order should not be used when futures private endpoint exists")


class _PositionCacheExchange:
    def __init__(self) -> None:
        self.urls = {"api": {}}
        self.options: dict[str, object] = {}
        self._seq = 0

    def fapiPrivatePostOrder(self, payload):  # noqa: ANN001
        self._seq += 1
        otype = str(payload.get("type", "MARKET")).upper()
        qty = str(payload.get("quantity", "0"))
        if otype == "MARKET":
            return {"orderId": self._seq, "status": "FILLED", "executedQty": qty, "avgPrice": "100.0", "price": "0"}
        return {"orderId": self._seq, "status": "NEW", "executedQty": "0", "avgPrice": "0", "price": "0"}

    def fapiPrivatePostAlgoOrder(self, payload):  # noqa: ANN001
        self._seq += 1
        return {"algoId": f"algo-{self._seq}", "status": "NEW"}

    def fapiPrivateGetOrder(self, payload):  # noqa: ANN001
        return {"orderId": payload.get("orderId"), "status": "NEW", "executedQty": "0", "avgPrice": "0", "price": "0"}


class _NoopUserStream:
    def start(self, on_event):  # noqa: ANN001
        self.on_event = on_event

    def stop(self) -> None:
        return None


class _MinNotionalRejectBeforeCreateExchange:
    def __init__(self) -> None:
        self.urls = {"api": {}}
        self.options: dict[str, object] = {}
        self.create_calls = 0

    def fetch_ticker(self, symbol: str):  # noqa: ANN201, ARG002
        return {"last": 100.0}

    def amount_to_precision(self, symbol, amount):  # noqa: ANN001, ARG002
        return f"{float(amount):.3f}"

    def fapiPublicGetExchangeInfo(self):
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                        {"filterType": "MIN_NOTIONAL", "minNotional": "100"},
                    ],
                }
            ]
        }

    def create_order(self, **kwargs):  # noqa: ANN003
        self.create_calls += 1
        return {"id": "oid-1", "status": "open", "filled": 0.0, "average": 0.0}


class _RestFillRecoveryExchange:
    def __init__(self) -> None:
        self.urls = {"api": {}}
        self.options: dict[str, object] = {}
        self.fetch_trades_calls = 0

    def fetch_ticker(self, symbol: str):  # noqa: ANN201, ARG002
        return {"last": 100.0}

    def create_order(self, *, symbol, type, side, amount, price, params):  # noqa: ANN001, ANN201, ARG002
        return {"id": "oid-rest-1", "status": "open", "filled": 0.0, "average": 0.0}

    def fetch_order(self, order_id, symbol=None, params=None):  # noqa: ANN001, ANN201, ARG002
        return {
            "id": order_id,
            "status": "FILLED",
            "filled": 0.5,
            "average": 100.0,
            "price": 100.0,
            "fee": {"cost": 0.02},
        }

    def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None):  # noqa: ANN001, ANN201, ARG002
        self.fetch_trades_calls += 1
        return [
            {
                "id": "trade-1",
                "timestamp": 1_700_000_000_000,
                "side": "buy",
                "amount": 0.5,
                "price": 100.0,
                "fee": {"cost": 0.02},
                "maker": False,
            }
        ]


class _RestFillAggregateFallbackExchange(_RestFillRecoveryExchange):
    def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None):  # noqa: ANN001, ANN201, ARG002
        self.fetch_trades_calls += 1
        raise RuntimeError("trade query unavailable")


class _RestPartialFillExchange(_RestFillRecoveryExchange):
    def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None):  # noqa: ANN001, ANN201, ARG002
        self.fetch_trades_calls += 1
        return [
            {
                "id": "trade-1",
                "timestamp": 1_700_000_000_000,
                "side": "buy",
                "amount": 0.2,
                "price": 100.0,
                "fee": {"cost": 0.01},
                "maker": False,
            },
            {
                "id": "trade-2",
                "timestamp": 1_700_000_000_100,
                "side": "buy",
                "amount": 0.3,
                "price": 100.1,
                "fee": {"cost": 0.01},
                "maker": False,
            },
        ]


class _PreflightCachingExchange:
    def __init__(self) -> None:
        self.urls = {"api": {}}
        self.options: dict[str, object] = {}

    def fetch_time(self):  # noqa: ANN201
        return int(time.time() * 1000)


def test_parse_futures_balance_snapshot_info_assets_dict() -> None:
    broker = LiveBinanceBroker.__new__(LiveBinanceBroker)
    payload = {
        "info": {
            "assets": [
                {
                    "asset": "USDT",
                    "walletBalance": "11.25",
                    "availableBalance": "10.5",
                }
            ]
        }
    }

    snap = broker._parse_futures_balance_snapshot(payload, quote_asset="USDT")

    assert snap["asset"] == "USDT"
    assert snap["available_balance"] == 10.5
    assert snap["total_balance"] == 11.25
    assert snap["source"] == "fetch_balance.info.assets"


def test_retry_fetch_balance_payload_falls_back_to_private_balance_rows() -> None:
    broker = LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=_DirectBalanceOnlyExchange(),
        testnet=True,
        live_trading=False,
    )

    payload = broker._retry_fetch_balance_payload()
    snap = broker._parse_futures_balance_snapshot(payload, quote_asset="USDT")

    assert isinstance(payload.get("info"), list)
    assert snap["available_balance"] == 125.5
    assert snap["total_balance"] == 250.0
    assert snap["source"] == "fetch_balance.info"


def test_parse_futures_balance_snapshot_info_account_dict() -> None:
    broker = LiveBinanceBroker.__new__(LiveBinanceBroker)
    payload = {"info": {"availableBalance": "9.0", "totalWalletBalance": "12.0"}}

    snap = broker._parse_futures_balance_snapshot(payload, quote_asset="USDT")

    assert snap["available_balance"] == 9.0
    assert snap["total_balance"] == 12.0
    assert snap["source"] == "fetch_balance.info.account"


def test_order_calls_always_include_futures_type_param() -> None:
    exchange = _OrderParamCaptureExchange()
    broker = LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=exchange,
        testnet=True,
        live_trading=True,
    )

    broker.place_order(OrderRequest(symbol="BTC/USDT", side="BUY", amount=0.001, order_type="MARKET"))
    broker.cancel_order("oid-1", symbol="BTC/USDT")

    assert exchange.create_params is not None
    assert exchange.create_params.get("type") == "future"
    assert exchange.cancel_params is not None
    assert exchange.cancel_params.get("type") == "future"


def test_order_uses_new_client_order_id_only() -> None:
    exchange = _OrderParamCaptureExchange()
    broker = LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=exchange,
        testnet=True,
        live_trading=True,
    )

    broker.place_order(
        OrderRequest(
            symbol="BTC/USDT",
            side="BUY",
            amount=0.001,
            order_type="MARKET",
            client_order_id="cid-123",
        )
    )

    assert exchange.create_params is not None
    assert exchange.create_params.get("newClientOrderId") == "cid-123"
    assert "clientOrderId" not in exchange.create_params


def test_cancel_order_falls_back_to_algo_endpoint_when_standard_cancel_fails() -> None:
    exchange = _AlgoCancelFallbackExchange()
    broker = LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=exchange,
        testnet=True,
        live_trading=True,
    )

    ok = broker.cancel_order("algo-123", symbol="ETH/USDT")

    assert ok is True
    assert exchange.deleted_payloads
    assert exchange.deleted_payloads[0].get("symbol") == "ETHUSDT"
    assert exchange.deleted_payloads[0].get("algoId") == "algo-123"


def test_cancel_all_algo_orders_respects_keep_client_ids() -> None:
    exchange = _AlgoSweepExchange()
    broker = LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=exchange,
        testnet=True,
        live_trading=True,
    )

    canceled = broker.cancel_all_algo_orders(symbol="BTC/USDT", keep_client_order_ids={"keep-me"})

    assert canceled == 1
    assert exchange.last_get_payload is not None
    assert exchange.last_get_payload.get("symbol") == "BTCUSDT"
    assert len(exchange.deleted_payloads) == 1
    assert (
        exchange.deleted_payloads[0].get("algoId") == "algo-drop"
        or exchange.deleted_payloads[0].get("clientAlgoId") == "drop-me"
    )


def test_init_forces_futures_exchange_options() -> None:
    exchange = _ExchangeWithOptions()
    LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=exchange,
        testnet=True,
        live_trading=False,
    )

    assert exchange.options.get("defaultType") == "future"
    assert exchange.options.get("fetchCurrencies") is False


def test_place_order_uses_futures_private_endpoint_when_available() -> None:
    exchange = _DirectOrderExchange()
    broker = LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=exchange,
        testnet=True,
        live_trading=True,
    )

    result = broker.place_order(OrderRequest(symbol="BTC/USDT", side="SELL", amount=0.001, order_type="MARKET"))

    assert result.order_id == "12345"
    assert exchange.last_payload is not None
    assert exchange.last_payload.get("symbol") == "BTCUSDT"
    assert exchange.last_payload.get("type") == "MARKET"


def test_rounding_falls_back_to_exchange_info_filters_when_precision_helpers_fail() -> None:
    broker = LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=_PrecisionFallbackExchange(),
        testnet=True,
        live_trading=False,
    )

    rounded_amount = broker._round_amount("BTC/USDT", 0.0014797)
    rounded_price = broker._round_price("BTC/USDT", 84123.456)

    assert rounded_amount == 0.001
    assert rounded_price == 84123.4


def test_fetch_order_uses_futures_private_endpoint_when_available() -> None:
    broker = LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=_DirectFetchOrderExchange(),
        testnet=True,
        live_trading=False,
    )

    row = broker._retry_fetch_order(order_id="123", symbol="BTC/USDT")

    assert row is not None
    assert row.get("status") == "FILLED"
    assert row.get("filled") == 0.001


def test_reduce_only_protective_order_uses_local_position_cache_after_entry_fill() -> None:
    broker = LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=_PositionCacheExchange(),
        testnet=True,
        live_trading=True,
        ws_order_wait_sec=0.01,
        ws_poll_interval_sec=0.001,
    )
    entry = broker.place_order(OrderRequest(symbol="ETH/USDT", side="BUY", amount=0.05, order_type="MARKET"))
    assert entry.status == "FILLED"

    protective = broker.place_order(
        OrderRequest(
            symbol="ETH/USDT",
            side="SELL",
            amount=0.05,
            order_type="STOP_MARKET",
            stop_price=95.0,
            reduce_only=True,
        )
    )
    assert protective.status in {"NEW", "FILLED"}


def test_reduce_only_protective_order_does_not_wait_for_terminal_user_stream_status() -> None:
    broker = LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=_PositionCacheExchange(),
        testnet=True,
        live_trading=True,
        use_user_stream=True,
        user_stream=_NoopUserStream(),
        ws_order_wait_sec=0.01,
        ws_poll_interval_sec=0.001,
    )
    entry = broker.place_order(
        OrderRequest(
            symbol="ETH/USDT",
            side="BUY",
            amount=0.05,
            order_type="MARKET",
        )
    )

    protective = broker.place_order(
        OrderRequest(
            symbol="ETH/USDT",
            side="SELL",
            amount=0.05,
            order_type="STOP_MARKET",
            stop_price=95.0,
            reduce_only=True,
            client_order_id="protective-1",
        )
    )

    assert entry.status == "FILLED"
    assert protective.status == "NEW"


def test_entry_notional_guard_rejects_before_create_order_call() -> None:
    exchange = _MinNotionalRejectBeforeCreateExchange()
    broker = LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=exchange,
        testnet=True,
        live_trading=True,
    )

    result = broker.place_order(OrderRequest(symbol="BTC/USDT", side="BUY", amount=0.9, order_type="MARKET"))

    assert result.status == "REJECTED"
    assert "entry_notional_too_small" in result.message
    assert exchange.create_calls == 0


def test_rest_fill_reconciliation_persists_fill_once_when_user_stream_misses_trade(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "rest_fill_recovery.db")
    try:
        exchange = _RestFillRecoveryExchange()
        broker = LiveBinanceBroker(
            api_key="k",
            api_secret="s",
            exchange=exchange,
            testnet=True,
            live_trading=True,
            use_user_stream=True,
            user_stream=_NoopUserStream(),
            ws_order_wait_sec=0.01,
            ws_poll_interval_sec=0.001,
        )
        broker.attach_storage(storage=storage, run_id="run-rest-fill")

        result = broker.place_order(
            OrderRequest(
                symbol="BTC/USDT",
                side="BUY",
                amount=0.5,
                order_type="MARKET",
                client_order_id="entry-rest-1",
            )
        )

        assert result.status == "FILLED"
        assert exchange.fetch_trades_calls >= 1
        row = storage._conn.execute(
            """
            SELECT symbol, fill_id, order_id, qty, price, source, provenance_detail, source_history
            FROM fills
            WHERE run_id = ?
            """,
            ("run-rest-fill",),
        ).fetchone()
        assert row is not None
        assert row["symbol"] == "BTC/USDT"
        assert row["order_id"] == "oid-rest-1"
        assert "trade-1" in str(row["fill_id"])
        assert float(row["qty"]) == 0.5
        assert float(row["price"]) == 100.0
        assert row["source"] == "rest_trade_reconcile"
        assert row["provenance_detail"] == "rest_trade_query"
        assert "rest_trade_reconcile" in str(row["source_history"])

        broker.handle_user_stream_event(
            {
                "e": "ORDER_TRADE_UPDATE",
                "E": 1_700_000_000_000,
                "o": {
                    "s": "BTCUSDT",
                    "c": "entry-rest-1",
                    "S": "BUY",
                    "o": "MARKET",
                    "X": "FILLED",
                    "x": "TRADE",
                    "i": "oid-rest-1",
                    "t": "trade-1",
                    "l": "0.5",
                    "L": "100.0",
                    "z": "0.5",
                    "ap": "100.0",
                    "n": "0.02",
                    "T": 1_700_000_000_000,
                },
            }
        )

        fill_rows = storage._conn.execute(
            "SELECT COUNT(*) FROM fills WHERE run_id = ?",
            ("run-rest-fill",),
        ).fetchone()
        assert fill_rows is not None
        assert int(fill_rows[0]) == 1
        merged = storage._conn.execute(
            "SELECT source, source_history FROM fills WHERE run_id = ? LIMIT 1",
            ("run-rest-fill",),
        ).fetchone()
        assert merged is not None
        assert merged["source"] == "rest_trade_reconcile"
        assert "user_stream" in str(merged["source_history"])
        status = storage.get_run_status("run-rest-fill")
        assert status["fills_count"] == 1
        assert status["fills_from_rest_reconcile_count"] == 1
        assert status["fills_from_user_stream_count"] == 0
        assert status["fill_provenance_consistency_pass"] is True
    finally:
        storage.close()


def test_rest_fill_reconciliation_marks_aggregated_fallback_when_trade_query_unavailable(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "rest_fill_aggregate_fallback.db")
    try:
        exchange = _RestFillAggregateFallbackExchange()
        broker = LiveBinanceBroker(
            api_key="k",
            api_secret="s",
            exchange=exchange,
            testnet=True,
            live_trading=True,
            use_user_stream=True,
            user_stream=_NoopUserStream(),
            ws_order_wait_sec=0.01,
            ws_poll_interval_sec=0.001,
        )
        broker.attach_storage(storage=storage, run_id="run-rest-fallback")

        result = broker.place_order(
            OrderRequest(
                symbol="BTC/USDT",
                side="BUY",
                amount=0.5,
                order_type="MARKET",
                client_order_id="entry-rest-fallback",
            )
        )

        assert result.status == "FILLED"
        row = storage._conn.execute(
            """
            SELECT source, provenance_detail, is_reconciled, reconciled_from_missing_ws, trade_query_available
            FROM fills
            WHERE run_id = ?
            """,
            ("run-rest-fallback",),
        ).fetchone()
        assert row is not None
        assert row["source"] == "aggregated_fallback"
        assert row["provenance_detail"] == "trade_query_unavailable"
        assert int(row["is_reconciled"]) == 1
        assert int(row["reconciled_from_missing_ws"]) == 1
        assert int(row["trade_query_available"]) == 0

        status = storage.get_run_status("run-rest-fallback")
        assert status["fills_from_aggregated_fallback_count"] == 1
        assert status["aggregated_fallback_fill_count"] == 1
        assert status["reconciled_missing_ws_fill_count"] == 1
        assert status["trade_query_unavailable_count"] == 1
    finally:
        storage.close()


def test_rest_fill_reconciliation_marks_partial_fill_groups(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "rest_partial_fill_recovery.db")
    try:
        exchange = _RestPartialFillExchange()
        broker = LiveBinanceBroker(
            api_key="k",
            api_secret="s",
            exchange=exchange,
            testnet=True,
            live_trading=True,
            use_user_stream=True,
            user_stream=_NoopUserStream(),
            ws_order_wait_sec=0.01,
            ws_poll_interval_sec=0.001,
        )
        broker.attach_storage(storage=storage, run_id="run-rest-partial")

        result = broker.place_order(
            OrderRequest(
                symbol="BTC/USDT",
                side="BUY",
                amount=0.5,
                order_type="MARKET",
                client_order_id="entry-rest-partial",
            )
        )

        assert result.status == "FILLED"
        rows = storage._conn.execute(
            """
            SELECT fill_id, source, is_partial_fill, partial_fill_group_key
            FROM fills
            WHERE run_id = ?
            ORDER BY fill_id
            """,
            ("run-rest-partial",),
        ).fetchall()
        assert len(rows) == 2
        assert {row["source"] for row in rows} == {"rest_trade_reconcile"}
        assert {int(row["is_partial_fill"]) for row in rows} == {1}
        assert len({str(row["partial_fill_group_key"]) for row in rows}) == 1

        status = storage.get_run_status("run-rest-partial")
        assert status["fills_count"] == 2
        assert status["fills_from_rest_reconcile_count"] == 2
        assert status["partial_fills_count"] == 2
        assert status["partial_fill_audit_summary"]["partial_fill_groups_count"] == 1
    finally:
        storage.close()


def test_run_status_counts_distinct_order_ids_and_ignores_duplicate_fill_ids(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "status_distinct_counts.db")
    try:
        storage.save_order(
            {
                "run_id": "run-distinct",
                "symbol": "BTC/USDT",
                "order_id": "oid-1",
                "client_order_id": "cid-1",
                "ts": "2026-03-15T00:00:00+00:00",
                "signal": "entry",
                "side": "BUY",
                "position_side": "BOTH",
                "reduce_only": False,
                "order_type": "MARKET",
                "qty": 0.1,
                "requested_price": 100.0,
                "stop_price": None,
                "time_in_force": None,
                "status": "new",
                "reason": "submitted",
            }
        )
        storage.save_order(
            {
                "run_id": "run-distinct",
                "symbol": "BTC/USDT",
                "order_id": "oid-1",
                "client_order_id": "cid-1",
                "ts": "2026-03-15T00:00:01+00:00",
                "signal": "ws_update",
                "side": "BUY",
                "position_side": "BOTH",
                "reduce_only": False,
                "order_type": "MARKET",
                "qty": 0.1,
                "requested_price": 100.0,
                "stop_price": None,
                "time_in_force": None,
                "status": "filled",
                "reason": "user stream update",
            }
        )
        fill_payload = {
            "run_id": "run-distinct",
            "symbol": "BTC/USDT",
            "fill_id": "fill-1",
            "order_id": "oid-1",
            "ts": "2026-03-15T00:00:01+00:00",
            "side": "BUY",
            "qty": 0.1,
            "price": 100.0,
            "fee": 0.01,
            "liquidity": "taker",
            "source": "user_stream",
            "provenance_detail": "ws_order_trade_update",
            "source_history": ["user_stream"],
        }
        storage.save_fill(fill_payload)
        storage.save_fill(fill_payload)

        fill_rows = storage._conn.execute(
            "SELECT COUNT(*) FROM fills WHERE run_id = ?",
            ("run-distinct",),
        ).fetchone()
        assert fill_rows is not None
        assert int(fill_rows[0]) == 1
        status = storage.get_run_status("run-distinct")
        assert status["orders_count"] == 1
        assert status["fills_count"] == 1
        assert status["fills_from_user_stream_count"] == 1
        assert status["fill_provenance_consistency_pass"] is True
    finally:
        storage.close()


def test_preflight_futures_permission_check_is_cached_across_symbols() -> None:
    broker = LiveBinanceBroker(
        api_key="k",
        api_secret="s",
        exchange=_PreflightCachingExchange(),
        testnet=True,
        live_trading=True,
    )
    calls = {"count": 0}

    def fake_fetch_futures_balance_direct():
        calls["count"] += 1
        return True, {"endpoint": "GET /fapi/v2/balance", "method": "fake_balance", "http_status": 200}

    broker._fetch_futures_balance_direct = fake_fetch_futures_balance_direct  # type: ignore[method-assign]
    broker._fetch_exchange_info_public = lambda: (  # type: ignore[method-assign]
        {"_markets": {"BTC/USDT": {"active": True}, "ETH/USDT": {"active": True}}},
        {"endpoint": "GET /fapi/v1/exchangeInfo", "method": "fake_exchange_info", "http_status": 200},
    )
    broker._fetch_position_risk = lambda symbol: (  # type: ignore[method-assign]
        {"leverage": 20, "marginType": "cross"},
        {
            "attempted": True,
            "ok": True,
            "detail": "position risk endpoint call finished",
            "endpoint": "GET /fapi/v2/positionRisk",
            "method": "fake_position_risk",
            "http_status": 200,
        },
    )

    ok_btc, _ = broker.preflight_check(symbol="BTC/USDT")
    ok_eth, _ = broker.preflight_check(symbol="ETH/USDT")

    assert ok_btc is True
    assert ok_eth is True
    assert calls["count"] == 1
