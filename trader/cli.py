from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from trader.backtest.engine import BacktestConfig, BacktestEngine
from trader.backtest.metrics import summarize_performance
from trader.backtest.report import print_backtest_report
from trader.broker.base import OrderRequest
from trader.broker.live_binance import LiveBinanceBroker
from trader.broker.paper import PaperBroker
from trader.config import AppConfig
from trader.data.binance import BinanceDataClient
from trader.data.binance_live import BinanceLiveFeed
from trader.logger_utils import setup_logging
from trader.notify import Notifier
from trader.experiments.runner import (
    _parse_duration_list,
    _parse_float_list,
    _parse_int_list,
    default_system_candidates,
    run_edge_validation,
    run_portfolio_validation,
    run_system_batch,
)
from trader.optimize import (
    Optimizer,
    export_results,
    generate_parameter_grid,
    load_grid_yaml,
    load_result_file,
    run_candidate_backtest,
    select_parameter_sets,
)
from trader.storage import SQLiteStorage
from trader.runtime import AccountBudgetGuard, RuntimeConfig, RuntimeEngine, RuntimeOrchestrator
from trader.risk.guards import RiskGuard
from trader.strategy.base import Bar, Strategy
from trader.strategy.bollinger import BollingerBandStrategy
from trader.strategy.ema_cross import EMACrossStrategy
from trader.strategy.macd import MACDStrategy
from trader.strategy.macd_final_candidate import (
    FINAL_CANDIDATE_MACD_PARAMS,
    FINAL_CANDIDATE_PROFILE,
    FINAL_CANDIDATE_REGIME_NAME,
    FinalCandidateRegime,
    MACDFinalCandidateStrategy,
)
from trader.strategy.rsi import RSIStrategy

AVAILABLE_STRATEGIES = ["ema_cross", "rsi", "macd", "macd_final_candidate", "bollinger"]

app = typer.Typer(help="Binance trader CLI (backtest / optimize / replay / run / paper / live)")
console = Console()


def _parse_symbols(symbols: str) -> list[str]:
    parsed = [x.strip() for x in symbols.split(",") if x.strip()]
    if not parsed:
        raise typer.BadParameter("At least one symbol is required")
    return parsed


def _parse_optional_float(raw: str) -> float | None:
    text = str(raw).strip().lower()
    if text in {"", "none", "off", "null"}:
        return None
    return float(text)


def _build_base_backtest_config(cfg: AppConfig) -> BacktestConfig:
    return BacktestConfig(
        symbol=cfg.symbol,
        timeframe=cfg.timeframe,
        initial_equity=cfg.initial_equity,
        leverage=cfg.leverage,
        order_type=cfg.order_type,
        execution_price_source=cfg.execution_price_source,
        slippage_bps=cfg.slippage_bps,
        maker_fee_bps=cfg.maker_fee_bps,
        taker_fee_bps=cfg.taker_fee_bps,
        sizing_mode=cfg.sizing_mode,
        fixed_notional_usdt=cfg.fixed_notional_usdt,
        equity_pct=cfg.equity_pct,
        atr_period=cfg.atr_period,
        atr_risk_pct=cfg.atr_risk_pct,
        atr_stop_multiple=cfg.atr_stop_multiple,
        enable_funding=cfg.enable_funding,
        db_path=cfg.db_path,
    )


def _is_testnet(cfg: AppConfig) -> bool:
    return cfg.binance_env == "testnet"


def _timeframe_seconds(timeframe: str) -> float:
    if timeframe.endswith("m"):
        return float(int(timeframe[:-1]) * 60)
    if timeframe.endswith("h"):
        return float(int(timeframe[:-1]) * 3600)
    if timeframe.endswith("d"):
        return float(int(timeframe[:-1]) * 86400)
    return 60.0


def _pct_text(value: float) -> str:
    return f"{float(value) * 100:.2f}%"


def _print_runtime_banner(*, cfg: AppConfig, runtime_cfg: RuntimeConfig) -> None:
    table = Table(title="Runtime Profile")
    table.add_column("key")
    table.add_column("value")
    table.add_row("mode", str(runtime_cfg.mode))
    table.add_row("BINANCE_ENV", str(runtime_cfg.binance_env))
    table.add_row("LIVE_TRADING", str(runtime_cfg.live_trading_enabled))
    table.add_row("budget_guard", str(runtime_cfg.budget_guard_enabled))
    table.add_row("budget_usdt_mode", str(runtime_cfg.budget_usdt_mode))
    table.add_row("budget_usdt_fixed", str(runtime_cfg.budget_usdt_fixed if runtime_cfg.budget_usdt_fixed is not None else "-"))
    table.add_row("dry_run", str(runtime_cfg.dry_run))
    table.add_row("preset", str(runtime_cfg.preset_name or "-"))
    table.add_row("sleep_mode", str(runtime_cfg.sleep_mode_enabled))
    table.add_row("allocation_pct", _pct_text(runtime_cfg.account_allocation_pct))
    table.add_row("leverage", str(cfg.leverage))
    table.add_row("fixed_notional", f"{runtime_cfg.fixed_notional_usdt:.2f} USDT")
    table.add_row("daily_loss_limit", _pct_text(runtime_cfg.daily_loss_limit_pct))
    table.add_row("max_dd", _pct_text(cfg.max_drawdown_pct))
    table.add_row("risk_per_trade", _pct_text(runtime_cfg.risk_per_trade_pct))
    table.add_row("max_position_notional", f"{runtime_cfg.max_position_notional_usdt:.2f} USDT")
    table.add_row("min_entry_notional", f"{runtime_cfg.min_entry_notional_usdt:.2f} USDT")
    table.add_row("protective_mode", str(runtime_cfg.protective_missing_policy))
    table.add_row(
        "sl/tp",
        (
            f"SL({runtime_cfg.sl_mode})={runtime_cfg.protective_stop_loss_pct:.4f} "
            f"TP({runtime_cfg.tp_mode})={runtime_cfg.protective_take_profit_pct:.4f}"
        ),
    )
    console.print(table)


def _validate_live_entry_sizing(runtime_cfg: RuntimeConfig) -> None:
    if runtime_cfg.mode != "live":
        return
    min_entry_notional = max(float(runtime_cfg.min_entry_notional_usdt), 0.0)
    if min_entry_notional <= 0:
        return
    fixed_notional = max(float(runtime_cfg.fixed_notional_usdt), 0.0)
    if fixed_notional + 1e-9 >= min_entry_notional:
        return
    console.print(
        (
            "[yellow]Live entry sizing warning:[/yellow] "
            f"fixed_notional_usdt={fixed_notional:.2f} < min_entry_notional_usdt={min_entry_notional:.2f}. "
            "Runtime startup will continue, and entries below the floor will be skipped at order time "
            "with entry_notional_below_floor diagnostics."
        )
    )


def _sleep_mode_warnings(cfg: AppConfig) -> list[str]:
    warnings: list[str] = []
    if cfg.leverage > 2:
        warnings.append(f"leverage {cfg.leverage} > 2")
    if cfg.daily_loss_limit_pct > 0.02:
        warnings.append(f"daily_loss_limit_pct {_pct_text(cfg.daily_loss_limit_pct)} > 2%")
    if cfg.account_allocation_pct > 0.30:
        warnings.append(f"allocation {_pct_text(cfg.account_allocation_pct)} > 30%")
    if cfg.live_trading and cfg.binance_env == "mainnet":
        warnings.append("LIVE_TRADING=true on mainnet")
    return warnings


def _print_optimize_top(df: pd.DataFrame, title: str, metric: str, top: int) -> None:
    top_df = df.head(top)
    table = Table(title=title)
    table.add_column("rank")
    table.add_column("run_id")
    table.add_column("symbol")
    table.add_column(metric)
    table.add_column("objective")
    table.add_column("params")
    for _, row in top_df.iterrows():
        params = row.get("params_json", "-")
        metric_val = row.get(metric, row.get("metric_value", 0.0))
        table.add_row(
            str(row.get("rank", "-")),
            str(row.get("candidate_run_id", "-")),
            str(row.get("symbol", "-")),
            f"{float(metric_val):.6f}" if pd.notna(metric_val) else "-",
            f"{float(row.get('objective', 0.0)):.6f}" if pd.notna(row.get("objective")) else "-",
            str(params)[:120],
        )
    console.print(table)


def _parse_params_from_row(row: pd.Series) -> dict[str, Any]:
    raw = row.get("params_json")
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            out: dict[str, Any] = {}
            for token in raw.split(";"):
                if ":" in token:
                    k, v = token.split(":", 1)
                    out[k.strip()] = v.strip()
            return out
    params: dict[str, Any] = {}
    for col, val in row.items():
        if col.startswith("param_"):
            params[col.removeprefix("param_")] = val
    return params


def _coerce_param_types(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, str):
            text = v.strip()
            if text.lower() in {"true", "false"}:
                out[k] = text.lower() == "true"
                continue
            try:
                if "." in text:
                    out[k] = float(text)
                else:
                    out[k] = int(text)
                continue
            except ValueError:
                out[k] = text
        else:
            out[k] = v
    return out


def _load_strategy_params_from_source(
    *,
    params_from: str | None,
    params_rank: int,
    storage: SQLiteStorage,
) -> tuple[dict[str, Any], str | None]:
    if not params_from:
        return {}, None

    p = Path(params_from)
    if p.exists():
        df = load_result_file(p)
        if df.empty:
            raise typer.BadParameter(f"No rows in params file: {params_from}")
        row: pd.Series
        if "rank" in df.columns:
            ranked = df[df["rank"] == params_rank]
            row = ranked.iloc[0] if not ranked.empty else df.sort_values("rank").iloc[0]
        else:
            row = df.iloc[max(0, params_rank - 1)] if len(df) >= params_rank else df.iloc[0]
        strategy_name = str(row.get("strategy", "ema_cross"))
        return _coerce_param_types(_parse_params_from_row(row)), strategy_name

    row = storage.get_optimize_result_by_candidate_run_id(params_from)
    if row is not None:
        return _coerce_param_types(json.loads(row["params_json"])), row.get("strategy")

    run_cfg = storage.get_backtest_run_config(params_from)
    if run_cfg is not None:
        params = run_cfg.get("strategy_params") or {}
        strategy_name = run_cfg.get("strategy_name")
        if isinstance(params, dict):
            return _coerce_param_types(params), strategy_name

    raise typer.BadParameter(f"Unable to resolve params-from source: {params_from}")


def _build_strategy(
    *,
    strategy_name: str,
    params: dict[str, Any],
    cfg: AppConfig,
) -> Strategy:
    """
    Build a strategy instance based on name and parameters.

    Supported strategies: ema_cross, rsi, macd, bollinger
    """
    stop_loss_pct = float(params.get("stop_loss_pct", cfg.ema_stop_loss_pct))
    take_profit_pct = float(params.get("take_profit_pct", cfg.ema_take_profit_pct))
    allow_short = bool(params.get("allow_short", True))

    if strategy_name == "ema_cross":
        fast_len = int(params.get("fast_len", params.get("short_window", cfg.short_window)))
        slow_len = int(params.get("slow_len", params.get("long_window", cfg.long_window)))
        return EMACrossStrategy(
            short_window=fast_len,
            long_window=slow_len,
            allow_short=allow_short,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )

    if strategy_name == "rsi":
        period = int(params.get("period", 14))
        overbought = float(params.get("overbought", 70.0))
        oversold = float(params.get("oversold", 30.0))
        return RSIStrategy(
            period=period,
            overbought=overbought,
            oversold=oversold,
            allow_short=allow_short,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )

    if strategy_name == "macd":
        fast_period = int(params.get("fast_period", 12))
        slow_period = int(params.get("slow_period", 26))
        signal_period = int(params.get("signal_period", 9))
        use_histogram = bool(params.get("use_histogram", False))
        histogram_threshold = float(params.get("histogram_threshold", 0.0))
        return MACDStrategy(
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
            allow_short=allow_short,
            use_histogram=use_histogram,
            histogram_threshold=histogram_threshold,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )

    if strategy_name == "macd_final_candidate":
        return MACDFinalCandidateStrategy()

    if strategy_name == "bollinger":
        period = int(params.get("period", 20))
        std_dev = float(params.get("std_dev", 2.0))
        mode = str(params.get("mode", "mean_reversion"))
        if mode not in ("mean_reversion", "breakout"):
            mode = "mean_reversion"
        return BollingerBandStrategy(
            period=period,
            std_dev=std_dev,
            mode=mode,  # type: ignore[arg-type]
            allow_short=allow_short,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )

    raise typer.BadParameter(f"Unsupported strategy: {strategy_name}. Available: {', '.join(AVAILABLE_STRATEGIES)}")


def _get_strategy_params(strategy_name: str, cfg: AppConfig) -> dict[str, Any]:
    """Get default parameters for a strategy based on config."""
    if strategy_name == "ema_cross":
        return {
            "fast_len": cfg.short_window,
            "slow_len": cfg.long_window,
            "stop_loss_pct": cfg.ema_stop_loss_pct,
            "take_profit_pct": cfg.ema_take_profit_pct,
        }
    if strategy_name == "rsi":
        return {
            "period": 14,
            "overbought": 70.0,
            "oversold": 30.0,
            "stop_loss_pct": cfg.ema_stop_loss_pct,
            "take_profit_pct": cfg.ema_take_profit_pct,
        }
    if strategy_name == "macd":
        return {
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "use_histogram": False,
            "stop_loss_pct": cfg.ema_stop_loss_pct,
            "take_profit_pct": cfg.ema_take_profit_pct,
        }
    if strategy_name == "macd_final_candidate":
        regime = FinalCandidateRegime()
        return {
            **dict(FINAL_CANDIDATE_MACD_PARAMS),
            "profile_name": FINAL_CANDIDATE_PROFILE,
            "regime_name": FINAL_CANDIDATE_REGIME_NAME,
            "regime_params": {
                "adx_window": regime.adx_window,
                "low_adx_threshold": regime.low_adx_threshold,
                "high_adx_threshold": regime.high_adx_threshold,
                "vol_window": regime.vol_window,
                "vol_percentile_window": regime.vol_percentile_window,
                "low_vol_quantile": regime.low_vol_quantile,
                "high_vol_quantile": regime.high_vol_quantile,
                "trend_ema_span": regime.trend_ema_span,
                "trend_slope_lookback": regime.trend_slope_lookback,
                "trend_slope_threshold": regime.trend_slope_threshold,
                "trend_distance_threshold": regime.trend_distance_threshold,
                "min_coverage_ratio": regime.min_coverage_ratio,
            },
        }
    if strategy_name == "bollinger":
        return {
            "period": 20,
            "std_dev": 2.0,
            "mode": "mean_reversion",
            "stop_loss_pct": cfg.ema_stop_loss_pct,
            "take_profit_pct": cfg.ema_take_profit_pct,
        }
    return {}


