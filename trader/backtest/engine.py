from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pandas as pd

from trader.storage import SQLiteStorage
from trader.strategy.base import Bar, Strategy, StrategyPosition

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
OrderStatus = Literal["filled", "rejected", "open"]
Liquidity = Literal["taker", "maker"]
OrderSide = Literal["BUY", "SELL"]
PositionSide = Literal["flat", "long", "short"]
SizingMode = Literal["fixed_usdt", "percent_equity", "atr"]
ExecutionPriceSource = Literal["close", "next_open"]
SlippageMode = Literal["fixed", "atr", "mixed"]


@dataclass(frozen=True)
class BacktestConfig:
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    initial_equity: float = 10_000.0
    leverage: float = 3.0
    order_type: OrderType = "MARKET"
    execution_price_source: ExecutionPriceSource = "next_open"
    slippage_bps: float = 1.0
    slippage_mode: SlippageMode = "fixed"
    atr_slippage_mult: float = 0.0
    latency_bars: int = 0
    maker_fee_bps: float = 2.0
    taker_fee_bps: float = 5.0
    fee_multiplier: float = 1.0
    default_liquidity: Liquidity = "taker"
    limit_timeout_bars: int = 1
    limit_price_offset_bps: float = 1.0
    limit_fill_probability: float = 1.0
    limit_unfilled_penalty_bps: float = 0.0
    random_seed: int = 42
    sizing_mode: SizingMode = "fixed_usdt"
    fixed_notional_usdt: float = 1_000.0
    equity_pct: float = 0.1
    atr_period: int = 14
    atr_risk_pct: float = 0.01
    atr_stop_multiple: float = 2.0
    enable_funding: bool = False
    strategy_name: str = ""
    strategy_params: dict[str, object] = field(default_factory=dict)
    notes: str = ""
    persist_to_db: bool = True
    db_path: Path = Path("data/trader.db")


@dataclass(frozen=True)
class Order:
    order_id: str
    run_id: str
    client_order_id: str
    ts: str
    signal: str
    side: OrderSide
    position_side: Literal["BOTH", "LONG", "SHORT"]
    reduce_only: bool
    order_type: OrderType
    qty: float
    requested_price: float | None
    stop_price: float | None
    time_in_force: str | None
    status: OrderStatus
    reason: str = ""


@dataclass(frozen=True)
class Fill:
    fill_id: str
    run_id: str
    order_id: str
    ts: str
    side: OrderSide
    qty: float
    price: float
    fee: float
    liquidity: Liquidity


@dataclass
class Position:
    side: PositionSide = "flat"
    qty: float = 0.0
    entry_price: float = 0.0
    entry_ts: str = ""
    leverage: float = 1.0
    entry_fee: float = 0.0
    funding_paid: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.side != "flat" and self.qty != 0.0

    def unrealized_pnl(self, mark_price: float) -> float:
        if not self.is_open:
            return 0.0
        return self.qty * (mark_price - self.entry_price)


@dataclass(frozen=True)
class Trade:
    trade_id: str
    run_id: str
    symbol: str
    side: Literal["long", "short"]
    entry_ts: str
    exit_ts: str
    qty: float
    entry_price: float
    exit_price: float
    gross_pnl: float
    fee_paid: float
    funding_paid: float
    net_pnl: float
    return_pct: float
    reason: str = ""


@dataclass
class BacktestResult:
    run_id: str
    summary: dict[str, float]
    initial_equity: float
    equity_curve: list[float]
    orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    final_position: Position = field(default_factory=Position)


