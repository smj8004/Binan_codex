from __future__ import annotations

import json
import math
import ast
import itertools
from dataclasses import dataclass, replace
from datetime import timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import numpy as np
import pandas as pd

from trader.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from trader.strategy.base import Bar, Strategy, StrategyPosition
from trader.strategy.bollinger import BollingerBandStrategy
from trader.strategy.ema_cross import EMACrossStrategy
from trader.strategy.macd import MACDStrategy
from trader.strategy.rsi import RSIStrategy
from trader.strategy import STRATEGY_FACTORIES

from .report import (
    save_bar_chart,
    save_dataframe_csv,
    save_histogram,
    save_json,
    save_line_chart,
    write_markdown_report,
)


DataSource = Literal["binance", "csv", "synthetic"]


@dataclass(frozen=True)
class EdgeRunOutput:
    run_id: str
    run_dir: Path
    summary: dict[str, Any]
    files: dict[str, str]


@dataclass(frozen=True)
class RiskTemplateConfig:
    name: str
    trailing_stop_pct: float = 0.0
    time_stop_bars: int = 0
    partial_take_profit_pct: float = 0.0
    partial_take_fraction: float = 0.0
    vol_target_lookback: int = 60
    vol_target_ratio: float = 0.7
    min_size_mult: float = 0.3
    max_size_mult: float = 1.2


RISK_TEMPLATES: dict[str, RiskTemplateConfig] = {
    "balanced": RiskTemplateConfig(
        name="balanced",
        trailing_stop_pct=0.012,
        time_stop_bars=96,
        partial_take_profit_pct=0.018,
        partial_take_fraction=0.50,
        vol_target_lookback=96,
        vol_target_ratio=0.80,
        min_size_mult=0.35,
        max_size_mult=1.00,
    ),
    "defensive": RiskTemplateConfig(
        name="defensive",
        trailing_stop_pct=0.009,
        time_stop_bars=72,
        partial_take_profit_pct=0.014,
        partial_take_fraction=0.40,
        vol_target_lookback=120,
        vol_target_ratio=0.65,
        min_size_mult=0.25,
        max_size_mult=0.90,
    ),
    "aggressive": RiskTemplateConfig(
        name="aggressive",
        trailing_stop_pct=0.018,
        time_stop_bars=144,
        partial_take_profit_pct=0.025,
        partial_take_fraction=0.33,
        vol_target_lookback=72,
        vol_target_ratio=0.95,
        min_size_mult=0.40,
        max_size_mult=1.20,
    ),
}


class RiskTemplateWrapper(Strategy):
    def __init__(self, *, base: Strategy, template: RiskTemplateConfig):
        self.base = base
        self.template = template
        self._bar_index = 0
        self._entry_bar_index: int | None = None
        self._high_watermark: float = 0.0
        self._low_watermark: float = 0.0
        self._partial_done = False
        self._pending_partial_fraction = 0.0
        self._returns: list[float] = []
        self._prev_close: float | None = None

    def _reset_trade_state(self) -> None:
        self._entry_bar_index = None
        self._high_watermark = 0.0
        self._low_watermark = 0.0
        self._partial_done = False
        self._pending_partial_fraction = 0.0

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> Literal["long", "short", "exit", "hold", "buy", "sell"]:
        self._bar_index += 1
        if self._prev_close is not None and self._prev_close > 0:
            self._returns.append((bar.close - self._prev_close) / self._prev_close)
            if len(self._returns) > 400:
                self._returns.pop(0)
        self._prev_close = bar.close

        if position is None or position.side == "flat":
            self._reset_trade_state()
            return self.base.on_bar(bar, position)

        if self._entry_bar_index is None:
            self._entry_bar_index = self._bar_index
            self._high_watermark = bar.close
            self._low_watermark = bar.close
            self._partial_done = False

        self._high_watermark = max(self._high_watermark, bar.close)
        self._low_watermark = min(self._low_watermark, bar.close)

        if self.template.time_stop_bars > 0 and (self._bar_index - self._entry_bar_index) >= self.template.time_stop_bars:
            return "exit"

        if self.template.trailing_stop_pct > 0:
            if position.side == "long":
                trail_price = self._high_watermark * (1.0 - self.template.trailing_stop_pct)
                if bar.close <= trail_price:
                    return "exit"
            elif position.side == "short":
                trail_price = self._low_watermark * (1.0 + self.template.trailing_stop_pct)
                if bar.close >= trail_price:
                    return "exit"

        if (
            (not self._partial_done)
            and self.template.partial_take_profit_pct > 0
            and self.template.partial_take_fraction > 0
            and position.entry_price > 0
        ):
            if position.side == "long":
                pnl_pct = (bar.close - position.entry_price) / position.entry_price
            else:
                pnl_pct = (position.entry_price - bar.close) / position.entry_price
            if pnl_pct >= self.template.partial_take_profit_pct:
                self._partial_done = True
                self._pending_partial_fraction = self.template.partial_take_fraction

        return self.base.on_bar(bar, position)

    def partial_exit_fraction(self, bar: Bar, position: StrategyPosition | None = None) -> float:
        value = self._pending_partial_fraction
        self._pending_partial_fraction = 0.0
        return value

    def size_multiplier(self, bar: Bar, position: StrategyPosition | None = None) -> float:
        lookback = max(self.template.vol_target_lookback, 20)
        if len(self._returns) < lookback:
            return 1.0
        recent = np.asarray(self._returns[-lookback:], dtype=float)
        vol = float(np.std(recent))
        if vol <= 1e-12:
            return 1.0
        scale = self.template.vol_target_ratio / vol
        return min(max(scale, self.template.min_size_mult), self.template.max_size_mult)


class RegimeSwitchStrategy(Strategy):
    def __init__(
        self,
        *,
        trend_strategy: Strategy,
        range_strategy: Strategy,
        trend_ema_span: int = 48,
        slope_lookback: int = 8,
        slope_threshold: float = 0.0015,
        vol_lookback: int = 96,
        high_vol_size_mult: float = 0.70,
        low_vol_size_mult: float = 1.00,
    ) -> None:
        self.trend_strategy = trend_strategy
        self.range_strategy = range_strategy
        self.trend_ema_span = trend_ema_span
        self.slope_lookback = slope_lookback
        self.slope_threshold = slope_threshold
        self.vol_lookback = vol_lookback
        self.high_vol_size_mult = high_vol_size_mult
        self.low_vol_size_mult = low_vol_size_mult
        self._prices: list[float] = []
        self._returns: list[float] = []
        self._last_regime = "range|low_vol"

    def _regime(self) -> str:
        if len(self._prices) < max(self.trend_ema_span + self.slope_lookback + 2, self.vol_lookback + 2):
            return "range|low_vol"
        series = pd.Series(self._prices, dtype="float64")
        ema = series.ewm(span=self.trend_ema_span, adjust=False).mean()
        slope = (ema.iloc[-1] - ema.iloc[-1 - self.slope_lookback]) / max(abs(ema.iloc[-1 - self.slope_lookback]), 1e-9)
        trend = "trend" if abs(float(slope)) >= self.slope_threshold else "range"
        vol = float(np.std(np.asarray(self._returns[-self.vol_lookback :], dtype=float)))
        vol_ref = float(np.std(np.asarray(self._returns[-(self.vol_lookback * 2) :], dtype=float))) if len(self._returns) >= self.vol_lookback * 2 else vol
        vol_label = "high_vol" if vol_ref > 0 and vol >= vol_ref else "low_vol"
        return f"{trend}|{vol_label}"

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> Literal["long", "short", "exit", "hold", "buy", "sell"]:
        if self._prices:
            prev = self._prices[-1]
            if prev > 0:
                self._returns.append((bar.close - prev) / prev)
                if len(self._returns) > 1000:
                    self._returns.pop(0)
        self._prices.append(bar.close)
        regime = self._regime()
        self._last_regime = regime
        if regime.startswith("trend|"):
            return self.trend_strategy.on_bar(bar, position)
        return self.range_strategy.on_bar(bar, position)

    def size_multiplier(self, bar: Bar, position: StrategyPosition | None = None) -> float:
        if self._last_regime.endswith("high_vol"):
            return self.high_vol_size_mult
        return self.low_vol_size_mult