@app.command()
def backtest(
    symbol: str = typer.Option("BTC/USDT", help="Market symbol, e.g. BTC/USDT"),
    timeframe: str = typer.Option("1h", help="Candle timeframe"),
    limit: int = typer.Option(300, min=100, help="Number of candles to fetch"),
    strategy: str = typer.Option("ema_cross", help="Strategy: ema_cross, rsi, macd, bollinger"),
) -> None:
    setup_logging()
    if strategy not in AVAILABLE_STRATEGIES:
        raise typer.BadParameter(f"Unknown strategy: {strategy}. Available: {', '.join(AVAILABLE_STRATEGIES)}")
    cfg = AppConfig.from_env().model_copy(update={"symbol": symbol, "timeframe": timeframe})
    strategy_params = _get_strategy_params(strategy, cfg)
    strategy_obj = _build_strategy(strategy_name=strategy, params=strategy_params, cfg=cfg)
    engine = BacktestEngine()
    backtest_config = replace(
        _build_base_backtest_config(cfg),
        strategy_name=strategy,
        strategy_params=strategy_params,
    )
    client = BinanceDataClient(testnet=_is_testnet(cfg))
    try:
        candles = client.fetch_ohlcv(symbol=cfg.symbol, timeframe=cfg.timeframe, limit=limit)
    finally:
        client.close()
    result = engine.run(candles=candles, strategy=strategy_obj, config=backtest_config)
    metrics = result.summary or summarize_performance(result.equity_curve, result.trades, cfg.initial_equity)
    print_backtest_report(result, metrics)


@app.command()
def optimize(
    strategy: str = typer.Option("ema_cross", help="Strategy name"),
    symbols: str = typer.Option(..., help="Comma-separated symbols, e.g. BTC/USDT,ETH/USDT"),
    timeframe: str = typer.Option("1h", help="Candle timeframe"),
    start: str = typer.Option(..., help="Backtest start, e.g. 2023-01-01"),
    end: str = typer.Option(..., help="Backtest end, e.g. 2025-01-01"),
    search: str = typer.Option("grid", help="Search type: grid | random"),
    grid: str = typer.Option(..., help="YAML grid file path"),
    metric: str = typer.Option("sharpe_like", help="Primary metric"),
    top: int = typer.Option(20, min=1, help="Top N to display"),
    export: str | None = typer.Option(None, help="Export path (.csv or .parquet)"),
    jobs: int = typer.Option(1, min=1, help="Parallel workers"),
    random_samples: int = typer.Option(100, min=1, help="Sample size for random search"),
    random_seed: int = typer.Option(42, help="Seed for random search"),
    walk_forward: bool = typer.Option(False, "--walk-forward", help="Enable rolling walk-forward"),
    train_days: int = typer.Option(180, min=1, help="Walk-forward train window days"),
    test_days: int = typer.Option(60, min=1, help="Walk-forward test window days"),
    top_per_train: int = typer.Option(10, min=1, help="Top K train params to evaluate on test"),
    constraints: str | None = typer.Option(None, help="Constraints, e.g. max_drawdown<=0.15,trades>=50"),
    score: str | None = typer.Option(None, help="Score expression, e.g. 0.6*win_rate + 0.4*profit_factor"),
) -> None:
    setup_logging()
    cfg = AppConfig.from_env().model_copy(update={"timeframe": timeframe})
    parsed_symbols = _parse_symbols(symbols)
    grid_map = load_grid_yaml(grid)
    all_params = generate_parameter_grid(grid_map)
    parameter_sets = select_parameter_sets(
        search_mode=search,
        all_params=all_params,
        random_samples=random_samples,
        random_seed=random_seed,
    )

    client = BinanceDataClient(testnet=_is_testnet(cfg))
    candles_by_symbol: dict[str, pd.DataFrame] = {}
    try:
        for sym in parsed_symbols:
            candles_by_symbol[sym] = client.fetch_ohlcv_range(
                symbol=sym, timeframe=timeframe, start=start, end=end
            )
    finally:
        client.close()

    base_bt_cfg = _build_base_backtest_config(cfg)
    output = Optimizer().run(
        strategy_name=strategy,
        symbols=parsed_symbols,
        timeframe=timeframe,
        candles_by_symbol=candles_by_symbol,
        parameter_sets=parameter_sets,
        metric=metric,
        top_n=top,
        base_backtest_config=base_bt_cfg,
        search_mode=search,
        jobs=jobs,
        constraints=constraints,
        score_expr=score,
        walk_forward=walk_forward,
        start=start,
        end=end,
        train_days=train_days,
        test_days=test_days,
        top_per_train=top_per_train,
    )

    console.print(f"optimize_run_id: {output.optimize_run_id}")
    if walk_forward:
        _print_optimize_top(output.train_top_results, "Walk-forward Train Top", metric, top)
        _print_optimize_top(output.test_top_results, "Walk-forward Test Top", metric, top)
    else:
        _print_optimize_top(output.top_results, "Optimize Top Results", metric, top)

    if export:
        export_results(output.results, export)
        console.print(f"Exported optimization results: {export}")


