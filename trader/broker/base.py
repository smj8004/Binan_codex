from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from typing import Literal

Side = Literal["BUY", "SELL", "buy", "sell"]
OrderType = Literal[
    "MARKET",
    "LIMIT",
    "STOP_MARKET",
    "TAKE_PROFIT_MARKET",
    "market",
    "limit",
    "stop_market",
    "take_profit_market",
]
OrderStatus = Literal["NEW", "FILLED", "REJECTED", "CANCELED"]
PositionSide = Literal["BOTH", "LONG", "SHORT"]


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: Side
    amount: float
    order_type: OrderType = "MARKET"
    price: float | None = None
    stop_price: float | None = None
    client_order_id: str | None = None
    reduce_only: bool = False
    time_in_force: str | None = None
    position_side: PositionSide = "BOTH"


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    status: OrderStatus
    filled_qty: float
    avg_price: float
    fee: float = 0.0
    message: str = ""
    client_order_id: str | None = None


class Broker(ABC):
    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    def get_balance(self) -> dict[str, float]:
        raise NotImplementedError

    def get_account_budget_snapshot(self, *, quote_asset: str = "USDT") -> dict[str, Any]:
        """
        Best-effort fallback budget snapshot.
        Concrete brokers should override this with exchange-specific fields when possible.
        """
        balance = self.get_balance()
        quote = str(quote_asset).upper()
        direct = balance.get(quote)
        if isinstance(direct, (int, float)):
            value = float(direct)
            return {
                "asset": quote,
                "available_balance": value,
                "total_balance": value,
                "source": "broker.get_balance.direct",
            }
        cash = balance.get("cash")
        if isinstance(cash, (int, float)):
            value = float(cash)
            return {
                "asset": quote,
                "available_balance": value,
                "total_balance": value,
                "source": "broker.get_balance.cash",
            }
        return {
            "asset": quote,
            "available_balance": 0.0,
            "total_balance": 0.0,
            "source": "broker.get_balance.empty",
        }
