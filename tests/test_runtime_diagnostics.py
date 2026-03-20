from __future__ import annotations

from pathlib import Path

from trader.runtime_diagnostics import build_runtime_diagnostic_summary, render_runtime_diagnostic_markdown
from trader.storage import SQLiteStorage


def _context(tmp_path: Path, *, run_id: str) -> dict[str, object]:
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    return {
        "db_path": str(tmp_path / "runtime.db"),
        "run_id": run_id,
        "previous_latest_run_id": "prev-run",
        "strategy": "macd_final_candidate",
        "candidate_profile": "macd_final_candidate",
        "mode": "live",
        "env": "testnet",
        "data_mode": "websocket",
        "timeframe": "4h",
        "symbols": "BTC/USDT",
        "preset": "macd_final_candidate_ops",
        "command": "uv run --active trader run --mode live",
        "start_utc": "2026-03-20T00:00:00+00:00",
        "end_utc": "2026-03-20T00:10:00+00:00",
        "startup_phase": "runtime_state_registered",
        "startup_failure_reason": "",
        "fresh_run_id_detected": True,
        "run_id_detection_source": "runtime_state",
        "first_status_seen": True,
        "first_event_seen": True,
        "first_event_type": "runtime_started",
        "first_event_ts": "2026-03-20T00:00:01+00:00",
        "exit_code": 0,
        "run_stdout": str(stdout_path),
        "run_stderr": str(stderr_path),
        "status_final": str(tmp_path / "status_final.txt"),
        "status_snapshots": str(tmp_path / "status_snapshots"),
        "doctor_preflight": str(tmp_path / "doctor.txt"),
    }


def _save_state(
    storage: SQLiteStorage,
    *,
    run_id: str,
    processed_bars: int,
    processed_live_bars: int,
    processed_backfill_bars: int,
    feed_event_count: int,
    feed_health: dict[str, object] | None = None,
) -> None:
    storage.save_runtime_state(
        run_id=run_id,
        last_bar_ts="2026-03-20 00:00:00+00:00" if processed_bars > 0 else None,
        open_positions={"symbol": "BTC/USDT", "qty": 0.0, "entry_price": 0.0, "entry_ts": "", "fee_pool": 0.0},
        open_orders={"symbol": "BTC/USDT"},
        strategy_state={"symbol": "BTC/USDT", "profile_name": "macd_final_candidate"},
        risk_state={
            "symbol": "BTC/USDT",
            "strategy": "macd_final_candidate",
            "candidate_profile": "macd_final_candidate",
            "processed_bars": processed_bars,
            "processed_live_bars": processed_live_bars,
            "processed_backfill_bars": processed_backfill_bars,
            "feed_event_count": feed_event_count,
            "feed_health": feed_health or {},
            "halted": False,
            "halt_reason": "",
        },
        updated_at="2026-03-20T00:10:00+00:00",
    )


def test_startup_no_bar_detection_classifies_missing_feed_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    storage = SQLiteStorage(db_path)
    try:
        run_id = "run-no-feed"
        storage.write_event("2026-03-20T00:00:01+00:00", "runtime_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:02+00:00",
            "feed_worker_thread_created",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:10:00+00:00",
            "zero_bar_session_detected",
            {"run_id": run_id, "symbol": "BTC/USDT", "session_elapsed_sec": 600.0},
        )
        storage.write_event(
            "2026-03-20T00:10:01+00:00",
            "runtime_stopped",
            {"run_id": run_id, "symbol": "BTC/USDT", "processed_bars": 0},
        )
        _save_state(
            storage,
            run_id=run_id,
            processed_bars=0,
            processed_live_bars=0,
            processed_backfill_bars=0,
            feed_event_count=0,
        )

        summary = build_runtime_diagnostic_summary(_context(tmp_path, run_id=run_id))

        assert summary["verdict"] == "FAIL"
        assert summary["diagnostic_verdict"] == "feed_worker_thread_not_started"
        assert summary["feed"]["stage_progression"]["first_missing_stage"] == "feed_worker_thread_started"
        assert summary["feed"]["processed_bars_total"] == 0
        assert summary["startup"]["runtime_stopped_status"] == "normal"
    finally:
        storage.close()