@app.command("experiments")
def experiments(
    suite: str = typer.Option("all", help="all | cost | walk | regime | portfolio"),
    symbol: str = typer.Option("BTC/USDT", help="Market symbol"),
    symbols: str = typer.Option(
        "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,ADA/USDT,DOGE/USDT,AVAX/USDT,LINK/USDT,TRX/USDT",
        help="Comma-separated symbols for portfolio suite",
    ),
    timeframe: str = typer.Option("15m", help="Candle timeframe"),
    start: str = typer.Option(..., help="Start timestamp/date (UTC), e.g. 2023-01-01"),
    end: str = typer.Option(..., help="End timestamp/date (UTC), e.g. 2025-01-01"),
    strategy: str = typer.Option("ema_cross", help="Strategy: ema_cross, rsi, macd, bollinger"),
    params_from: str | None = typer.Option(None, help="Optional params source (csv/parquet/run_id)"),
    params_rank: int = typer.Option(1, min=1, help="Rank to select from params source"),
    seed: int = typer.Option(42, help="Global random seed"),
    data_source: str = typer.Option("binance", help="binance | csv | synthetic"),
    csv_path: str | None = typer.Option(None, help="OHLCV CSV path for --data-source csv"),
    output_dir: str = typer.Option("out/experiments", help="Output root directory"),
    walk_grid: str = typer.Option("config/grids/ema_cross.yaml", help="Walk-forward grid yaml"),
    walk_metric: str = typer.Option("sharpe_like", help="Walk-forward selection metric"),
    fee_multipliers: str = typer.Option("1.0,1.5,2.0,3.0", help="Comma-separated fee multipliers"),
    fixed_slippage_bps: str = typer.Option("1,3,5,10", help="Fixed slippage bps list"),
    atr_slippage_mults: str = typer.Option("0.02,0.05,0.1,0.2", help="ATR slippage multipliers"),
    slippage_mode: str = typer.Option("mixed", help="fixed | atr | mixed"),
    latency_bars: str = typer.Option("0,1,3", help="Latency in bars"),
    order_models: str = typer.Option("market,limit", help="Comma-separated order models"),
    limit_timeout_bars: int = typer.Option(2, min=0, help="Limit timeout bars"),
    limit_fill_probability: float = typer.Option(0.9, min=0.0, max=1.0, help="Limit touch fill probability"),
    limit_unfilled_penalty_bps: float = typer.Option(3.0, min=0.0, help="Penalty bps on timeout market close"),
    walk_train_days: int = typer.Option(180, min=1, help="Walk-forward train window days"),
    walk_test_days: int = typer.Option(60, min=1, help="Walk-forward test window days"),
    walk_step_days: int = typer.Option(60, min=1, help="Walk-forward step days"),
    walk_top_pct: float = typer.Option(0.2, min=0.01, max=1.0, help="Top percentile for parameter cluster"),
    walk_max_candidates: int = typer.Option(100, min=1, help="Max parameter candidates per train window"),
    lookbacks: str = typer.Option("7d,14d,28d", help="Portfolio lookback list (e.g. 7d,14d,28d)"),
    rebalance: str = typer.Option("4h,1d", help="Portfolio rebalance list (e.g. 4h,1d)"),
    k: str = typer.Option("3,4", help="Portfolio long/short count list"),
    gross: str = typer.Option("1.0,1.5", help="Portfolio gross exposure cap list"),
    signal_models: str = typer.Option("momentum,mean_reversion", help="Portfolio signals: momentum,mean_reversion"),
    turnover_threshold: float = typer.Option(0.08, min=0.0, max=2.0, help="Minimum turnover ratio to rebalance"),
    turnover_threshold_high_vol: float | None = typer.Option(None, min=0.0, max=2.0, help="Optional high-vol turnover threshold"),
    turnover_threshold_low_vol: float | None = typer.Option(None, min=0.0, max=2.0, help="Optional low-vol turnover threshold"),
    vol_lookback: int = typer.Option(96, min=5, help="Portfolio volatility lookback in bars"),
    rank_buffer: str = typer.Option("0,1", help="Portfolio rank hysteresis buffer list"),
    high_vol_pcts: str = typer.Option("0.75,0.85,0.90", help="Portfolio high-vol percentile candidates"),
    gross_maps: str = typer.Option("highvol_050,balanced,conservative,off_range_highvol,ultra_defensive", help="Portfolio regime gross maps"),
    off_grace_bars: str = typer.Option("0,24", help="OFF regime grace bars list"),
    phased_entry_steps: str = typer.Option("1,2", help="Phased entry steps list"),
    debug_mode: bool = typer.Option(False, help="Enable portfolio debug diagnostics"),
    stop_on_anomaly: bool = typer.Option(False, help="Raise on portfolio anomalies when debug_mode is enabled"),
    max_cost_ratio_per_bar: float = typer.Option(0.05, min=0.0, max=1.0, help="Per-bar cost safety clamp vs equity"),
    enable_liquidation: bool = typer.Option(True, "--enable-liquidation/--disable-liquidation", help="Enable equity-floor liquidation and trading halt"),
    equity_floor_ratio: float = typer.Option(0.01, min=0.0, max=0.5, help="Equity floor ratio vs initial equity"),
    trading_halt_bars: int = typer.Option(168, min=0, help="Trading halt bars after liquidation"),
    skip_trades_if_cost_exceeds_equity_ratio: float = typer.Option(0.02, min=0.0, max=1.0, help="Skip trades when conservative cost estimate exceeds this equity ratio"),
    transition_smoother: bool = typer.Option(False, "--transition-smoother/--no-transition-smoother", help="Enable gross transition smoothing"),
    gross_step_up: float = typer.Option(0.10, min=0.0, max=1.0, help="Max gross increase per rebalance"),
    gross_step_down: float = typer.Option(0.25, min=0.0, max=1.0, help="Max gross decrease per rebalance"),
    post_halt_cooldown_bars: int = typer.Option(168, min=0, help="Extra cooldown bars after halt release"),
    post_halt_max_gross: float = typer.Option(0.15, min=0.0, max=2.0, help="Max gross during post-halt cooldown"),
    liquidation_lookback_bars: int = typer.Option(720, min=0, help="Liquidation lookback bars for churn gate"),
    liquidation_lookback_max_gross: float = typer.Option(0.15, min=0.0, max=2.0, help="Max gross if liquidation seen in lookback"),
    max_abs_weight_per_symbol: float = typer.Option(0.12, min=0.0, max=1.0, help="Absolute per-symbol target weight cap"),
    atr_shock_threshold: float = typer.Option(2.5, min=0.0, max=20.0, help="ATR shock threshold (ATR24h/ATR14d)"),
    gap_shock_threshold: float = typer.Option(0.10, min=0.0, max=1.0, help="1-bar return shock threshold"),
    shock_cooldown_bars: int = typer.Option(72, min=0, help="Bars to keep shock effect active"),
    shock_mode: str = typer.Option("downweight", help="Shock handling mode: exclude | downweight"),
    shock_weight_mult_atr: float = typer.Option(0.25, min=0.0, max=1.0, help="Weight multiplier when ATR shock is active in downweight mode"),
    shock_weight_mult_gap: float = typer.Option(0.10, min=0.0, max=1.0, help="Weight multiplier when gap shock is active in downweight mode"),
    shock_freeze_rebalance: bool | None = typer.Option(
        None,
        "--shock-freeze-rebalance/--no-shock-freeze-rebalance",
        help="Freeze rebalances on shock-active bars (default: ON for shock-mode downweight)",
    ),
    shock_freeze_min_fraction: float = typer.Option(
        0.30,
        min=0.0,
        max=1.0,
        help="Shock-active threshold as fraction of shocked symbols in basket",
    ),
    enable_symbol_shock_filters: bool = typer.Option(True, "--enable-symbol-shock-filters/--disable-symbol-shock-filters", help="Enable per-symbol ATR/gap shock filters"),
    cap_mode: str = typer.Option("adaptive", help="Turnover cap mode: fixed | adaptive"),
    base_cap: float = typer.Option(0.25, min=0.0, max=2.0, help="Base turnover cap (notional/equity)"),
    cap_min: float = typer.Option(0.20, min=0.0, max=2.0, help="Adaptive cap lower bound"),
    cap_max: float = typer.Option(0.40, min=0.0, max=2.0, help="Adaptive cap upper bound"),
    backlog_thresholds: str = typer.Option("0.25,0.50,0.75", help="Adaptive backlog ratio thresholds (3 floats)"),
    cap_steps: str = typer.Option("0.25,0.30,0.35,0.40", help="Adaptive cap steps (4 floats)"),
    high_vol_cap_max: float = typer.Option(0.30, min=0.0, max=2.0, help="Adaptive cap max in high-vol regime"),
    max_turnover_notional_to_equity: str = typer.Option("0.25", help="Per-rebalance turnover cap as equity multiple (use 'off' to disable)"),
    drift_threshold: str = typer.Option("0.35", help="Force-rebalance drift threshold L1 distance (use 'off' to disable)"),
    gross_decay_steps: int = typer.Option(3, min=1, max=20, help="OFF regime unwind steps (reduce-only)"),
    dd_controller: bool = typer.Option(True, "--dd-controller/--no-dd-controller", help="Enable drawdown-based deleveraging / kill switch"),
    dd_thresholds: str = typer.Option("0.10,0.20,0.30,0.40", help="DD stage enter thresholds (4 floats)"),
    dd_gross_mults: str = typer.Option("1.0,0.7,0.5,0.3,0.0", help="DD stage gross multipliers (5 floats)"),
    dd_recover_thresholds: str = typer.Option("0.08,0.16,0.24,0.32", help="DD stage recover thresholds (4 floats)"),
    kill_cooldown_bars: int = typer.Option(168, min=0, help="Kill-switch minimum hold bars before recovery"),
    disable_new_entry_when_dd: bool = typer.Option(True, "--disable-new-entry-when-dd/--allow-new-entry-when-dd", help="Block new entries while in DD stages"),
    rolling_peak_window_bars: str = typer.Option("720", help="Rolling peak window bars for DD (use 'off' for absolute peak)"),
    stage_down_confirm_bars: int = typer.Option(48, min=1, help="Bars to confirm stage-down recovery"),
    stage3_down_confirm_bars: int = typer.Option(96, min=1, help="Bars to confirm stage3->stage2 recovery"),
    disable_new_entry_stage: int = typer.Option(3, min=1, help="Disable new entry from this DD stage and above"),
    dd_turnover_threshold_mult: float = typer.Option(1.5, min=1.0, max=10.0, help="Turnover-threshold multiplier in DD stage>=2"),
    dd_rebalance_mult: str = typer.Option("off", help="Optional rebalance interval multiplier in DD stage>=2 (use 'off')"),
    max_notional_to_equity_mult: float = typer.Option(3.0, min=1.0, max=20.0, help="Per-bar turnover notional cap multiple"),
    high_vol_gross_mult: float = typer.Option(0.5, min=0.0, max=1.0, help="Regime sizing multiplier in high vol"),
    trend_ema_span: int = typer.Option(48, min=2, help="Regime trend EMA span"),
    trend_slope_lookback: int = typer.Option(8, min=1, help="Regime trend slope lookback"),
    trend_slope_threshold: float = typer.Option(0.0015, min=0.0, help="Trend/range slope threshold"),
    regime_atr_period: int = typer.Option(14, min=2, help="Regime ATR period"),
    regime_vol_lookback: int = typer.Option(120, min=5, help="Regime volatility lookback"),
    regime_vol_percentile: float = typer.Option(0.65, min=0.1, max=0.95, help="High vol percentile threshold"),
) -> None:
    setup_logging()
    if suite not in {"all", "cost", "walk", "regime", "portfolio"}:
        raise typer.BadParameter("--suite must be one of: all, cost, walk, regime, portfolio")
    if data_source not in {"binance", "csv", "synthetic"}:
        raise typer.BadParameter("--data-source must be one of: binance, csv, synthetic")
    if slippage_mode not in {"fixed", "atr", "mixed"}:
        raise typer.BadParameter("--slippage-mode must be one of: fixed, atr, mixed")

    parsed_order_models: list[str] = [x.strip().lower() for x in order_models.split(",") if x.strip()]
    if not parsed_order_models or any(x not in {"market", "limit"} for x in parsed_order_models):
        raise typer.BadParameter("--order-models must contain only market/limit")

    if suite == "portfolio":
        parsed_symbols = _parse_symbols(symbols)
        parsed_signal_models = [x.strip().lower() for x in signal_models.split(",") if x.strip()]
        if not parsed_signal_models:
            raise typer.BadParameter("--signal-models must include at least one of momentum,mean_reversion")
        for model in parsed_signal_models:
            if model not in {"momentum", "mean_reversion"}:
                raise typer.BadParameter("--signal-models supports only momentum,mean_reversion")
        parsed_gross_maps = [x.strip().lower() for x in gross_maps.split(",") if x.strip()]
        if not parsed_gross_maps:
            raise typer.BadParameter("--gross-maps must include at least one map name")
        parsed_cap_mode = cap_mode.strip().lower()
        if parsed_cap_mode not in {"fixed", "adaptive"}:
            raise typer.BadParameter("--cap-mode must be one of: fixed, adaptive")
        parsed_shock_mode = shock_mode.strip().lower()
        if parsed_shock_mode not in {"exclude", "downweight"}:
            raise typer.BadParameter("--shock-mode must be one of: exclude, downweight")
        parsed_shock_freeze_rebalance = shock_freeze_rebalance
        if parsed_shock_freeze_rebalance is None:
            parsed_shock_freeze_rebalance = bool(parsed_shock_mode == "downweight")
        try:
            parsed_turnover_cap = _parse_optional_float(max_turnover_notional_to_equity)
            parsed_drift_threshold = _parse_optional_float(drift_threshold)
            parsed_rolling_peak_window = _parse_optional_float(rolling_peak_window_bars)
            parsed_dd_rebalance_mult = _parse_optional_float(dd_rebalance_mult)
            parsed_backlog_thresholds = _parse_float_list(backlog_thresholds)
            parsed_cap_steps = _parse_float_list(cap_steps)
            parsed_dd_thresholds = _parse_float_list(dd_thresholds)
            parsed_dd_gross_mults = _parse_float_list(dd_gross_mults)
            parsed_dd_recover_thresholds = _parse_float_list(dd_recover_thresholds)
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid float option: {exc}") from exc
        if len(parsed_backlog_thresholds) != 3:
            raise typer.BadParameter("--backlog-thresholds must contain exactly 3 floats")
        if len(parsed_cap_steps) != 4:
            raise typer.BadParameter("--cap-steps must contain exactly 4 floats")
        if len(parsed_dd_thresholds) != 4:
            raise typer.BadParameter("--dd-thresholds must contain exactly 4 floats")
        if len(parsed_dd_gross_mults) != 5:
            raise typer.BadParameter("--dd-gross-mults must contain exactly 5 floats")
        if len(parsed_dd_recover_thresholds) != 4:
            raise typer.BadParameter("--dd-recover-thresholds must contain exactly 4 floats")

        cfg = AppConfig.from_env().model_copy(update={"symbol": parsed_symbols[0], "timeframe": timeframe})
        base_bt_cfg = replace(_build_base_backtest_config(cfg), persist_to_db=False)
        output = run_portfolio_validation(
            symbols=parsed_symbols,
            timeframe=timeframe,
            start=start,
            end=end,
            base_config=base_bt_cfg,
            output_root=Path(output_dir),
            seed=seed,
            data_source=data_source,  # type: ignore[arg-type]
            csv_path=csv_path,
            testnet=_is_testnet(cfg),
            signal_models=parsed_signal_models,
            lookback_bars=_parse_duration_list(lookbacks, timeframe=timeframe),
            rebalance_bars=_parse_duration_list(rebalance, timeframe=timeframe),
            k_values=_parse_int_list(k),
            gross_values=_parse_float_list(gross),
            rank_buffers=_parse_int_list(rank_buffer),
            high_vol_percentiles=_parse_float_list(high_vol_pcts),
            gross_maps=parsed_gross_maps,
            off_grace_bars_list=_parse_int_list(off_grace_bars),
            phased_entry_steps_list=_parse_int_list(phased_entry_steps),
            turnover_threshold=turnover_threshold,
            turnover_threshold_high_vol=turnover_threshold_high_vol,
            turnover_threshold_low_vol=turnover_threshold_low_vol,
            vol_lookback=vol_lookback,
            fee_multipliers=_parse_float_list(fee_multipliers),
            fixed_slippage_bps=_parse_float_list(fixed_slippage_bps),
            atr_slippage_mults=_parse_float_list(atr_slippage_mults),
            slippage_mode=slippage_mode,  # type: ignore[arg-type]
            latency_bars=_parse_int_list(latency_bars),
            order_models=parsed_order_models,  # type: ignore[arg-type]
            limit_timeout_bars=limit_timeout_bars,
            limit_fill_probability=limit_fill_probability,
            limit_unfilled_penalty_bps=limit_unfilled_penalty_bps,
            walk_train_days=walk_train_days,
            walk_test_days=walk_test_days,
            walk_step_days=walk_step_days,
            walk_top_pct=walk_top_pct,
            walk_max_candidates=walk_max_candidates,
            walk_metric=walk_metric,
            trend_ema_span=trend_ema_span,
            trend_slope_lookback=trend_slope_lookback,
            trend_slope_threshold=trend_slope_threshold,
            regime_atr_period=regime_atr_period,
            regime_vol_lookback=regime_vol_lookback,
            regime_vol_percentile=regime_vol_percentile,
            high_vol_gross_mult=high_vol_gross_mult,
            debug_mode=debug_mode,
            max_cost_ratio_per_bar=max_cost_ratio_per_bar,
            dd_controller_enabled=dd_controller,
            dd_thresholds=(parsed_dd_thresholds[0], parsed_dd_thresholds[1], parsed_dd_thresholds[2], parsed_dd_thresholds[3]),
            dd_gross_mults=(
                parsed_dd_gross_mults[0],
                parsed_dd_gross_mults[1],
                parsed_dd_gross_mults[2],
                parsed_dd_gross_mults[3],
                parsed_dd_gross_mults[4],
            ),
            dd_recover_thresholds=(
                parsed_dd_recover_thresholds[0],
                parsed_dd_recover_thresholds[1],
                parsed_dd_recover_thresholds[2],
                parsed_dd_recover_thresholds[3],
            ),
            kill_cooldown_bars=kill_cooldown_bars,
            disable_new_entry_when_dd=disable_new_entry_when_dd,
            rolling_peak_window_bars=None if parsed_rolling_peak_window is None else int(parsed_rolling_peak_window),
            stage_down_confirm_bars=stage_down_confirm_bars,
            stage3_down_confirm_bars=stage3_down_confirm_bars,
            reentry_ramp_steps=3,
            disable_new_entry_stage=disable_new_entry_stage,
            dd_turnover_threshold_mult=dd_turnover_threshold_mult,
            dd_rebalance_mult=parsed_dd_rebalance_mult,
            cap_mode=parsed_cap_mode,  # type: ignore[arg-type]
            base_cap=base_cap,
            cap_min=cap_min,
            cap_max=cap_max,
            backlog_thresholds=(parsed_backlog_thresholds[0], parsed_backlog_thresholds[1], parsed_backlog_thresholds[2]),
            cap_steps=(parsed_cap_steps[0], parsed_cap_steps[1], parsed_cap_steps[2], parsed_cap_steps[3]),
            high_vol_cap_max=high_vol_cap_max,
            max_turnover_notional_to_equity=parsed_turnover_cap,
            drift_threshold=parsed_drift_threshold,
            gross_decay_steps=gross_decay_steps,
            max_notional_to_equity_mult=max_notional_to_equity_mult,
            enable_liquidation=enable_liquidation,
            equity_floor_ratio=equity_floor_ratio,
            trading_halt_bars=trading_halt_bars,
            skip_trades_if_cost_exceeds_equity_ratio=skip_trades_if_cost_exceeds_equity_ratio,
            transition_smoother_enabled=transition_smoother,
            gross_step_up=gross_step_up,
            gross_step_down=gross_step_down,
            post_halt_cooldown_bars=post_halt_cooldown_bars,
            post_halt_max_gross=post_halt_max_gross,
            liquidation_lookback_bars=liquidation_lookback_bars,
            liquidation_lookback_max_gross=liquidation_lookback_max_gross,
            enable_symbol_shock_filters=enable_symbol_shock_filters,
            max_abs_weight_per_symbol=max_abs_weight_per_symbol,
            atr_shock_threshold=atr_shock_threshold,
            gap_shock_threshold=gap_shock_threshold,
            shock_cooldown_bars=shock_cooldown_bars,
            shock_mode=parsed_shock_mode,  # type: ignore[arg-type]
            shock_weight_mult_atr=shock_weight_mult_atr,
            shock_weight_mult_gap=shock_weight_mult_gap,
            shock_freeze_rebalance=parsed_shock_freeze_rebalance,
            shock_freeze_min_fraction=shock_freeze_min_fraction,
            stop_on_anomaly=stop_on_anomaly,
        )
        table = Table(title="Portfolio Experiment Summary")
        table.add_column("metric")
        table.add_column("value", justify="right")
        table.add_row("run_id", output.run_id)
        table.add_row("verdict", str(output.summary.get("verdict", "UNKNOWN")))
        table.add_row("oos_positive_ratio", f"{float(output.summary.get('oos_positive_ratio', 0.0)):.4f}")
        table.add_row("cost_positive_ratio", f"{float(output.summary.get('cost_positive_ratio', 0.0)):.4f}")
        table.add_row("portfolio_mdd", f"{float(output.summary.get('portfolio_max_drawdown', 0.0)):.4f}")
        table.add_row("btc_long_mdd", f"{float(output.summary.get('btc_long_max_drawdown', 0.0)):.4f}")
        table.add_row("rebalance_count", f"{int(float(output.summary.get('rebalance_count', 0.0)))}")
        console.print(table)
        console.print(f"results_dir: {output.run_dir}")
        return

    if strategy not in AVAILABLE_STRATEGIES:
        raise typer.BadParameter(f"Unknown strategy: {strategy}. Available: {', '.join(AVAILABLE_STRATEGIES)}")

    cfg = AppConfig.from_env().model_copy(update={"symbol": symbol, "timeframe": timeframe})
    storage = SQLiteStorage(cfg.db_path)
    try:
        loaded_params, loaded_strategy = _load_strategy_params_from_source(
            params_from=params_from,
            params_rank=params_rank,
            storage=storage,
        )
    finally:
        storage.close()

    strategy_name = loaded_strategy or strategy
    strategy_params = loaded_params if loaded_params else _get_strategy_params(strategy_name, cfg)
    base_bt_cfg = replace(
        _build_base_backtest_config(cfg),
        strategy_name=strategy_name,
        strategy_params=strategy_params,
        persist_to_db=False,
    )

    output = run_edge_validation(
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        strategy_name=strategy_name,
        strategy_params=strategy_params,
        base_config=base_bt_cfg,
        output_root=Path(output_dir),
        seed=seed,
        data_source=data_source,  # type: ignore[arg-type]
        csv_path=csv_path,
        testnet=_is_testnet(cfg),
        suite=suite,  # type: ignore[arg-type]
        fee_multipliers=_parse_float_list(fee_multipliers),
        fixed_slippage_bps=_parse_float_list(fixed_slippage_bps),
        atr_slippage_mults=_parse_float_list(atr_slippage_mults),
        slippage_mode=slippage_mode,  # type: ignore[arg-type]
        latency_bars=_parse_int_list(latency_bars),
        order_models=parsed_order_models,  # type: ignore[arg-type]
        limit_timeout_bars=limit_timeout_bars,
        limit_fill_probability=limit_fill_probability,
        limit_unfilled_penalty_bps=limit_unfilled_penalty_bps,
        walk_train_days=walk_train_days,
        walk_test_days=walk_test_days,
        walk_step_days=walk_step_days,
        walk_top_pct=walk_top_pct,
        walk_max_candidates=walk_max_candidates,
        walk_metric=walk_metric,
        walk_grid_path=walk_grid,
        trend_ema_span=trend_ema_span,
        trend_slope_lookback=trend_slope_lookback,
        trend_slope_threshold=trend_slope_threshold,
        regime_atr_period=regime_atr_period,
        regime_vol_lookback=regime_vol_lookback,
        regime_vol_percentile=regime_vol_percentile,
    )

    table = Table(title="Edge Experiment Summary")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("run_id", output.run_id)
    table.add_row("verdict", str(output.summary.get("verdict", "UNKNOWN")))
    table.add_row("robustness_score", f"{float(output.summary.get('robustness_score', 0.0)):.4f}")
    table.add_row("cost_positive_ratio", f"{float(output.summary.get('cost_positive_ratio', 0.0)):.4f}")
    table.add_row("wfo_oos_positive_ratio", f"{float(output.summary.get('wfo_oos_positive_ratio', 0.0)):.4f}")
    table.add_row("wfo_param_stability_score", f"{float(output.summary.get('wfo_param_stability_score', 0.0)):.4f}")
    table.add_row("regime_positive_ratio", f"{float(output.summary.get('regime_positive_ratio', 0.0)):.4f}")
    console.print(table)
    console.print(f"results_dir: {output.run_dir}")


