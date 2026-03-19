from pathlib import Path

import pandas as pd

from trader.cli import _build_strategy, _get_strategy_params
from trader.config import AppConfig
from trader.data.binance_live import LiveBar
from trader.notify import Notifier
from trader.risk.guards import RiskGuard
from trader.runtime import RuntimeConfig, RuntimeEngine
from trader.storage import SQLiteStorage
from trader.strategy.base import Strategy, StrategyPosition
from trader.strategy.macd_final_candidate import (
    FINAL_CANDIDATE_MACD_PARAMS,
    FINAL_CANDIDATE_PROFILE,
    FINAL_CANDIDATE_REGIME_NAME,
    FinalCandidateRegime,
    MACDFinalCandidateStrategy,
)
from trader.broker.paper import PaperBroker


class _HoldStrategy(Strategy):
    def on_bar(self, bar, position: StrategyPosition | None = None) -> str:
        return "hold"

    def get_state(self) -> dict[str, object]:
        return {
            "profile_name": FINAL_CANDIDATE_PROFILE,
            "regime_name": FINAL_CANDIDATE_REGIME_NAME,
            "fixed_params": dict(FINAL_CANDIDATE_MACD_PARAMS),
        }


class _DummyFeed:
    def __init__(self) -> None:
        self.event_callback = None

    def set_event_callback(self, callback) -> None:
        self.event_callback = callback

    def close(self) -> None:
        return None


def _live_bar(index: int, *, close: float | None = None, is_backfill: bool = False) -> LiveBar:
    bar_close = float(close if close is not None else 100.0 + index)
    return LiveBar(
        timestamp=pd.Timestamp("2026-03-01T00:00:00Z") + pd.Timedelta(hours=4 * index),
        open=bar_close - 0.5,
        high=bar_close + 1.0,
        low=bar_close - 1.0,
        close=bar_close,
        volume=1_000.0 + index,
        symbol="BTC/USDT",
        is_backfill=is_backfill,
    )


def _make_runtime_engine(
    *,
    db_path: Path,
    mode: str,
    validation_probe_enabled: bool = True,
    validation_probe_entry_after_bars: int = 3,
    validation_probe_exit_after_bars: int = 1,
    validation_allow_live_backfill_execution: bool = False,
) -> tuple[RuntimeEngine, SQLiteStorage, PaperBroker]:
    storage = SQLiteStorage(db_path)
    broker = PaperBroker(starting_cash=10_000.0)
    config = RuntimeConfig(
        mode=mode,
        symbol="BTC/USDT",
        timeframe="4h",
        fixed_notional_usdt=500.0,
        enable_protective_orders=True,
        protective_stop_loss_pct=0.01,
        protective_take_profit_pct=0.02,
        require_protective_orders=True,
        protective_missing_policy="halt",
        max_position_notional_usdt=5_000.0,
        min_entry_notional_usdt=250.0,
        strategy_name="macd_final_candidate",
        candidate_profile="macd_final_candidate",
        validation_probe_enabled=validation_probe_enabled,
        validation_probe_entry_after_bars=validation_probe_entry_after_bars,
        validation_probe_exit_after_bars=validation_probe_exit_after_bars,
        validation_allow_live_backfill_execution=validation_allow_live_backfill_execution,
        live_trading_enabled=(mode == "live"),
        binance_env="testnet",
    )
    engine = RuntimeEngine(
        config=config,
        strategy=_HoldStrategy(),
        broker=broker,
        feed=_DummyFeed(),
        storage=storage,
        risk_guard=RiskGuard(
            max_order_notional=5_000.0,
            max_position_notional=5_000.0,
            max_daily_loss=5_000.0,
            max_drawdown_pct=1.0,
            max_atr_pct=1.0,
        ),
        notifier=Notifier(),
        initial_equity=10_000.0,
    )
    return engine, storage, broker


def test_macd_final_candidate_preset_loads_operational_guards() -> None:
    cfg = AppConfig.from_env(preset="macd_final_candidate_ops", binance_env_override="testnet")

    assert str(cfg.preset_name).endswith("macd_final_candidate_ops.yaml")
    assert cfg.account_allocation_pct == 0.10
    assert cfg.max_position_notional_usdt == 1000.0
    assert cfg.min_entry_notional_usdt == 250.0
    assert cfg.require_protective_orders is True
    assert cfg.protective_missing_policy == "halt"
    assert cfg.sl_pct == 0.01
    assert cfg.tp_pct == 0.02