class BacktestEngine:
    def __init__(self, storage: SQLiteStorage | None = None) -> None:
        self.storage = storage

    def _fee_rate(self, *, order_type: OrderType, config: BacktestConfig) -> tuple[float, Liquidity]:
        normalized_order_type = str(order_type).upper()
        multiplier = max(float(config.fee_multiplier), 0.0)
        if normalized_order_type == "MARKET":
            return (config.taker_fee_bps * multiplier) / 10_000.0, "taker"
        if normalized_order_type == "LIMIT":
            return (config.maker_fee_bps * multiplier) / 10_000.0, "maker"
        if config.default_liquidity == "maker":
            return (config.maker_fee_bps * multiplier) / 10_000.0, "maker"
        return (config.taker_fee_bps * multiplier) / 10_000.0, "taker"

    def _normalize_signal(self, signal: str) -> Literal["long", "short", "exit", "hold"]:
        normalized = signal.lower()
        if normalized == "buy":
            return "long"
        if normalized == "sell":
            return "exit"
        if normalized in {"long", "short", "exit", "hold"}:
            return normalized
        return "hold"

    def _compute_atr(self, candles: pd.DataFrame, period: int) -> pd.Series:
        prev_close = candles["close"].shift(1)
        tr_components = pd.concat(
            [
                candles["high"] - candles["low"],
                (candles["high"] - prev_close).abs(),
                (candles["low"] - prev_close).abs(),
            ],
            axis=1,
        )
        true_range = tr_components.max(axis=1)
        return true_range.rolling(window=period, min_periods=1).mean()

    def _resolve_exec_index(
        self,
        candles: pd.DataFrame,
        row_index: int,
        *,
        config: BacktestConfig,
        apply_latency: bool,
    ) -> int:
        base_index = row_index
        if config.execution_price_source == "next_open":
            base_index = row_index + 1
        latency = max(int(config.latency_bars), 0) if apply_latency else 0
        resolved = base_index + latency
        if resolved < 0:
            return 0
        return min(resolved, len(candles) - 1)

    def _slippage_fraction(
        self,
        candles: pd.DataFrame,
        row_index: int,
        *,
        base_price: float,
        config: BacktestConfig,
    ) -> float:
        mode = str(config.slippage_mode).lower()
        fixed_frac = max(float(config.slippage_bps), 0.0) / 10_000.0
        atr_frac = 0.0
        atr_value = float(candles.iloc[row_index].get("atr", 0.0))
        if base_price > 0 and atr_value > 0:
            atr_frac = max(float(config.atr_slippage_mult), 0.0) * (atr_value / base_price)

        if mode == "atr":
            return atr_frac
        if mode == "mixed":
            return fixed_frac + atr_frac
        return fixed_frac

    def _execution_price(
        self,
        candles: pd.DataFrame,
        row_index: int,
        *,
        side: OrderSide,
        config: BacktestConfig,
        apply_latency: bool = True,
        use_slippage: bool = True,
    ) -> tuple[float, str, int]:
        resolved_idx = self._resolve_exec_index(
            candles,
            row_index,
            config=config,
            apply_latency=apply_latency,
        )
        if config.execution_price_source == "next_open":
            base_price = float(candles.iloc[resolved_idx]["open"])
        else:
            base_price = float(candles.iloc[resolved_idx]["close"])
        ts = str(candles.iloc[resolved_idx]["timestamp"])

        slippage = self._slippage_fraction(candles, resolved_idx, base_price=base_price, config=config) if use_slippage else 0.0
        price = base_price * (1 + slippage) if side == "BUY" else base_price * (1 - slippage)
        return max(price, 1e-12), ts, resolved_idx

    def _limit_order_price(self, *, reference_price: float, side: OrderSide, config: BacktestConfig) -> float:
        offset = max(float(config.limit_price_offset_bps), 0.0) / 10_000.0
        if side == "BUY":
            return reference_price * (1 - offset)
        return reference_price * (1 + offset)

    def _attempt_limit_fill(
        self,
        *,
        candles: pd.DataFrame,
        row_index: int,
        side: OrderSide,
        config: BacktestConfig,
        rng: random.Random,
    ) -> tuple[bool, float, str, int]:
        _, _, start_idx = self._execution_price(
            candles,
            row_index,
            side=side,
            config=config,
            apply_latency=True,
            use_slippage=False,
        )
        reference = float(candles.iloc[start_idx]["close"])
        limit_price = self._limit_order_price(reference_price=reference, side=side, config=config)
        timeout = max(int(config.limit_timeout_bars), 0)
        end_idx = min(start_idx + timeout, len(candles) - 1)
        fill_probability = min(max(float(config.limit_fill_probability), 0.0), 1.0)

        for idx in range(start_idx, end_idx + 1):
            bar = candles.iloc[idx]
            touched = (float(bar["low"]) <= limit_price) if side == "BUY" else (float(bar["high"]) >= limit_price)
            if not touched:
                continue
            if rng.random() <= fill_probability:
                return True, max(limit_price, 1e-12), str(bar["timestamp"]), idx
        return False, max(limit_price, 1e-12), str(candles.iloc[end_idx]["timestamp"]), end_idx

    def _target_notional(self, *, equity: float, mark_price: float, atr: float, config: BacktestConfig) -> float:
        safe_equity = max(equity, 0.0)
        if safe_equity <= 0:
            return 0.0

        if config.sizing_mode == "fixed_usdt":
            raw_notional = config.fixed_notional_usdt
        elif config.sizing_mode == "percent_equity":
            raw_notional = safe_equity * config.equity_pct * config.leverage
        else:
            if atr <= 0:
                raw_notional = 0.0
            else:
                risk_budget = safe_equity * config.atr_risk_pct
                qty = risk_budget / max(atr * config.atr_stop_multiple, 1e-9)
                raw_notional = qty * mark_price

        max_notional = safe_equity * config.leverage
        return max(0.0, min(raw_notional, max_notional))

    def _position_size_multiplier(
        self,
        *,
        strategy: Strategy,
        bar: Bar,
        strategy_position: StrategyPosition,
    ) -> float:
        size_fn = getattr(strategy, "size_multiplier", None)
        if not callable(size_fn):
            return 1.0
        try:
            value = float(size_fn(bar, strategy_position))
        except Exception:
            return 1.0
        return min(max(value, 0.0), 2.0)

    def _partial_exit_fraction(
        self,
        *,
        strategy: Strategy,
        bar: Bar,
        strategy_position: StrategyPosition,
    ) -> float:
        partial_fn = getattr(strategy, "partial_exit_fraction", None)
        if not callable(partial_fn):
            return 0.0
        try:
            value = float(partial_fn(bar, strategy_position))
        except Exception:
            return 0.0
        return min(max(value, 0.0), 1.0)

    def _maybe_store_order(self, order: Order, storage: SQLiteStorage | None) -> None:
        if storage is not None:
            storage.save_order(order)

    def _maybe_store_fill(self, fill: Fill, storage: SQLiteStorage | None) -> None:
        if storage is not None:
            storage.save_fill(fill)

    def _maybe_store_trade(self, trade: Trade, storage: SQLiteStorage | None) -> None:
        if storage is not None:
            storage.save_trade(trade)

    def run(self, candles: pd.DataFrame, strategy: Strategy, config: BacktestConfig) -> BacktestResult:
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required.difference(candles.columns)
        if missing:
            raise ValueError(f"Missing candle columns: {sorted(missing)}")
        if candles.empty:
            raise ValueError("candles is empty")

        market = candles.reset_index(drop=True).copy()
        market["close"] = market["close"].astype(float)
        market["open"] = market["open"].astype(float)
        market["high"] = market["high"].astype(float)
        market["low"] = market["low"].astype(float)
        market["atr"] = self._compute_atr(market, config.atr_period)
        rng = random.Random(int(config.random_seed))

        run_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()

        created_storage = False
        storage = self.storage
        if storage is None and config.persist_to_db:
            storage = SQLiteStorage(config.db_path)
            created_storage = True
        if storage is not None:
            persisted_config = asdict(config)
            if not persisted_config.get("strategy_name"):
                persisted_config["strategy_name"] = strategy.__class__.__name__
            storage.start_backtest_run(
                run_id=run_id,
                created_at=created_at,
                symbol=config.symbol,
                timeframe=config.timeframe,
                initial_equity=config.initial_equity,
                config=persisted_config,
            )

        cash = config.initial_equity
        position = Position(leverage=config.leverage)
        equity_curve: list[float] = []
        orders: list[Order] = []
        fills: list[Fill] = []
        trades: list[Trade] = []
        order_seq = 0
        fill_seq = 0
        trade_seq = 0

        def next_order_id() -> str:
            nonlocal order_seq
            order_seq += 1
            return f"{run_id}-o{order_seq:05d}"

        def next_fill_id() -> str:
            nonlocal fill_seq
            fill_seq += 1
            return f"{run_id}-f{fill_seq:05d}"

        def next_trade_id() -> str:
            nonlocal trade_seq
            trade_seq += 1
            return f"{run_id}-t{trade_seq:05d}"

        def current_equity(mark_price: float) -> float:
            return cash + position.unrealized_pnl(mark_price)

        def close_position(row_index: int, reason_signal: str, *, close_fraction: float = 1.0) -> None:
            nonlocal cash, position
            if not position.is_open:
                return
            fraction = min(max(float(close_fraction), 0.0), 1.0)
            if fraction <= 0.0:
                return

            side: OrderSide = "SELL" if position.side == "long" else "BUY"
            exit_order_type = str(config.order_type).upper()
            exec_price = 0.0
            exec_ts = str(market.iloc[row_index]["timestamp"])
            fee_order_type: OrderType = config.order_type
            close_qty = abs(position.qty) * fraction
            signed_close_qty = close_qty if position.side == "long" else -close_qty

            if exit_order_type == "LIMIT":
                filled, limit_price, limit_ts, timeout_idx = self._attempt_limit_fill(
                    candles=market,
                    row_index=row_index,
                    side=side,
                    config=config,
                    rng=rng,
                )
                if filled:
                    order_id = next_order_id()
                    order = Order(
                        order_id=order_id,
                        run_id=run_id,
                        client_order_id=f"cid-{order_id[-12:]}",
                        ts=limit_ts,
                        signal=reason_signal,
                        side=side,
                        position_side="LONG" if position.side == "long" else "SHORT",
                        reduce_only=True,
                        order_type="LIMIT",
                        qty=close_qty,
                        requested_price=limit_price,
                        stop_price=None,
                        time_in_force=None,
                        status="filled",
                    )
                    orders.append(order)
                    self._maybe_store_order(order, storage)
                    exec_price = limit_price
                    exec_ts = limit_ts
                    fee_order_type = "LIMIT"
                else:
                    rejected_id = next_order_id()
                    rejected = Order(
                        order_id=rejected_id,
                        run_id=run_id,
                        client_order_id=f"cid-{rejected_id[-12:]}",
                        ts=limit_ts,
                        signal=reason_signal,
                        side=side,
                        position_side="LONG" if position.side == "long" else "SHORT",
                        reduce_only=True,
                        order_type="LIMIT",
                        qty=close_qty,
                        requested_price=limit_price,
                        stop_price=None,
                        time_in_force=None,
                        status="rejected",
                        reason="Limit close unfilled; timeout fallback to market",
                    )
                    orders.append(rejected)
                    self._maybe_store_order(rejected, storage)
                    exec_price, exec_ts, _ = self._execution_price(
                        market,
                        timeout_idx,
                        side=side,
                        config=config,
                        apply_latency=False,
                        use_slippage=True,
                    )
                    fallback_id = next_order_id()
                    fallback = Order(
                        order_id=fallback_id,
                        run_id=run_id,
                        client_order_id=f"cid-{fallback_id[-12:]}",
                        ts=exec_ts,
                        signal=reason_signal,
                        side=side,
                        position_side="LONG" if position.side == "long" else "SHORT",
                        reduce_only=True,
                        order_type="MARKET",
                        qty=close_qty,
                        requested_price=exec_price,
                        stop_price=None,
                        time_in_force=None,
                        status="filled",
                        reason="limit_timeout_market_fallback",
                    )
                    orders.append(fallback)
                    self._maybe_store_order(fallback, storage)
                    fee_order_type = "MARKET"
            else:
                exec_price, exec_ts, _ = self._execution_price(
                    market,
                    row_index,
                    side=side,
                    config=config,
                    apply_latency=True,
                    use_slippage=True,
                )
                order_id = next_order_id()
                order = Order(
                    order_id=order_id,
                    run_id=run_id,
                    client_order_id=f"cid-{order_id[-12:]}",
                    ts=exec_ts,
                    signal=reason_signal,
                    side=side,
                    position_side="LONG" if position.side == "long" else "SHORT",
                    reduce_only=True,
                    order_type=config.order_type,
                    qty=close_qty,
                    requested_price=exec_price,
                    stop_price=None,
                    time_in_force=None,
                    status="filled",
                )
                orders.append(order)
                self._maybe_store_order(order, storage)

            fee_rate, liquidity = self._fee_rate(order_type=fee_order_type, config=config)
            exit_fee = abs(signed_close_qty * exec_price) * fee_rate
            fill = Fill(
                fill_id=next_fill_id(),
                run_id=run_id,
                order_id=orders[-1].order_id,
                ts=exec_ts,
                side=side,
                qty=close_qty,
                price=exec_price,
                fee=exit_fee,
                liquidity=liquidity,
            )
            fills.append(fill)
            self._maybe_store_fill(fill, storage)

            gross_pnl = signed_close_qty * (exec_price - position.entry_price)
            allocated_entry_fee = position.entry_fee * fraction
            allocated_funding = position.funding_paid * fraction
            fee_paid = allocated_entry_fee + exit_fee
            funding_paid = allocated_funding
            net_pnl = gross_pnl - fee_paid - funding_paid
            notional_entry = abs(signed_close_qty * position.entry_price)

            cash += gross_pnl
            cash -= exit_fee
            if exit_order_type == "LIMIT" and fee_order_type == "MARKET":
                timeout_penalty = abs(signed_close_qty * exec_price) * max(float(config.limit_unfilled_penalty_bps), 0.0) / 10_000.0
                cash -= timeout_penalty
                net_pnl -= timeout_penalty
                fee_paid += timeout_penalty
            return_pct = (net_pnl / notional_entry) if notional_entry > 0 else 0.0

            trade = Trade(
                trade_id=next_trade_id(),
                run_id=run_id,
                symbol=config.symbol,
                side="long" if position.side == "long" else "short",
                entry_ts=position.entry_ts,
                exit_ts=exec_ts,
                qty=close_qty,
                entry_price=position.entry_price,
                exit_price=exec_price,
                gross_pnl=gross_pnl,
                fee_paid=fee_paid,
                funding_paid=funding_paid,
                net_pnl=net_pnl,
                return_pct=return_pct,
                reason=reason_signal,
            )
            trades.append(trade)
            self._maybe_store_trade(trade, storage)

            remaining_fraction = max(1.0 - fraction, 0.0)
            if remaining_fraction <= 1e-9:
                position = Position(leverage=config.leverage)
            else:
                position.qty = position.qty * remaining_fraction
                position.entry_fee = position.entry_fee * remaining_fraction
                position.funding_paid = position.funding_paid * remaining_fraction

        def open_position(
            row_index: int,
            desired: Literal["long", "short"],
            source_signal: str,
            *,
            size_multiplier: float,
        ) -> None:
            nonlocal cash, position
            mark_price = float(market.iloc[row_index]["close"])
            atr = float(market.iloc[row_index]["atr"])
            equity = current_equity(mark_price)
            notional = self._target_notional(equity=equity, mark_price=mark_price, atr=atr, config=config)
            notional *= min(max(float(size_multiplier), 0.0), 2.0)
            side: OrderSide = "BUY" if desired == "long" else "SELL"

            if notional <= 0:
                order_id = next_order_id()
                rejected = Order(
                    order_id=order_id,
                    run_id=run_id,
                    client_order_id=f"cid-{order_id[-12:]}",
                    ts=str(market.iloc[row_index]["timestamp"]),
                    signal=source_signal,
                    side=side,
                    position_side="LONG" if desired == "long" else "SHORT",
                    reduce_only=False,
                    order_type=config.order_type,
                    qty=0.0,
                    requested_price=None,
                    stop_price=None,
                    time_in_force=None,
                    status="rejected",
                    reason="Notional size resolved to zero (or gated sizing=0)",
                )
                orders.append(rejected)
                self._maybe_store_order(rejected, storage)
                return

            if str(config.order_type).upper() == "LIMIT":
                filled, limit_price, limit_ts, _ = self._attempt_limit_fill(
                    candles=market,
                    row_index=row_index,
                    side=side,
                    config=config,
                    rng=rng,
                )
                order_id = next_order_id()
                if not filled:
                    rejected = Order(
                        order_id=order_id,
                        run_id=run_id,
                        client_order_id=f"cid-{order_id[-12:]}",
                        ts=limit_ts,
                        signal=source_signal,
                        side=side,
                        position_side="LONG" if desired == "long" else "SHORT",
                        reduce_only=False,
                        order_type="LIMIT",
                        qty=0.0,
                        requested_price=limit_price,
                        stop_price=None,
                        time_in_force=None,
                        status="rejected",
                        reason="Limit entry unfilled by timeout",
                    )
                    orders.append(rejected)
                    self._maybe_store_order(rejected, storage)
                    return
                exec_price = limit_price
                exec_ts = limit_ts
                entry_order_type: OrderType = "LIMIT"
            else:
                exec_price, exec_ts, _ = self._execution_price(
                    market,
                    row_index,
                    side=side,
                    config=config,
                    apply_latency=True,
                    use_slippage=True,
                )
                order_id = next_order_id()
                entry_order_type = config.order_type
            qty_abs = notional / exec_price
            qty = qty_abs if desired == "long" else -qty_abs

            order = Order(
                order_id=order_id,
                run_id=run_id,
                client_order_id=f"cid-{order_id[-12:]}",
                ts=exec_ts,
                signal=source_signal,
                side=side,
                position_side="LONG" if desired == "long" else "SHORT",
                reduce_only=False,
                order_type=entry_order_type,
                qty=qty_abs,
                requested_price=exec_price,
                stop_price=None,
                time_in_force=None,
                status="filled",
            )
            orders.append(order)
            self._maybe_store_order(order, storage)

            fee_rate, liquidity = self._fee_rate(order_type=entry_order_type, config=config)
            entry_fee = abs(qty * exec_price) * fee_rate
            fill = Fill(
                fill_id=next_fill_id(),
                run_id=run_id,
                order_id=order.order_id,
                ts=exec_ts,
                side=side,
                qty=qty_abs,
                price=exec_price,
                fee=entry_fee,
                liquidity=liquidity,
            )
            fills.append(fill)
            self._maybe_store_fill(fill, storage)

            cash -= entry_fee
            position = Position(
                side=desired,
                qty=qty,
                entry_price=exec_price,
                entry_ts=exec_ts,
                leverage=config.leverage,
                entry_fee=entry_fee,
                funding_paid=0.0,
            )

        try:
            for idx, row in market.iterrows():
                close_price = float(row["close"])
                strategy_position = StrategyPosition(
                    side=position.side,
                    qty=position.qty,
                    entry_price=position.entry_price,
                )
                bar = Bar(
                    timestamp=row["timestamp"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=close_price,
                    volume=float(row["volume"]),
                )
                signal = self._normalize_signal(strategy.on_bar(bar, strategy_position))
                size_multiplier = self._position_size_multiplier(
                    strategy=strategy,
                    bar=bar,
                    strategy_position=strategy_position,
                )
                partial_exit_fraction = self._partial_exit_fraction(
                    strategy=strategy,
                    bar=bar,
                    strategy_position=strategy_position,
                )

                if signal == "exit":
                    close_position(idx, reason_signal="exit")
                elif signal in {"long", "short"}:
                    if position.is_open and position.side != signal:
                        close_position(idx, reason_signal=signal)
                    if (not position.is_open) and signal in {"long", "short"}:
                        open_position(
                            idx,
                            desired=signal,
                            source_signal=signal,
                            size_multiplier=size_multiplier,
                        )
                elif position.is_open and partial_exit_fraction > 0.0:
                    close_position(
                        idx,
                        reason_signal=f"partial_{partial_exit_fraction:.2f}",
                        close_fraction=partial_exit_fraction,
                    )

                if config.enable_funding and position.is_open and "funding_rate" in row.index:
                    funding_rate = row["funding_rate"]
                    if pd.notna(funding_rate):
                        funding_payment = position.qty * close_price * float(funding_rate)
                        cash -= funding_payment
                        position.funding_paid += funding_payment

                equity_curve.append(current_equity(close_price))

            if position.is_open:
                close_position(len(market) - 1, reason_signal="forced_exit")
                if equity_curve:
                    equity_curve[-1] = cash

            from trader.backtest.metrics import summarize_performance

            summary = summarize_performance(
                equity_curve=equity_curve,
                trades=trades,
                initial_equity=config.initial_equity,
            )
            if storage is not None:
                storage.finish_backtest_run(run_id, summary)
        finally:
            if created_storage and storage is not None:
                storage.close()

        return BacktestResult(
            run_id=run_id,
            summary=summary,
            initial_equity=config.initial_equity,
            equity_curve=equity_curve,
            orders=orders,
            fills=fills,
            trades=trades,
            final_position=position,
        )