def test_feed_arrives_but_bar_dispatch_never_happens_is_classified(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    storage = SQLiteStorage(db_path)
    try:
        run_id = "run-closed-kline-no-dispatch"
        storage.write_event("2026-03-20T00:00:01+00:00", "runtime_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:02+00:00",
            "feed_worker_thread_created",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_thread_started",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_entered",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "feed_worker_entered_iter_closed_bars",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "binance_live_feed_initialized",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "websocket_worker_start_called",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event("2026-03-20T00:00:05+00:00", "ws_worker_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event("2026-03-20T00:00:05+00:00", "ws_worker_connected", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:06+00:00",
            "first_market_payload_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:06+00:00",
            "first_kline_payload_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:04:00+00:00",
            "first_closed_kline_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:10:00+00:00",
            "zero_bar_session_detected",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:10:01+00:00",
            "runtime_stopped",
            {"run_id": run_id, "symbol": "BTC/USDT", "processed_bars": 0},
        )
        _save_state(
            storage,
            run_id=run_id,
            processed_bars=0,
            processed_live_bars=0,
            processed_backfill_bars=0,
            feed_event_count=4,
            feed_health={"ws_payload_count": 1, "ws_kline_payload_count": 1, "ws_closed_kline_count": 1},
        )

        summary = build_runtime_diagnostic_summary(_context(tmp_path, run_id=run_id))

        assert summary["diagnostic_verdict"] == "zero_bar_closed_kline_no_dispatch"
        assert summary["feed"]["first_closed_kline_time"] == "2026-03-20T00:04:00+00:00"
        assert summary["feed"]["first_bar_processed_time"] is None
        assert summary["feed"]["stage_progression"]["last_confirmed_healthy_stage"] == "first_closed_kline_received"
    finally:
        storage.close()


def test_reconnect_followed_by_resumed_ingestion_closes_runtime_gap(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    storage = SQLiteStorage(db_path)
    try:
        run_id = "run-reconnect-then-bar"
        storage.write_event("2026-03-20T00:00:01+00:00", "runtime_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:02+00:00",
            "feed_worker_thread_created",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_thread_started",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_entered",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "feed_worker_entered_iter_closed_bars",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "binance_live_feed_initialized",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "websocket_worker_start_called",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event("2026-03-20T00:00:05+00:00", "ws_worker_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event("2026-03-20T00:00:05+00:00", "ws_worker_connected", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:10+00:00",
            "ws_worker_reconnect",
            {"run_id": run_id, "symbol": "BTC/USDT", "attempt": 1},
        )
        storage.write_event(
            "2026-03-20T00:00:20+00:00",
            "first_market_payload_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:20+00:00",
            "first_kline_payload_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:04:00+00:00",
            "first_closed_kline_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:04:01+00:00",
            "first_bar_dispatched",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:04:01+00:00",
            "first_bar_processed",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:04:01+00:00",
            "first_live_bar_processed",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:10:01+00:00",
            "runtime_stopped",
            {"run_id": run_id, "symbol": "BTC/USDT", "processed_bars": 1},
        )
        _save_state(
            storage,
            run_id=run_id,
            processed_bars=1,
            processed_live_bars=1,
            processed_backfill_bars=0,
            feed_event_count=6,
            feed_health={"ws_payload_count": 1, "ws_kline_payload_count": 1, "ws_closed_kline_count": 1},
        )

        summary = build_runtime_diagnostic_summary(_context(tmp_path, run_id=run_id))
        markdown = render_runtime_diagnostic_markdown(summary)

        assert summary["verdict"] == "PASS"
        assert summary["evidence_scope"] == "real_strategy_runtime"
        assert summary["strategy_lifecycle_validation_applicable"] is True
        assert summary["long_run_gap_closed"] is True
        assert summary["runtime_validation_confidence_advanced"] is True
        assert summary["feed"]["reconnect_count"] == 1
        assert summary["feed"]["stage_progression"]["first_missing_stage"] == "none"
        assert "Long-run gap closed: `true`" in markdown
    finally:
        storage.close()


def test_backfill_only_bars_generate_warning_verdict(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    storage = SQLiteStorage(db_path)
    try:
        run_id = "run-backfill-only"
        storage.write_event("2026-03-20T00:00:01+00:00", "runtime_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:02+00:00",
            "feed_worker_thread_created",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_thread_started",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "first_bar_dispatched",
            {"run_id": run_id, "symbol": "BTC/USDT", "is_backfill": True},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "first_bar_processed",
            {"run_id": run_id, "symbol": "BTC/USDT", "is_backfill": True},
        )
        storage.write_event(
            "2026-03-20T00:10:01+00:00",
            "runtime_stopped",
            {"run_id": run_id, "symbol": "BTC/USDT", "processed_bars": 1},
        )
        _save_state(
            storage,
            run_id=run_id,
            processed_bars=1,
            processed_live_bars=0,
            processed_backfill_bars=1,
            feed_event_count=0,
            feed_health={},
        )

        summary = build_runtime_diagnostic_summary(_context(tmp_path, run_id=run_id))

        assert summary["verdict"] == "WARNING"
        assert summary["diagnostic_verdict"] == "backfill_only_bars"
        assert summary["long_run_gap_closed"] is False
    finally:
        storage.close()


def test_short_run_with_kline_but_no_close_is_warning_not_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    storage = SQLiteStorage(db_path)
    try:
        run_id = "run-short-no-close"
        storage.write_event("2026-03-20T00:00:01+00:00", "runtime_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:02+00:00",
            "feed_worker_thread_created",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_thread_started",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_entered",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "feed_worker_entered_iter_closed_bars",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "binance_live_feed_initialized",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "websocket_worker_start_called",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event("2026-03-20T00:00:05+00:00", "ws_worker_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event("2026-03-20T00:00:05+00:00", "ws_worker_connected", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:10+00:00",
            "first_market_payload_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:10+00:00",
            "first_kline_payload_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:10:01+00:00",
            "runtime_stopped",
            {"run_id": run_id, "symbol": "BTC/USDT", "processed_bars": 0},
        )
        _save_state(
            storage,
            run_id=run_id,
            processed_bars=0,
            processed_live_bars=0,
            processed_backfill_bars=0,
            feed_event_count=2,
            feed_health={"ws_payload_count": 1, "ws_kline_payload_count": 1, "ws_closed_kline_count": 0},
        )
        context = _context(tmp_path, run_id=run_id)
        context["end_utc"] = "2026-03-20T00:10:00+00:00"

        summary = build_runtime_diagnostic_summary(context)

        assert summary["verdict"] == "WARNING"
        assert summary["diagnostic_verdict"] == "insufficient_elapsed_time_for_closed_bar"
        assert summary["long_run_gap_closed"] is False
    finally:
        storage.close()


def test_early_stop_before_feed_entry_is_diagnosable(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    storage = SQLiteStorage(db_path)
    try:
        run_id = "run-early-stop"
        storage.write_event("2026-03-20T00:00:01+00:00", "runtime_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:02+00:00",
            "feed_worker_thread_created",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_thread_started",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_stop_requested_before_start",
            {"run_id": run_id, "symbol": "BTC/USDT", "global_stop": True},
        )
        _save_state(
            storage,
            run_id=run_id,
            processed_bars=0,
            processed_live_bars=0,
            processed_backfill_bars=0,
            feed_event_count=3,
            feed_health={},
        )

        summary = build_runtime_diagnostic_summary(_context(tmp_path, run_id=run_id))

        assert summary["diagnostic_verdict"] == "feed_worker_started_no_feed_entry"
        assert summary["feed"]["stage_progression"]["first_missing_stage"] == "feed_worker_entered"
    finally:
        storage.close()


def test_missing_runtime_stopped_is_classified_explicitly(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    storage = SQLiteStorage(db_path)
    try:
        run_id = "run-missing-stopped"
        storage.write_event("2026-03-20T00:00:01+00:00", "runtime_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:02+00:00",
            "feed_worker_thread_created",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        _save_state(
            storage,
            run_id=run_id,
            processed_bars=0,
            processed_live_bars=0,
            processed_backfill_bars=0,
            feed_event_count=0,
            feed_health={},
        )

        summary = build_runtime_diagnostic_summary(_context(tmp_path, run_id=run_id))

        assert summary["startup"]["runtime_stopped_status"] == "missing"
        assert summary["startup"]["stop_classification"] == "missing_stop_record"
        assert summary["long_run_gap_closed"] is False
        assert "runtime_stopped_event_missing" in summary["warnings"]
    finally:
        storage.close()


def test_pipeline_proof_live_bar_passes_without_claiming_strategy_evidence(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    storage = SQLiteStorage(db_path)
    try:
        run_id = "run-pipeline-proof"
        storage.write_event("2026-03-20T00:00:01+00:00", "runtime_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:02+00:00",
            "feed_worker_thread_created",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_thread_started",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_entered",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "feed_worker_entered_iter_closed_bars",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "binance_live_feed_initialized",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "websocket_worker_start_called",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event("2026-03-20T00:00:05+00:00", "ws_worker_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event("2026-03-20T00:00:05+00:00", "ws_worker_connected", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:10+00:00",
            "first_market_payload_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:10+00:00",
            "first_kline_payload_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:01:00+00:00",
            "first_closed_kline_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:01:01+00:00",
            "first_bar_dispatched",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:01:01+00:00",
            "first_bar_processed",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:01:01+00:00",
            "first_live_bar_processed",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:01:02+00:00",
            "runtime_stopped",
            {"run_id": run_id, "symbol": "BTC/USDT", "processed_bars": 1},
        )
        _save_state(
            storage,
            run_id=run_id,
            processed_bars=1,
            processed_live_bars=1,
            processed_backfill_bars=0,
            feed_event_count=7,
            feed_health={"ws_payload_count": 1, "ws_kline_payload_count": 1, "ws_closed_kline_count": 1},
        )
        context = _context(tmp_path, run_id=run_id)
        context["timeframe"] = "1m"
        context["end_utc"] = "2026-03-20T00:01:02+00:00"
        context["validation_mode"] = "pipeline_proof"
        context["pipeline_proof_mode"] = True
        context["strategy_evidence_allowed"] = False
        context["realtime_only"] = True
        context["stop_policy"] = "graceful_after_first_live_bar"
        context["expected_next_close_utc"] = "2026-03-20T00:01:00+00:00"
        context["minutes_until_next_close_at_start"] = 1.0
        context["seconds_until_next_close_at_start"] = 60.0

        summary = build_runtime_diagnostic_summary(context)
        markdown = render_runtime_diagnostic_markdown(summary)

        assert summary["verdict"] == "PASS"
        assert summary["evidence_scope"] == "runtime_pipeline_only"
        assert summary["pipeline_proof_mode"] is True
        assert summary["strategy_evidence_allowed"] is False
        assert summary["strategy_lifecycle_validation_applicable"] is False
        assert summary["feed_runtime_chain_proven"] is True
        assert summary["runtime_validation_confidence_advanced"] is True
        assert summary["long_run_gap_closed"] is False
        assert summary["startup"]["stop_classification"] == "normal_graceful_stop"
        assert summary["expected_next_close_utc"] == "2026-03-20T00:01:00+00:00"
        assert summary["feed"]["stage_progression"]["last_confirmed_healthy_stage"] == "runtime_stopped"
        assert summary["feed"]["stage_progression"]["first_missing_stage"] == "none"
        assert "Validation mode: `pipeline_proof`" in markdown
        assert "Evidence scope: `runtime_pipeline_only`" in markdown
        assert "Strategy evidence allowed: `false`" in markdown
    finally:
        storage.close()


def test_live_bars_without_runtime_stopped_do_not_close_real_long_run_gap(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    storage = SQLiteStorage(db_path)
    try:
        run_id = "run-live-no-stop"
        storage.write_event("2026-03-20T00:00:01+00:00", "runtime_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:02+00:00",
            "feed_worker_thread_created",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_thread_started",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:03+00:00",
            "feed_worker_entered",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "feed_worker_entered_iter_closed_bars",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "binance_live_feed_initialized",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:04+00:00",
            "websocket_worker_start_called",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event("2026-03-20T00:00:05+00:00", "ws_worker_started", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event("2026-03-20T00:00:05+00:00", "ws_worker_connected", {"run_id": run_id, "symbol": "BTC/USDT"})
        storage.write_event(
            "2026-03-20T00:00:10+00:00",
            "first_market_payload_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T00:00:10+00:00",
            "first_kline_payload_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T04:00:00+00:00",
            "first_closed_kline_received",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T04:00:01+00:00",
            "first_bar_dispatched",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T04:00:01+00:00",
            "first_bar_processed",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        storage.write_event(
            "2026-03-20T04:00:01+00:00",
            "first_live_bar_processed",
            {"run_id": run_id, "symbol": "BTC/USDT"},
        )
        _save_state(
            storage,
            run_id=run_id,
            processed_bars=1,
            processed_live_bars=1,
            processed_backfill_bars=0,
            feed_event_count=7,
            feed_health={"ws_payload_count": 1, "ws_kline_payload_count": 1, "ws_closed_kline_count": 1},
        )

        summary = build_runtime_diagnostic_summary(_context(tmp_path, run_id=run_id))

        assert summary["feed_runtime_chain_proven"] is True
        assert summary["runtime_validation_confidence_advanced"] is False
        assert summary["startup"]["stop_classification"] == "missing_stop_record"
        assert summary["long_run_gap_closed"] is False
    finally:
        storage.close()