def test_macd_final_candidate_strategy_ignores_param_drift() -> None:
    cfg = AppConfig.from_env(preset="macd_final_candidate_ops", binance_env_override="testnet")

    strategy = _build_strategy(
        strategy_name="macd_final_candidate",
        params={
            "fast_period": 8,
            "slow_period": 21,
            "signal_period": 5,
            "allow_short": False,
        },
        cfg=cfg,
    )

    assert isinstance(strategy, MACDFinalCandidateStrategy)
    state = strategy.get_state()
    assert state["profile_name"] == FINAL_CANDIDATE_PROFILE
    assert state["regime_name"] == FINAL_CANDIDATE_REGIME_NAME
    assert state["fixed_params"] == FINAL_CANDIDATE_MACD_PARAMS

    params = _get_strategy_params("macd_final_candidate", cfg)
    assert params["fast_period"] == 12
    assert params["slow_period"] == 26
    assert params["signal_period"] == 9
    assert params["profile_name"] == FINAL_CANDIDATE_PROFILE
    assert params["regime_name"] == FINAL_CANDIDATE_REGIME_NAME
    assert params["regime_params"] == FinalCandidateRegime().__dict__


def test_macd_final_candidate_runner_scripts_reference_fixed_profile() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    validation_script = (repo_root / "scripts" / "run_macd_final_candidate_validation.ps1").read_text(encoding="utf-8")
    paper_script = (repo_root / "scripts" / "run_macd_final_candidate_paper.ps1").read_text(encoding="utf-8")
    testnet_script = (repo_root / "scripts" / "run_macd_final_candidate_testnet.ps1").read_text(encoding="utf-8")
    testnet_long_script = (repo_root / "scripts" / "run_macd_final_candidate_testnet_long.ps1").read_text(encoding="utf-8")

    assert "--strategy macd_final_candidate" in validation_script
    assert "--timeframe 4h" in validation_script
    assert "--preset $Preset" in validation_script
    assert "doctor_preflight.txt" in validation_script
    assert "status_final.txt" in validation_script
    assert "summary.json" in validation_script
    assert "out/operational_validation/macd_final_candidate_paper" in validation_script
    assert "out/operational_validation/macd_final_candidate_testnet" in validation_script
    assert "VALIDATION_PROBE_ENABLED" in validation_script
    assert "VALIDATION_ALLOW_LIVE_BACKFILL_EXECUTION" in validation_script
    assert "volatility_breaker_trigger_count" in validation_script
    assert "protective_orders_created_count" in validation_script
    assert "user_stream_no_running_event_loop" in validation_script
    assert "fill_provenance_breakdown" in validation_script
    assert "partial_fill_audit_summary" in validation_script
    assert "fills_from_aggregated_fallback_count" in validation_script
    assert "trade_query_unavailable_count" in validation_script
    assert "disconnected (no running event loop)" in validation_script
    assert "-Mode paper" in paper_script
    assert "-Mode live" in testnet_script
    assert "--strategy macd_final_candidate" in testnet_long_script
    assert "--timeframe 4h" in testnet_long_script
    assert "--preset $Preset" in testnet_long_script
    assert "out/operational_validation/macd_final_candidate_testnet_long" in testnet_long_script
    assert "doctor_preflight.txt" in testnet_long_script
    assert "status_final.txt" in testnet_long_script
    assert "summary.json" in testnet_long_script
    assert "reconciliation_audit.json" in testnet_long_script
    assert "fill_provenance_breakdown" in testnet_long_script
    assert "partial_fill_audit_summary" in testnet_long_script
    assert "protective_orders_canceled_count" in testnet_long_script
    assert "websocket_reconnect_count" in testnet_long_script
    assert "VALIDATION_PROBE_ENABLED" in testnet_long_script
    assert "VALIDATION_ALLOW_LIVE_BACKFILL_EXECUTION" in testnet_long_script
    assert "Get-FreshRuntimeActivity" in testnet_long_script
    assert "attempted_process_started" in testnet_long_script
    assert "fresh_run_id_detected" in testnet_long_script
    assert "startup_phase" in testnet_long_script
    assert "startup_failure_reason" in testnet_long_script
    assert "first_status_seen" in testnet_long_script
    assert "first_event_seen" in testnet_long_script
    assert "first_bar_seen" in testnet_long_script
    assert "first_order_seen" in testnet_long_script


