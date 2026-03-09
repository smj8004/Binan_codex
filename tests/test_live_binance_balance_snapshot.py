from trader.broker.base import OrderRequest
from trader.broker.live_binance import LiveBinanceBroker


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
