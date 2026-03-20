from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _parse_json(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return raw


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _iso_to_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _minutes_between(start: str | None, end: str | None) -> float | None:
    start_dt = _iso_to_datetime(start)
    end_dt = _iso_to_datetime(end)
    if start_dt is None or end_dt is None:
        return None
    return round((end_dt - start_dt).total_seconds() / 60.0, 2)


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _load_context(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    return raw if isinstance(raw, dict) else {}


def _timeframe_minutes(timeframe: str) -> float | None:
    text = str(timeframe).strip().lower()
    if not text:
        return None
    try:
        if text.endswith("m"):
            return float(int(text[:-1]))
        if text.endswith("h"):
            return float(int(text[:-1]) * 60)
        if text.endswith("d"):
            return float(int(text[:-1]) * 1440)
    except Exception:
        return None
    return None


def _normalize_symbol_map(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    if "symbol" in raw and isinstance(raw.get("symbol"), str):
        return {str(raw["symbol"]): raw}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def _run_events(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ts, event_type, payload
        FROM events
        WHERE json_extract(payload, '$.run_id') = ?
        ORDER BY id ASC
        """,
        (run_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = _parse_json(row["payload"])
        out.append(
            {
                "ts": str(row["ts"]),
                "event_type": str(row["event_type"]),
                "payload": payload if isinstance(payload, dict) else {},
            }
        )
    return out


def _runtime_state(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
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
        "run_id": str(row["run_id"]),
        "last_bar_ts": row["last_bar_ts"],
        "open_positions": _parse_json(row["open_positions"]),
        "open_orders": _parse_json(row["open_orders"]),
        "strategy_state": _parse_json(row["strategy_state"]),
        "risk_state": _parse_json(row["risk_state"]),
        "updated_at": str(row["updated_at"]),
    }


def _event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in events:
        counts[str(row["event_type"])] += 1
    return dict(counts)


def _first_event_time(events: list[dict[str, Any]], event_type: str) -> str | None:
    for row in events:
        if str(row["event_type"]) == event_type:
            return str(row["ts"])
    return None


def _first_payload_for(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    for row in events:
        if str(row["event_type"]) == event_type:
            payload = row.get("payload", {})
            return payload if isinstance(payload, dict) else {}
    return {}


def _stage_sequence() -> list[str]:
    return [
        "feed_worker_thread_created",
        "feed_worker_thread_started",
        "feed_worker_entered",
        "feed_worker_entered_iter_closed_bars",
        "binance_live_feed_initialized",
        "websocket_worker_start_called",
        "ws_worker_started",
        "ws_worker_connected",
        "first_market_payload_received",
        "first_kline_payload_received",
        "first_closed_kline_received",
        "first_bar_dispatched",
        "runtime_stopped",
    ]


def _stage_progression(events: list[dict[str, Any]], expected_symbols: int) -> dict[str, Any]:
    counts = _event_counts(events)
    ordered = _stage_sequence()
    worker_stages = ordered[:4]
    direct_feed_stages = ordered[4:]
    if all(int(counts.get(stage, 0)) == 0 for stage in worker_stages) and any(
        int(counts.get(stage, 0)) > 0 for stage in direct_feed_stages
    ):
        ordered = direct_feed_stages
    last_confirmed = ""
    first_missing = ""
    for stage in ordered:
        if int(counts.get(stage, 0)) >= expected_symbols:
            last_confirmed = stage
            continue
        first_missing = stage
        break
    if not first_missing:
        first_missing = "none"
    return {
        "expected_symbols": expected_symbols,
        "counts": {stage: int(counts.get(stage, 0)) for stage in ordered},
        "last_confirmed_healthy_stage": last_confirmed or "none",
        "first_missing_stage": first_missing,
    }


def _log_signals(stdout_text: str, stderr_text: str) -> dict[str, int]:
    combined = f"{stdout_text}\n{stderr_text}"
    return {
        "user_stream_no_running_event_loop": len(
            re.findall(re.escape("disconnected (no running event loop)"), combined)
        ),
        "user_stream_disconnect_count": len(re.findall(re.escape("[user-stream] disconnected"), combined)),
        "user_stream_dns_reconnect_count": len(re.findall(re.escape("Could not contact DNS servers"), combined)),
        "user_stream_reconnect_exhausted_count": len(re.findall(re.escape("[user-stream] reconnect exhausted"), combined)),
    }


def _summarize_symbols(state: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if state is None:
        return [], {
            "processed_total": 0,
            "processed_live_bars": 0,
            "processed_backfill_bars": 0,
            "feed_event_count": 0,
        }
    risk_state = _normalize_symbol_map(state.get("risk_state"))
    feed_totals = {
        "processed_total": 0,
        "processed_live_bars": 0,
        "processed_backfill_bars": 0,
        "feed_event_count": 0,
    }
    per_symbol: list[dict[str, Any]] = []
    for symbol in sorted(risk_state):
        item = dict(risk_state.get(symbol, {}))
        feed_health = item.get("feed_health", {}) if isinstance(item.get("feed_health"), dict) else {}
        summary = {
            "symbol": symbol,
            "processed_bars": int(item.get("processed_bars", 0) or 0),
            "processed_live_bars": int(item.get("processed_live_bars", 0) or 0),
            "processed_backfill_bars": int(item.get("processed_backfill_bars", 0) or 0),
            "feed_event_count": int(item.get("feed_event_count", 0) or 0),
            "first_bar_delay_sec": item.get("first_bar_delay_sec"),
            "first_live_bar_delay_sec": item.get("first_live_bar_delay_sec"),
            "last_feed_event_type": item.get("last_feed_event_type"),
            "halted": bool(item.get("halted", False)),
            "halt_reason": str(item.get("halt_reason", "") or ""),
            "ws_event_counts": dict(feed_health.get("ws_event_counts", {})) if isinstance(feed_health, dict) else {},
            "ws_payload_count": int(feed_health.get("ws_payload_count", 0) or 0),
            "ws_kline_payload_count": int(feed_health.get("ws_kline_payload_count", 0) or 0),
            "ws_closed_kline_count": int(feed_health.get("ws_closed_kline_count", 0) or 0),
            "last_emitted_ts": feed_health.get("last_emitted_ts"),
        }
        per_symbol.append(summary)
        feed_totals["processed_total"] += summary["processed_bars"]
        feed_totals["processed_live_bars"] += summary["processed_live_bars"]
        feed_totals["processed_backfill_bars"] += summary["processed_backfill_bars"]
        feed_totals["feed_event_count"] += summary["feed_event_count"]
    return per_symbol, feed_totals


def _diagnostic_verdict(
    *,
    processed_total: int,
    processed_live_bars: int,
    duration_minutes: float | None,
    timeframe: str,
    stage_progression: dict[str, Any],
    first_ws_connect_time: str | None,
    first_ws_payload_time: str | None,
    first_kline_time: str | None,
    first_closed_kline_time: str | None,
    first_bar_processed_time: str | None,
    symbol_mismatch_count: int,
) -> tuple[str, str]:
    if processed_total > 0:
        if processed_live_bars > 0:
            return (
                "non_zero_live_bars",
                "Closed bars were processed from the live runtime path.",
            )
        return (
            "backfill_only_bars",
            "Only backfill bars were processed; live closed-bar ingestion still needs confirmation.",
        )
    first_missing_stage = str(stage_progression.get("first_missing_stage", "") or "")
    if first_missing_stage == "feed_worker_thread_started":
        return (
            "feed_worker_thread_not_started",
            "Feed worker threads were created but never recorded a start milestone.",
        )
    if first_missing_stage in {"feed_worker_entered", "feed_worker_entered_iter_closed_bars"}:
        return (
            "feed_worker_started_no_feed_entry",
            "Feed worker threads started but did not reach the feed iteration path.",
        )
    if first_missing_stage in {"binance_live_feed_initialized", "websocket_worker_start_called"}:
        return (
            "feed_loop_entered_no_websocket_start",
            "The feed worker reached the feed loop but did not record websocket startup.",
        )
    if first_ws_connect_time is None:
        return ("zero_bar_no_feed_connection", "No websocket connection milestone was recorded.")
    if first_ws_payload_time is None:
        return ("zero_bar_connected_no_payload", "The websocket connected but no payload milestone was recorded.")
    if first_kline_time is None:
        return ("zero_bar_payload_without_kline", "Payloads arrived but no kline payload was recognized.")
    if first_closed_kline_time is None:
        tf_minutes = _timeframe_minutes(timeframe)
        if duration_minutes is not None and tf_minutes is not None and duration_minutes + 1e-9 < tf_minutes:
            return (
                "insufficient_elapsed_time_for_closed_bar",
                f"Kline payloads arrived, but the run ended after {duration_minutes:.2f} minutes, before a `{timeframe}` candle could reasonably close.",
            )
        if symbol_mismatch_count > 0:
            return (
                "zero_bar_symbol_or_stream_mismatch",
                "Kline payloads arrived but symbol or stream mismatches prevented closed-bar extraction.",
            )
        return (
            "zero_bar_kline_without_close",
            "Kline payloads arrived but no closed kline was observed before the session ended.",
        )
    if first_bar_processed_time is None:
        return (
            "zero_bar_closed_kline_no_dispatch",
            "A closed kline was observed but the runtime never recorded bar dispatch.",
        )
    return ("zero_bar_unclassified", "No processed bars were recorded and the failure pattern was not classified.")


def build_runtime_diagnostic_summary(context: dict[str, Any]) -> dict[str, Any]:
    db_path = Path(str(context["db_path"]))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        run_id = str(context.get("run_id", "") or "")
        state = _runtime_state(conn, run_id) if run_id else None
        events = _run_events(conn, run_id) if run_id else []
        counts = _event_counts(events)
        per_symbol, feed_totals = _summarize_symbols(state)
    finally:
        conn.close()

    stdout_path = Path(str(context.get("run_stdout", ""))) if context.get("run_stdout") else None
    stderr_path = Path(str(context.get("run_stderr", ""))) if context.get("run_stderr") else None
    stdout_text = _read_text(stdout_path) if stdout_path else ""
    stderr_text = _read_text(stderr_path) if stderr_path else ""
    log_signals = _log_signals(stdout_text, stderr_text)
    expected_symbols = max(len([sym.strip() for sym in str(context.get("symbols", "")).split(",") if sym.strip()]), 1)
    stage_progression = _stage_progression(events, expected_symbols=expected_symbols)

    first_feed_event_time = None
    for row in events:
        if str(row["event_type"]).startswith("ws_") or str(row["event_type"]).startswith("first_") or str(
            row["event_type"]
        ).startswith("feed_worker_") or str(row["event_type"]) in {"binance_live_feed_initialized", "websocket_worker_start_called"}:
            first_feed_event_time = str(row["ts"])
            break

    first_feed_worker_thread_created = _first_event_time(events, "feed_worker_thread_created")
    first_feed_worker_thread_started = _first_event_time(events, "feed_worker_thread_started")
    first_feed_worker_entered = _first_event_time(events, "feed_worker_entered")
    first_feed_worker_iter_entered = _first_event_time(events, "feed_worker_entered_iter_closed_bars")
    first_feed_initialized_time = _first_event_time(events, "binance_live_feed_initialized")
    first_websocket_start_called_time = _first_event_time(events, "websocket_worker_start_called")
    first_websocket_worker_started_time = _first_event_time(events, "ws_worker_started")
    first_ws_connect_time = _first_event_time(events, "ws_worker_connected")
    first_ws_payload_time = _first_event_time(events, "first_market_payload_received") or _first_event_time(
        events, "first_ws_payload_received"
    )
    first_kline_time = _first_event_time(events, "first_kline_payload_received")
    first_closed_kline_time = _first_event_time(events, "first_closed_kline_received")
    first_bar_dispatched_time = _first_event_time(events, "first_bar_dispatched")
    first_bar_processed_time = _first_event_time(events, "first_bar_processed")
    first_live_bar_processed_time = _first_event_time(events, "first_live_bar_processed")
    runtime_started_at = _first_event_time(events, "runtime_started")
    runtime_stopped_at = _first_event_time(events, "runtime_stopped")
    zero_bar_session_detected = counts.get("zero_bar_session_detected", 0) > 0
    reconnect_count = int(counts.get("ws_worker_reconnect", 0))
    stale_stream_incidents = int(counts.get("ws_receive_timeout", 0) + counts.get("feed_stall_detected", 0))
    symbol_mismatch_count = int(counts.get("ws_symbol_mismatch", 0))
    processed_total = int(feed_totals["processed_total"])
    processed_live_bars = int(feed_totals["processed_live_bars"])
    processed_backfill_bars = int(feed_totals["processed_backfill_bars"])
    duration_minutes = _minutes_between(context.get("start_utc"), context.get("end_utc"))
    validation_mode = str(context.get("validation_mode", "real_strategy") or "real_strategy")
    pipeline_proof_mode = _to_bool(context.get("pipeline_proof_mode", False))
    evidence_scope = "runtime_pipeline_only" if pipeline_proof_mode else "real_strategy_runtime"
    strategy_lifecycle_validation_applicable = _to_bool(
        context.get("strategy_evidence_allowed", validation_mode == "real_strategy")
    )
    verdict_code, verdict_reason = _diagnostic_verdict(
        processed_total=processed_total,
        processed_live_bars=processed_live_bars,
        duration_minutes=duration_minutes,
        timeframe=str(context.get("timeframe", "")),
        stage_progression=stage_progression,
        first_ws_connect_time=first_ws_connect_time,
        first_ws_payload_time=first_ws_payload_time,
        first_kline_time=first_kline_time,
        first_closed_kline_time=first_closed_kline_time,
        first_bar_processed_time=first_bar_processed_time,
        symbol_mismatch_count=symbol_mismatch_count,
    )

    issues: list[str] = []
    warnings: list[str] = []
    if not str(context.get("run_id", "")).strip():
        issues.append("fresh_run_id_missing")
    if verdict_code.startswith("zero_bar"):
        issues.append(verdict_code)
    elif verdict_code == "insufficient_elapsed_time_for_closed_bar":
        warnings.append("insufficient_elapsed_time_for_closed_bar")
    if stale_stream_incidents > 0:
        warnings.append("stale_stream_incidents_observed")
    if reconnect_count > 0:
        warnings.append("websocket_reconnects_observed")
    if processed_total > 0 and processed_live_bars == 0:
        warnings.append("processed_bars_are_backfill_only")
    if _to_bool(context.get("process_exited_before_runtime_stopped", False)):
        warnings.append("wrapper_process_exited_before_runtime_stopped_event")
    if log_signals["user_stream_reconnect_exhausted_count"] > 0:
        warnings.append("user_stream_reconnect_exhausted")

    runtime_stopped_payload = _first_payload_for(events, "runtime_stopped")
    stop_reason = str(runtime_stopped_payload.get("halt_reason", "") or context.get("startup_failure_reason", "") or "")
    forced_stop_applied = _to_bool(context.get("forced_stop_applied", False))
    raw_exit_code = context.get("exit_code", -999)
    process_exit_code = -999 if raw_exit_code is None or raw_exit_code == "" else int(raw_exit_code)

    stop_classification = "missing_stop_record"
    if runtime_stopped_at is not None and not forced_stop_applied and process_exit_code == 0:
        stop_classification = "normal_graceful_stop"
    elif forced_stop_applied:
        stop_classification = "forced_wall_stop"
    elif process_exit_code not in {0, -999}:
        stop_classification = "abnormal_stop"

    feed_runtime_chain_proven = bool(
        processed_total > 0
        and first_closed_kline_time is not None
        and first_bar_dispatched_time is not None
        and first_bar_processed_time is not None
    )
    runtime_validation_confidence_advanced = bool(
        feed_runtime_chain_proven and stop_classification == "normal_graceful_stop"
    )
    long_run_gap_closed = bool(
        validation_mode == "real_strategy"
        and runtime_validation_confidence_advanced
        and processed_live_bars > 0
        and first_kline_time is not None
        and stop_reason == ""
    )

    if pipeline_proof_mode and feed_runtime_chain_proven and stop_classification == "normal_graceful_stop":
        human_explanation = "The live feed/runtime chain reached closed kline, bar dispatch, processed bars, and graceful stop in pipeline-proof mode."
        next_action = "Use the real 4h incumbent command to capture the same chain on the intended timeframe."
        verdict = "PASS"
    elif long_run_gap_closed:
        human_explanation = "The incumbent MACD runtime processed live closed bars and preserved enough timeline detail to diagnose reconnect and startup behavior."
        next_action = "A longer paper or testnet validation run can proceed under the same artifact path."
        verdict = "PASS"
    elif verdict_code == "insufficient_elapsed_time_for_closed_bar":
        human_explanation = verdict_reason
        next_action = "Extend the same run past at least one full candle close before making a closure call on the long-run gap."
        verdict = "WARNING"
    elif verdict_code == "backfill_only_bars":
        human_explanation = "Startup processing worked, but only backfill bars were confirmed. The live closed-bar path still needs direct evidence before the long-run gap can be called closed."
        next_action = "Run the same validation long enough to record at least one live closed bar, or explicitly capture why live closed bars are not expected yet."
        verdict = "WARNING"
    else:
        human_explanation = verdict_reason
        next_action = "Use the classified failure stage to make the next runtime change narrow and reviewable."
        verdict = "FAIL"

    if runtime_stopped_at is None and str(context.get("run_id", "")).strip():
        warnings.append("runtime_stopped_event_missing")

    runtime_stopped_status = "missing"
    if runtime_stopped_at is not None:
        runtime_stopped_status = "abnormal" if stop_reason else "normal"

    user_stream_correlation = "none"
    if log_signals["user_stream_disconnect_count"] > 0:
        user_stream_correlation = (
            "incidental_until_market_feed_starts"
            if first_websocket_start_called_time is None
            else "concurrent_with_market_feed_startup"
        )

    return {
        "verdict": verdict,
        "feed_runtime_chain_proven": feed_runtime_chain_proven,
        "runtime_validation_confidence_advanced": runtime_validation_confidence_advanced,
        "long_run_gap_closed": long_run_gap_closed,
        "diagnostic_verdict": verdict_code,
        "diagnostic_reason": verdict_reason,
        "validation_mode": validation_mode,
        "pipeline_proof_mode": pipeline_proof_mode,
        "evidence_scope": evidence_scope,
        "strategy_evidence_allowed": strategy_lifecycle_validation_applicable,
        "strategy_lifecycle_validation_applicable": strategy_lifecycle_validation_applicable,
        "strategy": str(context.get("strategy", "")),
        "candidate_profile": str(context.get("candidate_profile", "")),
        "mode": str(context.get("mode", "")),
        "env": str(context.get("env", "")),
        "data_mode": str(context.get("data_mode", "")),
        "timeframe": str(context.get("timeframe", "")),
        "symbols": [sym.strip() for sym in str(context.get("symbols", "")).split(",") if sym.strip()],
        "preset": str(context.get("preset", "")),
        "run_id": str(context.get("run_id", "")),
        "previous_latest_run_id": str(context.get("previous_latest_run_id", "")),
        "start_utc": context.get("start_utc"),
        "end_utc": context.get("end_utc"),
        "duration_minutes": duration_minutes,
        "realtime_only": _to_bool(context.get("realtime_only", False)),
        "stop_policy": str(context.get("stop_policy", "")),
        "forced_stop_applied": forced_stop_applied,
        "forced_stop_reason": str(context.get("forced_stop_reason", "")),
        "expected_next_close_utc": context.get("expected_next_close_utc"),
        "minutes_until_next_close_at_start": context.get("minutes_until_next_close_at_start"),
        "seconds_until_next_close_at_start": context.get("seconds_until_next_close_at_start"),
        "command": str(context.get("command", "")),
        "startup": {
            "phase": str(context.get("startup_phase", "")),
            "failure_reason": str(context.get("startup_failure_reason", "")),
            "fresh_run_id_detected": _to_bool(context.get("fresh_run_id_detected", False)),
            "run_id_detection_source": str(context.get("run_id_detection_source", "")),
            "first_status_seen": _to_bool(context.get("first_status_seen", False)),
            "first_event_seen": _to_bool(context.get("first_event_seen", False)),
            "first_event_type": str(context.get("first_event_type", "")),
            "first_event_ts": context.get("first_event_ts"),
            "runtime_started_at": runtime_started_at,
            "runtime_stopped_at": runtime_stopped_at,
            "runtime_stopped_status": runtime_stopped_status,
            "stop_classification": stop_classification,
            "process_exit_code": process_exit_code,
            "process_exited_before_runtime_stopped": _to_bool(
                context.get("process_exited_before_runtime_stopped", False)
            ),
        },
        "feed": {
            "processed_bars_total": processed_total,
            "processed_live_bars": processed_live_bars,
            "processed_backfill_bars": processed_backfill_bars,
            "feed_event_count_total": int(feed_totals["feed_event_count"]),
            "event_counts": counts,
            "stage_progression": stage_progression,
            "first_feed_event_time": first_feed_event_time,
            "first_feed_worker_thread_created_time": first_feed_worker_thread_created,
            "first_feed_worker_thread_started_time": first_feed_worker_thread_started,
            "first_feed_worker_entered_time": first_feed_worker_entered,
            "first_feed_worker_entered_iter_time": first_feed_worker_iter_entered,
            "first_feed_initialized_time": first_feed_initialized_time,
            "first_websocket_start_called_time": first_websocket_start_called_time,
            "first_websocket_worker_started_time": first_websocket_worker_started_time,
            "first_ws_connect_time": first_ws_connect_time,
            "first_ws_payload_time": first_ws_payload_time,
            "first_kline_event_time": first_kline_time,
            "first_closed_kline_time": first_closed_kline_time,
            "first_bar_dispatched_time": first_bar_dispatched_time,
            "first_bar_processed_time": first_bar_processed_time,
            "first_live_bar_processed_time": first_live_bar_processed_time,
            "reconnect_count": reconnect_count,
            "stale_stream_incident_count": stale_stream_incidents,
            "zero_bar_session_detected": zero_bar_session_detected,
            "symbol_mismatch_count": symbol_mismatch_count,
            "per_symbol": per_symbol,
        },
        "user_stream": log_signals,
        "user_stream_correlation": user_stream_correlation,
        "stop_reason": stop_reason,
        "state_updated_at": state.get("updated_at") if state else None,
        "last_bar_ts": state.get("last_bar_ts") if state else None,
        "issues": issues,
        "warnings": warnings,
        "human_explanation": human_explanation,
        "next_action": next_action,
        "artifacts": {
            "run_stdout": str(stdout_path) if stdout_path else "",
            "run_stderr": str(stderr_path) if stderr_path else "",
            "status_final": str(context.get("status_final", "")),
            "status_snapshots": str(context.get("status_snapshots", "")),
            "doctor_preflight": str(context.get("doctor_preflight", "")),
        },
    }


def render_runtime_diagnostic_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# MACD Long-Run Diagnostic Summary",
        "",
        f"- Verdict: `{summary.get('verdict', 'FAIL')}`",
        f"- Validation mode: `{summary.get('validation_mode', '')}`",
        f"- Evidence scope: `{summary.get('evidence_scope', '')}`",
        f"- Strategy evidence allowed: `{str(bool(summary.get('strategy_evidence_allowed', False))).lower()}`",
        f"- Strategy lifecycle validation applicable: `{str(bool(summary.get('strategy_lifecycle_validation_applicable', False))).lower()}`",
        f"- Long-run gap closed: `{str(bool(summary.get('long_run_gap_closed', False))).lower()}`",
        f"- Feed/runtime chain proven: `{str(bool(summary.get('feed_runtime_chain_proven', False))).lower()}`",
        f"- Runtime validation confidence advanced: `{str(bool(summary.get('runtime_validation_confidence_advanced', False))).lower()}`",
        f"- Diagnostic verdict: `{summary.get('diagnostic_verdict', '')}`",
        f"- Run ID: `{summary.get('run_id', '')}`",
        f"- Runtime: `{summary.get('mode', '')}` / `{summary.get('env', '')}` / `{summary.get('data_mode', '')}` / timeframe `{summary.get('timeframe', '')}`",
        f"- Symbols: `{', '.join(summary.get('symbols', []))}`",
        "",
        "## What Happened",
        "",
        f"{summary.get('human_explanation', '')}",
        "",
        f"- Startup phase: `{summary.get('startup', {}).get('phase', '')}`",
        f"- Last confirmed healthy stage: `{summary.get('feed', {}).get('stage_progression', {}).get('last_confirmed_healthy_stage', '')}`",
        f"- First missing stage: `{summary.get('feed', {}).get('stage_progression', {}).get('first_missing_stage', '')}`",
        f"- Runtime stopped status: `{summary.get('startup', {}).get('runtime_stopped_status', '')}`",
        f"- Stop classification: `{summary.get('startup', {}).get('stop_classification', '')}`",
        f"- Expected next close at start: `{summary.get('expected_next_close_utc')}`",
        f"- First websocket payload: `{summary.get('feed', {}).get('first_ws_payload_time')}`",
        f"- First kline payload: `{summary.get('feed', {}).get('first_kline_event_time')}`",
        f"- First closed kline: `{summary.get('feed', {}).get('first_closed_kline_time')}`",
        f"- First bar dispatched: `{summary.get('feed', {}).get('first_bar_dispatched_time')}`",
        f"- First bar processed: `{summary.get('feed', {}).get('first_bar_processed_time')}`",
        f"- Processed bars: `{summary.get('feed', {}).get('processed_bars_total', 0)}` total, `{summary.get('feed', {}).get('processed_live_bars', 0)}` live, `{summary.get('feed', {}).get('processed_backfill_bars', 0)}` backfill",
        f"- Reconnect count: `{summary.get('feed', {}).get('reconnect_count', 0)}`",
        f"- Stale-stream incidents: `{summary.get('feed', {}).get('stale_stream_incident_count', 0)}`",
        f"- Stop reason: `{summary.get('stop_reason', '')}`",
        "",
        "## Triage",
        "",
        f"- Next action: {summary.get('next_action', '')}",
    ]
    issues = summary.get("issues", [])
    warnings = summary.get("warnings", [])
    if issues:
        lines.extend(["", "## Issues", ""])
        for item in issues:
            lines.append(f"- `{item}`")
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for item in warnings:
            lines.append(f"- `{item}`")
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build runtime diagnostic artifacts for MACD long-run validation.")
    parser.add_argument("--context-path", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    context = _load_context(Path(args.context_path))
    summary = build_runtime_diagnostic_summary(context)
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(render_runtime_diagnostic_markdown(summary), encoding="utf-8")


if __name__ == "__main__":
    main()
