from __future__ import annotations

import hashlib
import math
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

import pandas as pd

from trader.broker.base import Broker, OrderRequest, OrderResult
from trader.data.binance_live import BinanceLiveFeed, LiveBar
from trader.logger_utils import get_logger
from trader.notify import Notifier
from trader.risk.guards import RiskGuard
from trader.storage import SQLiteStorage
from trader.strategy.base import Bar, Strategy, StrategyPosition

logger = get_logger(__name__)

Signal = Literal["long", "short", "exit", "hold"]


def _normalize_fraction(value: float) -> float:
    if value > 1.0:
        return value / 100.0
    return value


@dataclass
class RuntimeConfig:
    mode: Literal["paper", "live"] = "paper"
    symbol: str = "BTC/USDT"
    timeframe: str = "1m"
    fixed_notional_usdt: float = 1_000.0
    atr_period: int = 14
    max_bars: int = 0
    dry_run: bool = False
    one_shot: bool = False
    halt_on_error: bool = False
    resume: bool = False
    resume_run_id: str | None = None
    state_save_every_n_bars: int = 1
    enable_protective_orders: bool = True
    protective_stop_loss_pct: float = 0.0
    protective_take_profit_pct: float = 0.0
    require_protective_orders: bool = True
    protective_missing_policy: Literal["halt", "recreate"] = "halt"
    api_error_halt_threshold: int = 3
    feed_stall_timeout_sec: float = 0.0
    bar_staleness_warn_sec: float = 0.0
    bar_staleness_halt: bool = False
    bar_staleness_halt_sec: float = 0.0
    preflight_max_time_drift_ms: int = 5_000
    preflight_expected_leverage: float | None = None
    preflight_expected_margin_mode: str | None = None
    # Sleep mode profile / diagnostics
    binance_env: Literal["mainnet", "testnet"] = "testnet"
    live_trading_enabled: bool = False
    budget_guard_enabled: bool = True
    budget_usdt_mode: Literal["risk", "auto", "fixed"] = "risk"
    budget_usdt_fixed: float | None = None
    preset_name: str | None = None
    sleep_mode_enabled: bool = False
    account_allocation_pct: float = 1.0
    max_position_notional_usdt: float = 4_000.0
    min_entry_notional_usdt: float = 250.0
    risk_per_trade_pct: float = 0.0
    daily_loss_limit_pct: float = 0.0
    capital_limit_usdt: float | None = None
    consec_loss_limit: int = 0
    sl_mode: Literal["pct", "atr"] = "pct"
    sl_atr_mult: float = 1.5
    tp_mode: Literal["pct", "atr"] = "pct"
    tp_atr_mult: float = 2.0
    trailing_stop_enabled: bool = False
    trail_pct: float = 0.0
    trail_atr_mult: float = 0.0
    cooldown_bars_after_halt: int = 0
    quiet_hours: str | None = None
    heartbeat_enabled: bool = False
    heartbeat_interval_minutes: int = 30
    protective_integrity_retries: int = 2