@app.command("system-batch")
def system_batch(
    symbols: str = typer.Option("BTC/USDT,ETH/USDT,SOL/USDT", help="Comma-separated symbols"),
    timeframe: str = typer.Option("1h", help="Candle timeframe (use one consistently)"),
    start: str = typer.Option("2021-01-01", help="Batch start date/time (UTC)"),
    end: str = typer.Option("2026-01-01", help="Batch end date/time (UTC)"),
    seed: int = typer.Option(42, help="Global seed"),
    data_source: str = typer.Option("binance", help="binance | csv | synthetic"),
    csv_path: str | None = typer.Option(None, help="CSV path when --data-source csv"),
    output_dir: str = typer.Option("out/experiments", help="Batch output root"),
) -> None:
    setup_logging()
    if data_source not in {"binance", "csv", "synthetic"}:
        raise typer.BadParameter("--data-source must be one of: binance, csv, synthetic")

    parsed_symbols = _parse_symbols(symbols)
    cfg = AppConfig.from_env().model_copy(update={"timeframe": timeframe})
    base_bt_cfg = replace(
        _build_base_backtest_config(cfg),
        persist_to_db=False,
    )

    output = run_system_batch(
        symbols=parsed_symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        base_config=base_bt_cfg,
        output_root=Path(output_dir),
        seed=seed,
        data_source=data_source,  # type: ignore[arg-type]
        csv_path=csv_path,
        testnet=_is_testnet(cfg),
        candidates=default_system_candidates(),
        walk_train_days=240,
        walk_test_days=60,
        walk_step_days=30,
        walk_top_pct=0.15,
        walk_max_candidates=120,
    )

    table = Table(title="System Candidate Batch")
    table.add_column("candidate")
    table.add_column("verdict")
    table.add_column("wfo>=0.60(2sym)")
    table.add_column("cost_robust")
    table.add_column("regime_pf_mdd")
    table.add_column("trades>=200")
    for row in output.candidate_results:
        table.add_row(
            str(row.get("candidate_id", "")),
            str(row.get("verdict", "")),
            str(row.get("gate_wfo_two_symbols", "")),
            str(row.get("gate_cost_robust", "")),
            str(row.get("gate_regime_consistency", "")),
            str(row.get("gate_trade_count", "")),
        )
    console.print(table)
    console.print(f"batch_run_id: {output.batch_run_id}")
    console.print(f"batch_dir: {output.batch_dir}")


