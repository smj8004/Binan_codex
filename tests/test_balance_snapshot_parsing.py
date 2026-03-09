from __future__ import annotations

from trader.broker.live_binance import LiveBinanceBroker


class _ExchangeWithLastUrl:
    def __init__(self, last_request_url: str) -> None:
        self.last_request_url = last_request_url


def test_parse_fapi_v2_balance_snapshot_prefers_usdt_available_and_balance_fields() -> None:
    broker = LiveBinanceBroker.__new__(LiveBinanceBroker)
    broker.exchange = _ExchangeWithLastUrl("https://testnet.binancefuture.com/fapi/v2/balance")
    payload = {
        "info": [
            {
                "asset": "BUSD",
                "balance": "10.0",
                "availableBalance": "10.0",
            },
            {
                "asset": "USDT",
                "balance": "5001.3173",
                "availableBalance": "4978.1021",
                "walletBalance": "5001.3173",
            },
        ]
    }

    snap = broker._parse_futures_balance_snapshot(payload, quote_asset="USDT")

    assert snap["asset"] == "USDT"
    assert snap["total_balance"] == 5001.3173
    assert snap["available_balance"] == 4978.1021
    assert snap["account_total_usdt"] == 5001.3173
    assert snap["account_available_usdt"] == 4978.1021
    assert snap["endpoint_used"] == "https://testnet.binancefuture.com/fapi/v2/balance"
