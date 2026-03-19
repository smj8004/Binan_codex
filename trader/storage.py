from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


class SQLiteStorage:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                started_at TEXT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                initial_equity REAL NOT NULL,
                config_json TEXT NOT NULL,
                summary_json TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                symbol TEXT,
                order_id TEXT NOT NULL,
                client_order_id TEXT,
                ts TEXT NOT NULL,
                signal TEXT NOT NULL,
                side TEXT NOT NULL,
                position_side TEXT,
                reduce_only INTEGER NOT NULL DEFAULT 0,
                order_type TEXT NOT NULL,
                qty REAL NOT NULL,
                requested_price REAL,
                stop_price REAL,
                time_in_force TEXT,
                status TEXT NOT NULL,
                reason TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                symbol TEXT,
                fill_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                price REAL NOT NULL,
                fee REAL NOT NULL,
                liquidity TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'unknown',
                provenance_detail TEXT,
                source_history TEXT NOT NULL DEFAULT '[]',
                is_partial_fill INTEGER NOT NULL DEFAULT 0,
                partial_fill_group_key TEXT,
                is_reconciled INTEGER NOT NULL DEFAULT 0,
                reconciled_from_missing_ws INTEGER NOT NULL DEFAULT 0,
                trade_query_available INTEGER,
                trade_query_attempted INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_ts TEXT NOT NULL,
                exit_ts TEXT NOT NULL,
                qty REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                gross_pnl REAL NOT NULL,
                fee_paid REAL NOT NULL,
                funding_paid REAL NOT NULL,
                net_pnl REAL NOT NULL,
                return_pct REAL NOT NULL,
                reason TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_state (
                run_id TEXT PRIMARY KEY,
                last_bar_ts TEXT,
                open_positions TEXT NOT NULL,
                open_orders TEXT NOT NULL,
                strategy_state TEXT NOT NULL,
                risk_state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS optimize_runs (
                optimize_run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                strategy TEXT NOT NULL,
                symbols TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                start_ts TEXT,
                end_ts TEXT,
                search_mode TEXT NOT NULL,
                metric TEXT NOT NULL,
                constraints TEXT,
                score_expr TEXT,
                top_n INTEGER NOT NULL,
                walk_forward INTEGER NOT NULL DEFAULT 0,
                train_days INTEGER,
                test_days INTEGER,
                top_per_train INTEGER,
                config_json TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS optimize_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                optimize_run_id TEXT NOT NULL,
                candidate_run_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                window_role TEXT NOT NULL,
                window_index INTEGER,
                window_start TEXT,
                window_end TEXT,
                params_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                metric_value REAL,
                score REAL,
                objective REAL,
                passed_constraints INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wfo_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                optimize_run_id TEXT NOT NULL,
                window_index INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                train_start TEXT NOT NULL,
                train_end TEXT NOT NULL,
                test_start TEXT NOT NULL,
                test_end TEXT NOT NULL,
                top_per_train INTEGER NOT NULL,
                selected_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._ensure_column("backtest_runs", "started_at", "TEXT")
        self._ensure_column("orders", "client_order_id", "TEXT")
        self._ensure_column("orders", "symbol", "TEXT")
        self._ensure_column("orders", "position_side", "TEXT")
        self._ensure_column("orders", "reduce_only", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("orders", "stop_price", "REAL")
        self._ensure_column("orders", "time_in_force", "TEXT")
        self._ensure_column("fills", "symbol", "TEXT")
        self._ensure_column("fills", "source", "TEXT NOT NULL DEFAULT 'unknown'")
        self._ensure_column("fills", "provenance_detail", "TEXT")
        self._ensure_column("fills", "source_history", "TEXT NOT NULL DEFAULT '[]'")
        self._ensure_column("fills", "is_partial_fill", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("fills", "partial_fill_group_key", "TEXT")
        self._ensure_column("fills", "is_reconciled", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("fills", "reconciled_from_missing_ws", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("fills", "trade_query_available", "INTEGER")
        self._ensure_column("fills", "trade_query_attempted", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("trades", "reason", "TEXT")
        self._conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def write_event(self, ts: str, event_type: str, payload: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO events (ts, event_type, payload) VALUES (?, ?, ?)",
            (ts, event_type, json.dumps(payload, default=str)),
        )
        self._conn.commit()

    def _obj_to_dict(self, obj: Any) -> dict[str, Any]:
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, dict):
            return obj
        raise TypeError("Expected a dataclass or dict payload")

    def start_backtest_run(
        self,
        *,
        run_id: str,
        created_at: str,
        symbol: str,
        timeframe: str,
        initial_equity: float,
        config: dict[str, Any],
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO backtest_runs
            (run_id, created_at, started_at, symbol, timeframe, initial_equity, config_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, created_at, created_at, symbol, timeframe, initial_equity, json.dumps(config, default=str)),
        )
        self._conn.commit()

    def finish_backtest_run(self, run_id: str, summary: dict[str, Any]) -> None:
        self._conn.execute(
            "UPDATE backtest_runs SET summary_json = ? WHERE run_id = ?",
            (json.dumps(summary, default=str), run_id),
        )
        self._conn.commit()

    def save_order(self, order: Any) -> None:
        row = self._obj_to_dict(order)
        self._conn.execute(
            """
            INSERT INTO orders
            (run_id, symbol, order_id, client_order_id, ts, signal, side, position_side, reduce_only,
             order_type, qty, requested_price, stop_price, time_in_force, status, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["run_id"],
                row.get("symbol"),
                row["order_id"],
                row.get("client_order_id"),
                row["ts"],
                row["signal"],
                row["side"],
                row.get("position_side"),
                1 if bool(row.get("reduce_only", False)) else 0,
                row["order_type"],
                row["qty"],
                row.get("requested_price"),
                row.get("stop_price"),
                row.get("time_in_force"),
                row["status"],
                row.get("reason", ""),
            ),
        )
        self._conn.commit()

    def save_fill(self, fill: Any) -> None:
        row = self._normalize_fill_row(self._obj_to_dict(fill))
        existing = self._conn.execute(
            """
            SELECT id
            FROM fills
            WHERE run_id = ? AND fill_id = ?
            LIMIT 1
            """,
            (row["run_id"], row["fill_id"]),
        ).fetchone()
        if existing is not None:
            self._update_fill_metadata(row["run_id"], row["fill_id"], row)
            return
        self._conn.execute(
            """
            INSERT INTO fills
            (
                run_id, symbol, fill_id, order_id, ts, side, qty, price, fee, liquidity,
                source, provenance_detail, source_history, is_partial_fill, partial_fill_group_key,
                is_reconciled, reconciled_from_missing_ws, trade_query_available, trade_query_attempted
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["run_id"],
                row.get("symbol"),
                row["fill_id"],
                row["order_id"],
                row["ts"],
                row["side"],
                row["qty"],
                row["price"],
                row["fee"],
                row["liquidity"],
                row["source"],
                row.get("provenance_detail"),
                json.dumps(row["source_history"], ensure_ascii=True),
                1 if bool(row.get("is_partial_fill", False)) else 0,
                row.get("partial_fill_group_key"),
                1 if bool(row.get("is_reconciled", False)) else 0,
                1 if bool(row.get("reconciled_from_missing_ws", False)) else 0,
                (
                    None
                    if row.get("trade_query_available") is None
                    else 1 if bool(row.get("trade_query_available")) else 0
                ),
                1 if bool(row.get("trade_query_attempted", False)) else 0,
            ),
        )
        self._refresh_partial_fill_group(
            run_id=row["run_id"],
            order_id=row["order_id"],
            partial_fill_group_key=row.get("partial_fill_group_key"),
        )
        self._conn.commit()

    def merge_fill_provenance(self, *, run_id: str, fill_id: str, update: dict[str, Any]) -> None:
        row = self._normalize_fill_row({"run_id": run_id, "fill_id": fill_id, **update}, allow_missing_required=True)
        self._update_fill_metadata(run_id, fill_id, row)
        if row.get("order_id"):
            self._refresh_partial_fill_group(
                run_id=run_id,
                order_id=str(row["order_id"]),
                partial_fill_group_key=row.get("partial_fill_group_key"),
            )
        self._conn.commit()

    def find_fill_id_by_order_source(self, *, run_id: str, order_id: str, source: str) -> str | None:
        row = self._conn.execute(
            """
            SELECT fill_id
            FROM fills
            WHERE run_id = ? AND order_id = ? AND source = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (run_id, order_id, source),
        ).fetchone()
        if row is None:
            return None
        return str(row["fill_id"])

    def count_fills_for_order(self, *, run_id: str, order_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT fill_id) AS c FROM fills WHERE run_id = ? AND order_id = ?",
            (run_id, order_id),
        ).fetchone()
        return int(row["c"]) if row is not None else 0

    def save_trade(self, trade: Any) -> None:
        row = self._obj_to_dict(trade)
        self._conn.execute(
            """
            INSERT INTO trades
            (run_id, trade_id, symbol, side, entry_ts, exit_ts, qty, entry_price, exit_price,
             gross_pnl, fee_paid, funding_paid, net_pnl, return_pct, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["run_id"],
                row["trade_id"],
                row["symbol"],
                row["side"],
                row["entry_ts"],
                row["exit_ts"],
                row["qty"],
                row["entry_price"],
                row["exit_price"],
                row["gross_pnl"],
                row["fee_paid"],
                row["funding_paid"],
                row["net_pnl"],
                row["return_pct"],
                row.get("reason"),
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def start_optimize_run(
        self,
        *,
        optimize_run_id: str,
        created_at: str,
        strategy: str,
        symbols: list[str],
        timeframe: str,
        start_ts: str | None,
        end_ts: str | None,
        search_mode: str,
        metric: str,
        constraints: str | None,
        score_expr: str | None,
        top_n: int,
        walk_forward: bool,
        train_days: int | None,
        test_days: int | None,
        top_per_train: int | None,
        config: dict[str, Any],
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO optimize_runs
            (optimize_run_id, created_at, strategy, symbols, timeframe, start_ts, end_ts, search_mode, metric,
             constraints, score_expr, top_n, walk_forward, train_days, test_days, top_per_train, config_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                optimize_run_id,
                created_at,
                strategy,
                ",".join(symbols),
                timeframe,
                start_ts,
                end_ts,
                search_mode,
                metric,
                constraints,
                score_expr,
                top_n,
                1 if walk_forward else 0,
                train_days,
                test_days,
                top_per_train,
                json.dumps(config, default=str),
            ),
        )
        self._conn.commit()

    def save_optimize_result(self, result_row: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO optimize_results
            (optimize_run_id, candidate_run_id, symbol, timeframe, window_role, window_index, window_start, window_end,
             params_json, metrics_json, metric_value, score, objective, passed_constraints, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_row["optimize_run_id"],
                result_row["candidate_run_id"],
                result_row["symbol"],
                result_row["timeframe"],
                result_row["window_role"],
                result_row.get("window_index"),
                result_row.get("window_start"),
                result_row.get("window_end"),
                json.dumps(result_row["params"], default=str),
                json.dumps(result_row["metrics"], default=str),
                result_row.get("metric_value"),
                result_row.get("score"),
                result_row.get("objective"),
                1 if bool(result_row.get("passed_constraints", False)) else 0,
                result_row["created_at"],
            ),
        )
        self._conn.commit()

    def save_wfo_window(
        self,
        *,
        optimize_run_id: str,
        window_index: int,
        symbol: str,
        train_start: str,
        train_end: str,
        test_start: str,
        test_end: str,
        top_per_train: int,
        selected_count: int,
        created_at: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO wfo_runs
            (optimize_run_id, window_index, symbol, train_start, train_end, test_start, test_end,
             top_per_train, selected_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                optimize_run_id,
                window_index,
                symbol,
                train_start,
                train_end,
                test_start,
                test_end,
                top_per_train,
                selected_count,
                created_at,
            ),
        )
        self._conn.commit()

    def get_optimize_result_by_candidate_run_id(self, candidate_run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT r.*, o.strategy, o.timeframe AS run_timeframe
            FROM optimize_results r
            JOIN optimize_runs o ON o.optimize_run_id = r.optimize_run_id
            WHERE r.candidate_run_id = ?
            ORDER BY r.id DESC
            LIMIT 1
            """,
            (candidate_run_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_backtest_run_config(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT config_json FROM backtest_runs WHERE run_id = ? LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            parsed = json.loads(row["config_json"])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def save_runtime_state(
        self,
        *,
        run_id: str,
        last_bar_ts: str | None,
        open_positions: dict[str, Any],
        open_orders: dict[str, Any],
        strategy_state: dict[str, Any],
        risk_state: dict[str, Any],
        updated_at: str,
    ) -> None:
        current_symbol = str(open_positions.get("symbol", "")) if isinstance(open_positions, dict) else ""
        existing = self.load_runtime_state(run_id)
        merged_open_positions = self._merge_symbol_map(
            existing.get("open_positions") if existing else {},
            open_positions,
            force_symbol=current_symbol or None,
        )
        merged_open_orders = self._merge_symbol_map(
            existing.get("open_orders") if existing else {},
            open_orders,
            force_symbol=current_symbol or None,
        )
        merged_strategy_state = self._merge_symbol_map(
            existing.get("strategy_state") if existing else {},
            strategy_state,
            force_symbol=current_symbol or None,
        )
        merged_risk_state = self._merge_symbol_map(
            existing.get("risk_state") if existing else {},
            risk_state,
            force_symbol=current_symbol or None,
        )
        merged_last_bar_ts = self._latest_ts(existing.get("last_bar_ts") if existing else None, last_bar_ts)
        self._conn.execute(
            """
            INSERT INTO runtime_state
            (run_id, last_bar_ts, open_positions, open_orders, strategy_state, risk_state, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              last_bar_ts = excluded.last_bar_ts,
              open_positions = excluded.open_positions,
              open_orders = excluded.open_orders,
              strategy_state = excluded.strategy_state,
              risk_state = excluded.risk_state,
              updated_at = excluded.updated_at
            """,
            (
                run_id,
                merged_last_bar_ts,
                json.dumps(merged_open_positions, default=str),
                json.dumps(merged_open_orders, default=str),
                json.dumps(merged_strategy_state, default=str),
                json.dumps(merged_risk_state, default=str),
                updated_at,
            ),
        )
        self._conn.commit()

    def load_runtime_state(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT run_id, last_bar_ts, open_positions, open_orders, strategy_state, risk_state, updated_at
            FROM runtime_state
            WHERE run_id = ?
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "last_bar_ts": row["last_bar_ts"],
            "open_positions": self._parse_json_obj(row["open_positions"]),
            "open_orders": self._parse_json_obj(row["open_orders"]),
            "strategy_state": self._parse_json_obj(row["strategy_state"]),
            "risk_state": self._parse_json_obj(row["risk_state"]),
            "updated_at": row["updated_at"],
        }

    def get_latest_runtime_state(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT run_id
            FROM runtime_state
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return self.load_runtime_state(str(row["run_id"]))

    def get_latest_run_id(self) -> str | None:
        runtime = self._conn.execute(
            """
            SELECT run_id, updated_at AS ts
            FROM runtime_state
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if runtime is not None:
            return str(runtime["run_id"])
        backtest = self._conn.execute(
            """
            SELECT run_id, COALESCE(started_at, created_at) AS ts
            FROM backtest_runs
            ORDER BY ts DESC
            LIMIT 1
            """
        ).fetchone()
        if backtest is not None:
            return str(backtest["run_id"])
        return None

    def list_recent_events_for_run(self, run_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        scan_limit = max(limit * 25, 200)
        rows = self._conn.execute(
            """
            SELECT ts, event_type, payload
            FROM events
            ORDER BY id DESC
            LIMIT ?
            """,
            (scan_limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = self._parse_json_any(row["payload"])
            if str(payload.get("run_id", "")) != run_id:
                continue
            out.append({"ts": row["ts"], "event_type": row["event_type"], "payload": payload})
            if len(out) >= limit:
                break
        return out

    def list_recent_errors_for_run(self, run_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        tokens = ("error", "exception", "halt", "failed", "reject")
        out: list[dict[str, Any]] = []
        for row in self.list_recent_events_for_run(run_id, limit=max(limit * 10, 50)):
            event_type = str(row.get("event_type", "")).lower()
            payload = row.get("payload", {})
            has_error_key = isinstance(payload, dict) and any(k in payload for k in ("error", "errors"))
            reason_text = str(payload.get("reason", "")).lower() if isinstance(payload, dict) else ""
            reason_flag = any(token in reason_text for token in tokens)
            if any(token in event_type for token in tokens) or has_error_key or reason_flag:
                out.append(row)
                if len(out) >= limit:
                    break
        return out

    def get_run_status(self, run_id: str) -> dict[str, Any]:
        state = self.load_runtime_state(run_id)
        if state is None:
            state = {
                "run_id": run_id,
                "last_bar_ts": None,
                "open_positions": {},
                "open_orders": {},
                "strategy_state": {},
                "risk_state": {},
                "updated_at": None,
            }
        counts = self._conn.execute(
            """
            SELECT
              (SELECT COUNT(DISTINCT order_id) FROM orders WHERE run_id = ?) AS orders_count,
              (SELECT COUNT(DISTINCT fill_id) FROM fills WHERE run_id = ?) AS fills_count,
              (SELECT COUNT(*) FROM trades WHERE run_id = ?) AS trades_count,
              (SELECT COALESCE(SUM(net_pnl), 0.0) FROM trades WHERE run_id = ?) AS trades_net_pnl,
              (SELECT COALESCE(SUM(fee_paid), 0.0) FROM trades WHERE run_id = ?) AS trades_fee
            """,
            (run_id, run_id, run_id, run_id, run_id),
        ).fetchone()
        fill_rows = self._conn.execute(
            """
            SELECT
              fill_id,
              order_id,
              source,
              source_history,
              is_partial_fill,
              partial_fill_group_key,
              is_reconciled,
              reconciled_from_missing_ws,
              trade_query_available,
              trade_query_attempted
            FROM fills
            WHERE run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()
        provenance = self._build_fill_observability(fill_rows)
        return {
            **state,
            "orders_count": int(counts["orders_count"]) if counts is not None else 0,
            "fills_count": int(counts["fills_count"]) if counts is not None else 0,
            "trades_count": int(counts["trades_count"]) if counts is not None else 0,
            "trades_net_pnl": float(counts["trades_net_pnl"]) if counts is not None else 0.0,
            "trades_fee": float(counts["trades_fee"]) if counts is not None else 0.0,
            **provenance,
        }

    def _parse_json_obj(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    def _parse_json_any(self, raw: Any) -> Any:
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return raw

    def _as_symbol_map(self, payload: dict[str, Any], *, force_symbol: str | None = None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        if force_symbol:
            return {str(force_symbol): payload}
        if "symbol" in payload and isinstance(payload.get("symbol"), str):
            sym = str(payload.get("symbol"))
            return {sym: payload}
        return dict(payload)

    def _merge_symbol_map(self, existing: Any, incoming: Any, *, force_symbol: str | None = None) -> dict[str, Any]:
        base = self._as_symbol_map(existing if isinstance(existing, dict) else {})
        add = self._as_symbol_map(incoming if isinstance(incoming, dict) else {}, force_symbol=force_symbol)
        if not add:
            return base
        for sym, payload in add.items():
            base[str(sym)] = payload
        return base

    def _latest_ts(self, left: str | None, right: str | None) -> str | None:
        if not left:
            return right
        if not right:
            return left
        try:
            return right if str(right) > str(left) else left
        except Exception:
            return right or left

    def _normalize_fill_row(self, row: dict[str, Any], *, allow_missing_required: bool = False) -> dict[str, Any]:
        source = str(row.get("source", row.get("provenance_source", "unknown")) or "unknown")
        history = self._normalize_source_history(row.get("source_history"), source=source)
        partial_fill_group_key = row.get("partial_fill_group_key")
        if not partial_fill_group_key and row.get("run_id") and row.get("order_id"):
            partial_fill_group_key = f"{row['run_id']}:{row['order_id']}"
        trade_query_available = row.get("trade_query_available")
        if trade_query_available is not None:
            trade_query_available = bool(trade_query_available)
        trade_query_attempted = bool(row.get("trade_query_attempted", trade_query_available is not None))
        normalized = {
            **row,
            "source": source,
            "provenance_detail": row.get("provenance_detail", row.get("source_detail")),
            "source_history": history,
            "is_partial_fill": bool(row.get("is_partial_fill", False)),
            "partial_fill_group_key": partial_fill_group_key,
            "is_reconciled": bool(row.get("is_reconciled", source in {"rest_trade_reconcile", "aggregated_fallback"})),
            "reconciled_from_missing_ws": bool(
                row.get("reconciled_from_missing_ws", source in {"rest_trade_reconcile", "aggregated_fallback"})
            ),
            "trade_query_available": trade_query_available,
            "trade_query_attempted": trade_query_attempted,
        }
        if allow_missing_required:
            return normalized
        required = ("run_id", "fill_id", "order_id", "ts", "side", "qty", "price", "fee", "liquidity")
        missing = [key for key in required if key not in normalized]
        if missing:
            raise KeyError(f"Missing fill fields: {', '.join(missing)}")
        return normalized

    def _normalize_source_history(self, raw: Any, *, source: str) -> list[str]:
        values: list[str] = []
        if isinstance(raw, str):
            text = raw.strip()
            if text:
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = [part.strip() for part in text.split(",") if part.strip()]
                raw = parsed
        if isinstance(raw, (list, tuple, set)):
            for item in raw:
                text = str(item).strip()
                if text and text not in values:
                    values.append(text)
        if source and source not in values:
            values.append(source)
        return values

    def _update_fill_metadata(self, run_id: str, fill_id: str, row: dict[str, Any]) -> None:
        existing = self._conn.execute(
            """
            SELECT
              source,
              provenance_detail,
              source_history,
              is_partial_fill,
              partial_fill_group_key,
              is_reconciled,
              reconciled_from_missing_ws,
              trade_query_available,
              trade_query_attempted
            FROM fills
            WHERE run_id = ? AND fill_id = ?
            LIMIT 1
            """,
            (run_id, fill_id),
        ).fetchone()
        if existing is None:
            return
        current_source = str(existing["source"] or "unknown")
        merged_history = self._normalize_source_history(existing["source_history"], source=current_source)
        for item in row.get("source_history", []):
            text = str(item).strip()
            if text and text not in merged_history:
                merged_history.append(text)
        detail = str(existing["provenance_detail"] or "").strip() or row.get("provenance_detail")
        partial_fill_group_key = existing["partial_fill_group_key"] or row.get("partial_fill_group_key")
        if row.get("order_id") and not partial_fill_group_key:
            partial_fill_group_key = f"{run_id}:{row['order_id']}"
        trade_query_available = existing["trade_query_available"]
        if trade_query_available is None and row.get("trade_query_available") is not None:
            trade_query_available = 1 if bool(row.get("trade_query_available")) else 0
        self._conn.execute(
            """
            UPDATE fills
            SET
              provenance_detail = ?,
              source_history = ?,
              is_partial_fill = ?,
              partial_fill_group_key = ?,
              is_reconciled = ?,
              reconciled_from_missing_ws = ?,
              trade_query_available = ?,
              trade_query_attempted = ?
            WHERE run_id = ? AND fill_id = ?
            """,
            (
                detail,
                json.dumps(merged_history, ensure_ascii=True),
                1 if (bool(existing["is_partial_fill"]) or bool(row.get("is_partial_fill", False))) else 0,
                partial_fill_group_key,
                1 if (bool(existing["is_reconciled"]) or bool(row.get("is_reconciled", False))) else 0,
                1
                if (
                    bool(existing["reconciled_from_missing_ws"]) or bool(row.get("reconciled_from_missing_ws", False))
                )
                else 0,
                trade_query_available,
                1 if (bool(existing["trade_query_attempted"]) or bool(row.get("trade_query_attempted", False))) else 0,
                run_id,
                fill_id,
            ),
        )

    def _refresh_partial_fill_group(self, *, run_id: str, order_id: str, partial_fill_group_key: str | None) -> None:
        rows = self._conn.execute(
            """
            SELECT fill_id
            FROM fills
            WHERE run_id = ? AND order_id = ?
            ORDER BY id ASC
            """,
            (run_id, order_id),
        ).fetchall()
        if len(rows) <= 1:
            return
        group_key = partial_fill_group_key or f"{run_id}:{order_id}"
        self._conn.execute(
            """
            UPDATE fills
            SET is_partial_fill = 1,
                partial_fill_group_key = COALESCE(partial_fill_group_key, ?)
            WHERE run_id = ? AND order_id = ?
            """,
            (group_key, run_id, order_id),
        )

    def _build_fill_observability(self, fill_rows: list[sqlite3.Row]) -> dict[str, Any]:
        by_source: Counter[str] = Counter()
        partial_fill_groups: set[str] = set()
        fills_with_source_history_count = 0
        partial_fills_count = 0
        fills_reconciled_count = 0
        reconciled_missing_ws_fill_count = 0
        trade_query_unavailable_count = 0
        for row in fill_rows:
            source = str(row["source"] or "unknown")
            by_source[source] += 1
            history = self._normalize_source_history(row["source_history"], source=source)
            if len(history) > 1:
                fills_with_source_history_count += 1
            if bool(row["is_partial_fill"]):
                partial_fills_count += 1
                group_key = str(row["partial_fill_group_key"] or "").strip()
                if group_key:
                    partial_fill_groups.add(group_key)
            if bool(row["is_reconciled"]):
                fills_reconciled_count += 1
            if bool(row["reconciled_from_missing_ws"]):
                reconciled_missing_ws_fill_count += 1
            if row["trade_query_available"] == 0:
                trade_query_unavailable_count += 1
        fills_count = len(fill_rows)
        by_source_dict = dict(sorted(by_source.items()))
        fills_from_user_stream_count = int(by_source.get("user_stream", 0))
        fills_from_rest_reconcile_count = int(by_source.get("rest_trade_reconcile", 0))
        fills_from_aggregated_fallback_count = int(by_source.get("aggregated_fallback", 0))
        fill_provenance_consistency_pass = fills_count == sum(by_source.values()) and all(
            str(source).strip() and str(source).strip().lower() != "unknown" for source in by_source
        )
        return {
            "fills_reconciled_count": fills_reconciled_count,
            "fills_from_user_stream_count": fills_from_user_stream_count,
            "fills_from_rest_reconcile_count": fills_from_rest_reconcile_count,
            "fills_from_aggregated_fallback_count": fills_from_aggregated_fallback_count,
            "aggregated_fallback_fill_count": fills_from_aggregated_fallback_count,
            "partial_fills_count": partial_fills_count,
            "reconciled_missing_ws_fill_count": reconciled_missing_ws_fill_count,
            "trade_query_unavailable_count": trade_query_unavailable_count,
            "fill_provenance_consistency_pass": fill_provenance_consistency_pass,
            "fill_provenance_breakdown": {
                "by_source": by_source_dict,
                "fills_with_source_history_count": fills_with_source_history_count,
                "fills_reconciled_count": fills_reconciled_count,
            },
            "partial_fill_audit_summary": {
                "partial_fill_groups_count": len(partial_fill_groups),
                "partial_fill_rows_count": partial_fills_count,
                "aggregated_fallback_fill_count": fills_from_aggregated_fallback_count,
                "reconciled_missing_ws_fill_count": reconciled_missing_ws_fill_count,
                "trade_query_unavailable_count": trade_query_unavailable_count,
                "fills_with_multiple_source_history_count": fills_with_source_history_count,
            },
        }