class AccountBudgetGuard:
    def __init__(self, *, broker: Broker, quote_asset: str = "USDT") -> None:
        self.broker = broker
        self.quote_asset = str(quote_asset).upper()
        self._lock = threading.Lock()
        self._snapshot_bar_key: str | None = None
        self._available_balance = 0.0
        self._total_balance = 0.0
        self._reserved_notional = 0.0
        self._asset = self.quote_asset
        self._source = "uninitialized"

    def _as_float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _normalize_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        available = self._as_float(
            payload.get(
                "available_balance",
                payload.get(
                    "account_available_usdt",
                    payload.get("available", payload.get("free", payload.get("withdraw_available", 0.0))),
                ),
            )
        )
        total = self._as_float(
            payload.get(
                "total_balance",
                payload.get("account_total_usdt", payload.get("total", payload.get("balance", 0.0))),
            )
        )
        asset = str(payload.get("asset", self.quote_asset)).upper()
        source = str(payload.get("source", "broker.get_account_budget_snapshot"))
        return {
            "asset": asset,
            "available_balance": max(available, 0.0),
            "total_balance": max(total, 0.0),
            "source": source,
        }

    def _fallback_snapshot(self) -> dict[str, Any]:
        balance = self.broker.get_balance()
        available = 0.0
        total = 0.0
        if isinstance(balance, dict):
            available = self._as_float(balance.get(self.quote_asset, balance.get("cash", 0.0)))
            total = self._as_float(balance.get(self.quote_asset, balance.get("cash", available)))
        return {
            "asset": self.quote_asset,
            "available_balance": max(available, 0.0),
            "total_balance": max(total, 0.0),
            "account_available_usdt": max(available, 0.0),
            "account_total_usdt": max(total, 0.0),
            "source": "broker.get_balance",
        }

    def _refresh_snapshot_locked(self, *, bar_key: str) -> dict[str, Any]:
        getter = getattr(self.broker, "get_account_budget_snapshot", None)
        snapshot: dict[str, Any]
        if callable(getter):
            try:
                try:
                    raw = getter(quote_asset=self.quote_asset)
                except TypeError:
                    raw = getter()
            except Exception:
                raw = None
            if isinstance(raw, dict):
                snapshot = self._normalize_snapshot(raw)
            else:
                snapshot = self._fallback_snapshot()
        else:
            snapshot = self._fallback_snapshot()
        self._snapshot_bar_key = bar_key
        self._available_balance = float(snapshot["available_balance"])
        self._total_balance = float(snapshot["total_balance"])
        self._reserved_notional = 0.0
        self._asset = str(snapshot["asset"])
        self._source = str(snapshot["source"])
        return snapshot

    def snapshot(self, *, bar_ts: pd.Timestamp | None = None) -> dict[str, Any]:
        bar_key = str(pd.to_datetime(bar_ts, utc=True)) if bar_ts is not None else "status"
        with self._lock:
            if self._snapshot_bar_key != bar_key:
                snap = self._refresh_snapshot_locked(bar_key=bar_key)
            else:
                snap = {
                    "asset": self._asset,
                    "available_balance": self._available_balance,
                    "total_balance": self._total_balance,
                    "source": self._source,
                }
            account_available = max(self._available_balance - self._reserved_notional, 0.0)
            snap["account_available_usdt"] = account_available
            snap["account_total_usdt"] = max(self._total_balance, 0.0)
            snap["reserved_notional"] = self._reserved_notional
            snap["effective_available"] = account_available
            return snap

    def check_and_reserve(
        self,
        *,
        bar_ts: pd.Timestamp,
        order_notional: float,
        reduce_only: bool,
        budget_cap_remaining_usdt: float | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        if reduce_only:
            return True, {"reason": "reduce_only_order", "reserved_notional": 0.0}
        req_notional = max(float(order_notional), 0.0)
        if req_notional <= 0:
            return True, {"reason": "zero_notional_order", "reserved_notional": 0.0}

        bar_key = str(pd.to_datetime(bar_ts, utc=True))
        with self._lock:
            if self._snapshot_bar_key != bar_key:
                self._refresh_snapshot_locked(bar_key=bar_key)
            account_available = max(self._available_balance - self._reserved_notional, 0.0)
            budget_cap_remaining = account_available
            if budget_cap_remaining_usdt is not None:
                budget_cap_remaining = max(float(budget_cap_remaining_usdt), 0.0)
            effective_available = min(account_available, budget_cap_remaining)
            if req_notional > effective_available + 1e-9:
                return (
                    False,
                    {
                        "reason": "insufficient_budget",
                        "asset": self._asset,
                        "bar_ts": bar_key,
                        "source": self._source,
                        "available_balance": self._available_balance,
                        "account_available_usdt": account_available,
                        "reserved_notional": self._reserved_notional,
                        "effective_available": effective_available,
                        "budget_cap_remaining_usdt": budget_cap_remaining,
                        "requested_notional": req_notional,
                        "total_balance": self._total_balance,
                        "account_total_usdt": self._total_balance,
                    },
                )
            self._reserved_notional += req_notional
            next_account_available = max(self._available_balance - self._reserved_notional, 0.0)
            return (
                True,
                {
                    "reason": "ok",
                    "asset": self._asset,
                    "bar_ts": bar_key,
                    "source": self._source,
                    "available_balance": self._available_balance,
                    "account_available_usdt": next_account_available,
                    "reserved_notional": self._reserved_notional,
                    "effective_available": min(next_account_available, budget_cap_remaining),
                    "budget_cap_remaining_usdt": budget_cap_remaining,
                    "requested_notional": req_notional,
                    "total_balance": self._total_balance,
                    "account_total_usdt": self._total_balance,
                },
            )

    def release(self, *, order_notional: float) -> None:
        amount = max(float(order_notional), 0.0)
        if amount <= 0:
            return
        with self._lock:
            self._reserved_notional = max(self._reserved_notional - amount, 0.0)


class RuntimeEngine:
    def __init__(
        self,
        *,
        config: RuntimeConfig,
        strategy: Strategy,
        broker: Broker,
        feed: BinanceLiveFeed,
        storage: SQLiteStorage,
        risk_guard: RiskGuard,
        budget_guard: AccountBudgetGuard | None = None,
        notifier: Notifier | None = None,
        initial_equity: float = 10_000.0,
        run_id: str | None = None,
    ) -> None:
        self.config = config
        self.strategy = strategy
        self.broker = broker
        self.feed = feed
        self.storage = storage
        self.risk_guard = risk_guard
        self.budget_guard = budget_guard
        self.notifier = notifier
        self.run_id = run_id or uuid4().hex
        self.initial_equity = float(initial_equity)
        self.position_qty = 0.0
        self.position_entry_price = 0.0
        self.position_entry_ts = ""
        self.position_fee_pool = 0.0
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self.cash = initial_equity
        self.peak_equity = initial_equity
        self.last_signal: Signal = "hold"
        self.halted = False
        self._halt_reason = ""
        self._halt_notified = False
        self._consecutive_api_errors = 0
        self._trade_seq = 0
        self._bars: list[LiveBar] = []
        self._processed_bars = 0
        self._resume_last_bar_ts: pd.Timestamp | None = None
        self._open_orders: dict[str, dict[str, Any]] = {}
        self._protective_pair: dict[str, str] = {}
        self._strategy_state: dict[str, Any] = {}
        self._consecutive_losses = 0
        self._last_heartbeat_sent_at: datetime | None = None
        self._last_order_block_reason = ""
        self._rejected_by_min_notional_count = 0
        self._protective_fail_count = 0
        self._min_entry_notional_block_count = 0
        self._min_entry_notional_samples: list[dict[str, Any]] = []
        self._session_started = False
        self._session_processed = 0
        self._last_processed_bar_ts: pd.Timestamp | None = None
        self._last_bar_recv_monotonic: float | None = None
        if self.budget_guard is None and self.config.mode == "live" and self.config.budget_guard_enabled:
            self.budget_guard = AccountBudgetGuard(broker=self.broker)

        if self.config.resume:
            self._restore_runtime_state()
        set_feed_event_cb = getattr(self.feed, "set_event_callback", None)
        if callable(set_feed_event_cb):
            try:
                set_feed_event_cb(self._event)
            except Exception:
                pass
        attach_storage = getattr(self.broker, "attach_storage", None)
        if callable(attach_storage):
            try:
                attach_storage(storage=self.storage, run_id=self.run_id)
            except Exception:
                pass
        if self.config.resume and self.config.mode == "live":
            reconcile = getattr(self.broker, "reconcile_runtime_state", None)
            if callable(reconcile):
                ok, reason = reconcile(
                    symbol=self.config.symbol,
                    open_positions=self._position_payload(),
                    open_orders=self._open_orders,
                )
                if not ok:
                    self.halted = True
                    self._halt_reason = "resume reconcile failed"
                    self._event("resume_reconcile_failed", {"reason": reason})

    def _notify(self, message: str) -> None:
        if self.notifier is not None:
            try:
                self.notifier.send(message)
            except Exception:
                pass

    def _event(self, event_type: str, payload: dict[str, object]) -> None:
        self.storage.write_event(
            datetime.now(timezone.utc).isoformat(),
            event_type,
            {"run_id": self.run_id, "symbol": self.config.symbol, **payload},
        )

    def _latest_mark_price(self) -> float:
        if self._bars:
            return float(self._bars[-1].close)
        return float(self.position_entry_price) if self.position_entry_price > 0 else 0.0

    def _broker_label(self) -> str:
        if self.config.mode == "live":
            return "live_binance"
        name = self.broker.__class__.__name__.lower()
        if "paper" in name:
            return "paper"
        return name

    def _runtime_profile_payload(self) -> dict[str, Any]:
        return {
            "mode": self.config.mode,
            "symbol": self.config.symbol,
            "timeframe": self.config.timeframe,
            "broker": self._broker_label(),
            "env": self.config.binance_env,
            "live_trading": self.config.live_trading_enabled,
            "budget_guard": self.config.budget_guard_enabled,
            "budget_usdt_mode": self.config.budget_usdt_mode,
            "budget_usdt_fixed": self.config.budget_usdt_fixed,
            "dry_run": self.config.dry_run,
            "sleep_mode": self.config.sleep_mode_enabled,
            "preset": self.config.preset_name,
            "allocation_pct": self.config.account_allocation_pct,
            "leverage": self.config.preflight_expected_leverage,
            "daily_loss_limit_pct": self.config.daily_loss_limit_pct,
            "max_drawdown_pct": self.risk_guard.max_drawdown_pct,
            "risk_per_trade_pct": self.config.risk_per_trade_pct,
            "max_position_notional_usdt": self.config.max_position_notional_usdt,
            "min_entry_notional_usdt": self.config.min_entry_notional_usdt,
            "protective_mode": self.config.protective_missing_policy,
            "sl_mode": self.config.sl_mode,
            "sl_pct": self.config.protective_stop_loss_pct,
            "sl_atr_mult": self.config.sl_atr_mult,
            "tp_mode": self.config.tp_mode,
            "tp_pct": self.config.protective_take_profit_pct,
            "tp_atr_mult": self.config.tp_atr_mult,
        }

    def _position_summary_payload(self, *, mark_price: float) -> dict[str, Any]:
        side = self._position_side()
        qty = abs(self.position_qty)
        unrealized = self.position_qty * (mark_price - self.position_entry_price) if self.position_qty != 0 else 0.0
        return {
            "side": side,
            "qty": qty,
            "entry_price": self.position_entry_price,
            "mark_price": mark_price,
            "unrealized_pnl": unrealized,
        }

    def _notify_halt(self, reason: str, error_summary: str | None = None) -> None:
        if self._halt_notified:
            return
        self._halt_notified = True
        recent_events: list[dict[str, Any]] = []
        list_recent = getattr(self.storage, "list_recent_events_for_run", None)
        if callable(list_recent):
            try:
                recent_events = list_recent(self.run_id, limit=5)  # type: ignore[assignment]
            except Exception:
                recent_events = []
        lines = [
            "runtime halted",
            f"run_id={self.run_id}",
            f"symbol={self.config.symbol}",
            f"mode={self.config.mode}",
            f"env={self.config.binance_env}",
            f"live_trading={self.config.live_trading_enabled}",
            f"dry_run={self.config.dry_run}",
            f"reason={reason}",
        ]
        pos = self._position_summary_payload(mark_price=self._latest_mark_price())
        lines.append(
            "position="
            f"side={pos['side']} qty={float(pos['qty']):.6f} "
            f"entry={float(pos['entry_price']):.4f} "
            f"unrealized={float(pos['unrealized_pnl']):.4f}"
        )
        if error_summary:
            lines.append(f"error={error_summary}")
        if recent_events:
            lines.append("recent_events:")
            for evt in recent_events:
                lines.append(f"- {evt.get('ts')} {evt.get('event_type')} {evt.get('payload')}")
        self._notify("\n".join(lines))

    def _halt(
        self,
        *,
        reason: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        error_summary: str | None = None,
    ) -> None:
        if self.halted:
            return
        self.halted = True
        self._halt_reason = reason
        body = {"reason": reason}
        if payload:
            body.update(payload)
        self._event(event_type, body)
        try:
            last_ts = self._resume_last_bar_ts
            if self._bars:
                last_ts = pd.to_datetime(self._bars[-1].timestamp, utc=True)
            self._save_runtime_state(last_bar_ts=last_ts)
        except Exception:
            pass
        self._notify_halt(reason, error_summary=error_summary)

    def request_halt(
        self,
        *,
        reason: str,
        event_type: str = "runtime_halt_requested",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._halt(reason=reason, event_type=event_type, payload=payload)

    def _register_api_error(self, *, where: str, error: Exception) -> None:
        self._consecutive_api_errors += 1
        self._event(
            "api_error",
            {
                "where": where,
                "error": str(error),
                "consecutive": self._consecutive_api_errors,
                "threshold": self.config.api_error_halt_threshold,
            },
        )
        if self._consecutive_api_errors >= self.config.api_error_halt_threshold:
            self._halt(
                reason=f"consecutive api errors >= {self.config.api_error_halt_threshold}",
                event_type="api_error_halt",
                payload={"where": where, "error": str(error)},
                error_summary=str(error),
            )

    def _run_preflight_checks(self) -> None:
        preflight = getattr(self.broker, "preflight_check", None)
        if not callable(preflight):
            self._event("preflight_skipped", {"reason": "broker has no preflight_check"})
            return
        try:
            ok, checks = preflight(
                symbol=self.config.symbol,
                max_time_drift_ms=self.config.preflight_max_time_drift_ms,
                expected_leverage=self.config.preflight_expected_leverage,
                expected_margin_mode=self.config.preflight_expected_margin_mode,
            )
        except Exception as exc:
            self._halt(
                reason="preflight execution failed",
                event_type="preflight_failed",
                payload={"error": str(exc)},
                error_summary=str(exc),
            )
            return
        for row in checks:
            if isinstance(row, dict):
                event_type = str(row.get("event_type", "preflight_check"))
                payload = dict(row)
                payload.pop("event_type", None)
                self._event(event_type, payload)
            else:
                self._event("preflight_check", {"detail": str(row)})
        if not ok:
            self._halt(reason="preflight check failed", event_type="preflight_failed")

    def _normalize_signal(self, signal: str) -> Signal:
        s = signal.lower()
        if s == "buy":
            return "long"
        if s == "sell":
            return "exit"
        if s in {"long", "short", "exit", "hold"}:
            return s  # type: ignore[return-value]
        return "hold"

    def _position_side(self) -> Literal["flat", "long", "short"]:
        if self.position_qty > 0:
            return "long"
        if self.position_qty < 0:
            return "short"
        return "flat"

    def _equity(self, mark_price: float) -> float:
        unrealized = 0.0
        if self.position_qty != 0:
            unrealized = self.position_qty * (mark_price - self.position_entry_price)
        return self.cash + unrealized

    def _atr_pct(self) -> float:
        atr = self._atr_value()
        if atr <= 0 or not self._bars:
            return 0.0
        last_close = float(self._bars[-1].close)
        return (atr / last_close) if last_close > 0 else 0.0

    def _atr_value(self) -> float:
        if len(self._bars) < 2:
            return 0.0
        frame = pd.DataFrame(
            {
                "high": [b.high for b in self._bars[-self.config.atr_period :]],
                "low": [b.low for b in self._bars[-self.config.atr_period :]],
                "close": [b.close for b in self._bars[-self.config.atr_period :]],
            }
        )
        prev_close = frame["close"].shift(1)
        tr = pd.concat(
            [
                frame["high"] - frame["low"],
                (frame["high"] - prev_close).abs(),
                (frame["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return float(tr.mean()) if not tr.empty else 0.0

    def _effective_sl_pct(self, *, mark_price: float) -> float:
        if self.config.sl_mode == "atr":
            atr = self._atr_value()
            if atr <= 0 or mark_price <= 0:
                return 0.0
            return (atr * self.config.sl_atr_mult) / mark_price if self.config.sl_atr_mult > 0 else 0.0
        return max(self.config.protective_stop_loss_pct, 0.0)

    def _effective_tp_pct(self, *, mark_price: float) -> float:
        if self.config.tp_mode == "atr":
            atr = self._atr_value()
            if atr <= 0 or mark_price <= 0:
                return 0.0
            return (atr * self.config.tp_atr_mult) / mark_price if self.config.tp_atr_mult > 0 else 0.0
        return max(self.config.protective_take_profit_pct, 0.0)

    def _position_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.config.symbol,
            "qty": self.position_qty,
            "entry_price": self.position_entry_price,
            "entry_ts": self.position_entry_ts,
            "fee_pool": self.position_fee_pool,
        }

    def _protective_orders_snapshot(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        broker_open: dict[str, Any] = {}
        get_open_orders = getattr(self.broker, "get_open_orders", None)
        if callable(get_open_orders):
            try:
                maybe = get_open_orders(symbol=self.config.symbol)
                if isinstance(maybe, dict):
                    broker_open = maybe
            except TypeError:
                try:
                    maybe = get_open_orders(self.config.symbol)
                    if isinstance(maybe, dict):
                        broker_open = maybe
                except Exception:
                    broker_open = {}
            except Exception:
                broker_open = {}
        for oid, meta in self._open_orders.items():
            if not isinstance(meta, dict):
                continue
            req = meta.get("request")
            if not isinstance(req, OrderRequest):
                continue
            broker_meta = broker_open.get(oid)
            status = str(broker_meta.get("status", "NEW")) if isinstance(broker_meta, dict) else "NEW"
            rows.append(
                {
                    "order_id": oid,
                    "type": str(meta.get("kind", "unknown")),
                    "price": req.stop_price,
                    "reduce_only": bool(req.reduce_only),
                    "status": status,
                }
            )
        return rows

    def _emit_execution_snapshot(
        self,
        *,
        event: str,
        bar: LiveBar,
        price_source: str,
        fill_price: float | None = None,
    ) -> None:
        self._event(
            "execution_snapshot",
            {
                "event": event,
                "price_source": price_source,
                "last_price": float(bar.close),
                "entry_price": float(self.position_entry_price),
                "position_qty": float(self.position_qty),
                "fill_price": (float(fill_price) if fill_price is not None else None),
                "protective_orders": self._protective_orders_snapshot(),
            },
        )

    def _required_protective_kinds(self) -> set[str]:
        required: set[str] = set()
        if (self.config.sl_mode == "pct" and self.config.protective_stop_loss_pct > 0) or (
            self.config.sl_mode == "atr" and self.config.sl_atr_mult > 0
        ):
            required.add("sl")
        if (self.config.tp_mode == "pct" and self.config.protective_take_profit_pct > 0) or (
            self.config.tp_mode == "atr" and self.config.tp_atr_mult > 0
        ):
            required.add("tp")
        return required

    def _emergency_close_position(self, *, bar: LiveBar, reason: str) -> bool:
        side = self._position_side()
        if side == "flat":
            return True
        close_side: Literal["BUY", "SELL"] = "SELL" if side == "long" else "BUY"
        qty = abs(float(self.position_qty))
        if qty <= 0:
            return True
        req = OrderRequest(
            symbol=self.config.symbol,
            side=close_side,
            amount=qty,
            order_type="MARKET",
            client_order_id=self._make_client_order_id(bar=bar, intent=f"emergency-close-{side}"),
            reduce_only=True,
            position_side="BOTH",
        )
        try:
            result = self.broker.place_order(req)
        except Exception as exc:
            self._event(
                "protective_emergency_close_failed",
                {"reason": reason, "error": str(exc), "side": side, "qty": qty},
            )
            return False

        self._save_order(bar, "exit", req, result)
        if result.status == "FILLED":
            if not bool(getattr(self.broker, "handles_fill_persistence", False)):
                self._save_fill(bar, req, result)
            self._apply_fill(
                bar=bar,
                side=close_side,
                qty=result.filled_qty,
                price=result.avg_price,
                fee=result.fee,
                reason="protective_emergency_close",
            )
        else:
            self._event(
                "protective_emergency_close_unfilled",
                {"reason": reason, "status": result.status, "message": result.message, "side": side, "qty": qty},
            )

        if self.config.mode == "live":
            snapshot = self._fetch_live_position_snapshot()
            if snapshot is not None:
                self._apply_live_position_snapshot(bar=bar, snapshot=snapshot, source="emergency_close_sync")
        return self._position_side() == "flat"

    def _handle_protective_failure(
        self,
        *,
        bar: LiveBar,
        reason: str,
        missing: list[str],
        error: str | None = None,
    ) -> None:
        self._protective_fail_count += 1
        self._event(
            "protective_order_failed",
            {
                "reason": reason,
                "missing": ",".join(missing),
                "error": error or "",
                "attempted_emergency_close": True,
            },
        )
        closed = self._emergency_close_position(bar=bar, reason=reason)
        if self._position_side() == "flat":
            self._cancel_all_protective_orders(bar=bar, reason="protective_failure_cleanup")
        self._halt(
            reason="protective_order_failed_emergency_close",
            event_type="protective_orders_halt",
            payload={
                "failure_reason": reason,
                "missing": ",".join(missing),
                "error": error or "",
                "emergency_close_success": closed,
            },
            error_summary=error,
        )

    def _enforce_protective_integrity(self, *, bar: LiveBar) -> None:
        if not self.config.enable_protective_orders or not self.config.require_protective_orders:
            return
        if self._position_side() == "flat":
            return
        required = self._required_protective_kinds()
        if not required:
            return
        current = {
            str(meta.get("kind", "")).lower()
            for meta in self._open_orders.values()
            if isinstance(meta, dict)
        }
        missing = sorted(required - current)
        if not missing:
            return
        self._event(
            "protective_orders_missing",
            {"missing": ",".join(missing), "policy": self.config.protective_missing_policy},
        )
        retries = max(int(self.config.protective_integrity_retries), 0)
        for attempt in range(retries):
            if self.halted:
                return
            self._event(
                "protective_orders_recreate_attempt",
                {
                    "attempt": attempt + 1,
                    "max_attempts": retries,
                    "missing": ",".join(missing),
                    "policy": self.config.protective_missing_policy,
                },
            )
            self._cancel_all_protective_orders(bar=bar, reason="recreate_missing_protective")
            self._maybe_create_protective_orders(bar=bar, fail_on_missing=False)
            current = {
                str(meta.get("kind", "")).lower()
                for meta in self._open_orders.values()
                if isinstance(meta, dict)
            }
            if required.issubset(current):
                self._event("protective_orders_recreated", {"required": ",".join(sorted(required))})
                return
            missing = sorted(required - current)
        self._handle_protective_failure(
            bar=bar,
            reason="position exists without required protective orders",
            missing=missing,
        )

    def _save_order(self, bar: LiveBar, signal: str, request: OrderRequest, result: OrderResult) -> None:
        side = str(request.side).upper()
        self.storage.save_order(
            {
                "run_id": self.run_id,
                "symbol": self.config.symbol,
                "order_id": result.order_id,
                "client_order_id": result.client_order_id,
                "ts": str(bar.timestamp),
                "signal": signal,
                "side": side,
                "position_side": request.position_side,
                "reduce_only": request.reduce_only,
                "order_type": str(request.order_type).upper(),
                "qty": request.amount,
                "requested_price": request.price,
                "stop_price": request.stop_price,
                "time_in_force": request.time_in_force,
                "status": result.status.lower(),
                "reason": result.message,
            }
        )

    def _save_fill(self, bar: LiveBar, request: OrderRequest, result: OrderResult) -> None:
        self.storage.save_fill(
            {
                "run_id": self.run_id,
                "symbol": self.config.symbol,
                "fill_id": f"{self.run_id}-fill-{result.order_id}",
                "order_id": result.order_id,
                "ts": str(bar.timestamp),
                "side": str(request.side).upper(),
                "qty": result.filled_qty,
                "price": result.avg_price,
                "fee": result.fee,
                "liquidity": "taker"
                if str(request.order_type).upper() in {"MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"}
                else "maker",
            }
        )

    def _record_trade(
        self,
        *,
        side: Literal["long", "short"],
        entry_ts: str,
        exit_ts: str,
        qty: float,
        entry_price: float,
        exit_price: float,
        gross_pnl: float,
        fee_paid: float,
        reason: str,
    ) -> None:
        self._trade_seq += 1
        notional = abs(qty * entry_price)
        net_pnl = gross_pnl - fee_paid
        return_pct = net_pnl / notional if notional > 0 else 0.0
        self.storage.save_trade(
            {
                "run_id": self.run_id,
                "trade_id": f"{self.run_id}-rt-{self._trade_seq:06d}",
                "symbol": self.config.symbol,
                "side": side,
                "entry_ts": entry_ts,
                "exit_ts": exit_ts,
                "qty": qty,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "gross_pnl": gross_pnl,
                "fee_paid": fee_paid,
                "funding_paid": 0.0,
                "net_pnl": net_pnl,
                "return_pct": return_pct,
                "reason": reason,
            }
        )
        if net_pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
        if self.config.consec_loss_limit > 0 and self._consecutive_losses >= self.config.consec_loss_limit:
            self._halt(
                reason=f"consecutive loss limit reached ({self._consecutive_losses})",
                event_type="consecutive_loss_halt",
                payload={"consecutive_losses": self._consecutive_losses, "limit": self.config.consec_loss_limit},
            )

    def _apply_fill(self, *, bar: LiveBar, side: str, qty: float, price: float, fee: float, reason: str) -> None:
        side_up = side.upper()
        signed_fill = qty if side_up == "BUY" else -qty
        q_before = self.position_qty
        entry_before = self.position_entry_price
        ts_before = self.position_entry_ts
        self.cash -= fee
        self.fees_paid += fee

        if q_before == 0 or (q_before > 0 and signed_fill > 0) or (q_before < 0 and signed_fill < 0):
            new_qty = q_before + signed_fill
            if q_before == 0:
                self.position_entry_price = price
                self.position_entry_ts = str(bar.timestamp)
            else:
                weighted = abs(q_before) * self.position_entry_price + abs(signed_fill) * price
                self.position_entry_price = weighted / abs(new_qty)
            self.position_qty = new_qty
            self.position_fee_pool += fee
            self._emit_execution_snapshot(event="fill_applied", bar=bar, price_source="order_fill_avg_price", fill_price=price)
            return

        close_qty = min(abs(q_before), abs(signed_fill))
        side_before: Literal["long", "short"] = "long" if q_before > 0 else "short"
        pnl = close_qty * (price - entry_before) * (1 if q_before > 0 else -1)
        self.cash += pnl
        self.realized_pnl += pnl
        entry_fee_alloc = self.position_fee_pool * (close_qty / abs(q_before)) if abs(q_before) > 0 else 0.0
        self.position_fee_pool = max(self.position_fee_pool - entry_fee_alloc, 0.0)
        trade_fee = entry_fee_alloc + fee
        self._record_trade(
            side=side_before,
            entry_ts=ts_before,
            exit_ts=str(bar.timestamp),
            qty=close_qty,
            entry_price=entry_before,
            exit_price=price,
            gross_pnl=pnl,
            fee_paid=trade_fee,
            reason=reason,
        )

        self.position_qty = q_before + signed_fill
        if abs(self.position_qty) < 1e-12:
            self.position_qty = 0.0
            self.position_entry_price = 0.0
            self.position_entry_ts = ""
            self.position_fee_pool = 0.0
        else:
            self.position_entry_price = price
            self.position_entry_ts = str(bar.timestamp)
            self.position_fee_pool = fee
        self._emit_execution_snapshot(event="fill_applied", bar=bar, price_source="order_fill_avg_price", fill_price=price)

    def _order_qty(self, price: float) -> float:
        return max(self.config.fixed_notional_usdt / max(price, 1e-9), 0.0)

    def _make_client_order_id(self, *, bar: LiveBar, intent: str) -> str:
        raw = f"{self.run_id}:{self.config.symbol}:{int(bar.timestamp.timestamp())}:{intent}"
        return f"rt-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:24]}"

    def _round_up_to_step(self, *, value: float, step: float) -> float:
        if step <= 0:
            return max(float(value), 0.0)
        units = math.ceil(max(float(value), 0.0) / step - 1e-12)
        return max(units * step, 0.0)

    def _get_symbol_order_constraints(self) -> dict[str, float]:
        getter = getattr(self.broker, "get_symbol_order_constraints", None)
        if not callable(getter):
            return {"min_notional": 0.0, "min_qty": 0.0, "step_size": 0.0, "tick_size": 0.0}
        try:
            try:
                raw = getter(symbol=self.config.symbol)
            except TypeError:
                raw = getter(self.config.symbol)
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        return {
            "min_notional": float(raw.get("min_notional", 0.0) or 0.0),
            "min_qty": float(raw.get("min_qty", 0.0) or 0.0),
            "step_size": float(raw.get("step_size", 0.0) or 0.0),
            "tick_size": float(raw.get("tick_size", 0.0) or 0.0),
        }

    def _account_budget_snapshot(self, *, bar_ts: pd.Timestamp | None = None) -> dict[str, float | str]:
        def _to_float(value: Any) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        if self.budget_guard is None or not self.config.budget_guard_enabled:
            return {
                "account_available_usdt": 0.0,
                "account_total_usdt": 0.0,
                "source": "budget_guard_disabled",
            }
        snap_ts = bar_ts if bar_ts is not None else pd.Timestamp.now(tz="UTC")
        try:
            snapshot = self.budget_guard.snapshot(bar_ts=snap_ts)
        except Exception:
            snapshot = {}
        return {
            "account_available_usdt": _to_float(
                snapshot.get("account_available_usdt", snapshot.get("available_balance", 0.0)) if isinstance(snapshot, dict) else 0.0
            ),
            "account_total_usdt": _to_float(
                snapshot.get("account_total_usdt", snapshot.get("total_balance", 0.0)) if isinstance(snapshot, dict) else 0.0
            ),
            "source": str(snapshot.get("source", "budget_guard_unavailable")) if isinstance(snapshot, dict) else "budget_guard_unavailable",
        }

    def _resolve_budget_cap(
        self,
        *,
        equity: float,
        current_exposure_notional: float,
        account_available_usdt: float,
    ) -> tuple[float, float, str]:
        mode = str(self.config.budget_usdt_mode or "risk").lower()
        cap_source = "risk"
        budget_cap_usdt = self.risk_guard.budget_usdt(equity=equity)
        if mode == "fixed" and (self.config.budget_usdt_fixed or 0.0) > 0:
            budget_cap_usdt = float(self.config.budget_usdt_fixed or 0.0)
            cap_source = "fixed"
        elif mode == "auto":
            if account_available_usdt > 0:
                budget_cap_usdt = float(account_available_usdt)
                cap_source = "auto_available_usdt"
            else:
                cap_source = "auto_fallback_risk"
        budget_cap_remaining_usdt = max(float(budget_cap_usdt) - float(current_exposure_notional), 0.0)
        return max(float(budget_cap_usdt), 0.0), budget_cap_remaining_usdt, cap_source

    def _apply_minimum_entry_constraints(
        self,
        *,
        signal: Signal,
        intent: str,
        price: float,
        qty: float,
    ) -> tuple[float, float, bool]:
        constraints = self._get_symbol_order_constraints()
        min_notional = max(float(constraints.get("min_notional", 0.0)), 0.0)
        min_qty = max(float(constraints.get("min_qty", 0.0)), 0.0)
        step_size = max(float(constraints.get("step_size", 0.0)), 0.0)
        qty_before = max(float(qty), 0.0)
        qty_after = qty_before
        attempted_notional = qty_before * max(float(price), 0.0)

        if step_size > 0:
            qty_after = self._round_up_to_step(value=qty_after, step=step_size)
        if min_qty > 0 and qty_after < min_qty:
            qty_after = self._round_up_to_step(value=min_qty, step=step_size if step_size > 0 else min_qty)

        if min_notional > 0 and attempted_notional + 1e-9 < min_notional:
            self._rejected_by_min_notional_count += 1
            self._last_order_block_reason = "entry_notional_too_small"
            self._event(
                "entry_notional_too_small",
                {
                    "signal": signal,
                    "intent": intent,
                    "attempted_notional": attempted_notional,
                    "final_notional": qty_after * price,
                    "min_notional": min_notional,
                    "qty_before_round": qty_before,
                    "qty_after_round": qty_after,
                    "skip_reason": "attempted_notional_below_min_notional",
                    "min_qty": min_qty,
                    "step_size": step_size,
                    "tick_size": float(constraints.get("tick_size", 0.0)),
                },
            )
            return 0.0, 0.0, True

        if min_notional > 0 and price > 0 and (qty_after * price) + 1e-9 < min_notional:
            needed_qty = min_notional / price
            qty_after = self._round_up_to_step(value=max(qty_after, needed_qty), step=step_size if step_size > 0 else needed_qty)
            if min_qty > 0 and qty_after < min_qty:
                qty_after = self._round_up_to_step(value=min_qty, step=step_size if step_size > 0 else min_qty)

        final_notional = qty_after * max(price, 0.0)
        if abs(qty_after - qty_before) > 1e-12:
            self._event(
                "entry_size_rounded",
                {
                    "signal": signal,
                    "intent": intent,
                    "attempted_notional": attempted_notional,
                    "final_notional": final_notional,
                    "min_notional": min_notional,
                    "qty_before_round": qty_before,
                    "qty_after_round": qty_after,
                    "skip_reason": "",
                    "min_qty": min_qty,
                    "step_size": step_size,
                    "tick_size": float(constraints.get("tick_size", 0.0)),
                },
            )
        return qty_after, final_notional, False

    def _record_min_entry_notional_block(
        self,
        *,
        requested_notional: float,
        price: float,
        qty_before_round: float,
        qty_after_round: float,
    ) -> None:
        self._min_entry_notional_block_count += 1
        sample = {
            "symbol": self.config.symbol,
            "requested_notional": float(requested_notional),
            "min_entry_notional": float(self.config.min_entry_notional_usdt),
            "price": float(price),
            "qty_before_round": float(qty_before_round),
            "qty_after_round": float(qty_after_round),
        }
        self._min_entry_notional_samples.append(sample)
        if len(self._min_entry_notional_samples) > 5:
            self._min_entry_notional_samples = self._min_entry_notional_samples[-5:]

    def _fetch_live_position_snapshot(self) -> dict[str, float] | None:
        getter = getattr(self.broker, "get_position_snapshot", None)
        if not callable(getter):
            return None
        try:
            try:
                raw = getter(symbol=self.config.symbol)
            except TypeError:
                raw = getter(self.config.symbol)
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        return {
            "qty": float(raw.get("qty", 0.0)),
            "entry_price": float(raw.get("entry_price", 0.0)),
        }

    def _apply_live_position_snapshot(self, *, bar: LiveBar, snapshot: dict[str, float], source: str) -> None:
        prev_qty = float(self.position_qty)
        prev_entry = float(self.position_entry_price)
        live_qty = float(snapshot.get("qty", 0.0))
        live_entry = float(snapshot.get("entry_price", 0.0))
        same_qty = abs(live_qty - prev_qty) < 1e-12
        same_entry = abs(live_entry - prev_entry) < 1e-9
        if same_qty and (abs(live_qty) < 1e-12 or same_entry):
            return

        self.position_qty = live_qty
        if abs(live_qty) < 1e-12:
            self.position_qty = 0.0
            self.position_entry_price = 0.0
            self.position_entry_ts = ""
            self.position_fee_pool = 0.0
        else:
            if live_entry > 0:
                self.position_entry_price = live_entry
            if not self.position_entry_ts:
                self.position_entry_ts = str(bar.timestamp)
        self._event(
            "position_synced_from_broker",
            {
                "source": source,
                "prev_qty": prev_qty,
                "prev_entry_price": prev_entry,
                "live_qty": self.position_qty,
                "live_entry_price": self.position_entry_price,
            },
        )

    def _recover_reduce_only_rejection(
        self,
        *,
        bar: LiveBar,
        signal: Signal,
        intent: str,
        side: Literal["BUY", "SELL"],
        error_text: str,
    ) -> bool:
        snapshot = self._fetch_live_position_snapshot()
        if snapshot is None:
            return False
        self._apply_live_position_snapshot(bar=bar, snapshot=snapshot, source="reduce_only_reject")
        still_reducible = (side == "BUY" and self.position_qty < 0) or (side == "SELL" and self.position_qty > 0)
        self._event(
            "reduce_only_rejected_recovered",
            {
                "signal": signal,
                "intent": intent,
                "side": side,
                "still_reducible": still_reducible,
                "error": error_text,
            },
        )
        return True

    def _retry_protective_after_algo_limit_cleanup(
        self,
        *,
        req: OrderRequest,
        signal: Signal,
        intent: str,
        error_text: str,
    ) -> OrderResult | None:
        cleanup = getattr(self.broker, "cancel_all_algo_orders", None)
        if not callable(cleanup):
            return None
        keep_client_ids = {
            str(meta["request"].client_order_id)
            for meta in self._open_orders.values()
            if isinstance(meta, dict)
            and isinstance(meta.get("request"), OrderRequest)
            and meta["request"].client_order_id
        }
        canceled = 0
        try:
            try:
                canceled = int(
                    cleanup(
                        symbol=self.config.symbol,
                        keep_client_order_ids=keep_client_ids,
                    )
                    or 0
                )
            except TypeError:
                canceled = int(cleanup(symbol=self.config.symbol) or 0)
        except Exception as exc:
            self._event(
                "algo_order_limit_recovery_failed",
                {"signal": signal, "intent": intent, "stage": "cleanup", "error": str(exc)},
            )
            return None

        self._event(
            "algo_order_limit_recovery",
            {
                "signal": signal,
                "intent": intent,
                "canceled_algo_orders": canceled,
                "error": error_text,
            },
        )
        try:
            return self.broker.place_order(req)
        except Exception as exc:
            self._event(
                "algo_order_limit_recovery_failed",
                {"signal": signal, "intent": intent, "stage": "retry", "error": str(exc)},
            )
            return None

    def _place_order(
        self,
        *,
        bar: LiveBar,
        signal: Signal,
        side: Literal["BUY", "SELL"],
        reduce_only: bool,
        order_type: str = "MARKET",
        stop_price: float | None = None,
        time_in_force: str | None = None,
        qty: float | None = None,
        intent: str,
    ) -> tuple[OrderRequest, OrderResult] | None:
        requested_qty = qty if qty is not None else (abs(self.position_qty) if reduce_only else self._order_qty(bar.close))
        if requested_qty <= 0:
            return None
        order_notional = requested_qty * bar.close
        self._last_order_block_reason = ""

        if not reduce_only:
            equity_now = self._equity(bar.close)
            current_notional = abs(self.position_qty) * bar.close
            budget_snapshot = self._account_budget_snapshot(bar_ts=pd.to_datetime(bar.timestamp, utc=True))
            account_available_usdt = float(budget_snapshot.get("account_available_usdt", 0.0))
            _budget_cap_usdt, budget_cap_remaining_usdt, _budget_cap_source = self._resolve_budget_cap(
                equity=equity_now,
                current_exposure_notional=current_notional,
                account_available_usdt=account_available_usdt,
            )
            sl_distance_pct = None
            if stop_price is not None and bar.close > 0:
                sl_distance_pct = abs(stop_price - bar.close) / bar.close
            elif self.config.protective_stop_loss_pct > 0:
                sl_distance_pct = self._effective_sl_pct(mark_price=bar.close)
            requested_notional_before = order_notional
            allowed_notional, adjust_reason = self.risk_guard.suggest_entry_notional(
                equity=equity_now,
                current_position_notional=current_notional,
                requested_order_notional=order_notional,
                realized_pnl_today=self.realized_pnl,
                sl_distance_pct=sl_distance_pct,
            )
            if allowed_notional <= 0:
                self._event("risk_blocked_order", {"reason": adjust_reason, "signal": signal, "intent": intent})
                self._notify(f"[{self.config.mode}] risk blocked order: {adjust_reason}")
                return None
            if allowed_notional + 1e-12 < order_notional and bar.close > 0:
                requested_qty = allowed_notional / bar.close
                order_notional = allowed_notional
                self._event(
                    "risk_size_clamped",
                    {
                        "reason": adjust_reason,
                        "requested_notional": requested_notional_before,
                        "allowed_notional": allowed_notional,
                    },
                )
            min_entry_floor = max(float(self.config.min_entry_notional_usdt), 0.0)
            if min_entry_floor > 0 and order_notional + 1e-9 < min_entry_floor:
                self._record_min_entry_notional_block(
                    requested_notional=order_notional,
                    price=float(bar.close),
                    qty_before_round=requested_qty,
                    qty_after_round=requested_qty,
                )
                self._last_order_block_reason = "entry_notional_below_floor"
                self._event(
                    "entry_notional_below_floor",
                    {
                        "signal": signal,
                        "intent": intent,
                        "symbol": self.config.symbol,
                        "requested_notional": order_notional,
                        "min_entry_notional": min_entry_floor,
                        "price": float(bar.close),
                        "qty_before_round": requested_qty,
                        "qty_after_round": requested_qty,
                    },
                )
                return None
            requested_qty, order_notional, skipped_by_min_guard = self._apply_minimum_entry_constraints(
                signal=signal,
                intent=intent,
                price=float(bar.close),
                qty=requested_qty,
            )
            if skipped_by_min_guard:
                return None
            allowed_after_round, _ = self.risk_guard.suggest_entry_notional(
                equity=equity_now,
                current_position_notional=current_notional,
                requested_order_notional=order_notional,
                realized_pnl_today=self.realized_pnl,
                sl_distance_pct=sl_distance_pct,
            )
            if order_notional > max(allowed_after_round, 0.0) + 1e-9:
                self._event(
                    "entry_notional_too_small",
                    {
                        "signal": signal,
                        "intent": intent,
                        "attempted_notional": requested_notional_before,
                        "final_notional": order_notional,
                        "min_notional": float(self._get_symbol_order_constraints().get("min_notional", 0.0)),
                        "qty_before_round": requested_notional_before / max(float(bar.close), 1e-9),
                        "qty_after_round": requested_qty,
                        "skip_reason": "min_notional_roundup_exceeds_risk_budget",
                    },
                )
                self._last_order_block_reason = "entry_notional_too_small"
                self._rejected_by_min_notional_count += 1
                return None
            ok, reason = self.risk_guard.check_order(
                current_position_notional=current_notional,
                order_notional=order_notional,
                realized_pnl_today=self.realized_pnl,
                equity=equity_now,
            )
            if not ok:
                self._event("risk_blocked_order", {"reason": reason, "signal": signal})
                self._notify(f"[{self.config.mode}] risk blocked order: {reason}")
                return None
        else:
            budget_cap_remaining_usdt = None

        req = OrderRequest(
            symbol=self.config.symbol,
            side=side,
            amount=requested_qty,
            order_type=order_type,
            stop_price=stop_price,
            client_order_id=self._make_client_order_id(bar=bar, intent=intent),
            reduce_only=reduce_only,
            time_in_force=time_in_force,
            position_side="BOTH",
        )

        reserved_notional = 0.0
        if (
            self.config.budget_guard_enabled
            and not reduce_only
            and not self.config.dry_run
            and self.budget_guard is not None
        ):
            try:
                budget_ok, budget_meta = self.budget_guard.check_and_reserve(
                    bar_ts=pd.to_datetime(bar.timestamp, utc=True),
                    order_notional=order_notional,
                    reduce_only=reduce_only,
                    budget_cap_remaining_usdt=budget_cap_remaining_usdt,
                )
            except Exception as exc:
                budget_ok = False
                budget_meta = {
                    "reason": "insufficient_budget",
                    "error": str(exc),
                    "requested_notional": order_notional,
                }
            if not budget_ok:
                self._last_order_block_reason = "insufficient_budget"
                self._event(
                    "insufficient_budget",
                    {
                        "signal": signal,
                        "intent": intent,
                        "symbol": self.config.symbol,
                        "side": side,
                        "qty": requested_qty,
                        "order_notional": order_notional,
                        **budget_meta,
                    },
                )
                self._notify(
                    f"[{self.config.mode}] insufficient budget: "
                    f"requested_notional={order_notional:.4f} detail={budget_meta.get('reason', 'unknown')}"
                )
                return None
            reserved_notional = float(budget_meta.get("requested_notional", order_notional))

        if self.config.dry_run:
            result = OrderResult(
                order_id=f"dry-{req.client_order_id}",
                status="CANCELED",
                filled_qty=0.0,
                avg_price=0.0,
                fee=0.0,
                message="dry-run payload logged only",
                client_order_id=req.client_order_id,
            )
            self._save_order(bar, signal, req, result)
            self._event(
                "dry_run_order",
                {
                    "symbol": req.symbol,
                    "side": req.side,
                    "order_type": req.order_type,
                    "qty": req.amount,
                    "stop_price": req.stop_price,
                    "reduce_only": req.reduce_only,
                    "time_in_force": req.time_in_force,
                    "client_order_id": req.client_order_id,
                },
            )
            return req, result

        try:
            result = self.broker.place_order(req)
            if self.config.mode == "live":
                self._consecutive_api_errors = 0
        except Exception as exc:
            if reserved_notional > 0 and self.budget_guard is not None:
                self.budget_guard.release(order_notional=reserved_notional)
            err_text = str(exc)
            if self.config.mode == "live" and '"code":-4164' in err_text:
                self._rejected_by_min_notional_count += 1
                self._last_order_block_reason = "entry_notional_too_small"
                self._event(
                    "entry_notional_too_small",
                    {
                        "signal": signal,
                        "intent": intent,
                        "error": err_text,
                        "attempted_notional": order_notional,
                        "final_notional": order_notional,
                        "min_notional": float(self._get_symbol_order_constraints().get("min_notional", 0.0)),
                        "qty_before_round": requested_qty,
                        "qty_after_round": requested_qty,
                        "skip_reason": "exchange_rejected_min_notional",
                    },
                )
                return None
            if (
                self.config.mode == "live"
                and reduce_only
                and '"code":-2022' in err_text
                and self._recover_reduce_only_rejection(
                    bar=bar,
                    signal=signal,
                    intent=intent,
                    side=side,
                    error_text=err_text,
                )
            ):
                return None
            recovered_result: OrderResult | None = None
            if self.config.mode == "live" and intent.startswith("protective-") and '"code":-4045' in err_text:
                recovered_result = self._retry_protective_after_algo_limit_cleanup(
                    req=req,
                    signal=signal,
                    intent=intent,
                    error_text=err_text,
                )
            if recovered_result is not None:
                result = recovered_result
                self._consecutive_api_errors = 0
            else:
                self._register_api_error(where="place_order", error=exc)
                if self.config.halt_on_error:
                    self._halt(
                        reason="broker exception with halt_on_error enabled",
                        event_type="broker_error",
                        payload={"error": str(exc), "signal": signal, "intent": intent},
                        error_summary=str(exc),
                    )
                    return None
                if self.config.mode == "live":
                    return None
                raise

        if result.status in {"REJECTED", "CANCELED"} and reserved_notional > 0 and self.budget_guard is not None:
            self.budget_guard.release(order_notional=reserved_notional)

        self._save_order(bar, signal, req, result)

        if result.status == "FILLED":
            if not bool(getattr(self.broker, "handles_fill_persistence", False)):
                self._save_fill(bar, req, result)
            self._apply_fill(
                bar=bar,
                side=side,
                qty=result.filled_qty,
                price=result.avg_price,
                fee=result.fee,
                reason=signal,
            )
            self._notify(
                f"[{self.config.mode}] fill {side} qty={result.filled_qty:.6f} price={result.avg_price:.4f} signal={signal}"
            )
        elif (
            self.config.mode == "live"
            and str(req.order_type).upper() in {"MARKET", "LIMIT"}
            and result.status in {"REJECTED", "CANCELED"}
            and ("-4164" in str(result.message) or "notional" in str(result.message).lower())
        ):
            self._rejected_by_min_notional_count += 1
            self._last_order_block_reason = "entry_notional_too_small"
            self._event(
                "entry_notional_too_small",
                {
                    "signal": signal,
                    "intent": intent,
                    "error": str(result.message),
                    "attempted_notional": order_notional,
                    "final_notional": order_notional,
                    "min_notional": float(self._get_symbol_order_constraints().get("min_notional", 0.0)),
                    "qty_before_round": requested_qty,
                    "qty_after_round": requested_qty,
                    "skip_reason": "broker_result_rejected_min_notional",
                },
            )
            return None
        elif self.config.mode == "live" and str(req.order_type).upper() in {"MARKET", "LIMIT"}:
            self._halt(
                reason=f"live order failed: {result.status}",
                event_type="live_order_failed",
                payload={"status": result.status, "message": result.message, "intent": intent},
                error_summary=result.message,
            )

        return req, result

    def _cancel_open_order(self, *, bar: LiveBar, order_id: str, reason: str) -> None:
        meta = self._open_orders.pop(order_id, None)
        if meta is None:
            return
        canceled = False
        cancel_fn = getattr(self.broker, "cancel_order", None)
        if callable(cancel_fn):
            try:
                if cancel_fn(order_id, symbol=self.config.symbol):  # type: ignore[misc]
                    canceled = True
            except TypeError:
                canceled = bool(cancel_fn(order_id))  # type: ignore[misc]
            except Exception:
                canceled = False
        req: OrderRequest = meta["request"]
        result = OrderResult(
            order_id=order_id,
            status="CANCELED",
            filled_qty=0.0,
            avg_price=0.0,
            fee=0.0,
            message=reason if canceled else f"{reason} (local-cancel)",
            client_order_id=req.client_order_id,
        )
        self._save_order(bar, "exit", req, result)
        self._event("protective_order_canceled", {"order_id": order_id, "reason": reason})

    def _cancel_all_protective_orders(self, *, bar: LiveBar, reason: str) -> None:
        for oid in list(self._open_orders.keys()):
            self._cancel_open_order(bar=bar, order_id=oid, reason=reason)
        self._protective_pair = {}

    def _maybe_create_protective_orders(self, *, bar: LiveBar, fail_on_missing: bool = True) -> None:
        if not self.config.enable_protective_orders:
            return
        mark = max(float(self.position_entry_price), float(bar.close), 1e-9)
        stop_pct = self._effective_sl_pct(mark_price=mark)
        tp_pct = self._effective_tp_pct(mark_price=mark)
        if self.position_qty == 0 or (stop_pct <= 0 and tp_pct <= 0):
            return
        side = self._position_side()
        qty = abs(self.position_qty)
        if qty <= 0:
            return
        required = self._required_protective_kinds()
        if not required:
            return

        sl_order_id: str | None = None
        tp_order_id: str | None = None
        if side == "long":
            sl_side = "SELL"
            tp_side = "SELL"
            sl_price = self.position_entry_price * (1 - stop_pct) if stop_pct > 0 else None
            tp_price = self.position_entry_price * (1 + tp_pct) if tp_pct > 0 else None
        elif side == "short":
            sl_side = "BUY"
            tp_side = "BUY"
            sl_price = self.position_entry_price * (1 + stop_pct) if stop_pct > 0 else None
            tp_price = self.position_entry_price * (1 - tp_pct) if tp_pct > 0 else None
        else:
            return

        if sl_price is not None:
            placed = self._place_order(
                bar=bar,
                signal="exit",
                side=sl_side,  # type: ignore[arg-type]
                reduce_only=True,
                order_type="STOP_MARKET",
                stop_price=sl_price,
                qty=qty,
                intent=f"protective-sl-{side}",
            )
            if placed is not None:
                req, res = placed
                if res.status == "NEW":
                    self._open_orders[res.order_id] = {"request": req, "kind": "sl"}
                    sl_order_id = res.order_id

        if tp_price is not None:
            placed = self._place_order(
                bar=bar,
                signal="exit",
                side=tp_side,  # type: ignore[arg-type]
                reduce_only=True,
                order_type="TAKE_PROFIT_MARKET",
                stop_price=tp_price,
                qty=qty,
                intent=f"protective-tp-{side}",
            )
            if placed is not None:
                req, res = placed
                if res.status == "NEW":
                    self._open_orders[res.order_id] = {"request": req, "kind": "tp"}
                    tp_order_id = res.order_id

        if sl_order_id or tp_order_id:
            self._protective_pair = {"sl": sl_order_id or "", "tp": tp_order_id or ""}
            self._event(
                "protective_orders_created",
                {
                    "side": side,
                    "sl_order_id": sl_order_id,
                    "tp_order_id": tp_order_id,
                    "entry_price": self.position_entry_price,
                    "qty": qty,
                },
            )
            self._emit_execution_snapshot(
                event="protective_orders_created",
                bar=bar,
                price_source="bar_close",
                fill_price=None,
            )
        current = {
            str(meta.get("kind", "")).lower()
            for meta in self._open_orders.values()
            if isinstance(meta, dict)
        }
        missing = sorted(required - current)
        if missing and fail_on_missing:
            self._handle_protective_failure(
                bar=bar,
                reason="entry protective creation failed",
                missing=missing,
            )

    def _maybe_update_trailing_stop(self, *, bar: LiveBar) -> None:
        if not self.config.trailing_stop_enabled:
            return
        side = self._position_side()
        if side not in {"long", "short"}:
            return
        sl_order_id = next(
            (
                oid
                for oid, meta in self._open_orders.items()
                if isinstance(meta, dict) and str(meta.get("kind", "")).lower() == "sl"
            ),
            None,
        )
        if sl_order_id is None:
            return
        sl_meta = self._open_orders.get(sl_order_id)
        if not isinstance(sl_meta, dict):
            return
        req_obj = sl_meta.get("request")
        if not isinstance(req_obj, OrderRequest):
            return

        trail_distance = 0.0
        if self.config.trail_pct > 0:
            trail_distance = float(bar.close) * self.config.trail_pct
        elif self.config.trail_atr_mult > 0:
            atr = self._atr_value()
            if atr > 0:
                trail_distance = atr * self.config.trail_atr_mult
        if trail_distance <= 0:
            return

        current_stop = float(req_obj.stop_price) if req_obj.stop_price is not None else 0.0
        if side == "long":
            new_stop = float(bar.close) - trail_distance
            if current_stop > 0 and new_stop <= current_stop + 1e-12:
                return
            stop_side: Literal["BUY", "SELL"] = "SELL"
        else:
            new_stop = float(bar.close) + trail_distance
            if current_stop > 0 and new_stop >= current_stop - 1e-12:
                return
            stop_side = "BUY"

        qty = abs(self.position_qty)
        if qty <= 0:
            return

        self._cancel_open_order(bar=bar, order_id=sl_order_id, reason="trailing_stop_adjust")
        placed = self._place_order(
            bar=bar,
            signal="exit",
            side=stop_side,
            reduce_only=True,
            order_type="STOP_MARKET",
            stop_price=new_stop,
            qty=qty,
            intent=f"trail-sl-{side}",
        )
        if placed is None:
            return
        new_req, new_res = placed
        if new_res.status != "NEW":
            return
        self._open_orders[new_res.order_id] = {"request": new_req, "kind": "sl"}
        self._protective_pair["sl"] = new_res.order_id
        self._event(
            "trailing_stop_updated",
            {"old_sl_order_id": sl_order_id, "new_sl_order_id": new_res.order_id, "new_stop_price": new_stop},
        )

    def _handle_trigger_fills_from_broker(self, *, bar: LiveBar) -> None:
        poll = getattr(self.broker, "poll_filled_orders", None)
        if not callable(poll):
            return
        try:
            try:
                updates = poll(symbol=self.config.symbol)
            except TypeError:
                try:
                    updates = poll(self.config.symbol)
                except TypeError:
                    updates = poll()
            if self.config.mode == "live":
                self._consecutive_api_errors = 0
        except Exception as exc:
            self._register_api_error(where="poll_filled_orders", error=exc)
            updates = []
        for req, res in updates:
            if str(req.symbol) != self.config.symbol:
                continue
            self._save_order(bar, "exit", req, res)
            if res.status != "FILLED":
                continue
            if not bool(getattr(self.broker, "handles_fill_persistence", False)):
                self._save_fill(bar, req, res)
            self._apply_fill(
                bar=bar,
                side=str(req.side),
                qty=res.filled_qty,
                price=res.avg_price,
                fee=res.fee,
                reason=str(req.order_type).lower(),
            )
            self._event("protective_order_filled", {"order_id": res.order_id, "order_type": req.order_type})
            sibling_id = None
            if self._protective_pair.get("sl") == res.order_id:
                sibling_id = self._protective_pair.get("tp")
            elif self._protective_pair.get("tp") == res.order_id:
                sibling_id = self._protective_pair.get("sl")
            self._open_orders.pop(res.order_id, None)
            if sibling_id:
                self._cancel_open_order(bar=bar, order_id=sibling_id, reason="paired protective filled")
            self._protective_pair = {}
            self._emit_execution_snapshot(
                event="protective_order_filled",
                bar=bar,
                price_source="trigger_order_fill_avg_price",
                fill_price=res.avg_price,
            )

    def _new_entry_allowed(self, *, bar: LiveBar) -> tuple[bool, str]:
        dt = pd.to_datetime(bar.timestamp, utc=True).to_pydatetime()
        if self.risk_guard.quiet_hours_active(now_utc=dt):
            return False, "quiet_hours active: new entries blocked"
        return True, "ok"

    def _handle_signal(self, bar: LiveBar, signal: Signal) -> None:
        side = self._position_side()
        if signal == "long":
            if side == "short":
                self._cancel_all_protective_orders(bar=bar, reason="signal_reverse_to_long")
                self._place_order(bar=bar, signal=signal, side="BUY", reduce_only=True, intent="close-short")
            if self._position_side() == "flat":
                allowed, block_reason = self._new_entry_allowed(bar=bar)
                if not allowed:
                    self._event("quiet_hours_entry_blocked", {"signal": signal, "reason": block_reason})
                    return
                if self._open_orders:
                    self._cancel_all_protective_orders(bar=bar, reason="pre_open_long_cleanup")
                placed = self._place_order(bar=bar, signal=signal, side="BUY", reduce_only=False, intent="open-long")
                if placed is not None and placed[1].status == "FILLED":
                    self._maybe_create_protective_orders(bar=bar)
        elif signal == "short":
            if side == "long":
                self._cancel_all_protective_orders(bar=bar, reason="signal_reverse_to_short")
                self._place_order(bar=bar, signal=signal, side="SELL", reduce_only=True, intent="close-long")
            if self._position_side() == "flat":
                allowed, block_reason = self._new_entry_allowed(bar=bar)
                if not allowed:
                    self._event("quiet_hours_entry_blocked", {"signal": signal, "reason": block_reason})
                    return
                if self._open_orders:
                    self._cancel_all_protective_orders(bar=bar, reason="pre_open_short_cleanup")
                placed = self._place_order(bar=bar, signal=signal, side="SELL", reduce_only=False, intent="open-short")
                if placed is not None and placed[1].status == "FILLED":
                    self._maybe_create_protective_orders(bar=bar)
        elif signal == "exit":
            if side == "long":
                self._place_order(bar=bar, signal=signal, side="SELL", reduce_only=True, intent="manual-exit-long")
            elif side == "short":
                self._place_order(bar=bar, signal=signal, side="BUY", reduce_only=True, intent="manual-exit-short")
            if self._position_side() == "flat":
                self._cancel_all_protective_orders(bar=bar, reason="position exited")

    def _status_line(self, bar: LiveBar) -> str:
        equity = self._equity(bar.close)
        unrealized = self.position_qty * (bar.close - self.position_entry_price) if self.position_qty != 0 else 0.0
        exposure = abs(self.position_qty) * bar.close
        budget_snapshot = self._account_budget_snapshot(bar_ts=pd.to_datetime(bar.timestamp, utc=True))
        account_available = float(budget_snapshot.get("account_available_usdt", 0.0))
        budget_cap, _, _ = self._resolve_budget_cap(
            equity=equity,
            current_exposure_notional=exposure,
            account_available_usdt=account_available,
        )
        return (
            f"ts={bar.timestamp} symbol={self.config.symbol} broker={self._broker_label()} side={self._position_side()} qty={abs(self.position_qty):.6f} "
            f"entry={self.position_entry_price:.4f} unrealized={unrealized:.2f} "
            f"realized={self.realized_pnl:.2f} equity={equity:.2f} signal={self.last_signal} "
            f"budget_cap={budget_cap:.2f} exposure={exposure:.2f} "
            f"open_protective={len(self._open_orders)} halted={self.halted}"
        )

    def _restore_runtime_state(self) -> None:
        resume_state = None
        if self.config.resume_run_id:
            resume_state = self.storage.load_runtime_state(self.config.resume_run_id)
        if resume_state is None:
            resume_state = self.storage.get_latest_runtime_state()
        if resume_state is None:
            return

        self.run_id = str(resume_state["run_id"])
        last_bar_ts = resume_state.get("last_bar_ts")
        if isinstance(last_bar_ts, str) and last_bar_ts:
            self._resume_last_bar_ts = pd.to_datetime(last_bar_ts, utc=True)
        open_positions = resume_state.get("open_positions") or {}
        if (
            isinstance(open_positions, dict)
            and "qty" not in open_positions
            and self.config.symbol in open_positions
            and isinstance(open_positions.get(self.config.symbol), dict)
        ):
            open_positions = open_positions.get(self.config.symbol, {})
        if isinstance(open_positions, dict):
            self.position_qty = float(open_positions.get("qty", 0.0))
            self.position_entry_price = float(open_positions.get("entry_price", 0.0))
            self.position_entry_ts = str(open_positions.get("entry_ts", ""))
            self.position_fee_pool = float(open_positions.get("fee_pool", 0.0))
        risk_state = resume_state.get("risk_state") or {}
        if (
            isinstance(risk_state, dict)
            and "processed_bars" not in risk_state
            and self.config.symbol in risk_state
            and isinstance(risk_state.get(self.config.symbol), dict)
        ):
            risk_state = risk_state.get(self.config.symbol, {})
        if isinstance(risk_state, dict):
            self.realized_pnl = float(risk_state.get("realized_pnl", self.realized_pnl))
            self.fees_paid = float(risk_state.get("fees_paid", self.fees_paid))
            self.cash = float(risk_state.get("cash", self.cash))
            self.peak_equity = float(risk_state.get("peak_equity", self.peak_equity))
            self.last_signal = self._normalize_signal(str(risk_state.get("last_signal", "hold")))
            self._processed_bars = int(risk_state.get("processed_bars", 0))
            self._halt_reason = str(risk_state.get("halt_reason", ""))
            self._consecutive_losses = int(risk_state.get("consecutive_losses", 0))
            self._last_order_block_reason = str(risk_state.get("last_order_block_reason", self._last_order_block_reason))
            self._rejected_by_min_notional_count = int(
                risk_state.get("rejected_by_min_notional_count", self._rejected_by_min_notional_count)
            )
            self._protective_fail_count = int(risk_state.get("protective_fail_count", self._protective_fail_count))
            self._min_entry_notional_block_count = int(
                risk_state.get("min_entry_notional_block_count", self._min_entry_notional_block_count)
            )
            restored_samples = risk_state.get("min_entry_notional_block_samples", self._min_entry_notional_samples)
            if isinstance(restored_samples, list):
                self._min_entry_notional_samples = [s for s in restored_samples if isinstance(s, dict)][-5:]
        loaded_open_orders = resume_state.get("open_orders") or {}
        if (
            isinstance(loaded_open_orders, dict)
            and self.config.symbol in loaded_open_orders
            and isinstance(loaded_open_orders.get(self.config.symbol), dict)
        ):
            loaded_open_orders = loaded_open_orders.get(self.config.symbol, {})
        if "_pair" in loaded_open_orders and isinstance(loaded_open_orders["_pair"], dict):
            pair = loaded_open_orders.pop("_pair")
            self._protective_pair = {"sl": str(pair.get("sl", "")), "tp": str(pair.get("tp", ""))}
        self._open_orders = {}
        if isinstance(loaded_open_orders, dict):
            for oid, payload in loaded_open_orders.items():
                if str(oid).startswith("_") or not isinstance(payload, dict):
                    continue
                req = OrderRequest(
                    symbol=str(payload.get("symbol", self.config.symbol)),
                    side=str(payload.get("side", "SELL")),
                    amount=float(payload.get("qty", 0.0)),
                    order_type=str(payload.get("order_type", "STOP_MARKET")),
                    stop_price=float(payload["stop_price"]) if payload.get("stop_price") is not None else None,
                    client_order_id=str(payload["client_order_id"]) if payload.get("client_order_id") else None,
                    reduce_only=bool(payload.get("reduce_only", False)),
                    position_side="BOTH",
                )
                self._open_orders[str(oid)] = {"request": req, "kind": "restored"}
        self._strategy_state = resume_state.get("strategy_state") or {}
        restore_broker = getattr(self.broker, "restore_runtime_state", None)
        if callable(restore_broker):
            try:
                restore_broker(open_positions=self._position_payload(), open_orders=loaded_open_orders)
            except Exception:
                pass

    def _strategy_state_payload(self) -> dict[str, Any]:
        get_state = getattr(self.strategy, "get_state", None)
        if callable(get_state):
            try:
                state = get_state()
                if isinstance(state, dict):
                    return state
            except Exception:
                pass
        return self._strategy_state if isinstance(self._strategy_state, dict) else {}

    def _risk_state_payload(self, *, mark_price: float) -> dict[str, Any]:
        equity = self._equity(mark_price)
        drawdown = ((self.peak_equity - equity) / self.peak_equity) if self.peak_equity > 0 else 0.0
        current_exposure_notional = abs(self.position_qty) * mark_price
        snap_ts = pd.to_datetime(self._bars[-1].timestamp, utc=True) if self._bars else pd.Timestamp.now(tz="UTC")
        budget_snapshot = self._account_budget_snapshot(bar_ts=snap_ts)
        account_available_usdt = float(budget_snapshot.get("account_available_usdt", 0.0))
        account_total_usdt = float(budget_snapshot.get("account_total_usdt", 0.0))
        account_source = str(budget_snapshot.get("source", "unavailable"))
        budget_cap_usdt, budget_cap_remaining_usdt, budget_cap_source = self._resolve_budget_cap(
            equity=equity,
            current_exposure_notional=current_exposure_notional,
            account_available_usdt=account_available_usdt,
        )
        return {
            "realized_pnl": self.realized_pnl,
            "fees_paid": self.fees_paid,
            "cash": self.cash,
            "peak_equity": self.peak_equity,
            "last_signal": self.last_signal,
            "processed_bars": self._processed_bars,
            "halted": self.halted,
            "halt_reason": self._halt_reason,
            "equity": equity,
            "drawdown_pct": drawdown,
            "broker": self._broker_label(),
            "budget_cap_usdt": budget_cap_usdt,
            "budget_usdt": budget_cap_usdt,
            "budget_cap_remaining_usdt": budget_cap_remaining_usdt,
            "budget_cap_source": budget_cap_source,
            "allocation_pct": self.config.account_allocation_pct,
            "current_exposure_notional": current_exposure_notional,
            "daily_loss_limit_pct": self.config.daily_loss_limit_pct,
            "daily_loss_limit_usdt": self.risk_guard.daily_loss_limit_usdt(equity=equity),
            "daily_loss_remaining_usdt": self.risk_guard.remaining_daily_loss_usdt(
                equity=equity,
                realized_pnl_today=self.realized_pnl,
            ),
            "max_position_notional": self.config.max_position_notional_usdt,
            "min_entry_notional": self.config.min_entry_notional_usdt,
            "min_entry_notional_usdt": self.config.min_entry_notional_usdt,
            "account_available_usdt": account_available_usdt,
            "account_total_usdt": account_total_usdt,
            "account_balance_source": account_source,
            "max_drawdown_pct_limit": self.risk_guard.max_drawdown_pct,
            "max_position_notional_usdt": self.config.max_position_notional_usdt,
            "env": self.config.binance_env,
            "live_trading": self.config.live_trading_enabled,
            "budget_guard": self.config.budget_guard_enabled,
            "dry_run": self.config.dry_run,
            "preset": self.config.preset_name,
            "sleep_mode": self.config.sleep_mode_enabled,
            "quiet_hours": self.config.quiet_hours,
            "quiet_hours_active": self.risk_guard.quiet_hours_active(),
            "consecutive_losses": self._consecutive_losses,
            "consecutive_loss_limit": self.config.consec_loss_limit,
            "last_order_block_reason": self._last_order_block_reason,
            "rejected_by_min_notional_count": self._rejected_by_min_notional_count,
            "protective_fail_count": self._protective_fail_count,
            "min_entry_notional_block_count": self._min_entry_notional_block_count,
            "min_entry_notional_block_samples": list(self._min_entry_notional_samples),
        }

    def _maybe_send_heartbeat(self, *, bar: LiveBar) -> None:
        if not self.config.heartbeat_enabled:
            return
        now = datetime.now(timezone.utc)
        if self._last_heartbeat_sent_at is not None:
            delta_sec = (now - self._last_heartbeat_sent_at).total_seconds()
            if delta_sec < float(self.config.heartbeat_interval_minutes * 60):
                return
        self._last_heartbeat_sent_at = now
        mark = float(bar.close)
        equity = self._equity(mark)
        drawdown = ((self.peak_equity - equity) / self.peak_equity) if self.peak_equity > 0 else 0.0
        pos = self._position_summary_payload(mark_price=mark)
        self._notify(
            "\n".join(
                [
                    "runtime heartbeat",
                    f"run_id={self.run_id}",
                    f"symbol={self.config.symbol}",
                    f"equity={equity:.4f}",
                    f"drawdown_pct={drawdown:.4%}",
                    f"position_side={pos['side']} qty={float(pos['qty']):.6f}",
                    f"realized_pnl={self.realized_pnl:.4f}",
                ]
            )
        )

    def _save_runtime_state(self, *, last_bar_ts: pd.Timestamp | None) -> None:
        open_orders_payload = {
            oid: {
                "order_id": oid,
                "symbol": self.config.symbol,
                "side": str(meta["request"].side).upper(),
                "order_type": str(meta["request"].order_type).upper(),
                "qty": float(meta["request"].amount),
                "stop_price": meta["request"].stop_price,
                "reduce_only": bool(meta["request"].reduce_only),
                "client_order_id": meta["request"].client_order_id,
            }
            for oid, meta in self._open_orders.items()
            if isinstance(meta, dict) and isinstance(meta.get("request"), OrderRequest)
        }
        if self.config.mode == "live":
            get_open_orders = getattr(self.broker, "get_open_orders", None)
            if callable(get_open_orders):
                broker_open: dict[str, Any] = {}
                try:
                    maybe = get_open_orders(symbol=self.config.symbol)
                    if isinstance(maybe, dict):
                        broker_open = maybe
                except TypeError:
                    try:
                        maybe = get_open_orders(self.config.symbol)
                        if isinstance(maybe, dict):
                            broker_open = maybe
                    except Exception:
                        broker_open = {}
                except Exception:
                    broker_open = {}
                for oid, payload in broker_open.items():
                    key = str(oid)
                    if key in open_orders_payload or not isinstance(payload, dict):
                        continue
                    open_orders_payload[key] = {
                        "order_id": key,
                        "symbol": str(payload.get("symbol", self.config.symbol)),
                        "side": str(payload.get("side", "")),
                        "order_type": str(payload.get("order_type", payload.get("type", ""))),
                        "qty": float(payload.get("qty", payload.get("amount", 0.0)) or 0.0),
                        "stop_price": payload.get("stop_price", payload.get("stopPrice")),
                        "reduce_only": bool(payload.get("reduce_only", False)),
                        "client_order_id": payload.get("client_order_id"),
                    }
        if self._protective_pair:
            open_orders_payload["_pair"] = dict(self._protective_pair)
        broker_snapshot = getattr(self.broker, "get_state_snapshot", None)
        if callable(broker_snapshot):
            try:
                snap = broker_snapshot()
                if isinstance(snap, dict) and "open_orders" in snap and isinstance(snap["open_orders"], dict):
                    open_orders_payload.setdefault("_broker_open_orders", snap["open_orders"])
            except Exception:
                pass

        self.storage.save_runtime_state(
            run_id=self.run_id,
            last_bar_ts=str(last_bar_ts) if last_bar_ts is not None else None,
            open_positions=self._position_payload(),
            open_orders=open_orders_payload,
            strategy_state=self._strategy_state_payload(),
            risk_state=self._risk_state_payload(mark_price=self._latest_mark_price()),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _check_bar_staleness(self, *, bar: LiveBar, bar_ts: pd.Timestamp) -> None:
        warn_threshold = float(self.config.bar_staleness_warn_sec)
        if warn_threshold <= 0:
            return
        if bool(getattr(bar, "is_backfill", False)):
            return
        staleness_sec = float((pd.Timestamp.now(tz="UTC") - bar_ts).total_seconds())
        if staleness_sec <= warn_threshold:
            return
        payload: dict[str, Any] = {
            "staleness_sec": staleness_sec,
            "warn_threshold": warn_threshold,
            "bar_ts": str(bar_ts),
            "is_backfill": False,
            "action": "warn",
        }
        self._event("bar_stale_detected", payload)
        if not self.config.bar_staleness_halt:
            return
        halt_threshold = float(self.config.bar_staleness_halt_sec) if self.config.bar_staleness_halt_sec > 0 else warn_threshold
        if staleness_sec > halt_threshold:
            self._halt(
                reason=f"bar staleness too high ({staleness_sec:.1f}s)",
                event_type="bar_stale_halt",
                payload={
                    "staleness_sec": staleness_sec,
                    "halt_threshold": halt_threshold,
                    "bar_ts": str(bar_ts),
                },
            )

    def start_session(self) -> None:
        if self._session_started:
            return
        self._session_started = True
        self._session_processed = 0
        self._last_bar_recv_monotonic = None
        profile = self._runtime_profile_payload()
        self._event("runtime_started", profile)
        self._event("runtime_profile", profile)
        if self.config.mode == "live":
            self._run_preflight_checks()

    def process_bar(self, bar: LiveBar) -> bool:
        bar_ts = pd.to_datetime(bar.timestamp, utc=True)
        if self._resume_last_bar_ts is not None and bar_ts <= self._resume_last_bar_ts:
            return True
        try:
            recv_now = time.monotonic()
            if self.config.feed_stall_timeout_sec > 0 and self._last_bar_recv_monotonic is not None:
                recv_gap_sec = recv_now - self._last_bar_recv_monotonic
                if recv_gap_sec > self.config.feed_stall_timeout_sec:
                    self._halt(
                        reason=f"feed stall detected ({recv_gap_sec:.1f}s recv gap)",
                        event_type="feed_stall_detected",
                        payload={
                            "recv_gap_seconds": recv_gap_sec,
                            "threshold": self.config.feed_stall_timeout_sec,
                        },
                    )
                    return False
            self._last_bar_recv_monotonic = recv_now

            self._check_bar_staleness(bar=bar, bar_ts=bar_ts)
            if self.halted:
                return False

            if hasattr(self.broker, "update_market_price"):
                self.broker.update_market_price(self.config.symbol, bar.close)  # type: ignore[attr-defined]
            if self.config.mode == "live":
                snapshot = self._fetch_live_position_snapshot()
                if snapshot is not None:
                    self._apply_live_position_snapshot(bar=bar, snapshot=snapshot, source="bar_sync")

            self._handle_trigger_fills_from_broker(bar=bar)
            if self._position_side() == "flat" and self._open_orders:
                self._cancel_all_protective_orders(bar=bar, reason="flat_position_cleanup")

            self._bars.append(bar)
            if len(self._bars) > max(self.config.atr_period * 3, 200):
                self._bars = self._bars[-max(self.config.atr_period * 3, 200) :]

            equity = self._equity(bar.close)
            self.peak_equity = max(self.peak_equity, equity)
            atr_pct = self._atr_pct()
            ok_runtime, reason_runtime = self.risk_guard.check_runtime(
                equity=equity,
                peak_equity=self.peak_equity,
                atr_pct=atr_pct,
            )
            if not ok_runtime:
                self._halt(
                    reason=reason_runtime,
                    event_type="risk_halt",
                    payload={"atr_pct": atr_pct, "equity": equity},
                )

            strategy_position = StrategyPosition(
                side=self._position_side(),
                qty=abs(self.position_qty),
                entry_price=self.position_entry_price,
            )
            signal = self._normalize_signal(
                self.strategy.on_bar(
                    bar=Bar(
                        timestamp=bar.timestamp,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=bar.volume,
                    ),
                    position=strategy_position,
                )
            )
            self.last_signal = signal
            if not self.halted:
                self._handle_signal(bar, signal)
                self._maybe_update_trailing_stop(bar=bar)
                self._enforce_protective_integrity(bar=bar)
                self._maybe_send_heartbeat(bar=bar)

            logger.info(self._status_line(bar))
            self._processed_bars += 1
            self._session_processed += 1
            self._last_processed_bar_ts = bar_ts

            if self._processed_bars % self.config.state_save_every_n_bars == 0:
                self._save_runtime_state(last_bar_ts=bar_ts)
            if self.config.one_shot:
                self._event("one_shot_exit", {"last_bar_ts": str(bar_ts)})
                return False
            if self.halted:
                return False
            return True
        except Exception as exc:
            if self.config.halt_on_error:
                self._halt(
                    reason="runtime exception with halt_on_error enabled",
                    event_type="runtime_exception",
                    payload={"error": str(exc), "bar_ts": str(bar_ts)},
                    error_summary=str(exc),
                )
                self._save_runtime_state(last_bar_ts=bar_ts)
                return False
            raise

    def finish_session(self) -> dict[str, object]:
        last_ts = self._last_processed_bar_ts or self._resume_last_bar_ts
        if last_ts is None and self._bars:
            last_ts = pd.to_datetime(self._bars[-1].timestamp, utc=True)
        self._save_runtime_state(last_bar_ts=last_ts)
        self._event(
            "runtime_stopped",
            {
                "processed_bars": self._session_processed,
                "processed_total": self._processed_bars,
                "halted": self.halted,
                "halt_reason": self._halt_reason,
            },
        )
        self._session_started = False
        return {
            "run_id": self.run_id,
            "processed_bars": self._session_processed,
            "processed_total": self._processed_bars,
            "halted": self.halted,
            "halt_reason": self._halt_reason,
        }

    def run(self) -> dict[str, object]:
        max_bars = self.config.max_bars if self.config.max_bars > 0 else None
        self.start_session()
        if self.halted:
            return self.finish_session()
        pending_error: Exception | None = None
        try:
            for bar in self.feed.iter_closed_bars(max_bars=max_bars):
                keep_running = self.process_bar(bar)
                if not keep_running:
                    break
        except Exception as exc:
            pending_error = exc
        finally:
            result = self.finish_session()
        if pending_error is not None:
            raise pending_error
        return result


class RuntimeOrchestrator:
    def __init__(
        self,
        *,
        engines: dict[str, RuntimeEngine],
        feeds: dict[str, BinanceLiveFeed],
        max_bars: int | None = None,
        account_risk_guard: RiskGuard | None = None,
        account_initial_equity: float | None = None,
    ) -> None:
        self.engines = engines
        self.feeds = feeds
        self.max_bars = max_bars
        self.account_risk_guard = account_risk_guard
        if account_initial_equity is not None:
            self.account_initial_equity = float(account_initial_equity)
        elif engines:
            self.account_initial_equity = float(next(iter(engines.values())).initial_equity)
        else:
            self.account_initial_equity = 0.0
        self._account_peak_equity = self.account_initial_equity

    def _account_snapshot(self) -> dict[str, float]:
        realized = float(sum(float(engine.realized_pnl) for engine in self.engines.values()))
        unrealized = 0.0
        for engine in self.engines.values():
            if engine.position_qty == 0:
                continue
            mark = engine._latest_mark_price()
            unrealized += float(engine.position_qty) * (float(mark) - float(engine.position_entry_price))
        equity = max(self.account_initial_equity + realized + unrealized, 0.0)
        self._account_peak_equity = max(self._account_peak_equity, equity)
        drawdown = 0.0
        if self._account_peak_equity > 0:
            drawdown = (self._account_peak_equity - equity) / self._account_peak_equity
        return {
            "equity": equity,
            "peak_equity": self._account_peak_equity,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "drawdown_pct": drawdown,
        }

    def _account_limit_breached(self) -> tuple[bool, str, dict[str, Any]]:
        if not self.engines:
            return False, "", {}
        guard = self.account_risk_guard or next(iter(self.engines.values())).risk_guard
        snap = self._account_snapshot()
        max_dd = _normalize_fraction(float(guard.max_drawdown_pct))
        if max_dd > 0 and snap["drawdown_pct"] >= max_dd:
            return (
                True,
                "account max drawdown stop triggered",
                {
                    "account_equity": snap["equity"],
                    "account_peak_equity": snap["peak_equity"],
                    "account_drawdown_pct": snap["drawdown_pct"],
                    "max_drawdown_pct_limit": max_dd,
                },
            )
        remaining_daily = guard.remaining_daily_loss_usdt(
            equity=snap["equity"],
            realized_pnl_today=snap["realized_pnl"],
        )
        if remaining_daily <= 0:
            return (
                True,
                "account daily loss limit reached",
                {
                    "account_equity": snap["equity"],
                    "account_realized_pnl": snap["realized_pnl"],
                    "daily_loss_remaining_usdt": remaining_daily,
                    "daily_loss_limit_usdt": guard.daily_loss_limit_usdt(equity=snap["equity"]),
                },
            )
        return False, "", {}

    def _halt_all(self, *, reason: str, payload: dict[str, Any]) -> None:
        for engine in self.engines.values():
            engine.request_halt(
                reason=reason,
                event_type="account_risk_halt",
                payload=payload,
            )

    def run(self) -> dict[str, Any]:
        if not self.engines:
            return {"run_id": "", "processed_total": 0, "halted": False, "symbols": {}}
        run_id = next(iter(self.engines.values())).run_id
        events_q: queue.Queue[tuple[str, LiveBar | None, Exception | None, bool]] = queue.Queue()
        global_stop_event = threading.Event()
        symbol_stop_events = {symbol: threading.Event() for symbol in self.feeds}
        threads: list[threading.Thread] = []
        symbol_results: dict[str, dict[str, Any]] = {}

        def _feed_worker(
            symbol: str,
            feed: BinanceLiveFeed,
            max_bars: int | None,
            stop_event: threading.Event,
            symbol_stop_event: threading.Event,
        ) -> None:
            try:
                for bar in feed.iter_closed_bars(max_bars=max_bars):
                    if stop_event.is_set() or symbol_stop_event.is_set():
                        break
                    events_q.put((symbol, bar, None, False))
            except Exception as exc:
                events_q.put((symbol, None, exc, False))
            finally:
                events_q.put((symbol, None, None, True))

        try:
            for symbol, engine in self.engines.items():
                engine.start_session()
            if any(engine.halted for engine in self.engines.values()):
                global_stop_event.set()
            for symbol, feed in self.feeds.items():
                t = threading.Thread(
                    target=_feed_worker,
                    args=(symbol, feed, self.max_bars, global_stop_event, symbol_stop_events[symbol]),
                    name=f"feed-{symbol.replace('/', '')}",
                    daemon=True,
                )
                threads.append(t)
                t.start()

            done_symbols: set[str] = set()
            while len(done_symbols) < len(self.feeds):
                try:
                    symbol, bar, error, is_done = events_q.get(timeout=1.0)
                except queue.Empty:
                    if global_stop_event.is_set():
                        break
                    continue
                if error is not None:
                    if symbol_stop_events.get(symbol) is not None and symbol_stop_events[symbol].is_set():
                        done_symbols.add(symbol)
                        continue
                    global_stop_event.set()
                    raise RuntimeError(f"feed worker failed for {symbol}: {error}") from error
                if is_done:
                    done_symbols.add(symbol)
                    continue
                if bar is None or global_stop_event.is_set():
                    continue
                symbol_stop = symbol_stop_events.get(symbol)
                if symbol_stop is not None and symbol_stop.is_set():
                    continue
                engine = self.engines.get(symbol)
                if engine is None:
                    continue
                keep_running = engine.process_bar(bar)
                violated, reason, payload = self._account_limit_breached()
                if violated:
                    self._halt_all(reason=reason, payload=payload)
                    global_stop_event.set()
                    break
                if not keep_running:
                    if symbol_stop is not None:
                        symbol_stop.set()
                    feed = self.feeds.get(symbol)
                    if feed is not None:
                        try:
                            feed.close()
                        except Exception:
                            pass
        finally:
            global_stop_event.set()
            for feed in self.feeds.values():
                try:
                    feed.close()
                except Exception:
                    pass
            for t in threads:
                t.join(timeout=5.0)
            for symbol, engine in self.engines.items():
                symbol_results[symbol] = engine.finish_session()

        halted = any(bool(result.get("halted", False)) for result in symbol_results.values())
        halt_reason = ""
        for result in symbol_results.values():
            reason = str(result.get("halt_reason", ""))
            if reason:
                halt_reason = reason
                break
        return {
            "run_id": run_id,
            "processed_total": int(sum(int(r.get("processed_bars", 0)) for r in symbol_results.values())),
            "halted": halted,
            "halt_reason": halt_reason,
            "account": self._account_snapshot(),
            "symbols": symbol_results,
        }