def _timeframe_to_seconds(timeframe: str) -> int:
    raw = timeframe.strip().lower()
    if raw.endswith("m"):
        return int(raw[:-1]) * 60
    if raw.endswith("h"):
        return int(raw[:-1]) * 3600
    if raw.endswith("d"):
        return int(raw[:-1]) * 86400
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _to_utc_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _ts_key(value: str | pd.Timestamp) -> str:
    return _to_utc_timestamp(value).isoformat()


def _ensure_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {missing}")
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = out[col].astype(float)
    out = out.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return out


def _generate_synthetic_ohlcv(
    *,
    timeframe: str,
    start: str,
    end: str,
    seed: int,
    base_price: float = 30_000.0,
) -> pd.DataFrame:
    tf_seconds = _timeframe_to_seconds(timeframe)
    start_ts = _to_utc_timestamp(start)
    end_ts = _to_utc_timestamp(end)
    if end_ts <= start_ts:
        raise ValueError("end must be later than start")
    periods = int((end_ts - start_ts).total_seconds() // tf_seconds)
    periods = max(periods, 200)
    idx = pd.date_range(start=start_ts, periods=periods, freq=pd.Timedelta(seconds=tf_seconds), tz="UTC")
    rng = np.random.default_rng(seed)

    rets = np.zeros(periods, dtype=float)
    chunks = max(periods // 120, 1)
    for c in range(chunks):
        s = c * 120
        e = min((c + 1) * 120, periods)
        drift = rng.normal(0.00002, 0.00015)
        vol = max(rng.uniform(0.0008, 0.006), 1e-6)
        rets[s:e] = drift + rng.normal(0.0, vol, e - s)
    close = np.empty(periods, dtype=float)
    close[0] = base_price
    for i in range(1, periods):
        close[i] = max(close[i - 1] * (1.0 + rets[i]), 100.0)
    open_ = np.r_[close[0], close[:-1]]
    spread = np.maximum(close * (0.0004 + rng.uniform(0.0, 0.0015, periods)), 0.01)
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    vol = rng.uniform(50, 3000, periods) * (1.0 + np.abs(rets) * 1000)
    return pd.DataFrame(
        {
            "timestamp": idx,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
        }
    )


def load_candles(
    *,
    data_source: DataSource,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    seed: int,
    csv_path: str | None = None,
    testnet: bool = False,
) -> pd.DataFrame:
    if data_source == "csv":
        if not csv_path:
            raise ValueError("csv_path is required when data_source=csv")
        df = pd.read_csv(csv_path)
        return _ensure_ohlcv(df)
    if data_source == "synthetic":
        return _generate_synthetic_ohlcv(timeframe=timeframe, start=start, end=end, seed=seed)
    from trader.data.binance import BinanceDataClient

    client = BinanceDataClient(testnet=testnet)
    try:
        fetched = client.fetch_ohlcv_range(symbol=symbol, timeframe=timeframe, start=start, end=end)
    finally:
        client.close()
    return _ensure_ohlcv(fetched)


def _build_strategy(strategy_name: str, params: dict[str, Any]) -> Strategy:
    stop_loss_pct = float(params.get("stop_loss_pct", 0.0))
    take_profit_pct = float(params.get("take_profit_pct", 0.0))
    allow_short = bool(params.get("allow_short", True))
    risk_template_name = str(params.get("risk_template", "") or "").strip().lower()

    if strategy_name == "regime_switch":
        trend_type = str(params.get("trend_strategy_type", "trend:donchian"))
        range_type = str(params.get("range_strategy_type", "meanrev:zscore"))
        trend_params = dict(params.get("trend_params", {}))
        range_params = dict(params.get("range_params", {}))
        trend_params.setdefault("allow_short", allow_short)
        range_params.setdefault("allow_short", allow_short)
        trend_params.setdefault("stop_loss_pct", stop_loss_pct)
        trend_params.setdefault("take_profit_pct", take_profit_pct)
        range_params.setdefault("stop_loss_pct", stop_loss_pct)
        range_params.setdefault("take_profit_pct", take_profit_pct)
        trend_strategy = _build_strategy(trend_type, trend_params)
        range_strategy = _build_strategy(range_type, range_params)
        base = RegimeSwitchStrategy(
            trend_strategy=trend_strategy,
            range_strategy=range_strategy,
            trend_ema_span=int(params.get("trend_ema_span", 48)),
            slope_lookback=int(params.get("trend_slope_lookback", 8)),
            slope_threshold=float(params.get("trend_slope_threshold", 0.0015)),
            vol_lookback=int(params.get("vol_lookback", 96)),
            high_vol_size_mult=float(params.get("high_vol_size_mult", 0.7)),
            low_vol_size_mult=float(params.get("low_vol_size_mult", 1.0)),
        )
        if risk_template_name in RISK_TEMPLATES:
            return RiskTemplateWrapper(base=base, template=RISK_TEMPLATES[risk_template_name])
        return base

    if strategy_name == "ema_cross":
        fast_len = int(params.get("fast_len", params.get("short_window", 12)))
        slow_len = int(params.get("slow_len", params.get("long_window", 26)))
        base: Strategy = EMACrossStrategy(
            short_window=fast_len,
            long_window=slow_len,
            allow_short=allow_short,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )
    elif strategy_name == "rsi":
        base = RSIStrategy(
            period=int(params.get("period", 14)),
            overbought=float(params.get("overbought", 70.0)),
            oversold=float(params.get("oversold", 30.0)),
            allow_short=allow_short,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )
    elif strategy_name == "macd":
        base = MACDStrategy(
            fast_period=int(params.get("fast_period", 12)),
            slow_period=int(params.get("slow_period", 26)),
            signal_period=int(params.get("signal_period", 9)),
            use_histogram=bool(params.get("use_histogram", False)),
            histogram_threshold=float(params.get("histogram_threshold", 0.0)),
            allow_short=allow_short,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )
    elif strategy_name == "bollinger":
        mode = str(params.get("mode", "mean_reversion"))
        if mode not in {"mean_reversion", "breakout"}:
            mode = "mean_reversion"
        base = BollingerBandStrategy(
            period=int(params.get("period", 20)),
            std_dev=float(params.get("std_dev", 2.0)),
            mode=mode,  # type: ignore[arg-type]
            allow_short=allow_short,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )
    elif ":" in strategy_name:
        family, variant = strategy_name.split(":", 1)
        family = family.strip().lower()
        variant = variant.strip()
        factory = STRATEGY_FACTORIES.get(family)
        if factory is None:
            raise ValueError(f"Unsupported strategy family: {family}")
        base = factory(
            variant,
            params,
            allow_short=allow_short,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )
    else:
        raise ValueError(f"Unsupported strategy: {strategy_name}")

    if risk_template_name in RISK_TEMPLATES:
        return RiskTemplateWrapper(base=base, template=RISK_TEMPLATES[risk_template_name])
    return base


def _extract_metrics(result: BacktestResult, candles: pd.DataFrame) -> dict[str, float]:
    summary = result.summary
    initial = float(result.initial_equity)
    final_equity = float(summary.get("final_equity", initial))
    net_pnl = final_equity - initial
    trade_count = int(summary.get("trades", float(len(result.trades))))
    avg_trade = net_pnl / trade_count if trade_count > 0 else 0.0

    start_ts = _to_utc_timestamp(candles["timestamp"].iloc[0])
    end_ts = _to_utc_timestamp(candles["timestamp"].iloc[-1])
    years = max((end_ts - start_ts).total_seconds() / (365.25 * 24 * 3600), 1e-9)
    if initial > 0 and final_equity > 0:
        cagr = (final_equity / initial) ** (1.0 / years) - 1.0
    else:
        cagr = -1.0

    return {
        "final_equity": final_equity,
        "net_pnl": net_pnl,
        "cagr": cagr,
        "max_drawdown": float(summary.get("max_drawdown", 0.0)),
        "profit_factor": float(summary.get("profit_factor", 0.0)),
        "win_rate": float(summary.get("win_rate", 0.0)),
        "avg_trade": avg_trade,
        "trade_count": float(trade_count),
        "sharpe_like": float(summary.get("sharpe_like", 0.0)),
    }


def _run_backtest(
    *,
    candles: pd.DataFrame,
    strategy_name: str,
    strategy_params: dict[str, Any],
    base_config: BacktestConfig,
    overrides: dict[str, Any],
) -> tuple[BacktestResult, dict[str, float]]:
    cfg = replace(
        base_config,
        persist_to_db=False,
        strategy_name=strategy_name,
        strategy_params=dict(strategy_params),
        **overrides,
    )
    strategy = _build_strategy(strategy_name, strategy_params)
    result = BacktestEngine().run(candles=candles, strategy=strategy, config=cfg)
    return result, _extract_metrics(result, candles)


def _parse_float_list(raw: str) -> list[float]:
    out = [float(x.strip()) for x in raw.split(",") if x.strip()]
    if not out:
        raise ValueError(f"Empty float list: {raw}")
    return out


def _parse_int_list(raw: str) -> list[int]:
    out = [int(x.strip()) for x in raw.split(",") if x.strip()]
    if not out:
        raise ValueError(f"Empty int list: {raw}")
    return out


def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _load_grid_file(path: str) -> dict[str, list[Any]]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text) or {}
        if not isinstance(loaded, dict):
            raise ValueError("grid file must be a mapping")
        out: dict[str, list[Any]] = {}
        for k, v in loaded.items():
            out[str(k)] = list(v) if isinstance(v, list) else [v]
        return out
    except Exception:
        out: dict[str, list[Any]] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if not val:
                continue
            parsed = ast.literal_eval(val)
            out[key] = list(parsed) if isinstance(parsed, list) else [parsed]
        return out


def _generate_parameter_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = list(grid.keys())
    vals = [grid[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*vals)]


def run_cost_stress(
    *,
    candles: pd.DataFrame,
    strategy_name: str,
    strategy_params: dict[str, Any],
    base_config: BacktestConfig,
    fee_multipliers: list[float],
    fixed_slippage_bps: list[float],
    atr_slippage_mults: list[float],
    slippage_mode: Literal["fixed", "atr", "mixed"],
    latency_bars: list[int],
    order_models: list[Literal["market", "limit"]],
    limit_timeout_bars: int,
    limit_fill_probability: float,
    limit_unfilled_penalty_bps: float,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    scenario_idx = 0
    if slippage_mode == "fixed":
        slippage_grid = [("fixed", s, 0.0) for s in fixed_slippage_bps]
    elif slippage_mode == "atr":
        slippage_grid = [("atr", 0.0, a) for a in atr_slippage_mults]
    else:
        slippage_grid = [("mixed", s, a) for s in fixed_slippage_bps for a in atr_slippage_mults]

    for fee_mult in fee_multipliers:
        for latency in latency_bars:
            for order_model in order_models:
                for slip_mode, slip_bps, atr_mult in slippage_grid:
                    scenario_idx += 1
                    overrides = {
                        "order_type": "LIMIT" if order_model == "limit" else "MARKET",
                        "fee_multiplier": fee_mult,
                        "slippage_mode": slip_mode,
                        "slippage_bps": slip_bps,
                        "atr_slippage_mult": atr_mult,
                        "latency_bars": latency,
                        "limit_timeout_bars": limit_timeout_bars,
                        "limit_fill_probability": limit_fill_probability,
                        "limit_unfilled_penalty_bps": limit_unfilled_penalty_bps,
                        "random_seed": seed + scenario_idx,
                    }
                    result, metrics = _run_backtest(
                        candles=candles,
                        strategy_name=strategy_name,
                        strategy_params=strategy_params,
                        base_config=base_config,
                        overrides=overrides,
                    )
                    order_count = max(len(result.orders), 1)
                    fill_count = sum(1 for o in result.orders if o.status == "filled")
                    reject_count = sum(1 for o in result.orders if o.status == "rejected")
                    rows.append(
                        {
                            "scenario_id": f"cost_{scenario_idx:04d}",
                            "fee_multiplier": fee_mult,
                            "slippage_mode": slip_mode,
                            "slippage_bps": slip_bps,
                            "atr_slippage_mult": atr_mult,
                            "latency_bars": latency,
                            "order_model": order_model,
                            "limit_timeout_bars": limit_timeout_bars if order_model == "limit" else 0,
                            "limit_fill_probability": limit_fill_probability if order_model == "limit" else 1.0,
                            "limit_unfilled_penalty_bps": limit_unfilled_penalty_bps if order_model == "limit" else 0.0,
                            "fill_rate": fill_count / order_count,
                            "reject_rate": reject_count / order_count,
                            **metrics,
                        }
                    )
    df = pd.DataFrame(rows).sort_values("net_pnl", ascending=False).reset_index(drop=True)
    sensitivity = (
        df.groupby(["order_model", "fee_multiplier", "latency_bars"], as_index=False)[["net_pnl", "sharpe_like", "max_drawdown"]]
        .mean()
        .sort_values(["order_model", "fee_multiplier", "latency_bars"])
        .reset_index(drop=True)
    )
    return df, sensitivity


def _window_iter(
    *,
    start: str,
    end: str,
    train_days: int,
    test_days: int,
    step_days: int,
) -> list[dict[str, Any]]:
    start_ts = _to_utc_timestamp(start)
    end_ts = _to_utc_timestamp(end)
    cursor = start_ts
    windows: list[dict[str, Any]] = []
    i = 0
    while True:
        train_end = cursor + pd.Timedelta(days=train_days)
        test_end = train_end + pd.Timedelta(days=test_days)
        if test_end > end_ts:
            break
        windows.append(
            {
                "window_index": i,
                "train_start": cursor,
                "train_end": train_end,
                "test_start": train_end,
                "test_end": test_end,
            }
        )
        i += 1
        cursor = cursor + pd.Timedelta(days=step_days)
    return windows


def _slice_candles(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    out = df[(df["timestamp"] >= start) & (df["timestamp"] < end)].copy()
    return out.reset_index(drop=True)


def _metric_value(metrics: dict[str, float], metric_name: str) -> float:
    return float(metrics.get(metric_name, float("-inf")))


def _param_dispersion(params_list: list[dict[str, Any]]) -> float:
    if not params_list:
        return 0.0
    numeric_keys: set[str] = set()
    for p in params_list:
        for k, v in p.items():
            if isinstance(v, (int, float)):
                numeric_keys.add(k)
    if not numeric_keys:
        return 0.0
    cvs: list[float] = []
    for k in sorted(numeric_keys):
        vals = np.asarray([float(p.get(k, 0.0)) for p in params_list], dtype=float)
        mean = float(np.mean(vals))
        std = float(np.std(vals))
        cvs.append(std / (abs(mean) + 1e-9))
    return float(np.mean(cvs)) if cvs else 0.0


def _param_stability(best_params: list[dict[str, Any]]) -> float:
    if not best_params:
        return 0.0
    numeric_keys: set[str] = set()
    for p in best_params:
        for k, v in p.items():
            if isinstance(v, (int, float)):
                numeric_keys.add(k)
    if not numeric_keys:
        return 1.0
    norm_vars: list[float] = []
    for key in sorted(numeric_keys):
        vals = np.asarray([float(p.get(key, 0.0)) for p in best_params], dtype=float)
        var = float(np.var(vals))
        mean = float(np.mean(vals))
        norm_vars.append(var / (abs(mean) + 1e-9))
    avg = float(np.mean(norm_vars)) if norm_vars else 0.0
    return 1.0 / (1.0 + avg)


def run_walk_forward(
    *,
    candles: pd.DataFrame,
    strategy_name: str,
    base_strategy_params: dict[str, Any],
    param_grid_path: str,
    base_config: BacktestConfig,
    metric: str,
    train_days: int,
    test_days: int,
    step_days: int,
    top_pct: float,
    max_candidates: int,
    seed: int,
    start: str,
    end: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    grid = _load_grid_file(param_grid_path)
    param_sets = _generate_parameter_grid(grid)
    if not param_sets:
        param_sets = [dict(base_strategy_params)]

    rng = np.random.default_rng(seed)
    if len(param_sets) > max_candidates:
        idxs = rng.choice(len(param_sets), size=max_candidates, replace=False)
        param_sets = [param_sets[int(i)] for i in idxs]

    windows = _window_iter(start=start, end=end, train_days=train_days, test_days=test_days, step_days=step_days)
    if not windows:
        return pd.DataFrame(), pd.DataFrame(), {"oos_positive_ratio": 0.0, "param_stability_score": 0.0}

    window_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    best_params_list: list[dict[str, Any]] = []
    window_i = 0
    for w in windows:
        train_df = _slice_candles(candles, w["train_start"], w["train_end"])
        test_df = _slice_candles(candles, w["test_start"], w["test_end"])
        if len(train_df) < 120 or len(test_df) < 30:
            continue

        train_scores: list[dict[str, Any]] = []
        for p in param_sets:
            params = dict(base_strategy_params)
            params.update(p)
            _, metrics = _run_backtest(
                candles=train_df,
                strategy_name=strategy_name,
                strategy_params=params,
                base_config=base_config,
                overrides={"random_seed": seed + window_i},
            )
            item = {
                "window_index": window_i,
                "role": "train",
                "params_json": json.dumps(params, sort_keys=True),
                "metric_value": _metric_value(metrics, metric),
                "net_pnl": metrics["net_pnl"],
                "sharpe_like": metrics["sharpe_like"],
                "max_drawdown": metrics["max_drawdown"],
                "trade_count": metrics["trade_count"],
            }
            train_scores.append(item)
            candidate_rows.append(item)

        train_scores = sorted(train_scores, key=lambda x: x["metric_value"], reverse=True)
        if not train_scores:
            continue
        top_k = max(1, int(math.ceil(len(train_scores) * top_pct)))
        top_train = train_scores[:top_k]
        top_params = [json.loads(x["params_json"]) for x in top_train]
        best_params = json.loads(top_train[0]["params_json"])
        best_params_list.append(best_params)

        test_scores: list[dict[str, Any]] = []
        for i, p in enumerate(top_params):
            _, metrics = _run_backtest(
                candles=test_df,
                strategy_name=strategy_name,
                strategy_params=p,
                base_config=base_config,
                overrides={"random_seed": seed + window_i + i + 1},
            )
            item = {
                "window_index": window_i,
                "role": "test",
                "params_json": json.dumps(p, sort_keys=True),
                "metric_value": _metric_value(metrics, metric),
                "net_pnl": metrics["net_pnl"],
                "sharpe_like": metrics["sharpe_like"],
                "max_drawdown": metrics["max_drawdown"],
                "trade_count": metrics["trade_count"],
            }
            test_scores.append(item)
            candidate_rows.append(item)

        test_sorted = sorted(test_scores, key=lambda x: x["metric_value"], reverse=True)
        best_test = test_sorted[0] if test_sorted else {"metric_value": float("-inf"), "net_pnl": 0.0, "sharpe_like": 0.0}
        median_test_metric = float(np.median([x["metric_value"] for x in test_scores])) if test_scores else float("-inf")
        test_positive_ratio = float(np.mean([1.0 if x["net_pnl"] > 0 else 0.0 for x in test_scores])) if test_scores else 0.0

        window_rows.append(
            {
                "window_index": window_i,
                "train_start": w["train_start"].isoformat(),
                "train_end": w["train_end"].isoformat(),
                "test_start": w["test_start"].isoformat(),
                "test_end": w["test_end"].isoformat(),
                "train_candidates": len(train_scores),
                "top_cluster_size": len(top_train),
                "best_train_metric": float(top_train[0]["metric_value"]),
                "best_test_metric": float(best_test["metric_value"]),
                "best_test_net_pnl": float(best_test["net_pnl"]),
                "best_test_sharpe_like": float(best_test["sharpe_like"]),
                "median_test_metric": median_test_metric,
                "test_positive_ratio_top_cluster": test_positive_ratio,
                "top_cluster_param_dispersion": _param_dispersion(top_params),
                "best_params_json": json.dumps(best_params, sort_keys=True),
            }
        )
        window_i += 1

    wf_df = pd.DataFrame(window_rows)
    candidate_df = pd.DataFrame(candidate_rows)
    if wf_df.empty:
        return wf_df, candidate_df, {"oos_positive_ratio": 0.0, "param_stability_score": 0.0}

    summary = {
        "window_count": float(len(wf_df)),
        "oos_positive_ratio": float(np.mean((wf_df["best_test_net_pnl"] > 0).astype(float))),
        "oos_median_best_test_metric": float(wf_df["best_test_metric"].median()),
        "oos_median_best_test_sharpe_like": float(wf_df["best_test_sharpe_like"].median()),
        "oos_mean_top_cluster_positive_ratio": float(wf_df["test_positive_ratio_top_cluster"].mean()),
        "param_stability_score": float(_param_stability(best_params_list)),
        "median_top_cluster_dispersion": float(wf_df["top_cluster_param_dispersion"].median()),
    }
    return wf_df, candidate_df, summary


class RegimeGatedStrategy(Strategy):
    def __init__(
        self,
        *,
        base_strategy: Strategy,
        regime_by_ts: dict[str, str],
        mode: Literal["on_off", "sizing"],
        allowed_regimes: set[str] | None = None,
        size_map: dict[str, float] | None = None,
    ) -> None:
        self.base_strategy = base_strategy
        self.regime_by_ts = regime_by_ts
        self.mode = mode
        self.allowed_regimes = allowed_regimes or set(regime_by_ts.values())
        self.size_map = size_map or {}

    def _regime(self, bar: Bar) -> str:
        return self.regime_by_ts.get(_ts_key(bar.timestamp), "range|low_vol")

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> Literal["long", "short", "exit", "hold", "buy", "sell"]:
        signal = self.base_strategy.on_bar(bar, position)
        regime = self._regime(bar)
        if self.mode == "on_off" and regime not in self.allowed_regimes:
            if position is not None and position.side != "flat":
                return "exit"
            return "hold"
        return signal

    def size_multiplier(self, bar: Bar, position: StrategyPosition | None = None) -> float:
        if self.mode != "sizing":
            return 1.0
        return float(self.size_map.get(self._regime(bar), 1.0))


def _label_regimes(
    df: pd.DataFrame,
    *,
    trend_ema_span: int,
    trend_slope_lookback: int,
    trend_slope_threshold: float,
    atr_period: int,
    vol_lookback: int,
    vol_percentile: float,
) -> pd.Series:
    ema = df["close"].ewm(span=max(trend_ema_span, 2), adjust=False).mean()
    slope = ema.pct_change(max(trend_slope_lookback, 1)).fillna(0.0)
    trend = np.where(np.abs(slope) >= trend_slope_threshold, "trend", "range")

    atr = _calc_atr(df, period=max(atr_period, 2))
    vol_cut = atr.rolling(max(vol_lookback, 5), min_periods=max(5, vol_lookback // 5)).quantile(vol_percentile)
    vol_cut = vol_cut.fillna(atr.median())
    vol = np.where(atr >= vol_cut, "high_vol", "low_vol")
    return pd.Series([f"{t}|{v}" for t, v in zip(trend, vol)], index=df.index, dtype="object")


def run_regime_gating(
    *,
    candles: pd.DataFrame,
    strategy_name: str,
    strategy_params: dict[str, Any],
    base_config: BacktestConfig,
    seed: int,
    trend_ema_span: int,
    trend_slope_lookback: int,
    trend_slope_threshold: float,
    atr_period: int,
    vol_lookback: int,
    vol_percentile: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    regimes = _label_regimes(
        candles,
        trend_ema_span=trend_ema_span,
        trend_slope_lookback=trend_slope_lookback,
        trend_slope_threshold=trend_slope_threshold,
        atr_period=atr_period,
        vol_lookback=vol_lookback,
        vol_percentile=vol_percentile,
    )
    regime_by_ts = {_ts_key(ts): str(label) for ts, label in zip(candles["timestamp"], regimes)}
    regime_names = sorted(set(regime_by_ts.values()))

    scenarios: list[tuple[str, Literal["on_off", "sizing"], set[str], dict[str, float]]] = []
    scenarios.append(("baseline", "on_off", set(regime_names), {}))
    for rg in regime_names:
        scenarios.append((f"onoff_{rg.replace('|', '_')}", "on_off", {rg}, {}))
    scenarios.append(
        (
            "sizing_regime_map",
            "sizing",
            set(regime_names),
            {
                "trend|low_vol": 1.00,
                "trend|high_vol": 0.75,
                "range|low_vol": 0.55,
                "range|high_vol": 0.30,
            },
        )
    )

    rows: list[dict[str, Any]] = []
    regime_table_rows: list[dict[str, Any]] = []
    for idx, (scenario_id, mode, allowed, size_map) in enumerate(scenarios):
        base_strategy = _build_strategy(strategy_name, strategy_params)
        wrapped = RegimeGatedStrategy(
            base_strategy=base_strategy,
            regime_by_ts=regime_by_ts,
            mode=mode,
            allowed_regimes=allowed,
            size_map=size_map,
        )
        cfg = replace(
            base_config,
            persist_to_db=False,
            random_seed=seed + idx,
            strategy_name=strategy_name,
            strategy_params=strategy_params,
        )
        result = BacktestEngine().run(candles=candles, strategy=wrapped, config=cfg)
        metrics = _extract_metrics(result, candles)
        row = {
            "scenario_id": scenario_id,
            "gate_mode": mode,
            "allowed_regimes": "|".join(sorted(allowed)),
            "size_map_json": json.dumps(size_map, sort_keys=True),
            **metrics,
        }
        rows.append(row)
        if scenario_id.startswith("onoff_"):
            regime_name = scenario_id.replace("onoff_", "").replace("_", "|", 1)
            regime_table_rows.append(
                {
                    "regime": regime_name,
                    "win_rate": metrics["win_rate"],
                    "profit_factor": metrics["profit_factor"],
                    "max_drawdown": metrics["max_drawdown"],
                    "sharpe_like": metrics["sharpe_like"],
                    "trade_count": metrics["trade_count"],
                    "avg_trade": metrics["avg_trade"],
                    "net_pnl": metrics["net_pnl"],
                }
            )

    return (
        pd.DataFrame(rows).sort_values("net_pnl", ascending=False).reset_index(drop=True),
        pd.DataFrame(regime_table_rows).sort_values("net_pnl", ascending=False).reset_index(drop=True),
    )


def _verdict(summary: dict[str, float]) -> tuple[str, float]:
    cost_score = float(summary.get("cost_positive_ratio", 0.0))
    wfo_score = float(summary.get("wfo_oos_positive_ratio", 0.0))
    regime_score = float(summary.get("regime_positive_ratio", 0.0))
    stability = float(summary.get("wfo_param_stability_score", 0.0))
    sharpe_term = max(min((float(summary.get("wfo_median_sharpe_like", 0.0)) + 1.0) / 2.0, 1.0), 0.0)
    robustness = 0.30 * cost_score + 0.40 * wfo_score + 0.10 * regime_score + 0.10 * stability + 0.10 * sharpe_term
    if robustness >= 0.65 and wfo_score >= 0.55 and cost_score >= 0.40:
        return "HAS EDGE", robustness
    if robustness < 0.45 or wfo_score < 0.35:
        return "NO EDGE", robustness
    return "UNCERTAIN", robustness


def run_edge_validation(
    *,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    strategy_name: str,
    strategy_params: dict[str, Any],
    base_config: BacktestConfig,
    output_root: Path,
    seed: int,
    data_source: DataSource,
    csv_path: str | None,
    testnet: bool,
    suite: Literal["all", "cost", "walk", "regime"],
    fee_multipliers: list[float],
    fixed_slippage_bps: list[float],
    atr_slippage_mults: list[float],
    slippage_mode: Literal["fixed", "atr", "mixed"],
    latency_bars: list[int],
    order_models: list[Literal["market", "limit"]],
    limit_timeout_bars: int,
    limit_fill_probability: float,
    limit_unfilled_penalty_bps: float,
    walk_train_days: int,
    walk_test_days: int,
    walk_step_days: int,
    walk_top_pct: float,
    walk_max_candidates: int,
    walk_metric: str,
    walk_grid_path: str,
    trend_ema_span: int,
    trend_slope_lookback: int,
    trend_slope_threshold: float,
    regime_atr_period: int,
    regime_vol_lookback: int,
    regime_vol_percentile: float,
) -> EdgeRunOutput:
    run_id = f"edge_{pd.Timestamp.now(tz='UTC').strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    candles = load_candles(
        data_source=data_source,
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        seed=seed,
        csv_path=csv_path,
        testnet=testnet,
    )
    if candles.empty:
        raise ValueError("No candles loaded for experiments")

    base_cfg = replace(
        base_config,
        symbol=symbol,
        timeframe=timeframe,
        strategy_name=strategy_name,
        strategy_params=strategy_params,
        persist_to_db=False,
        random_seed=seed,
    )

    cost_df = pd.DataFrame()
    sensitivity_df = pd.DataFrame()
    if suite in {"all", "cost"}:
        cost_df, sensitivity_df = run_cost_stress(
            candles=candles,
            strategy_name=strategy_name,
            strategy_params=strategy_params,
            base_config=base_cfg,
            fee_multipliers=fee_multipliers,
            fixed_slippage_bps=fixed_slippage_bps,
            atr_slippage_mults=atr_slippage_mults,
            slippage_mode=slippage_mode,
            latency_bars=latency_bars,
            order_models=order_models,
            limit_timeout_bars=limit_timeout_bars,
            limit_fill_probability=limit_fill_probability,
            limit_unfilled_penalty_bps=limit_unfilled_penalty_bps,
            seed=seed,
        )
    wf_df = pd.DataFrame()
    wf_candidates_df = pd.DataFrame()
    wf_summary: dict[str, float] = {}
    if suite in {"all", "walk"}:
        wf_df, wf_candidates_df, wf_summary = run_walk_forward(
            candles=candles,
            strategy_name=strategy_name,
            base_strategy_params=strategy_params,
            param_grid_path=walk_grid_path,
            base_config=base_cfg,
            metric=walk_metric,
            train_days=walk_train_days,
            test_days=walk_test_days,
            step_days=walk_step_days,
            top_pct=walk_top_pct,
            max_candidates=walk_max_candidates,
            seed=seed,
            start=start,
            end=end,
        )
    regime_df = pd.DataFrame()
    regime_table_df = pd.DataFrame()
    if suite in {"all", "regime"}:
        regime_df, regime_table_df = run_regime_gating(
            candles=candles,
            strategy_name=strategy_name,
            strategy_params=strategy_params,
            base_config=base_cfg,
            seed=seed,
            trend_ema_span=trend_ema_span,
            trend_slope_lookback=trend_slope_lookback,
            trend_slope_threshold=trend_slope_threshold,
            atr_period=regime_atr_period,
            vol_lookback=regime_vol_lookback,
            vol_percentile=regime_vol_percentile,
        )

    summary: dict[str, float] = {
        "bars": float(len(candles)),
        "cost_positive_ratio": float(np.mean((cost_df["net_pnl"] > 0).astype(float))) if not cost_df.empty else 0.0,
        "cost_median_net_pnl": float(cost_df["net_pnl"].median()) if not cost_df.empty else 0.0,
        "cost_median_trade_count": float(cost_df["trade_count"].median()) if not cost_df.empty else 0.0,
        "cost_min_trade_count": float(cost_df["trade_count"].min()) if not cost_df.empty else 0.0,
        "wfo_oos_positive_ratio": float(wf_summary.get("oos_positive_ratio", 0.0)),
        "wfo_median_sharpe_like": float(wf_summary.get("oos_median_best_test_sharpe_like", 0.0)),
        "wfo_param_stability_score": float(wf_summary.get("param_stability_score", 0.0)),
        "regime_positive_ratio": float(np.mean((regime_df["net_pnl"] > 0).astype(float))) if not regime_df.empty else 0.0,
        "regime_best_net_pnl": float(regime_df["net_pnl"].max()) if not regime_df.empty else 0.0,
        "regime_best_profit_factor": float(regime_table_df["profit_factor"].max()) if not regime_table_df.empty else 0.0,
        "regime_best_max_drawdown": float(regime_table_df["max_drawdown"].max()) if not regime_table_df.empty else 0.0,
    }
    verdict, robustness = _verdict(summary)
    summary["robustness_score"] = robustness

    config_dump = {
        "run_id": run_id,
        "suite": suite,
        "symbol": symbol,
        "timeframe": timeframe,
        "start": str(_to_utc_timestamp(start)),
        "end": str(_to_utc_timestamp(end)),
        "strategy_name": strategy_name,
        "strategy_params": strategy_params,
        "seed": seed,
        "data_source": data_source,
        "generated_at": pd.Timestamp.now(tz=timezone.utc).isoformat(),
        "cost": {
            "fee_multipliers": fee_multipliers,
            "fixed_slippage_bps": fixed_slippage_bps,
            "atr_slippage_mults": atr_slippage_mults,
            "slippage_mode": slippage_mode,
            "latency_bars": latency_bars,
            "order_models": order_models,
            "limit_timeout_bars": limit_timeout_bars,
            "limit_fill_probability": limit_fill_probability,
            "limit_unfilled_penalty_bps": limit_unfilled_penalty_bps,
        },
        "walk_forward": {
            "train_days": walk_train_days,
            "test_days": walk_test_days,
            "step_days": walk_step_days,
            "top_pct": walk_top_pct,
            "max_candidates": walk_max_candidates,
            "metric": walk_metric,
            "grid": walk_grid_path,
        },
        "regime": {
            "trend_ema_span": trend_ema_span,
            "trend_slope_lookback": trend_slope_lookback,
            "trend_slope_threshold": trend_slope_threshold,
            "atr_period": regime_atr_period,
            "vol_lookback": regime_vol_lookback,
            "vol_percentile": regime_vol_percentile,
        },
    }

    summary_out = dict(summary)
    summary_out["verdict"] = verdict

    save_json(config_dump, run_dir / "config.json")
    save_dataframe_csv(candles, run_dir / "candles_sample.csv")
    save_dataframe_csv(cost_df, run_dir / "cost_stress.csv")
    save_dataframe_csv(sensitivity_df, run_dir / "cost_sensitivity.csv")
    save_dataframe_csv(wf_df, run_dir / "walk_forward_windows.csv")
    save_dataframe_csv(wf_candidates_df, run_dir / "walk_forward_candidates.csv")
    save_dataframe_csv(regime_df, run_dir / "regime_scenarios.csv")
    save_dataframe_csv(regime_table_df, run_dir / "regime_table.csv")
    save_json(summary_out, run_dir / "summary.json")
    summary_df = pd.DataFrame({"metric": list(summary_out.keys()), "value": list(summary_out.values())})
    save_dataframe_csv(summary_df, run_dir / "summary.csv")

    plots_dir = run_dir / "plots"
    save_line_chart(plots_dir / "cost_net_pnl_line.png", cost_df["net_pnl"].tolist() if not cost_df.empty else [])
    save_histogram(plots_dir / "walk_forward_oos_hist.png", wf_df["best_test_net_pnl"].tolist() if not wf_df.empty else [])
    save_bar_chart(plots_dir / "regime_net_pnl_bar.png", regime_table_df["net_pnl"].tolist() if not regime_table_df.empty else [])

    write_markdown_report(
        path=run_dir / "report.md",
        run_id=run_id,
        config=config_dump,
        summary=summary_out,
        cost_df=cost_df,
        wfo_df=wf_df,
        regime_df=regime_table_df,
    )

    files = {
        "config_json": str(run_dir / "config.json"),
        "summary_csv": str(run_dir / "summary.csv"),
        "summary_json": str(run_dir / "summary.json"),
        "report_md": str(run_dir / "report.md"),
        "cost_csv": str(run_dir / "cost_stress.csv"),
        "walk_forward_csv": str(run_dir / "walk_forward_windows.csv"),
        "regime_csv": str(run_dir / "regime_table.csv"),
        "cost_plot": str(plots_dir / "cost_net_pnl_line.png"),
        "walk_plot": str(plots_dir / "walk_forward_oos_hist.png"),
        "regime_plot": str(plots_dir / "regime_net_pnl_bar.png"),
    }

    return EdgeRunOutput(run_id=run_id, run_dir=run_dir, summary=summary_out, files=files)


@dataclass(frozen=True)
class SystemCandidate:
    system_id: str
    title: str
    track: str
    strategy_name: str
    strategy_params: dict[str, Any]
    walk_grid_path: str
    notes: str


@dataclass(frozen=True)
class SystemBatchOutput:
    batch_run_id: str
    batch_dir: Path
    candidate_results: list[dict[str, Any]]


def default_system_candidates() -> list[SystemCandidate]:
    return [
        SystemCandidate(
            system_id="A_beta_hedged_carry_momo",
            title="Carry-Momentum + Beta Hedge",
            track="A",
            strategy_name="carry:momentum",
            strategy_params={
                "momentum_fast": 8,
                "momentum_slow": 34,
                "carry_period": 24,
                "carry_weight": 0.35,
                "allow_short": True,
                "stop_loss_pct": 0.012,
                "take_profit_pct": 0.03,
                "risk_template": "balanced",
            },
            walk_grid_path="config/grids/carry_momentum_narrow.yaml",
            notes="Track A: direction dependency 완화, BTC 베타 헷지 프록시 점검",
        ),
        SystemCandidate(
            system_id="B_regime_switch_trend_range",
            title="Regime Switch (Trend vs Range)",
            track="B",
            strategy_name="regime_switch",
            strategy_params={
                "trend_strategy_type": "trend:donchian",
                "range_strategy_type": "meanrev:zscore",
                "trend_params": {"entry_period": 20, "exit_period": 10, "allow_short": True},
                "range_params": {"lookback": 24, "entry_zscore": 2.0, "exit_zscore": 0.5, "allow_short": True},
                "trend_ema_span": 48,
                "trend_slope_lookback": 8,
                "trend_slope_threshold": 0.0015,
                "vol_lookback": 96,
                "high_vol_size_mult": 0.60,
                "low_vol_size_mult": 1.00,
                "stop_loss_pct": 0.010,
                "take_profit_pct": 0.03,
                "risk_template": "defensive",
            },
            walk_grid_path="config/grids/regime_switch_narrow.yaml",
            notes="Track B: trend/range 분리 운용 + high-vol 사이징 축소",
        ),
        SystemCandidate(
            system_id="C_breakout_atr_risk_template",
            title="Breakout ATR + Execution Aware Risk",
            track="C",
            strategy_name="breakout:atr_channel",
            strategy_params={
                "sma_period": 30,
                "atr_period": 14,
                "atr_mult": 1.8,
                "allow_short": True,
                "stop_loss_pct": 0.013,
                "take_profit_pct": 0.04,
                "risk_template": "aggressive",
            },
            walk_grid_path="config/grids/breakout_atr_narrow.yaml",
            notes="Track C: 고정 리스크 템플릿 + 지정가/시장가 혼합 비용 강건성 점검",
        ),
    ]


def _safe_symbol(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":", "_")


def _calc_beta_proxy(
    *,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    seed: int,
    data_source: DataSource,
    csv_path: str | None,
    testnet: bool,
) -> float:
    if symbol.upper() in {"BTC/USDT", "BTCUSDT"}:
        return 1.0
    sym_df = load_candles(
        data_source=data_source,
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        seed=seed,
        csv_path=csv_path,
        testnet=testnet,
    )
    btc_df = load_candles(
        data_source=data_source,
        symbol="BTC/USDT",
        timeframe=timeframe,
        start=start,
        end=end,
        seed=seed + 1,
        csv_path=csv_path if (data_source == "csv" and symbol.upper() in {"BTC/USDT", "BTCUSDT"}) else None,
        testnet=testnet,
    )
    if sym_df.empty or btc_df.empty:
        return 1.0
    merged = pd.merge(
        sym_df[["timestamp", "close"]].rename(columns={"close": "sym"}),
        btc_df[["timestamp", "close"]].rename(columns={"close": "btc"}),
        on="timestamp",
        how="inner",
    )
    if len(merged) < 50:
        return 1.0
    sym_ret = merged["sym"].pct_change().dropna()
    btc_ret = merged["btc"].pct_change().dropna()
    min_len = min(len(sym_ret), len(btc_ret))
    if min_len < 20:
        return 1.0
    sym_ret = sym_ret.iloc[-min_len:]
    btc_ret = btc_ret.iloc[-min_len:]
    var = float(np.var(btc_ret))
    if var <= 1e-12:
        return 1.0
    cov = float(np.cov(sym_ret, btc_ret)[0, 1])
    beta = cov / var
    return float(np.clip(beta, -3.0, 3.0))


def _evaluate_candidate_gates(candidate_dir: Path, symbol_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows_df = pd.DataFrame(symbol_rows)
    if rows_df.empty:
        return {
            "verdict": "불합격",
            "gate_wfo_two_symbols": False,
            "gate_cost_robust": False,
            "gate_regime_consistency": False,
            "gate_trade_count": False,
            "reason": "no symbol results",
        }

    target = rows_df[rows_df["symbol"].isin(["BTC/USDT", "ETH/USDT"])].copy()
    wfo_pass_count = int((target["wfo_oos_positive_ratio"] >= 0.60).sum())
    gate_wfo = wfo_pass_count >= 2

    gate_cost = bool((rows_df["cost_collapse_score"] <= 2.5).all() and (rows_df["cost_positive_ratio"] >= 0.30).all())
    gate_regime = bool(rows_df["regime_pf_mdd_flag"].all())
    gate_trade = bool((rows_df["cost_median_trade_count"] >= 200).all())

    if gate_wfo and gate_cost and gate_regime and gate_trade:
        verdict = "합격"
    elif (gate_wfo and gate_regime) or (gate_cost and gate_trade):
        verdict = "불확실"
    else:
        verdict = "불합격"

    return {
        "verdict": verdict,
        "gate_wfo_two_symbols": gate_wfo,
        "gate_cost_robust": gate_cost,
        "gate_regime_consistency": gate_regime,
        "gate_trade_count": gate_trade,
        "wfo_pass_symbol_count": wfo_pass_count,
    }


def run_system_batch(
    *,
    symbols: list[str],
    timeframe: str,
    start: str,
    end: str,
    base_config: BacktestConfig,
    output_root: Path,
    seed: int,
    data_source: DataSource,
    csv_path: str | None,
    testnet: bool,
    candidates: list[SystemCandidate] | None = None,
    walk_train_days: int = 240,
    walk_test_days: int = 60,
    walk_step_days: int = 30,
    walk_top_pct: float = 0.15,
    walk_max_candidates: int = 120,
    fee_multipliers: list[float] | None = None,
    fixed_slippage_bps: list[float] | None = None,
    atr_slippage_mults: list[float] | None = None,
    latency_bars: list[int] | None = None,
) -> SystemBatchOutput:
    systems = candidates or default_system_candidates()
    batch_run_id = f"systems_{pd.Timestamp.now(tz='UTC').strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    batch_dir = output_root / batch_run_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    fee_list = fee_multipliers or [1.0, 1.5, 2.0, 3.0]
    slip_bps_list = fixed_slippage_bps or [1.0, 3.0, 5.0, 10.0]
    slip_atr_list = atr_slippage_mults or [0.02, 0.05, 0.10, 0.20]
    lat_list = latency_bars or [0, 1, 3]

    candidate_outputs: list[dict[str, Any]] = []

    for ci, candidate in enumerate(systems):
        candidate_dir = batch_dir / candidate.system_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        symbol_rows: list[dict[str, Any]] = []

        for si, symbol in enumerate(symbols):
            sym_out_root = candidate_dir / "symbols" / _safe_symbol(symbol)
            out = run_edge_validation(
                symbol=symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                strategy_name=candidate.strategy_name,
                strategy_params=candidate.strategy_params,
                base_config=base_config,
                output_root=sym_out_root,
                seed=seed + (ci * 1000) + (si * 31),
                data_source=data_source,
                csv_path=csv_path,
                testnet=testnet,
                suite="all",
                fee_multipliers=fee_list,
                fixed_slippage_bps=slip_bps_list,
                atr_slippage_mults=slip_atr_list,
                slippage_mode="mixed",
                latency_bars=lat_list,
                order_models=["market", "limit"],
                limit_timeout_bars=2,
                limit_fill_probability=0.9,
                limit_unfilled_penalty_bps=3.0,
                walk_train_days=walk_train_days,
                walk_test_days=walk_test_days,
                walk_step_days=walk_step_days,
                walk_top_pct=walk_top_pct,
                walk_max_candidates=walk_max_candidates,
                walk_metric="sharpe_like",
                walk_grid_path=candidate.walk_grid_path,
                trend_ema_span=48,
                trend_slope_lookback=8,
                trend_slope_threshold=0.0015,
                regime_atr_period=14,
                regime_vol_lookback=120,
                regime_vol_percentile=0.65,
            )

            cost_df = pd.read_csv(out.files["cost_csv"])
            regime_df = pd.read_csv(out.files["regime_csv"])
            cost_median = float(cost_df["net_pnl"].median()) if not cost_df.empty else 0.0
            cost_min = float(cost_df["net_pnl"].min()) if not cost_df.empty else 0.0
            collapse_score = (cost_median - cost_min) / (abs(cost_median) + 1e-9) if cost_median != 0 else float("inf")

            regime_pf_mdd_flag = False
            if not regime_df.empty:
                pf_cond = regime_df["profit_factor"] > 1.1
                mdd_cond = regime_df["max_drawdown"] > regime_df["max_drawdown"].median()
                regime_pf_mdd_flag = bool((pf_cond & mdd_cond).any())

            beta = _calc_beta_proxy(
                symbol=symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                seed=seed + si,
                data_source=data_source,
                csv_path=csv_path,
                testnet=testnet,
            )

            symbol_rows.append(
                {
                    "symbol": symbol,
                    "edge_run_id": out.run_id,
                    "edge_run_dir": str(out.run_dir),
                    "wfo_oos_positive_ratio": float(out.summary.get("wfo_oos_positive_ratio", 0.0)),
                    "cost_positive_ratio": float(out.summary.get("cost_positive_ratio", 0.0)),
                    "cost_median_trade_count": float(out.summary.get("cost_median_trade_count", 0.0)),
                    "regime_best_profit_factor": float(out.summary.get("regime_best_profit_factor", 0.0)),
                    "regime_best_max_drawdown": float(out.summary.get("regime_best_max_drawdown", 0.0)),
                    "cost_collapse_score": float(collapse_score),
                    "regime_pf_mdd_flag": regime_pf_mdd_flag,
                    "beta_proxy": beta,
                }
            )

        gates = _evaluate_candidate_gates(candidate_dir, symbol_rows)
        symbol_df = pd.DataFrame(symbol_rows)
        save_dataframe_csv(symbol_df, candidate_dir / "candidate_symbol_summary.csv")
        summary_payload = {
            "candidate_id": candidate.system_id,
            "title": candidate.title,
            "track": candidate.track,
            "notes": candidate.notes,
            **gates,
        }
        save_json(summary_payload, candidate_dir / "candidate_summary.json")
        save_dataframe_csv(
            pd.DataFrame({"metric": list(summary_payload.keys()), "value": list(summary_payload.values())}),
            candidate_dir / "candidate_summary.csv",
        )
        report_lines = [
            f"# Candidate Report: {candidate.system_id}",
            "",
            f"- title: {candidate.title}",
            f"- track: {candidate.track}",
            f"- verdict: **{gates['verdict']}**",
            f"- gate_wfo_two_symbols: {gates['gate_wfo_two_symbols']}",
            f"- gate_cost_robust: {gates['gate_cost_robust']}",
            f"- gate_regime_consistency: {gates['gate_regime_consistency']}",
            f"- gate_trade_count: {gates['gate_trade_count']}",
            "",
            "## Symbol Summary",
            ("```csv\n" + symbol_df.to_csv(index=False) + "```") if not symbol_df.empty else "_(no rows)_",
        ]
        (candidate_dir / "report.md").write_text("\n".join(report_lines), encoding="utf-8")

        candidate_outputs.append(
            {
                "candidate_id": candidate.system_id,
                "candidate_dir": str(candidate_dir),
                "verdict": gates["verdict"],
                "gate_wfo_two_symbols": bool(gates["gate_wfo_two_symbols"]),
                "gate_cost_robust": bool(gates["gate_cost_robust"]),
                "gate_regime_consistency": bool(gates["gate_regime_consistency"]),
                "gate_trade_count": bool(gates["gate_trade_count"]),
            }
        )

    save_dataframe_csv(pd.DataFrame(candidate_outputs), batch_dir / "batch_summary.csv")
    save_json({"batch_run_id": batch_run_id, "candidates": candidate_outputs}, batch_dir / "batch_summary.json")
    return SystemBatchOutput(batch_run_id=batch_run_id, batch_dir=batch_dir, candidate_results=candidate_outputs)


__all__ = [
    "EdgeRunOutput",
    "SystemCandidate",
    "SystemBatchOutput",
    "run_system_batch",
    "default_system_candidates",
    "run_edge_validation",
    "load_candles",
    "run_cost_stress",
    "run_walk_forward",
    "run_regime_gating",
    "_parse_float_list",
    "_parse_int_list",
]