@app.command()
def run(
    mode: str = typer.Option("paper", help="paper | live"),
    symbol: str = typer.Option("BTC/USDT", help="Market symbol"),
    symbols: str | None = typer.Option(None, "--symbols", help="Comma-separated symbols, e.g. BTC/USDT,ETH/USDT"),
    timeframe: str = typer.Option("1m", help="Runtime timeframe"),
    env: str | None = typer.Option(None, "--env", help="Binance env override: mainnet | testnet"),
    preset: str | None = typer.Option(None, "--preset", help="Preset file/name (e.g. sleep_mode.yaml)"),
    sleep_mode: bool = typer.Option(False, "--sleep-mode", help="Apply sleep_mode preset defaults"),
    strategy: str = typer.Option("ema_cross", help="Strategy name"),
    data_mode: str = typer.Option("rest", help="Data mode: rest | websocket"),
    params_from: str | None = typer.Option(None, help="csv/parquet path or run_id source"),
    params_rank: int = typer.Option(1, min=1, help="Rank to select from params file"),
    max_bars: int = typer.Option(0, min=0, help="Stop after N closed bars (0 = infinite)"),
    realtime_only: bool = typer.Option(
        False,
        "--realtime-only",
        help="Websocket mode only: disable historical backfill and process live bars only",
    ),
    poll_interval_sec: float | None = typer.Option(None, min=0.1, help="REST polling interval"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Log order payload only, do not send"),
    budget_guard: bool | None = typer.Option(
        None,
        "--budget-guard/--no-budget-guard",
        help="Pre-order available-balance check (default from config)",
    ),
    one_shot: bool = typer.Option(False, "--one-shot", help="Process one closed bar then exit"),
    halt_on_error: bool = typer.Option(False, "--halt-on-error", help="Halt immediately on runtime exception"),
    resume: bool = typer.Option(False, "--resume", help="Resume from saved runtime_state"),
    resume_run_id: str | None = typer.Option(None, "--resume-run-id", help="Specific run_id to resume"),
    state_save_every_n_bars: int = typer.Option(1, min=1, help="Persist runtime state every N bars"),
    feed_stall_seconds: float | None = typer.Option(None, min=0.0, help="Halt when closed-bar receive gap exceeds this many seconds"),
    bar_staleness_warn_seconds: float | None = typer.Option(None, min=0.0, help="Warn when bar timestamp staleness exceeds this many seconds"),
    bar_staleness_halt: bool | None = typer.Option(None, "--bar-staleness-halt/--no-bar-staleness-halt", help="Halt on stale bars when threshold is exceeded"),
    bar_staleness_halt_seconds: float | None = typer.Option(None, min=0.0, help="Optional halt threshold for stale bars (default: warn threshold)"),
    api_error_halt_threshold: int | None = typer.Option(None, min=1, help="Consecutive API errors before halt"),
    auto_protective: bool = typer.Option(True, "--auto-protective/--no-auto-protective", help="Auto-create SL/TP orders after entry"),
    run_stop_loss_pct: float | None = typer.Option(None, min=0.0, help="Protective stop loss pct"),
    run_take_profit_pct: float | None = typer.Option(None, min=0.0, help="Protective take profit pct"),
    budget_usdt: str | None = typer.Option(
        None,
        "--budget-usdt",
        help="Budget cap in USDT: numeric value or 'auto'",
    ),
    max_position_notional: float | None = typer.Option(
        None,
        "--max-position-notional",
        min=1.0,
        help="Max position exposure cap in USDT",
    ),
    min_entry_notional: float | None = typer.Option(
        None,
        "--min-entry-notional",
        min=0.0,
        help="Minimum entry notional floor in USDT (reduce-only excluded)",
    ),
    capital: float | None = typer.Option(None, "--capital", min=1.0, help="Optional fixed budget cap (USDT)"),
    yes_i_understand_live_risk: bool = typer.Option(
        False,
        "--yes-i-understand-live-risk",
        help="Required when --mode live",
    ),
) -> None:
    if mode not in {"paper", "live"}:
        raise typer.BadParameter("mode must be one of: paper, live")
    if data_mode not in {"rest", "websocket"}:
        raise typer.BadParameter("data-mode must be one of: rest, websocket")
    if mode == "live" and not yes_i_understand_live_risk:
        raise typer.BadParameter("--yes-i-understand-live-risk is required for live mode")
    if env is not None and env.strip().lower() not in {"mainnet", "testnet"}:
        raise typer.BadParameter("--env must be one of: mainnet, testnet")
    if budget_usdt is not None and capital is not None:
        raise typer.BadParameter("Use either --budget-usdt or --capital, not both")

    parsed_symbols = _parse_symbols(symbols) if symbols else [symbol]
    primary_symbol = parsed_symbols[0]

    setup_logging()
    selected_preset = "sleep_mode" if sleep_mode else preset
    env_override = env.strip().lower() if env is not None else None
    cfg = AppConfig.from_env(
        preset=selected_preset,
        binance_env_override=(env_override if env_override in {"mainnet", "testnet"} else None),  # type: ignore[arg-type]
    ).model_copy(update={"symbol": primary_symbol, "timeframe": timeframe})
    update_payload: dict[str, Any] = {"sleep_mode": sleep_mode}
    if env is not None:
        env_norm = env.strip().lower()
        update_payload["binance_env"] = env_norm
        update_payload["binance_testnet"] = env_norm == "testnet"
    if capital is not None:
        update_payload["capital_limit_usdt"] = float(capital)
    if max_position_notional is not None:
        update_payload["max_position_notional_usdt"] = float(max_position_notional)
    if min_entry_notional is not None:
        update_payload["min_entry_notional_usdt"] = float(min_entry_notional)
    cfg = cfg.model_copy(update=update_payload)
    if mode == "live" and cfg.binance_env != "testnet":
        raise typer.BadParameter("live mode is restricted to testnet only; use --env testnet")
    storage = SQLiteStorage(cfg.db_path)
    effective_budget_guard = cfg.budget_guard_enabled if budget_guard is None else bool(budget_guard)

    loaded_params, loaded_strategy = _load_strategy_params_from_source(
        params_from=params_from,
        params_rank=params_rank,
        storage=storage,
    )
    strategy_name = loaded_strategy or strategy
    strategy_params = loaded_params if loaded_params else _get_strategy_params(strategy_name, cfg)
    strategy_obj = _build_strategy(strategy_name=strategy_name, params=strategy_params, cfg=cfg)

    effective_sl_pct = float(
        run_stop_loss_pct
        if run_stop_loss_pct is not None
        else (
            loaded_params.get("stop_loss_pct", cfg.sl_pct)
            if cfg.sl_mode == "pct"
            else cfg.run_stop_loss_pct
        )
    )
    effective_tp_pct = float(
        run_take_profit_pct
        if run_take_profit_pct is not None
        else (
            loaded_params.get("take_profit_pct", cfg.tp_pct)
            if cfg.tp_mode == "pct"
            else cfg.run_take_profit_pct
        )
    )

    budget_mode = str(cfg.budget_usdt_mode or "risk").lower()
    budget_fixed = float(cfg.budget_usdt_value or 0.0) if cfg.budget_usdt_value is not None else None
    if budget_usdt is not None:
        budget_raw = str(budget_usdt).strip().lower()
        if budget_raw == "auto":
            budget_mode = "auto"
            budget_fixed = None
        else:
            try:
                parsed_budget = float(budget_raw)
            except ValueError as exc:
                raise typer.BadParameter("--budget-usdt must be a number or 'auto'") from exc
            if parsed_budget <= 0:
                raise typer.BadParameter("--budget-usdt numeric value must be > 0")
            budget_mode = "fixed"
            budget_fixed = parsed_budget
    elif capital is not None:
        budget_mode = "fixed"
        budget_fixed = float(capital)
    elif mode == "live" and cfg.binance_env == "testnet" and budget_mode == "risk":
        budget_mode = "auto"

    runtime_cfg = RuntimeConfig(
        mode=mode,  # type: ignore[arg-type]
        symbol=primary_symbol,
        timeframe=timeframe,
        fixed_notional_usdt=float(loaded_params.get("fixed_notional_usdt", cfg.run_fixed_notional_usdt)),
        atr_period=cfg.atr_period,
        max_bars=max_bars,
        dry_run=dry_run,
        one_shot=one_shot,
        halt_on_error=halt_on_error,
        resume=resume,
        resume_run_id=resume_run_id,
        state_save_every_n_bars=state_save_every_n_bars if state_save_every_n_bars > 0 else cfg.run_state_save_every_n_bars,
        enable_protective_orders=auto_protective and cfg.enable_protective_orders,
        require_protective_orders=cfg.require_protective_orders,
        protective_missing_policy=cfg.protective_missing_policy,
        api_error_halt_threshold=int(api_error_halt_threshold or cfg.api_error_halt_threshold),
        feed_stall_timeout_sec=(
            float(feed_stall_seconds)
            if feed_stall_seconds is not None
            else (float(cfg.feed_stall_seconds) if cfg.feed_stall_seconds > 0 else _timeframe_seconds(timeframe) * 3.0)
        ),
        bar_staleness_warn_sec=(
            float(bar_staleness_warn_seconds)
            if bar_staleness_warn_seconds is not None
            else float(cfg.bar_staleness_warn_seconds)
        ),
        bar_staleness_halt=(cfg.bar_staleness_halt if bar_staleness_halt is None else bool(bar_staleness_halt)),
        bar_staleness_halt_sec=(
            float(bar_staleness_halt_seconds)
            if bar_staleness_halt_seconds is not None
            else float(cfg.bar_staleness_halt_seconds)
        ),
        preflight_max_time_drift_ms=cfg.preflight_max_time_drift_ms,
        preflight_expected_leverage=cfg.leverage,
        preflight_expected_margin_mode=cfg.expected_margin_mode,
        protective_stop_loss_pct=effective_sl_pct,
        protective_take_profit_pct=effective_tp_pct,
        binance_env=cfg.binance_env,
        live_trading_enabled=cfg.live_trading,
        budget_guard_enabled=effective_budget_guard,
        budget_usdt_mode=(budget_mode if budget_mode in {"risk", "auto", "fixed"} else "risk"),  # type: ignore[arg-type]
        budget_usdt_fixed=budget_fixed,
        preset_name=cfg.preset_name,
        sleep_mode_enabled=sleep_mode,
        account_allocation_pct=cfg.account_allocation_pct,
        max_position_notional_usdt=cfg.max_position_notional_usdt,
        min_entry_notional_usdt=cfg.min_entry_notional_usdt,
        risk_per_trade_pct=cfg.risk_per_trade_pct,
        daily_loss_limit_pct=cfg.daily_loss_limit_pct,
        capital_limit_usdt=cfg.capital_limit_usdt,
        consec_loss_limit=cfg.consec_loss_limit,
        sl_mode=cfg.sl_mode,
        sl_atr_mult=cfg.sl_atr_mult,
        tp_mode=cfg.tp_mode,
        tp_atr_mult=cfg.tp_atr_mult,
        trailing_stop_enabled=cfg.trailing_stop_enabled,
        trail_pct=cfg.trail_pct,
        trail_atr_mult=cfg.trail_atr_mult,
        cooldown_bars_after_halt=cfg.cooldown_bars_after_halt,
        quiet_hours=(cfg.quiet_hours if sleep_mode else None),
        heartbeat_enabled=cfg.heartbeat_enabled,
        heartbeat_interval_minutes=cfg.heartbeat_interval_minutes,
        strategy_name=strategy_name,
        strategy_params=dict(strategy_params),
        candidate_profile=(FINAL_CANDIDATE_PROFILE if strategy_name == FINAL_CANDIDATE_PROFILE else None),
        validation_probe_enabled=cfg.validation_probe_enabled,
        validation_probe_entry_after_bars=cfg.validation_probe_entry_after_bars,
        validation_probe_exit_after_bars=cfg.validation_probe_exit_after_bars,
        validation_allow_live_backfill_execution=cfg.validation_allow_live_backfill_execution,
    )
    _validate_live_entry_sizing(runtime_cfg)
    risk_guard = RiskGuard(
        max_order_notional=cfg.max_order_notional,
        max_position_notional=cfg.max_position_notional_usdt,
        max_daily_loss=cfg.max_daily_loss,
        max_drawdown_pct=cfg.max_drawdown_pct,
        max_atr_pct=cfg.max_atr_pct,
        account_allocation_pct=cfg.account_allocation_pct,
        risk_per_trade_pct=cfg.risk_per_trade_pct,
        daily_loss_limit_pct=cfg.daily_loss_limit_pct,
        consec_loss_limit=cfg.consec_loss_limit,
        quiet_hours=(cfg.quiet_hours if sleep_mode else None),
        capital_limit_usdt=cfg.capital_limit_usdt,
    )
    notifier = Notifier(
        telegram_bot_token=cfg.telegram_bot_token.get_secret_value() if cfg.telegram_bot_token else None,
        telegram_chat_id=cfg.telegram_chat_id.get_secret_value() if cfg.telegram_chat_id else None,
        discord_webhook_url=cfg.discord_webhook_url.get_secret_value() if cfg.discord_webhook_url else None,
    )
    _print_runtime_banner(cfg=cfg, runtime_cfg=runtime_cfg)

    if mode == "paper":
        broker = PaperBroker(
            starting_cash=cfg.initial_equity,
            slippage_bps=cfg.slippage_bps,
            taker_fee_bps=cfg.taker_fee_bps,
            maker_fee_bps=cfg.maker_fee_bps,
        )
    else:
        if not cfg.binance_api_key or not cfg.binance_api_secret:
            raise typer.BadParameter("BINANCE_API_KEY and BINANCE_API_SECRET are required for live mode")
        broker = LiveBinanceBroker(
            api_key=cfg.binance_api_key.get_secret_value(),
            api_secret=cfg.binance_api_secret.get_secret_value(),
            testnet=_is_testnet(cfg),
            live_trading=cfg.live_trading,
            use_user_stream=cfg.use_user_stream,
            listenkey_renew_secs=cfg.listenkey_renew_secs,
        )

    feeds: dict[str, BinanceLiveFeed] = {}
    engines: dict[str, RuntimeEngine] = {}
    shared_run_id = uuid4().hex
    shared_budget_guard: AccountBudgetGuard | None = None
    if mode == "live" and runtime_cfg.budget_guard_enabled:
        shared_budget_guard = AccountBudgetGuard(broker=broker)
    try:
        for sym in parsed_symbols:
            per_cfg = replace(runtime_cfg, symbol=sym)
            per_strategy = _build_strategy(strategy_name=strategy_name, params=loaded_params, cfg=cfg)
            bootstrap_bars = 0
            if data_mode == "websocket" and not realtime_only and max_bars > 0:
                bootstrap_bars = max_bars
            per_feed = BinanceLiveFeed(
                symbol=sym,
                timeframe=timeframe,
                mode=data_mode,
                poll_interval_sec=poll_interval_sec or cfg.run_poll_interval_sec,
                testnet=_is_testnet(cfg),
                bootstrap_history_bars=bootstrap_bars,
            )
            feeds[sym] = per_feed
            engines[sym] = RuntimeEngine(
                config=per_cfg,
                strategy=per_strategy,
                broker=broker,
                feed=per_feed,
                storage=storage,
                risk_guard=risk_guard,
                budget_guard=shared_budget_guard,
                notifier=notifier,
                initial_equity=cfg.initial_equity,
                run_id=shared_run_id,
            )

        if len(parsed_symbols) == 1:
            result = engines[primary_symbol].run()
        else:
            orchestrator = RuntimeOrchestrator(
                engines=engines,
                feeds=feeds,
                max_bars=(max_bars if max_bars > 0 else None),
                account_risk_guard=risk_guard,
                account_initial_equity=cfg.initial_equity,
            )
            result = orchestrator.run()
        console.print(result)
    finally:
        for feed in feeds.values():
            feed.close()
        if hasattr(broker, "close"):
            broker.close()  # type: ignore[attr-defined]
        storage.close()


@app.command()
def arm_sleep(
    preset: str = typer.Option("sleep_mode", "--preset", help="Preset to validate for unattended operation"),
    env: str | None = typer.Option(None, "--env", help="mainnet | testnet"),
) -> None:
    setup_logging()
    env_override = env.strip().lower() if env is not None else None
    if env_override is not None and env_override not in {"mainnet", "testnet"}:
        raise typer.BadParameter("--env must be one of: mainnet, testnet")
    cfg = AppConfig.from_env(
        preset=preset,
        binance_env_override=env_override,  # type: ignore[arg-type]
    )
    if env is not None:
        env_norm = env.strip().lower()
        cfg = cfg.model_copy(update={"binance_env": env_norm, "binance_testnet": env_norm == "testnet"})

    checklist = Table(title="Sleep Mode Checklist")
    checklist.add_column("item")
    checklist.add_column("value")
    checklist.add_row("preset", str(cfg.preset_name or preset))
    checklist.add_row("BINANCE_ENV", str(cfg.binance_env))
    checklist.add_row("LIVE_TRADING", str(cfg.live_trading))
    checklist.add_row("allocation_pct", _pct_text(cfg.account_allocation_pct))
    checklist.add_row("leverage", str(cfg.leverage))
    checklist.add_row("daily_loss_limit_pct", _pct_text(cfg.daily_loss_limit_pct))
    checklist.add_row("max_drawdown_pct", _pct_text(cfg.max_drawdown_pct))
    checklist.add_row("risk_per_trade_pct", _pct_text(cfg.risk_per_trade_pct))
    checklist.add_row("max_position_notional", f"{cfg.max_position_notional_usdt:.2f}")
    checklist.add_row("protective_mode", str(cfg.protective_missing_policy))
    checklist.add_row("quiet_hours", str(cfg.quiet_hours or "-"))
    console.print(checklist)

    warnings = _sleep_mode_warnings(cfg)
    if warnings:
        warning_table = Table(title="Strong Warnings")
        warning_table.add_column("warning")
        for w in warnings:
            warning_table.add_row(w)
        console.print(warning_table)
    else:
        console.print("[green]No high-risk warning detected for current sleep profile.[/green]")


@app.command()
def doctor(
    env: str = typer.Option("testnet", "--env", help="mainnet | testnet"),
    symbol: str | None = typer.Option(None, help="Symbol for filter validation (default: SYMBOL env)"),
) -> None:
    requested_env = env.strip().lower()
    if requested_env not in {"mainnet", "testnet"}:
        raise typer.BadParameter("--env must be one of: mainnet, testnet")

    setup_logging()
    cfg = AppConfig.from_env(binance_env_override=requested_env)  # type: ignore[arg-type]
    target_symbol = symbol or cfg.symbol
    is_testnet = requested_env == "testnet"
    has_whitespace = bool(cfg.binance_api_key_has_whitespace or cfg.binance_api_secret_has_whitespace)
    contains_newline = bool(cfg.binance_api_key_contains_newline or cfg.binance_api_secret_contains_newline)

    diag = Table(title=f"Doctor ({requested_env}) - key diagnostics (masked)")
    diag.add_column("item")
    diag.add_column("value")
    diag.add_row("env", requested_env)
    diag.add_row("cwd", str(Path.cwd()))
    diag.add_row("env_file_used", str(cfg.env_file_used or "-"))
    diag.add_row("key_source", str(cfg.binance_api_key_source))
    diag.add_row("key_source_origin", str(cfg.binance_api_key_source_origin))
    diag.add_row("key_len", str(cfg.binance_api_key_len))
    diag.add_row("key_prefix", str(cfg.binance_api_key_prefix or "-"))
    diag.add_row("secret_source", str(cfg.binance_api_secret_source))
    diag.add_row("secret_source_origin", str(cfg.binance_api_secret_source_origin))
    diag.add_row("secret_len", str(cfg.binance_api_secret_len))
    diag.add_row("has_whitespace", str(has_whitespace))
    diag.add_row(
        "has_whitespace(detail)",
        f"key={cfg.binance_api_key_has_whitespace} secret={cfg.binance_api_secret_has_whitespace}",
    )
    diag.add_row("contains_newline", str(contains_newline))
    diag.add_row(
        "contains_newline(detail)",
        f"key={cfg.binance_api_key_contains_newline} secret={cfg.binance_api_secret_contains_newline}",
    )
    diag.add_row("looks_like_hmac", str(cfg.binance_api_secret_looks_like_hmac))
    console.print(diag)

    broker = LiveBinanceBroker(
        api_key=cfg.binance_api_key.get_secret_value() if cfg.binance_api_key else "",
        api_secret=cfg.binance_api_secret.get_secret_value() if cfg.binance_api_secret else "",
        testnet=is_testnet,
        live_trading=False,
        use_user_stream=False,
    )
    balance_snapshot: dict[str, Any] | None = None
    balance_snapshot_error = ""
    try:
        ok, checks = broker.preflight_check(
            symbol=target_symbol,
            max_time_drift_ms=cfg.preflight_max_time_drift_ms,
            expected_leverage=cfg.leverage,
            expected_margin_mode=cfg.expected_margin_mode,
            include_leverage_margin=False,
        )
        try:
            raw_snapshot = broker.get_account_budget_snapshot(quote_asset="USDT")
            if isinstance(raw_snapshot, dict):
                balance_snapshot = raw_snapshot
        except Exception as exc:
            balance_snapshot_error = str(exc)
    finally:
        broker.close()

    if balance_snapshot is not None:
        account_total = float(balance_snapshot.get("account_total_usdt", balance_snapshot.get("total_balance", 0.0)) or 0.0)
        account_available = float(
            balance_snapshot.get("account_available_usdt", balance_snapshot.get("available_balance", 0.0)) or 0.0
        )
        endpoint_used = str(balance_snapshot.get("endpoint_used", "/fapi/v2/balance"))
        checks.append(
            {
                "event_type": "preflight_balance",
                "check": "futures_balance_snapshot",
                "ok": True,
                "detail": (
                    f"account_total_usdt={account_total:.8f} "
                    f"account_available_usdt={account_available:.8f} "
                    f"endpoint_used={endpoint_used}"
                ),
            }
        )
    elif balance_snapshot_error:
        checks.append(
            {
                "event_type": "preflight_balance",
                "check": "futures_balance_snapshot",
                "ok": False,
                "detail": f"balance snapshot fetch failed: {balance_snapshot_error}",
            }
        )

    table = Table(title=f"Doctor ({requested_env}) - auth/time/symbol checks")
    table.add_column("event")
    table.add_column("check")
    table.add_column("ok")
    table.add_column("detail")
    for row in checks:
        if not isinstance(row, dict):
            table.add_row("preflight_check", "-", "-", str(row))
            continue
        event_type = str(row.get("event_type", "preflight_check"))
        check_name = str(row.get("check", "-"))
        ok_text = "yes" if bool(row.get("ok", False)) else "no"
        detail = str(row.get("detail", "-"))
        if event_type == "preflight_environment":
            detail = (
                f"BINANCE_ENV={row.get('binance_env')} "
                f"base_url={row.get('base_url')} ws_url={row.get('ws_url')}"
            )
        elif event_type == "preflight_credentials":
            detail = (
                f"api_key_present={row.get('api_key_present')} api_key_len={row.get('api_key_len')} "
                f"api_secret_present={row.get('api_secret_present')} api_secret_len={row.get('api_secret_len')}"
            )
        elif event_type == "preflight_endpoint":
            endpoint = row.get("endpoint", "-")
            status = row.get("http_status", "unknown")
            detail = f"endpoint={endpoint} http_status={status}"
            if row.get("error_code") is not None:
                detail += f" error_code={row.get('error_code')}"
            base_detail = str(row.get("detail", "")).strip()
            if base_detail:
                detail += f" ({base_detail})"
        elif event_type == "preflight_auth_guidance":
            guide = row.get("guide")
            if isinstance(guide, list):
                detail = " | ".join(str(item) for item in guide)
        table.add_row(event_type, check_name, ok_text, detail)
    console.print(table)

    if not ok:
        has_2014 = any(
            isinstance(row, dict)
            and str(row.get("event_type", "")) == "preflight_endpoint"
            and int(row.get("error_code", 0) or 0) == -2014
            for row in checks
        )
        if has_2014:
            hint = Table(title="Doctor Hint -2014 (key format invalid)")
            hint.add_column("check")
            hint.add_row("If key_source_origin=process_env, clear BINANCE_TESTNET_API_KEY/SECRET from shell")
            hint.add_row("Confirm BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET are used for --env testnet")
            hint.add_row("Remove quotes/spaces/newlines from .env key lines")
            hint.add_row("Ensure project root .env is loaded (run from repo root, or set ENV_FILE explicitly)")
            hint.add_row("Re-issue testnet futures API key/secret if format still fails")
            console.print(hint)
        console.print("[bold red]Doctor failed. Check endpoint/auth diagnostics above.[/bold red]")
        raise typer.Exit(code=1)
    console.print("[green]Doctor passed. No orders were sent.[/green]")


@app.command()
def replay(
    run_id: str | None = typer.Option(None, help="Candidate run_id to replay"),
    from_opt: str | None = typer.Option(None, help="Optimization result file (.csv/.parquet)"),
    top: int = typer.Option(20, min=1, help="Top N rows from --from-opt"),
    export: str | None = typer.Option(None, help="Export directory or report file path"),
) -> None:
    if not run_id and not from_opt:
        raise typer.BadParameter("Either --run-id or --from-opt is required")

    setup_logging()
    cfg = AppConfig.from_env()
    base_bt_cfg = _build_base_backtest_config(cfg)
    storage = SQLiteStorage(cfg.db_path)
    client = BinanceDataClient(testnet=_is_testnet(cfg))
    cache: dict[tuple[str, str, str, str], pd.DataFrame] = {}

    def fetch_cached(symbol: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        key = (symbol, timeframe, start, end)
        if key not in cache:
            cache[key] = client.fetch_ohlcv_range(symbol=symbol, timeframe=timeframe, start=start, end=end)
        return cache[key]

    try:
        if run_id:
            row = storage.get_optimize_result_by_candidate_run_id(run_id)
            if row is None:
                raise typer.BadParameter(f"run_id not found in optimize_results: {run_id}")
            params = json.loads(row["params_json"])
            symbol = row["symbol"]
            timeframe = row["timeframe"] or row.get("run_timeframe") or cfg.timeframe
            start = row["window_start"]
            end = row["window_end"]
            strategy_name = row.get("strategy", "ema_cross")
            candles = fetch_cached(symbol, timeframe, start, end)
            result = run_candidate_backtest(
                candles=candles,
                symbol=symbol,
                timeframe=timeframe,
                strategy_name=strategy_name,
                params=params,
                base_backtest_config=base_bt_cfg,
            )
            print_backtest_report(result, result.summary)
            if export:
                export_dir = Path(export)
                export_dir.mkdir(parents=True, exist_ok=True)
                trades_df = pd.DataFrame([t.__dict__ for t in result.trades])
                equity_df = pd.DataFrame({"equity": result.equity_curve})
                trades_df.to_csv(export_dir / f"{run_id}_replay_trades.csv", index=False)
                equity_df.to_csv(export_dir / f"{run_id}_replay_equity.csv", index=False)
                console.print(f"Exported replay artifacts to {export_dir}")
            return

        source_df = load_result_file(from_opt)
        if source_df.empty:
            console.print("No rows in optimization result file.")
            return
        if "rank" in source_df.columns:
            source_df = source_df.sort_values("rank", ascending=True)
        elif "objective" in source_df.columns:
            source_df = source_df.sort_values("objective", ascending=False)
        source_df = source_df.head(top).reset_index(drop=True)

        comparisons: list[dict[str, Any]] = []
        for _, row in source_df.iterrows():
            params = _parse_params_from_row(row)
            symbol = str(row.get("symbol", cfg.symbol))
            timeframe = str(row.get("timeframe", cfg.timeframe))
            window_start = str(row.get("window_start", ""))
            window_end = str(row.get("window_end", ""))
            if not window_start or not window_end or window_start == "nan" or window_end == "nan":
                continue
            strategy_name = str(row.get("strategy", "ema_cross"))
            primary_metric = str(row.get("primary_metric", "sharpe_like"))
            candles = fetch_cached(symbol, timeframe, window_start, window_end)
            replay_result = run_candidate_backtest(
                candles=candles,
                symbol=symbol,
                timeframe=timeframe,
                strategy_name=strategy_name,
                params=params,
                base_backtest_config=base_bt_cfg,
            )
            orig_obj = float(row.get("objective", float("nan")))
            replay_obj = float(replay_result.summary.get(primary_metric, 0.0))
            comparisons.append(
                {
                    "candidate_run_id": row.get("candidate_run_id", "-"),
                    "symbol": symbol,
                    "metric": primary_metric,
                    "orig_objective": orig_obj,
                    "replay_metric_value": replay_obj,
                    "delta": replay_obj - orig_obj if pd.notna(orig_obj) else float("nan"),
                }
            )

        report_df = pd.DataFrame(comparisons)
        console.print(report_df.to_string(index=False))
        if export:
            export_path = Path(export)
            export_path.parent.mkdir(parents=True, exist_ok=True)
            if export_path.suffix.lower() == ".parquet":
                report_df.to_parquet(export_path, index=False)
            else:
                report_df.to_csv(export_path, index=False)
            console.print(f"Exported replay comparison: {export_path}")
    finally:
        client.close()
        storage.close()


@app.command()
def paper(
    symbol: str = typer.Option("BTC/USDT", help="Market symbol, e.g. BTC/USDT"),
    timeframe: str = typer.Option("1h", help="Candle timeframe"),
    limit: int = typer.Option(300, min=100, help="Number of candles to fetch"),
    starting_cash: float = typer.Option(10_000.0, min=100.0),
    trade_notional: float = typer.Option(1_000.0, min=10.0),
) -> None:
    setup_logging()
    cfg = AppConfig.from_env().model_copy(update={"symbol": symbol, "timeframe": timeframe})
    strategy = EMACrossStrategy(
        short_window=cfg.short_window,
        long_window=cfg.long_window,
        allow_short=False,
        stop_loss_pct=cfg.ema_stop_loss_pct,
        take_profit_pct=cfg.ema_take_profit_pct,
    )
    broker = PaperBroker(starting_cash=starting_cash)
    client = BinanceDataClient(testnet=_is_testnet(cfg))
    try:
        candles = client.fetch_ohlcv(symbol=cfg.symbol, timeframe=cfg.timeframe, limit=limit)
    finally:
        client.close()

    for row in candles.itertuples(index=False):
        close_price = float(row.close)
        broker.update_market_price(cfg.symbol, close_price)
        signal = strategy.on_bar(
            Bar(
                timestamp=row.timestamp,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=close_price,
                volume=float(row.volume),
            )
        )
        if signal in {"long", "buy"}:
            qty = trade_notional / close_price
            broker.place_order(OrderRequest(symbol=cfg.symbol, side="buy", amount=qty))
        elif signal in {"exit", "sell"}:
            pos = broker.get_position(cfg.symbol)
            qty = abs(pos.qty)
            if qty > 0:
                broker.place_order(OrderRequest(symbol=cfg.symbol, side="sell", amount=qty))

    console.print(broker.get_balance())


@app.command()
def live(
    symbol: str = typer.Option("BTC/USDT", help="Market symbol, e.g. BTC/USDT"),
    side: str = typer.Option("buy", help="Order side: buy/sell"),
    amount: float = typer.Option(..., min=0.0001, help="Order amount"),
    price: float | None = typer.Option(None, help="If provided, submit a limit order"),
    yes_i_understand_live_risk: bool = typer.Option(
        False,
        "--yes-i-understand-live-risk",
        help="Explicit confirmation before live trading",
    ),
) -> None:
    if side not in {"buy", "sell"}:
        raise typer.BadParameter("side must be one of: buy, sell")

    if not yes_i_understand_live_risk:
        console.print(
            "[bold red]Live trading is blocked.[/bold red] "
            "Add --yes-i-understand-live-risk after reviewing your settings."
        )
        raise typer.Exit(code=1)

    cfg = AppConfig.from_env()
    if cfg.binance_env != "testnet":
        raise typer.BadParameter("live command is restricted to testnet only; set BINANCE_ENV=testnet")
    if not cfg.binance_api_key or not cfg.binance_api_secret:
        console.print("[bold red]BINANCE_API_KEY / BINANCE_API_SECRET are required for live mode.[/bold red]")
        raise typer.Exit(code=1)

    broker = LiveBinanceBroker(
        api_key=cfg.binance_api_key.get_secret_value(),
        api_secret=cfg.binance_api_secret.get_secret_value(),
        testnet=_is_testnet(cfg),
        live_trading=cfg.live_trading,
    )
    try:
        result = broker.place_order(
            OrderRequest(
                symbol=symbol,
                side=side,
                amount=amount,
                order_type="limit" if price is not None else "market",
                price=price,
            )
        )
        console.print(result)
    finally:
        broker.close()


@app.command()
def status(
    run_id: str | None = typer.Option(None, help="Runtime run_id"),
    latest: bool = typer.Option(False, "--latest", help="Use latest runtime/backtest run_id"),
    db_path: str | None = typer.Option(None, help="SQLite DB path (default: config DB_PATH)"),
    events: int = typer.Option(10, min=1, help="Recent events to display"),
    errors: int = typer.Option(5, min=1, help="Recent errors to display"),
) -> None:
    def _as_symbol_map(payload: Any, *, default_symbol: str) -> dict[str, dict[str, Any]]:
        if not isinstance(payload, dict):
            return {}
        if "symbol" in payload and isinstance(payload.get("symbol"), str):
            return {str(payload["symbol"]): dict(payload)}
        has_symbol_like_keys = any("/" in str(k) and isinstance(v, dict) for k, v in payload.items())
        if has_symbol_like_keys:
            return {str(k): dict(v) for k, v in payload.items() if isinstance(v, dict)}
        return {default_symbol: dict(payload)}

    cfg = AppConfig.from_env()
    storage = SQLiteStorage(db_path or cfg.db_path)
    try:
        target_run_id = run_id
        if latest or not target_run_id:
            target_run_id = storage.get_latest_run_id()
        if not target_run_id:
            raise typer.BadParameter("No run found. Provide --run-id or run trader first.")

        summary = storage.get_run_status(target_run_id)
        pos_map = _as_symbol_map(summary.get("open_positions") or {}, default_symbol=cfg.symbol)
        risk_map = _as_symbol_map(summary.get("risk_state") or {}, default_symbol=cfg.symbol)
        open_orders_raw = summary.get("open_orders") or {}
        open_orders_map = _as_symbol_map(open_orders_raw, default_symbol=cfg.symbol)
        default_symbol = next(iter(pos_map.keys()), cfg.symbol)
        pos = pos_map.get(default_symbol, {})
        risk_state = risk_map.get(default_symbol, {})

        overview = Table(title=f"Runtime Status: {target_run_id}")
        overview.add_column("key")
        overview.add_column("value")
        overview.add_row("updated_at", str(summary.get("updated_at", "-")))
        overview.add_row("last_bar_ts", str(summary.get("last_bar_ts", "-")))
        overview.add_row("symbols", ",".join(sorted(pos_map.keys())))
        overview.add_row("strategy", str(risk_state.get("strategy", "-")))
        overview.add_row("candidate_profile", str(risk_state.get("candidate_profile", "-")))
        overview.add_row("halted", str(risk_state.get("halted", False)))
        overview.add_row("halt_reason", str(risk_state.get("halt_reason", "")))
        overview.add_row("preset", str(risk_state.get("preset", "-")))
        overview.add_row("sleep_mode", str(risk_state.get("sleep_mode", False)))
        overview.add_row("env", str(risk_state.get("env", "-")))
        overview.add_row("live_trading", str(risk_state.get("live_trading", False)))
        broker_name = str(risk_state.get("broker", "") or "")
        if not broker_name and bool(risk_state.get("live_trading", False)):
            broker_name = "live_binance"
        overview.add_row("broker", broker_name or "-")
        overview.add_row("dry_run", str(risk_state.get("dry_run", False)))
        overview.add_row("account_total_usdt", str(risk_state.get("account_total_usdt", "-")))
        overview.add_row("account_available_usdt", str(risk_state.get("account_available_usdt", "-")))
        overview.add_row("budget_cap_usdt", str(risk_state.get("budget_cap_usdt", risk_state.get("budget_usdt", "-"))))
        overview.add_row("budget_cap_remaining_usdt", str(risk_state.get("budget_cap_remaining_usdt", "-")))
        overview.add_row("budget_cap_source", str(risk_state.get("budget_cap_source", "-")))
        overview.add_row("budget_usdt", str(risk_state.get("budget_usdt", "-")))
        overview.add_row("allocation_pct", str(risk_state.get("allocation_pct", "-")))
        overview.add_row("max_position_notional", str(risk_state.get("max_position_notional", risk_state.get("max_position_notional_usdt", "-"))))
        overview.add_row("min_entry_notional", str(risk_state.get("min_entry_notional", risk_state.get("min_entry_notional_usdt", "-"))))
        overview.add_row("current_exposure_notional", str(risk_state.get("current_exposure_notional", "-")))
        overview.add_row("daily_loss_remaining_usdt", str(risk_state.get("daily_loss_remaining_usdt", "-")))
        overview.add_row("drawdown_pct", str(risk_state.get("drawdown_pct", "-")))
        overview.add_row("max_dd_limit", str(risk_state.get("max_drawdown_pct_limit", "-")))
        overview.add_row("quiet_hours", str(risk_state.get("quiet_hours", "-")))
        overview.add_row("quiet_hours_active", str(risk_state.get("quiet_hours_active", False)))
        overview.add_row("trades", str(summary.get("trades_count", 0)))
        overview.add_row("orders", str(summary.get("orders_count", 0)))
        overview.add_row("fills", str(summary.get("fills_count", 0)))
        overview.add_row("rejected_by_min_notional", str(risk_state.get("rejected_by_min_notional_count", 0)))
        overview.add_row("entry_below_floor_count", str(risk_state.get("min_entry_notional_block_count", 0)))
        overview.add_row("protective_fail_count", str(risk_state.get("protective_fail_count", 0)))
        overview.add_row("net_pnl", f"{float(summary.get('trades_net_pnl', 0.0)):.4f}")
        console.print(overview)

        if int(summary.get("fills_count", 0) or 0) > 0:
            provenance_table = Table(title="Fill Provenance")
            provenance_table.add_column("key")
            provenance_table.add_column("value")
            provenance_table.add_row("user_stream", str(summary.get("fills_from_user_stream_count", 0)))
            provenance_table.add_row("rest_trade_reconcile", str(summary.get("fills_from_rest_reconcile_count", 0)))
            provenance_table.add_row("aggregated_fallback", str(summary.get("fills_from_aggregated_fallback_count", 0)))
            provenance_table.add_row("reconciled_missing_ws", str(summary.get("reconciled_missing_ws_fill_count", 0)))
            provenance_table.add_row("partial_fills", str(summary.get("partial_fills_count", 0)))
            provenance_table.add_row("trade_query_unavailable", str(summary.get("trade_query_unavailable_count", 0)))
            provenance_table.add_row(
                "provenance_consistency",
                str(summary.get("fill_provenance_consistency_pass", False)),
            )
            provenance_table.add_row(
                "breakdown",
                json.dumps(summary.get("fill_provenance_breakdown", {}), ensure_ascii=True, sort_keys=True),
            )
            provenance_table.add_row(
                "partial_fill_audit",
                json.dumps(summary.get("partial_fill_audit_summary", {}), ensure_ascii=True, sort_keys=True),
            )
            console.print(provenance_table)

        strategy_state_map = _as_symbol_map(summary.get("strategy_state") or {}, default_symbol=cfg.symbol)
        strategy_state = strategy_state_map.get(default_symbol, {})
        if strategy_state:
            strategy_table = Table(title="Strategy State")
            strategy_table.add_column("key")
            strategy_table.add_column("value")
            strategy_table.add_row("profile_name", str(strategy_state.get("profile_name", "-")))
            strategy_table.add_row("regime_name", str(strategy_state.get("regime_name", "-")))
            strategy_table.add_row("base_signal", str(strategy_state.get("base_signal", "-")))
            strategy_table.add_row("gated_signal", str(strategy_state.get("gated_signal", "-")))
            strategy_table.add_row("allow_long", str(strategy_state.get("allow_long", "-")))
            strategy_table.add_row("allow_short", str(strategy_state.get("allow_short", "-")))
            strategy_table.add_row("coverage_ratio", str(strategy_state.get("coverage_ratio", "-")))
            strategy_table.add_row("fixed_params", json.dumps(strategy_state.get("fixed_params", {}), ensure_ascii=True, sort_keys=True))
            console.print(strategy_table)

        if risk_map:
            total_exposure = 0.0
            total_realized = 0.0
            min_daily_remaining: float | None = None
            halted_symbols = 0
            for sym, r in risk_map.items():
                if not isinstance(r, dict):
                    continue
                total_exposure += float(r.get("current_exposure_notional", 0.0) or 0.0)
                total_realized += float(r.get("realized_pnl", 0.0) or 0.0)
                remaining = float(r.get("daily_loss_remaining_usdt", 0.0) or 0.0)
                min_daily_remaining = remaining if min_daily_remaining is None else min(min_daily_remaining, remaining)
                if bool(r.get("halted", False)):
                    halted_symbols += 1
            account_table = Table(title="Account Risk Summary")
            account_table.add_column("key")
            account_table.add_column("value")
            account_table.add_row("symbols_total", str(len(risk_map)))
            account_table.add_row("symbols_halted", str(halted_symbols))
            account_table.add_row("exposure_notional_total", f"{total_exposure:.4f}")
            account_table.add_row("realized_pnl_total", f"{total_realized:.4f}")
            account_table.add_row(
                "daily_loss_remaining_min",
                f"{(min_daily_remaining if min_daily_remaining is not None else 0.0):.4f}",
            )
            console.print(account_table)

        if pos_map:
            sym_table = Table(title="Per-Symbol Summary")
            sym_table.add_column("symbol")
            sym_table.add_column("position_qty")
            sym_table.add_column("entry_price")
            sym_table.add_column("open_orders")
            sym_table.add_column("halted")
            sym_table.add_column("halt_reason")
            sym_table.add_column("signal")
            for sym in sorted(pos_map.keys()):
                p = pos_map.get(sym, {})
                r = risk_map.get(sym, {})
                oo = open_orders_map.get(sym, {})
                if not isinstance(oo, dict):
                    oo = {}
                open_count = len([k for k, v in oo.items() if not str(k).startswith("_") and isinstance(v, dict)])
                sym_table.add_row(
                    sym,
                    str(p.get("qty", 0.0)),
                    str(p.get("entry_price", 0.0)),
                    str(open_count),
                    str(r.get("halted", False)),
                    str(r.get("halt_reason", "")),
                    str(r.get("last_signal", "-")),
                )
            console.print(sym_table)

        events_rows = storage.list_recent_events_for_run(target_run_id, limit=events)
        if events_rows:
            event_table = Table(title=f"Recent Events ({events})")
            event_table.add_column("ts")
            event_table.add_column("event_type")
            event_table.add_column("summary")
            for row in events_rows:
                payload = row.get("payload", {})
                if isinstance(payload, dict):
                    slim = {k: payload[k] for k in payload if k not in {"run_id"}}
                    summary_text = json.dumps(slim, default=str)[:120]
                else:
                    summary_text = "-"
                event_table.add_row(str(row.get("ts", "-")), str(row.get("event_type", "-")), summary_text)
            console.print(event_table)

        error_rows = storage.list_recent_errors_for_run(target_run_id, limit=errors)
        if error_rows:
            error_table = Table(title=f"Recent Errors ({errors})")
            error_table.add_column("ts")
            error_table.add_column("event_type")
            error_table.add_column("summary")
            for row in error_rows:
                payload = row.get("payload", {})
                if isinstance(payload, dict):
                    summary_text = json.dumps(payload, default=str)[:140]
                else:
                    summary_text = "-"
                error_table.add_row(str(row.get("ts", "-")), str(row.get("event_type", "-")), summary_text)
            console.print(error_table)
    finally:
        storage.close()


@app.command()
def daemon(
    symbols: str = typer.Option("BTC/USDT", help="Comma-separated symbols, e.g. BTC/USDT,ETH/USDT"),
    strategy: str = typer.Option("ema_cross", help="Strategy: ema_cross, rsi, macd, bollinger"),
    timeframe: str = typer.Option("1m", help="Candle timeframe"),
    initial_equity: float = typer.Option(10_000.0, help="Starting paper equity (USDT)"),
    testnet: bool = typer.Option(True, help="Use Binance testnet"),
    data_dir: str = typer.Option("data", help="Directory for data storage"),
    no_prevent_sleep: bool = typer.Option(False, "--no-prevent-sleep", help="Don't prevent system sleep"),
) -> None:
    """
    Run 24/7 paper trading daemon.

    Continuously monitors real-time data and executes paper trades.
    Prevents system sleep and accumulates market data.
    Press Ctrl+C to stop gracefully.
    """
    from trader.daemon import DaemonConfig, TradingDaemon
    from pathlib import Path

    if strategy not in AVAILABLE_STRATEGIES:
        raise typer.BadParameter(f"Unknown strategy: {strategy}. Available: {', '.join(AVAILABLE_STRATEGIES)}")

    parsed_symbols = _parse_symbols(symbols)

    config = DaemonConfig(
        symbols=parsed_symbols,
        strategy=strategy,
        timeframe=timeframe,
        initial_equity=initial_equity,
        testnet=testnet,
        data_dir=Path(data_dir),
        prevent_sleep=not no_prevent_sleep,
    )

    daemon_instance = TradingDaemon(config)
    daemon_instance.run()


@app.command("compare")
def compare_strategies(
    symbol: str = typer.Option("BTC/USDT", help="Market symbol"),
    timeframe: str = typer.Option("1m", help="Candle timeframe"),
    initial_equity: float = typer.Option(10_000.0, help="Starting equity per strategy (USDT)"),
    testnet: bool = typer.Option(True, help="Use Binance testnet"),
    data_dir: str = typer.Option("data/multi_strategy", help="Directory for results"),
    no_prevent_sleep: bool = typer.Option(False, "--no-prevent-sleep", help="Don't prevent system sleep"),
    leaderboard_interval: int = typer.Option(10, help="Leaderboard display interval (minutes)"),
    save_interval: int = typer.Option(5, help="Data save interval (minutes)"),
) -> None:
    """
    Run multiple strategies simultaneously for comparison.

    Tests a matrix of strategy configurations (EMA, RSI, MACD, Bollinger)
    with different parameters to find the best performer.

    Results are saved to data_dir with:
    - leaderboard.csv: Ranked strategy performance
    - strategies/: Detailed results per strategy
    - market_data.parquet: Collected price data
    """
    from trader.multi_strategy_daemon import MultiStrategyConfig, MultiStrategyDaemon
    from pathlib import Path

    config = MultiStrategyConfig(
        symbol=symbol,
        timeframe=timeframe,
        initial_equity=initial_equity,
        testnet=testnet,
        data_dir=Path(data_dir),
        prevent_sleep=not no_prevent_sleep,
        leaderboard_interval_minutes=leaderboard_interval,
        save_interval_minutes=save_interval,
    )

    daemon = MultiStrategyDaemon(config)
    daemon.run()


@app.command("backtest-compare")
def backtest_compare(
    symbol: str = typer.Option("BTC/USDT", help="Market symbol"),
    timeframe: str = typer.Option("1m", help="Candle timeframe"),
    days: int = typer.Option(365, help="Number of days of historical data"),
    initial_equity: float = typer.Option(10_000.0, help="Starting equity per strategy (USDT)"),
    data_dir: str = typer.Option("data/backtest", help="Directory for results"),
) -> None:
    """
    Backtest multiple strategies on historical data.

    Downloads historical data from Binance and tests all 44 strategy
    configurations to find the best performer. Much faster than real-time
    testing - can test years of data in minutes.

    Examples:
        # Test 1 year of data
        python main.py backtest-compare --days 365

        # Test 3 years of ETH data
        python main.py backtest-compare --symbol ETH/USDT --days 1095

    Results are saved to data_dir with:
    - leaderboard.csv: Ranked strategy performance
    - strategies/: Detailed results per strategy
    """
    from trader.backtest_compare import BacktestConfig, MultiStrategyBacktester
    from pathlib import Path

    config = BacktestConfig(
        symbol=symbol,
        timeframe=timeframe,
        days=days,
        initial_equity=initial_equity,
        data_dir=Path(data_dir),
    )

    backtester = MultiStrategyBacktester(config)
    backtester.run()


@app.command("download-data")
def download_data(
    symbols: str = typer.Option("BTC/USDT,ETH/USDT,XRP/USDT", help="Comma-separated symbols"),
    timeframe: str = typer.Option("1m", help="Candle timeframe"),
    days: int = typer.Option(1095, help="Number of days to download (default: 3 years)"),
    cache_dir: str = typer.Option("data/historical", help="Directory to save data"),
) -> None:
    """
    Download and cache historical data from Binance.

    Downloads historical kline data for multiple symbols and saves to CSV cache.
    This data is used by backtest-compare for fast offline backtesting.

    Examples:
        # Download 3 years of BTC, ETH, XRP data (default)
        python main.py download-data

        # Download 1 year of specific symbols
        python main.py download-data --symbols BTC/USDT,ETH/USDT --days 365

        # Download 5 years of BTC only
        python main.py download-data --symbols BTC/USDT --days 1825
    """
    from trader.data.historical import download_multiple_symbols

    parsed_symbols = [s.strip() for s in symbols.split(",") if s.strip()]

    if not parsed_symbols:
        raise typer.BadParameter("At least one symbol is required")

    download_multiple_symbols(
        symbols=parsed_symbols,
        timeframe=timeframe,
        days=days,
        cache_dir=cache_dir,
    )


@app.command("futures-backtest")
def futures_backtest(
    symbol: str = typer.Option("BTCUSDT", help="Futures symbol"),
    data_dir: str = typer.Option("data/futures", help="Data directory"),
    output_dir: str = typer.Option("data/futures_backtest", help="Output directory"),
    initial_equity: float = typer.Option(10_000.0, help="Initial equity (USDT)"),
    timeframes: str = typer.Option("5m,15m,1h,4h", help="Comma-separated timeframes"),
    leverages: str = typer.Option("1,2,3,5,10", help="Comma-separated leverage values"),
) -> None:
    """
    Run comprehensive futures backtesting.

    Tests ALL combinations of:
    - 4 strategies (EMA, RSI, MACD, Bollinger)
    - Multiple parameters per strategy
    - Multiple timeframes (5m, 15m, 1h, 4h)
    - Multiple leverages (1x, 2x, 3x, 5x, 10x)
    - Long-only vs Long+Short
    - Multiple SL/TP combinations

    Includes futures-specific features:
    - Funding rate costs (8h intervals)
    - Leverage and margin simulation
    - Liquidation simulation

    Results saved to output_dir/results.csv

    Example:
        python main.py futures-backtest --timeframes 1h,4h --leverages 1,3,5
    """
    from trader.futures_backtest import run_futures_backtest

    run_futures_backtest(
        symbol=symbol,
        data_dir=data_dir,
        output_dir=output_dir,
        initial_equity=initial_equity,
        timeframes=timeframes,
        leverages=leverages,
    )


@app.command("mtf-backtest")
def mtf_backtest(
    symbol: str = typer.Option("BTCUSDT", help="Futures symbol"),
    days: int = typer.Option(365, help="Days of data to backtest"),
    data_dir: str = typer.Option("data/futures", help="Data directory"),
    output_dir: str = typer.Option("data/futures/mtf_results", help="Output directory"),
    leverages: str = typer.Option("1,3,5,10", help="Comma-separated leverage values"),
) -> None:
    """
    Run Multi-Timeframe (MTF) futures backtesting.

    This is the MOST REALISTIC backtesting approach:
    - Uses 1m as base, calculates 5m/15m/1h/4h in real-time
    - Multiple timeframe confirmation for entries/exits
    - Higher timeframe for trend, lower for entry timing

    MTF Strategies included:
    - TrendFollow_MTF: 4h trend + 1h pullback + 15m entry
    - MomentumBreakout_MTF: BB squeeze + volume breakout
    - MACDDivergence_MTF: 1h divergence + 15m confirmation
    - RSIMeanReversion_MTF: Extreme RSI + mean reversion
    - AdaptiveTrend_MTF: ADX-based mode switching

    Features:
    - Next-bar execution (no lookahead bias)
    - Stop loss / Take profit / Trailing stop
    - Funding rate costs
    - Liquidation simulation

    Example:
        python main.py mtf-backtest --days 365 --leverages 3,5,10
    """
    from trader.mtf_backtest import run_mtf_backtest

    leverage_list = [int(x.strip()) for x in leverages.split(",") if x.strip()]

    run_mtf_backtest(
        symbol=symbol,
        days=days,
        leverages=leverage_list,
        data_dir=data_dir,
        output_dir=output_dir,
    )


@app.command("mtf-ml")
def mtf_ml(
    symbol: str = typer.Option("BTCUSDT", help="Futures symbol"),
    days: int = typer.Option(90, help="Days of data"),
    strategy: str = typer.Option("TrendFollow", help="Strategy: TrendFollow, MACDDivergence, MomentumBreakout, RSIMeanReversion"),
    trials: int = typer.Option(50, help="Number of optimization trials"),
    leverage: int = typer.Option(3, help="Leverage"),
    data_dir: str = typer.Option("data/futures", help="Data directory"),
    output_dir: str = typer.Option("data/futures/ml_optimization", help="Output directory"),
) -> None:
    """
    Machine Learning optimization for MTF strategies.

    Uses Bayesian optimization to find optimal parameters:
    - Explores parameter space intelligently
    - Balances exploration vs exploitation
    - Composite objective: Sharpe + WinRate - Drawdown

    Example:
        python main.py mtf-ml --strategy TrendFollow --trials 50
    """
    from trader.mtf_advanced import run_ml_optimization

    run_ml_optimization(
        symbol=symbol,
        days=days,
        strategy_name=strategy,
        n_trials=trials,
        leverage=leverage,
        data_dir=data_dir,
        output_dir=output_dir,
    )


@app.command("mtf-walkforward")
def mtf_walkforward(
    symbol: str = typer.Option("BTCUSDT", help="Futures symbol"),
    strategy: str = typer.Option("TrendFollow", help="Strategy name"),
    train_days: int = typer.Option(60, help="Training window days"),
    test_days: int = typer.Option(30, help="Testing window days"),
    trials: int = typer.Option(30, help="Optimization trials per window"),
    leverage: int = typer.Option(3, help="Leverage"),
    data_dir: str = typer.Option("data/futures", help="Data directory"),
    output_dir: str = typer.Option("data/futures/walk_forward", help="Output directory"),
) -> None:
    """
    Walk-forward validation to prevent overfitting.

    Process:
    1. Split data into rolling train/test windows
    2. Optimize on train, validate on test (out-of-sample)
    3. Roll forward and repeat
    4. Aggregate results across all windows

    This is the GOLD STANDARD for validating trading strategies.
    If a strategy works in walk-forward, it's more likely to work live.

    Example:
        python main.py mtf-walkforward --strategy TrendFollow --train-days 60 --test-days 30
    """
    from trader.mtf_advanced import run_walk_forward

    run_walk_forward(
        symbol=symbol,
        strategy_name=strategy,
        train_days=train_days,
        test_days=test_days,
        n_trials=trials,
        leverage=leverage,
        data_dir=data_dir,
        output_dir=output_dir,
    )


@app.command("mtf-optimize")
def mtf_optimize(
    symbol: str = typer.Option("BTCUSDT", help="Futures symbol"),
    days: int = typer.Option(90, help="Days of data to optimize on"),
    data_dir: str = typer.Option("data/futures", help="Data directory"),
    output_dir: str = typer.Option("data/futures/optimization", help="Output directory"),
    leverages: str = typer.Option("3,5", help="Comma-separated leverage values"),
) -> None:
    """
    Optimize MTF strategies with grid search.

    Features:
    1. Grid search over strategy parameters
    2. Market regime detection (trending/ranging/volatile)
    3. Best strategy selection per regime
    4. Risk parameter optimization (SL/TP/holding period)

    Tests ALL combinations of:
    - Strategy parameters (ADX thresholds, RSI levels, etc.)
    - Risk parameters (stop loss, take profit, min holding)
    - Leverage levels

    Output:
    - optimization_*.csv: All results ranked by Sharpe ratio
    - regime_best_*.json: Best strategy for each market regime

    Example:
        python main.py mtf-optimize --days 90 --leverages 3,5
    """
    from trader.mtf_optimizer import run_mtf_optimization

    leverage_list = [int(x.strip()) for x in leverages.split(",") if x.strip()]

    run_mtf_optimization(
        symbol=symbol,
        days=days,
        leverages=leverage_list,
        data_dir=data_dir,
        output_dir=output_dir,
    )


@app.command("download-futures")
def download_futures(
    symbols: str = typer.Option("BTCUSDT,ETHUSDT", help="Comma-separated futures symbols (no slash)"),
    days: int = typer.Option(365, help="Number of days to download"),
    base_dir: str = typer.Option("data/futures", help="Output directory"),
    delay: float = typer.Option(0.25, help="Delay between requests in seconds (increase if rate limited)"),
    force: bool = typer.Option(False, "--force", help="Force re-download even if cache exists"),
    include_trades: bool = typer.Option(False, "--include-trades", help="Include aggTrades (very heavy)"),
    skip_ohlcv: bool = typer.Option(False, "--skip-ohlcv", help="Skip OHLCV download"),
    skip_funding: bool = typer.Option(False, "--skip-funding", help="Skip funding rate"),
    skip_mark: bool = typer.Option(False, "--skip-mark", help="Skip mark price"),
    skip_index: bool = typer.Option(False, "--skip-index", help="Skip index price"),
    skip_oi: bool = typer.Option(False, "--skip-oi", help="Skip open interest"),
    skip_ratio: bool = typer.Option(False, "--skip-ratio", help="Skip long/short ratio"),
) -> None:
    """
    Download USDT-M Futures data from Binance FAPI.

    Downloads comprehensive futures data for realistic backtesting:
    - OHLCV (1m klines, auto-resampled to 5m/15m/1h/4h)
    - Funding Rate (8h intervals)
    - Mark Price Klines (for liquidation simulation)
    - Index Price Klines (weighted spot average)
    - Open Interest History (market sentiment)
    - Long/Short Ratio (positioning data)
    - Aggregated Trades (optional, for slippage modeling)

    Data is saved in 3-tier structure:
    - raw/: Original API responses (CSV)
    - clean/: Validated & processed (Parquet)
    - meta/: Exchange info & manifests (JSON)

    Examples:
        # Download 1 year of BTC and ETH futures data
        python main.py download-futures --days 365

        # Download 6 months with aggregated trades
        python main.py download-futures --days 180 --include-trades

        # Quick download (OHLCV + funding only)
        python main.py download-futures --skip-mark --skip-index --skip-oi --skip-ratio
    """
    from pathlib import Path
    from trader.data.futures_data import FuturesDataConfig, FuturesDataDownloader

    parsed_symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    if not parsed_symbols:
        raise typer.BadParameter("At least one symbol is required")

    config = FuturesDataConfig(
        symbols=parsed_symbols,
        days=days,
        base_dir=Path(base_dir),
        request_delay=delay,
        force_download=force,
        download_ohlcv=not skip_ohlcv,
        download_funding=not skip_funding,
        download_mark_price=not skip_mark,
        download_index_price=not skip_index,
        download_open_interest=not skip_oi,
        download_long_short_ratio=not skip_ratio,
        download_exchange_info=True,
        download_agg_trades=include_trades,
    )

    downloader = FuturesDataDownloader(config)
    downloader.download_all()


@app.command("funding-download")
def funding_download(
    symbols: str = typer.Option(
        "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT",
        help="Comma-separated futures symbols"
    ),
    force_full: bool = typer.Option(False, "--force", help="Force full 30-day download (ignore existing)"),
) -> None:
    """
    Download and accumulate Funding Rate data from Binance.

    Funding rates are settled every 8 hours (00:00, 08:00, 16:00 UTC).
    Binance API only provides last 30 days, so run regularly to accumulate history.

    Data is saved to data/futures/funding/{SYMBOL}_funding.parquet
    Each run merges new data with existing, avoiding duplicates.

    Examples:
        # Download default 10 symbols
        python main.py funding-download

        # Download specific symbols
        python main.py funding-download --symbols BTCUSDT,ETHUSDT

        # Force full re-download
        python main.py funding-download --force
    """
    from trader.funding_rate import run_download

    parsed_symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    run_download(symbols=parsed_symbols, force_full=force_full)


@app.command("funding-analyze")
def funding_analyze(
    symbols: str = typer.Option(
        "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT",
        help="Comma-separated futures symbols"
    ),
) -> None:
    """
    Analyze accumulated Funding Rate data.

    Shows:
    - Mean funding rate (overall, 7-day, 30-day)
    - Annual return estimate (rate * 3 * 365)
    - Positive rate ratio (stability indicator)
    - Recommended symbols for arbitrage

    A positive funding rate means LONG pays SHORT.
    For arbitrage (spot long + futures short), you want positive rates.

    Examples:
        python main.py funding-analyze
        python main.py funding-analyze --symbols BTCUSDT,ETHUSDT
    """
    from trader.funding_rate import run_analyze

    parsed_symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    run_analyze(symbols=parsed_symbols)


@app.command("funding-backtest")
def funding_backtest(
    symbol: str = typer.Option("BTCUSDT", help="Futures symbol"),
    initial_capital: float = typer.Option(10000.0, help="Initial capital (USDT)"),
) -> None:
    """
    Backtest Funding Rate arbitrage strategy.

    Strategy:
    1. BUY spot BTC + SHORT futures BTC (equal size)
    2. Price movement is hedged (delta neutral)
    3. Collect funding rate every 8 hours
    4. Close position when funding goes negative

    Returns:
    - Total return and annualized return
    - Funding received vs fees paid
    - Max drawdown
    - Sharpe ratio

    Example:
        python main.py funding-backtest --symbol BTCUSDT --initial-capital 10000
    """
    from trader.funding_rate import run_backtest

    run_backtest(symbol=symbol, initial_capital=initial_capital)


@app.command("funding-monitor")
def funding_monitor() -> None:
    """
    Monitor real-time Funding Rate opportunities.

    Shows top 15 symbols ranked by annualized return:
    - Current funding rate
    - Estimated annual return
    - Next funding settlement time

    Use this to find the best symbols for arbitrage entry.

    Example:
        python main.py funding-monitor
    """
    from trader.funding_rate import run_monitor

    run_monitor()


@app.command("matrix-backtest")
def matrix_backtest(
    symbol: str = typer.Option("BTCUSDT", help="Futures symbol"),
    output_dir: str = typer.Option("data/matrix_results", help="Output directory"),
) -> None:
    """
    Run comprehensive matrix backtest across strategies, leverages, and timeframes.

    Tests ALL combinations of:
    - Strategies: TrendFollow, Momentum, VolBreakout, MeanReversion
    - Leverages: 1x, 2x, 3x, 5x, 7x, 10x
    - Timeframes: 15m, 1h, 4h
    - SL/TP ratios: 1:2, 1:3 combinations
    - Max daily trades: 2, 3, 5

    Goal: Find configurations achieving 30-50% annual return with manageable risk.

    Output:
    - matrix_results_*.csv: All results
    - Analysis summary printed to console

    Example:
        python main.py matrix-backtest --symbol BTCUSDT

    Note: This may take 10-30 minutes depending on data size.
    """
    from trader.matrix_backtest import run_matrix_backtest

    run_matrix_backtest(symbol=symbol, output_dir=output_dir)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