def test_validation_probe_creates_and_closes_position_with_protective_orders(tmp_path: Path) -> None:
    engine, storage, broker = _make_runtime_engine(db_path=tmp_path / "paper_probe.db", mode="paper")

    engine.start_session()
    for idx in range(1, 7):
        assert engine.process_bar(_live_bar(idx)) is True
    result = engine.finish_session()

    status = storage.get_run_status(engine.run_id)
    events = storage.list_recent_events_for_run(engine.run_id, limit=100)
    event_types = [str(row["event_type"]) for row in events]

    assert result["halted"] is False
    assert "validation_probe_signal_override" in event_types
    assert "protective_orders_created" in event_types
    assert status["orders_count"] >= 4
    assert status["fills_count"] >= 2
    assert status["trades_count"] >= 1
    assert broker.get_open_orders("BTC/USDT") == {}

    runtime_state = storage.load_runtime_state(engine.run_id)
    assert runtime_state is not None
    assert float(runtime_state["open_positions"]["BTC/USDT"]["qty"]) == 0.0
    assert runtime_state["open_orders"]["BTC/USDT"] == {}


def test_runtime_start_session_persists_initial_state_for_fresh_run_detection(tmp_path: Path) -> None:
    engine, storage, _ = _make_runtime_engine(db_path=tmp_path / "startup_state.db", mode="live")

    engine.start_session()
    runtime_state = storage.load_runtime_state(engine.run_id)

    assert runtime_state is not None
    assert runtime_state["last_bar_ts"] is None
    risk_state = runtime_state["risk_state"]["BTC/USDT"]
    strategy_state = runtime_state["strategy_state"]["BTC/USDT"]
    assert risk_state["strategy"] == "macd_final_candidate"
    assert risk_state["candidate_profile"] == "macd_final_candidate"
    assert risk_state["processed_bars"] == 0
    assert strategy_state["profile_name"] == FINAL_CANDIDATE_PROFILE
    assert strategy_state["regime_name"] == FINAL_CANDIDATE_REGIME_NAME

    engine.finish_session()


def test_validation_probe_live_backfill_override_unblocks_execution(tmp_path: Path) -> None:
    suppressed_engine, suppressed_storage, _ = _make_runtime_engine(
        db_path=tmp_path / "live_suppressed.db",
        mode="live",
        validation_probe_entry_after_bars=1,
        validation_probe_exit_after_bars=1,
        validation_allow_live_backfill_execution=False,
    )

    suppressed_engine.start_session()
    assert suppressed_engine.process_bar(_live_bar(1, is_backfill=True)) is True
    suppressed_result = suppressed_engine.finish_session()
    suppressed_events = suppressed_storage.list_recent_events_for_run(suppressed_engine.run_id, limit=50)
    suppressed_event_types = [str(row["event_type"]) for row in suppressed_events]

    assert suppressed_result["halted"] is False
    assert "live_backfill_signal_suppressed" in suppressed_event_types
    assert suppressed_storage.get_run_status(suppressed_engine.run_id)["orders_count"] == 0

    enabled_engine, enabled_storage, enabled_broker = _make_runtime_engine(
        db_path=tmp_path / "live_enabled.db",
        mode="live",
        validation_probe_entry_after_bars=1,
        validation_probe_exit_after_bars=1,
        validation_allow_live_backfill_execution=True,
    )

    enabled_engine.start_session()
    for idx in range(1, 5):
        assert enabled_engine.process_bar(_live_bar(idx, is_backfill=True, close=100.0 + idx)) is True
    enabled_result = enabled_engine.finish_session()
    enabled_events = enabled_storage.list_recent_events_for_run(enabled_engine.run_id, limit=100)
    enabled_event_types = [str(row["event_type"]) for row in enabled_events]

    assert enabled_result["halted"] is False
    assert "validation_probe_signal_override" in enabled_event_types
    assert "protective_orders_created" in enabled_event_types
    assert "live_backfill_signal_suppressed" not in enabled_event_types
    assert enabled_storage.get_run_status(enabled_engine.run_id)["orders_count"] >= 4
    assert enabled_broker.get_open_orders("BTC/USDT") == {}
