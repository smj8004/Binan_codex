from __future__ import annotations

import json
import math
import ast
import itertools
import random
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
    save_dual_line_chart,
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
class PortfolioRunOutput:
    run_id: str
    run_dir: Path
    summary: dict[str, Any]
    files: dict[str, str]


@dataclass(frozen=True)
class PortfolioParams:
    signal_model: Literal["momentum", "mean_reversion"]
    lookback_bars: int
    rebalance_bars: int
    k: int
    gross_exposure: float
    turnover_threshold: float
    vol_lookback: int
    rank_buffer: int = 0
    high_vol_percentile: float = 0.65
    gross_map: str = "balanced"
    off_grace_bars: int = 0
    phased_entry_steps: int = 1


@dataclass(frozen=True)
class PortfolioCostConfig:
    order_model: Literal["market", "limit"] = "limit"
    fee_multiplier: float = 1.0
    slippage_mode: Literal["fixed", "atr", "mixed"] = "mixed"
    slippage_bps: float = 3.0
    atr_slippage_mult: float = 0.05
    latency_bars: int = 0
    limit_timeout_bars: int = 2
    limit_fill_probability: float = 0.9
    limit_unfilled_penalty_bps: float = 3.0
    limit_price_offset_bps: float = 1.0


@dataclass(frozen=True)
class PortfolioMarketData:
    symbols: list[str]
    timestamps: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr: np.ndarray

    @property
    def bars(self) -> int:
        return int(self.close.shape[0])

    @property
    def symbol_count(self) -> int:
        return int(self.close.shape[1]) if self.close.ndim == 2 else 0


@dataclass(frozen=True)
class PortfolioSimResult:
    metrics: dict[str, float]
    equity_curve: pd.DataFrame
    dd_timeline: pd.DataFrame
    gross_target_applied: pd.DataFrame
    excluded_symbols: pd.DataFrame
    symbol_risk_caps: pd.DataFrame
    positions: pd.DataFrame
    turnover: pd.DataFrame
    cost_breakdown: pd.DataFrame
    liquidation_events: pd.DataFrame
    diagnostics: dict[str, Any]
    debug_dump: list[dict[str, Any]]


REGIME_GROSS_PROFILES: dict[str, dict[str, float]] = {
    "highvol_050": {
        "trend|low_vol": 1.0,
        "range|low_vol": 1.0,
        "trend|high_vol": 0.50,
        "range|high_vol": 0.25,
    },
    "balanced": {
        "trend|low_vol": 1.0,
        "range|low_vol": 1.0,
        "trend|high_vol": 0.25,
        "range|high_vol": 0.10,
    },
    "conservative": {
        "trend|low_vol": 1.0,
        "range|low_vol": 1.0,
        "trend|high_vol": 0.10,
        "range|high_vol": 0.00,
    },
    "off_range_highvol": {
        "trend|low_vol": 1.0,
        "range|low_vol": 0.75,
        "trend|high_vol": 0.25,
        "range|high_vol": 0.00,
    },
    "ultra_defensive": {
        "trend|low_vol": 0.75,
        "range|low_vol": 0.75,
        "trend|high_vol": 0.10,
        "range|high_vol": 0.00,
    },
    "off_highvol_all": {
        "trend|low_vol": 1.0,
        "range|low_vol": 0.75,
        "trend|high_vol": 0.00,
        "range|high_vol": 0.00,
    },
    "minimal_risk": {
        "trend|low_vol": 0.25,
        "range|low_vol": 0.00,
        "trend|high_vol": 0.00,
        "range|high_vol": 0.00,
    },
    "aggressive": {
        "trend|low_vol": 1.0,
        "range|low_vol": 1.0,
        "trend|high_vol": 0.25,
        "range|high_vol": 0.25,
    },
}


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


def _duration_to_bars(duration: str, *, timeframe_seconds: int) -> int:
    raw = str(duration).strip().lower()
    if not raw:
        raise ValueError("empty duration")
    if raw.endswith("d"):
        sec = int(float(raw[:-1]) * 86_400)
    elif raw.endswith("h"):
        sec = int(float(raw[:-1]) * 3_600)
    elif raw.endswith("m"):
        sec = int(float(raw[:-1]) * 60)
    else:
        bars = int(float(raw))
        return max(bars, 1)
    return max(int(round(sec / max(timeframe_seconds, 1))), 1)


def _parse_duration_list(raw: str, *, timeframe: str) -> list[int]:
    tf_seconds = _timeframe_to_seconds(timeframe)
    values = [x.strip() for x in raw.split(",") if x.strip()]
    if not values:
        raise ValueError(f"empty duration list: {raw}")
    return [_duration_to_bars(x, timeframe_seconds=tf_seconds) for x in values]


def load_multi_candles(
    *,
    data_source: DataSource,
    symbols: list[str],
    timeframe: str,
    start: str,
    end: str,
    seed: int,
    csv_path: str | None = None,
    testnet: bool = False,
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for idx, symbol in enumerate(symbols):
        sym_seed = seed + idx * 997 + abs(hash(symbol)) % 10_000
        out[symbol] = load_candles(
            data_source=data_source,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            seed=sym_seed,
            csv_path=csv_path,
            testnet=testnet,
        )
    return out


def _build_portfolio_market(
    candles_by_symbol: dict[str, pd.DataFrame],
    *,
    atr_period: int,
) -> PortfolioMarketData:
    if not candles_by_symbol:
        raise ValueError("empty candles_by_symbol")
    symbols = list(candles_by_symbol.keys())
    close_df = pd.concat(
        [candles_by_symbol[s].set_index("timestamp")["close"].rename(s) for s in symbols],
        axis=1,
        join="inner",
    ).dropna()
    if close_df.empty:
        raise ValueError("no common timestamp across symbols")

    idx = close_df.index
    open_df = pd.concat(
        [candles_by_symbol[s].set_index("timestamp")["open"].rename(s) for s in symbols],
        axis=1,
        join="inner",
    ).reindex(idx)
    high_df = pd.concat(
        [candles_by_symbol[s].set_index("timestamp")["high"].rename(s) for s in symbols],
        axis=1,
        join="inner",
    ).reindex(idx)
    low_df = pd.concat(
        [candles_by_symbol[s].set_index("timestamp")["low"].rename(s) for s in symbols],
        axis=1,
        join="inner",
    ).reindex(idx)

    atr_cols: dict[str, pd.Series] = {}
    for s in symbols:
        df = candles_by_symbol[s].copy()
        df = df.set_index("timestamp").reindex(idx)
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr_cols[s] = tr.rolling(max(atr_period, 2), min_periods=1).mean()
    atr_df = pd.DataFrame(atr_cols).reindex(idx).fillna(0.0)

    frame = pd.concat(
        {
            "open": open_df,
            "high": high_df,
            "low": low_df,
            "close": close_df,
            "atr": atr_df,
        },
        axis=1,
    ).dropna()
    if frame.empty:
        raise ValueError("insufficient aligned bars for portfolio market")
    idx = frame.index
    return PortfolioMarketData(
        symbols=symbols,
        timestamps=idx,
        open=frame["open"][symbols].to_numpy(dtype=float),
        high=frame["high"][symbols].to_numpy(dtype=float),
        low=frame["low"][symbols].to_numpy(dtype=float),
        close=frame["close"][symbols].to_numpy(dtype=float),
        atr=frame["atr"][symbols].to_numpy(dtype=float),
    )


def _slice_portfolio_market(market: PortfolioMarketData, start: pd.Timestamp, end: pd.Timestamp) -> PortfolioMarketData:
    ts = market.timestamps
    mask = (ts >= start) & (ts < end)
    idx = np.where(mask)[0]
    if idx.size == 0:
        return PortfolioMarketData(
            symbols=list(market.symbols),
            timestamps=pd.DatetimeIndex([], tz="UTC"),
            open=np.zeros((0, market.symbol_count), dtype=float),
            high=np.zeros((0, market.symbol_count), dtype=float),
            low=np.zeros((0, market.symbol_count), dtype=float),
            close=np.zeros((0, market.symbol_count), dtype=float),
            atr=np.zeros((0, market.symbol_count), dtype=float),
        )
    return PortfolioMarketData(
        symbols=list(market.symbols),
        timestamps=ts[idx],
        open=market.open[idx, :],
        high=market.high[idx, :],
        low=market.low[idx, :],
        close=market.close[idx, :],
        atr=market.atr[idx, :],
    )


def _portfolio_fee_rate(
    *,
    order_model: Literal["market", "limit"],
    base_config: BacktestConfig,
    fee_multiplier: float,
) -> float:
    if order_model == "limit":
        return max(float(base_config.maker_fee_bps), 0.0) * max(fee_multiplier, 0.0) / 10_000.0
    return max(float(base_config.taker_fee_bps), 0.0) * max(fee_multiplier, 0.0) / 10_000.0


def _portfolio_slippage_fraction(
    *,
    base_price: float,
    atr_value: float,
    slippage_mode: Literal["fixed", "atr", "mixed"],
    slippage_bps: float,
    atr_slippage_mult: float,
) -> float:
    fixed_frac = max(float(slippage_bps), 0.0) / 10_000.0
    atr_frac = 0.0
    if base_price > 0 and atr_value > 0:
        atr_frac = max(float(atr_slippage_mult), 0.0) * (atr_value / base_price)
    if slippage_mode == "atr":
        return atr_frac
    if slippage_mode == "mixed":
        return fixed_frac + atr_frac
    return fixed_frac


def _max_drawdown(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(values)
    denom = np.where(peaks > 0, peaks, 1.0)
    dd = (values - peaks) / denom
    return float(np.min(dd))


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


def _portfolio_rebalance_indices(total_bars: int, rebalance_bars: int, warmup_bars: int) -> list[int]:
    step = max(int(rebalance_bars), 1)
    start_idx = max(int(warmup_bars), 1)
    out = list(range(start_idx, total_bars, step))
    if not out and total_bars > 1:
        out = [total_bars - 1]
    return out


def _portfolio_signal_scores(
    close_slice: np.ndarray,
    *,
    signal_model: Literal["momentum", "mean_reversion"],
    mode: Literal["single", "median_3"] = "single",
) -> np.ndarray:
    end = close_slice[-1]
    scores = np.full_like(end, np.nan, dtype=float)
    if mode == "median_3":
        lookbacks = (24 * 7, 24 * 14, 24 * 28)
        stack: list[np.ndarray] = []
        for lb in lookbacks:
            if close_slice.shape[0] < lb + 1:
                continue
            start_i = close_slice[-(lb + 1)]
            part = np.full_like(end, np.nan, dtype=float)
            valid_i = start_i > 0
            part[valid_i] = (end[valid_i] / start_i[valid_i]) - 1.0
            stack.append(part)
        if stack:
            scores = np.nanmedian(np.vstack(stack), axis=0)
    else:
        start = close_slice[0]
        valid = start > 0
        scores = np.zeros_like(end, dtype=float)
        scores[valid] = (end[valid] / start[valid]) - 1.0
    if signal_model == "mean_reversion":
        scores = -scores
    return scores


def _portfolio_target_weights(
    *,
    close: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    idx: int,
    lookback_bars: int,
    vol_lookback: int,
    k: int,
    rank_buffer: int,
    prev_long_idx: set[int] | None,
    prev_short_idx: set[int] | None,
    gross_exposure: float,
    signal_model: Literal["momentum", "mean_reversion"],
    lookback_score_mode: Literal["single", "median_3"] = "single",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    symbol_count = close.shape[1]
    weights = np.zeros(symbol_count, dtype=float)
    score_mode = str(lookback_score_mode).strip().lower()
    if score_mode not in {"single", "median_3"}:
        score_mode = "single"
    lb = max(int(lookback_bars), 1)
    score_lb = max(lb, 24 * 28) if score_mode == "median_3" else lb
    if idx <= score_lb or symbol_count < max(k * 2, 2):
        return weights, np.asarray([], dtype=int), np.asarray([], dtype=int), "warmup"
    vol_lb = max(int(vol_lookback), 5)
    if idx < max(score_lb, vol_lb):
        return weights, np.asarray([], dtype=int), np.asarray([], dtype=int), "warmup"

    score_window = close[idx - score_lb : idx + 1]
    if score_window.shape[0] < score_lb + 1:
        return weights, np.asarray([], dtype=int), np.asarray([], dtype=int), "insufficient_score_window"
    valid_price = np.all(
        np.isfinite(open_[idx - score_lb : idx + 1])
        & np.isfinite(high[idx - score_lb : idx + 1])
        & np.isfinite(low[idx - score_lb : idx + 1])
        & np.isfinite(close[idx - score_lb : idx + 1]),
        axis=0,
    ) & (np.min(open_[idx - score_lb : idx + 1], axis=0) > 1e-9) & (np.min(close[idx - score_lb : idx + 1], axis=0) > 1e-9)
    scores = _portfolio_signal_scores(score_window, signal_model=signal_model, mode=score_mode)  # type: ignore[arg-type]
    valid = np.isfinite(scores) & valid_price
    if int(valid.sum()) < max(k * 2, 2):
        return weights, np.asarray([], dtype=int), np.asarray([], dtype=int), "insufficient_valid_symbols"

    valid_idx = np.where(valid)[0]
    ordered = valid_idx[np.argsort(scores[valid_idx])]
    short_idx = ordered[:k].astype(int)
    long_idx = ordered[-k:].astype(int)
    if len(set(long_idx.tolist()).intersection(set(short_idx.tolist()))) > 0:
        return weights, np.asarray([], dtype=int), np.asarray([], dtype=int), "long_short_overlap"
    if rank_buffer > 0 and len(ordered) >= max(k * 2, 2):
        band = min(k + rank_buffer, len(ordered))
        long_pool = ordered[-band:].astype(int)
        short_pool = ordered[:band].astype(int)
        long_core = ordered[-k:].astype(int)
        short_core = ordered[:k].astype(int)
        long_selected: list[int] = []
        short_selected: list[int] = []
        prev_long = prev_long_idx or set()
        prev_short = prev_short_idx or set()
        for x in reversed(long_pool.tolist()):
            if int(x) in prev_long and int(x) not in long_selected:
                long_selected.append(int(x))
            if len(long_selected) >= k:
                break
        for x in reversed(long_core.tolist()):
            if len(long_selected) >= k:
                break
            if int(x) not in long_selected:
                long_selected.append(int(x))
        for x in reversed(long_pool.tolist()):
            if len(long_selected) >= k:
                break
            if int(x) not in long_selected:
                long_selected.append(int(x))
        for x in short_pool:
            if int(x) in prev_short and int(x) not in short_selected:
                short_selected.append(int(x))
            if len(short_selected) >= k:
                break
        for x in short_core.tolist():
            if len(short_selected) >= k:
                break
            if int(x) not in short_selected:
                short_selected.append(int(x))
        for x in short_pool.tolist():
            if len(short_selected) >= k:
                break
            if int(x) not in short_selected:
                short_selected.append(int(x))
        long_idx = np.asarray(long_selected[:k], dtype=int)
        short_idx = np.asarray(short_selected[:k], dtype=int)
        if len(long_idx) < k or len(short_idx) < k:
            return weights, np.asarray([], dtype=int), np.asarray([], dtype=int), "hysteresis_select_failed"
        if len(set(long_idx.tolist()).intersection(set(short_idx.tolist()))) > 0:
            return weights, np.asarray([], dtype=int), np.asarray([], dtype=int), "hysteresis_overlap"

    price_now = close[idx - vol_lb + 1 : idx + 1]
    price_prev = np.maximum(close[idx - vol_lb : idx], 1e-12)
    ret_window = (price_now / price_prev) - 1.0
    vol = np.std(ret_window, axis=0)
    vol = np.where(np.isfinite(vol) & (vol > 1e-9), vol, np.nan)

    long_inv = 1.0 / vol[long_idx]
    short_inv = 1.0 / vol[short_idx]
    if np.any(~np.isfinite(long_inv)) or np.any(~np.isfinite(short_inv)):
        return weights, np.asarray([], dtype=int), np.asarray([], dtype=int), "invalid_vol"
    long_sum = float(np.sum(long_inv))
    short_sum = float(np.sum(short_inv))
    if long_sum <= 0 or short_sum <= 0:
        return weights, np.asarray([], dtype=int), np.asarray([], dtype=int), "invalid_vol_sum"

    half_gross = max(float(gross_exposure), 0.0) * 0.5
    weights[long_idx] = half_gross * (long_inv / long_sum)
    weights[short_idx] = -half_gross * (short_inv / short_sum)
    return weights, long_idx.astype(int), short_idx.astype(int), "ok"


def _portfolio_interval_metrics(interval_pnls: list[float]) -> tuple[float, float]:
    if not interval_pnls:
        return 0.0, 0.0
    arr = np.asarray(interval_pnls, dtype=float)
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    win_rate = float(np.mean(arr > 0))
    profit_factor = float(np.sum(wins) / max(abs(np.sum(losses)), 1e-9)) if losses.size else float("inf")
    return win_rate, profit_factor


def _resolve_turnover_cap_used(
    *,
    cap_mode: Literal["fixed", "adaptive"],
    backlog_ratio: float,
    regime_label: str,
    base_cap: float,
    cap_min: float,
    cap_max: float,
    backlog_thresholds: tuple[float, float, float],
    cap_steps: tuple[float, float, float, float],
    high_vol_cap_max: float,
) -> float | None:
    if cap_mode == "fixed":
        cap = float(base_cap)
    else:
        t1, t2, t3 = backlog_thresholds
        c1, c2, c3, c4 = cap_steps
        if backlog_ratio <= t1:
            cap = float(c1)
        elif backlog_ratio <= t2:
            cap = float(c2)
        elif backlog_ratio <= t3:
            cap = float(c3)
        else:
            cap = float(c4)
    cap = min(max(cap, float(cap_min)), float(cap_max))
    if "high_vol" in regime_label:
        cap = min(cap, max(float(high_vol_cap_max), 0.0))
    if cap <= 0.0:
        return None
    return cap


def _simulate_portfolio(
    *,
    market: PortfolioMarketData,
    params: PortfolioParams,
    base_config: BacktestConfig,
    cost_cfg: PortfolioCostConfig,
    seed: int,
    regime_by_ts: dict[str, str] | None = None,
    regime_mode: Literal["none", "on_off", "sizing"] = "none",
    allowed_regimes: set[str] | None = None,
    regime_size_map: dict[str, float] | None = None,
    regime_turnover_threshold_map: dict[str, float] | None = None,
    debug_mode: bool = False,
    max_cost_ratio_per_bar: float = 0.05,
    dd_controller_enabled: bool = False,
    dd_thresholds: tuple[float, float, float, float] = (0.10, 0.20, 0.30, 0.40),
    dd_gross_mults: tuple[float, float, float, float, float] = (1.0, 0.70, 0.50, 0.30, 0.0),
    dd_recover_thresholds: tuple[float, float, float, float] = (0.08, 0.16, 0.24, 0.32),
    kill_cooldown_bars: int = 168,
    disable_new_entry_when_dd: bool = True,
    rolling_peak_window_bars: int | None = None,
    stage_down_confirm_bars: int = 48,
    stage3_down_confirm_bars: int = 96,
    reentry_ramp_steps: int = 3,
    disable_new_entry_stage: int = 3,
    dd_turnover_threshold_mult: float = 1.5,
    dd_rebalance_mult: float | None = None,
    cap_mode: Literal["fixed", "adaptive"] = "fixed",
    base_cap: float = 0.25,
    cap_min: float = 0.20,
    cap_max: float = 0.40,
    backlog_thresholds: tuple[float, float, float] = (0.25, 0.50, 0.75),
    cap_steps: tuple[float, float, float, float] = (0.25, 0.30, 0.35, 0.40),
    high_vol_cap_max: float = 0.30,
    # legacy alias: fixed turnover cap
    max_turnover_notional_to_equity: float | None = 0.25,
    drift_threshold: float | None = 0.35,
    gross_decay_steps: int = 3,
    max_notional_to_equity_mult: float = 3.0,
    enable_liquidation: bool = True,
    equity_floor_ratio: float = 0.01,
    trading_halt_bars: int = 168,
    skip_trades_if_cost_exceeds_equity_ratio: float = 0.02,
    transition_smoother_enabled: bool = False,
    gross_step_up: float = 0.10,
    gross_step_down: float = 0.25,
    post_halt_cooldown_bars: int = 168,
    post_halt_max_gross: float = 0.15,
    liquidation_lookback_bars: int = 720,
    liquidation_lookback_max_gross: float = 0.15,
    enable_symbol_shock_filters: bool = True,
    max_abs_weight_per_symbol: float = 0.12,
    atr_shock_threshold: float = 2.5,
    gap_shock_threshold: float = 0.10,
    shock_cooldown_bars: int = 72,
    shock_mode: Literal["exclude", "downweight"] = "downweight",
    shock_weight_mult_atr: float = 0.25,
    shock_weight_mult_gap: float = 0.10,
    shock_freeze_rebalance: bool | None = None,
    shock_freeze_min_fraction: float = 0.30,
    lookback_score_mode: Literal["single", "median_3"] = "single",
    stop_on_anomaly: bool = False,
) -> PortfolioSimResult:
    if market.bars <= 2:
        raise ValueError("insufficient bars for portfolio simulation")
    if dd_controller_enabled:
        if len(dd_thresholds) != 4:
            dd_thresholds = (0.10, 0.20, 0.30, 0.40)
        if len(dd_gross_mults) != 5:
            dd_gross_mults = (1.0, 0.70, 0.50, 0.30, 0.0)
        if len(dd_recover_thresholds) != 4:
            dd_recover_thresholds = (0.08, 0.16, 0.24, 0.32)
        dd_thresholds = tuple(sorted(float(x) for x in dd_thresholds))  # type: ignore[assignment]
        dd_recover_thresholds = tuple(sorted(float(x) for x in dd_recover_thresholds))  # type: ignore[assignment]
        dd_gross_mults = tuple(max(float(x), 0.0) for x in dd_gross_mults)  # type: ignore[assignment]
    else:
        dd_thresholds = (1.1, 1.2, 1.3, 1.4)
        dd_recover_thresholds = (1.0, 1.0, 1.0, 1.0)
        dd_gross_mults = (1.0, 1.0, 1.0, 1.0, 1.0)
    if rolling_peak_window_bars is not None and int(rolling_peak_window_bars) <= 0:
        rolling_peak_window_bars = None
    stage_down_confirm_bars = max(int(stage_down_confirm_bars), 1)
    stage3_down_confirm_bars = max(int(stage3_down_confirm_bars), stage_down_confirm_bars)
    reentry_ramp_steps = max(int(reentry_ramp_steps), 1)
    disable_new_entry_stage = max(int(disable_new_entry_stage), 1)
    dd_turnover_threshold_mult = max(float(dd_turnover_threshold_mult), 1.0)
    if dd_rebalance_mult is not None and float(dd_rebalance_mult) <= 1.0:
        dd_rebalance_mult = None
    equity_floor_ratio = min(max(float(equity_floor_ratio), 0.0), 0.99)
    trading_halt_bars = max(int(trading_halt_bars), 0)
    skip_trades_if_cost_exceeds_equity_ratio = max(float(skip_trades_if_cost_exceeds_equity_ratio), 0.0)
    gross_step_up = max(float(gross_step_up), 0.0)
    gross_step_down = max(float(gross_step_down), 0.0)
    post_halt_cooldown_bars = max(int(post_halt_cooldown_bars), 0)
    post_halt_max_gross = max(float(post_halt_max_gross), 0.0)
    liquidation_lookback_bars = max(int(liquidation_lookback_bars), 0)
    liquidation_lookback_max_gross = max(float(liquidation_lookback_max_gross), 0.0)
    max_abs_weight_per_symbol = max(float(max_abs_weight_per_symbol), 0.0)
    atr_shock_threshold = max(float(atr_shock_threshold), 0.0)
    gap_shock_threshold = max(float(gap_shock_threshold), 0.0)
    shock_cooldown_bars = max(int(shock_cooldown_bars), 0)
    shock_mode = str(shock_mode).strip().lower()  # type: ignore[assignment]
    if shock_mode not in {"exclude", "downweight"}:
        shock_mode = "downweight"  # type: ignore[assignment]
    shock_weight_mult_atr = min(max(float(shock_weight_mult_atr), 0.0), 1.0)
    shock_weight_mult_gap = min(max(float(shock_weight_mult_gap), 0.0), 1.0)
    if shock_freeze_rebalance is None:
        shock_freeze_rebalance = bool(shock_mode == "downweight")
    else:
        shock_freeze_rebalance = bool(shock_freeze_rebalance)
    shock_freeze_min_fraction = min(max(float(shock_freeze_min_fraction), 0.0), 1.0)
    lookback_score_mode = str(lookback_score_mode).strip().lower()  # type: ignore[assignment]
    if lookback_score_mode not in {"single", "median_3"}:
        lookback_score_mode = "single"  # type: ignore[assignment]
    if cap_mode not in {"fixed", "adaptive"}:
        cap_mode = "fixed"
    t = tuple(float(x) for x in backlog_thresholds)
    if len(t) != 3:
        t = (0.25, 0.50, 0.75)
    c = tuple(float(x) for x in cap_steps)
    if len(c) != 4:
        c = (0.25, 0.30, 0.35, 0.40)
    backlog_thresholds = tuple(sorted(t))  # type: ignore[assignment]
    cap_steps = c  # type: ignore[assignment]
    n = market.bars
    s = market.symbol_count
    close = market.close
    open_ = market.open
    high = market.high
    low = market.low
    atr = market.atr
    timestamps = market.timestamps

    qty = np.zeros(s, dtype=float)
    cash = float(base_config.initial_equity)
    initial_equity = float(base_config.initial_equity)
    equity_floor = max(initial_equity * equity_floor_ratio, 1e-9)
    equity_points: list[dict[str, Any]] = []
    dd_rows: list[dict[str, Any]] = []
    gross_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    risk_cap_rows: list[dict[str, Any]] = []
    position_rows: list[dict[str, Any]] = []
    turnover_rows: list[dict[str, Any]] = []
    cost_rows: list[dict[str, Any]] = []
    liquidation_rows: list[dict[str, Any]] = []
    interval_pnls: list[float] = []
    trade_count = 0
    fill_count = 0
    reject_count = 0
    skipped_trade_count = 0
    cost_fee_total = 0.0
    cost_slippage_total = 0.0
    cost_penalty_total = 0.0
    rebalance_attempts = 0
    rebalance_execs = 0
    skip_reasons: dict[str, int] = {}
    debug_events: list[dict[str, Any]] = []
    liquidation_reason_counts: dict[str, int] = {
        "negative_equity_due_to_fee": 0,
        "negative_equity_due_to_slippage": 0,
        "negative_equity_due_to_penalty": 0,
        "negative_equity_due_to_price_gap": 0,
        "negative_equity_due_to_gross_transition": 0,
        "negative_equity_due_to_backlog_execution": 0,
        "negative_equity_due_to_other": 0,
    }
    excluded_counts_by_reason: dict[str, int] = {"atr_shock": 0, "gap_shock": 0, "missing_data": 0}
    shocked_counts_by_reason: dict[str, int] = {"atr_shock": 0, "gap_shock": 0, "missing_data": 0}
    shocked_symbol_counts: dict[str, int] = {}
    shocked_mult_sum_by_reason: dict[str, float] = {"atr_shock": 0.0, "gap_shock": 0.0, "missing_data": 0.0}
    shocked_mult_count_by_reason: dict[str, int] = {"atr_shock": 0, "gap_shock": 0, "missing_data": 0}
    cap_hit_counts_by_symbol: dict[str, int] = {}

    rng = random.Random(int(seed))
    warmup = max(params.lookback_bars, params.vol_lookback + 1)
    rebalance_idxs = _portfolio_rebalance_indices(n, params.rebalance_bars, warmup)

    prev_rebalance_equity: float | None = None
    last_weights = np.zeros(s, dtype=float)
    prev_long_idx: set[int] = set()
    prev_short_idx: set[int] = set()
    prev_effective_scale: float = 1.0
    phased_progress: int = 0
    off_bars = 0
    on_bars = 0
    off_to_on_count = 0
    on_to_off_count = 0
    gross_change_turnover = 0.0
    off_decay_progress = 0
    cap_hit_count = 0
    executed_fraction_sum = 0.0
    executed_fraction_count = 0
    backlog_notional_sum = 0.0
    backlog_notional_max = 0.0
    backlog_ratio_sum = 0.0
    backlog_ratio_max = 0.0
    prev_backlog_notional = 0.0
    cap_used_sum = 0.0
    cap_used_count = 0
    cap_histogram: dict[str, int] = {}
    reduce_first_candidates = 0
    reduce_first_execs = 0
    reduce_first_total_execs = 0
    effective_base_cap = float(base_cap)
    if cap_mode == "fixed" and max_turnover_notional_to_equity is not None:
        effective_base_cap = float(max_turnover_notional_to_equity)
    drift_force_count = 0
    peak_equity = float(base_config.initial_equity)
    dd_stage = 0
    dd_trigger_counts: dict[str, int] = {f"stage_{i}": 0 for i in range(len(dd_gross_mults))}
    time_in_dd_stage: dict[str, int] = {f"stage_{i}": 0 for i in range(len(dd_gross_mults))}
    kill_switch_count = 0
    kill_switch_first_ts: str | None = None
    kill_bars = 0
    kill_cooldown_left = 0
    stage_transitions_up = 0
    stage_transitions_down = 0
    stage_down_confirm_accum_bars = 0
    prev_attempt_idx: int | None = None
    dd_rebalance_counter = 0
    stage3_streak_bars = 0
    stage3_longest_streak_bars = 0
    gross_sum_by_stage: dict[str, float] = {f"stage_{i}": 0.0 for i in range(len(dd_gross_mults))}
    gross_cnt_by_stage: dict[str, int] = {f"stage_{i}": 0 for i in range(len(dd_gross_mults))}
    reentry_ramp_active = False
    reentry_ramp_from = 1.0
    reentry_ramp_to = 1.0
    reentry_ramp_progress = 0
    dd_mult_last = 1.0
    trading_halt_left = 0
    post_halt_cooldown_left = 0
    post_halt_reentry_step = 0
    last_halt_release_idx: int | None = None
    liquidation_indices: list[int] = []
    symbol_shock_cooldown_left = np.zeros(s, dtype=int)
    symbol_shock_reason = np.array([""] * s, dtype=object)
    any_shock_active_bar_count = 0
    shock_active_bars_count = 0
    rebalance_skipped_due_to_shock_count = 0
    liquidation_after_halt_count = 0
    gross_transition_magnitude_sum = 0.0
    gross_transition_max = 0.0
    gross_error_sum = 0.0
    gross_error_max = 0.0
    applied_gross_sum = 0.0
    applied_gross_sq_sum = 0.0
    applied_gross_count = 0
    gross_exposure_time_sum = 0.0
    fee_cost_per_gross_time = 0.0
    rolling_peak_records: list[tuple[int, float]] = []
    regime_off_bars: dict[str, int] = {}
    regime_total_bars: dict[str, int] = {}
    for idx in rebalance_idxs:
        rebalance_attempts += 1
        mark_prices = close[idx]
        equity = float(cash + np.dot(qty, mark_prices))
        if not math.isfinite(equity):
            equity = equity_floor if enable_liquidation else 0.0
            qty[:] = 0.0
            cash = equity
        if prev_rebalance_equity is not None:
            interval_pnls.append(equity - prev_rebalance_equity)
        prev_rebalance_equity = equity
        equity_abs = max(abs(equity), 1e-9)
        current_weights = (qty * mark_prices) / equity_abs
        had_open_position = bool(np.any(np.abs(qty) > 1e-12))
        liquidation_triggered = False
        if enable_liquidation and equity <= equity_floor and had_open_position:
            liquidation_triggered = True
            trading_halt_left = max(trading_halt_left, trading_halt_bars)

        ts_key = _ts_key(timestamps[idx])
        regime_label = regime_by_ts.get(ts_key, "range|low_vol") if regime_by_ts else "all"
        regime_scale = 1.0
        if regime_mode == "on_off":
            allowed = allowed_regimes or {"trend|low_vol", "trend|high_vol", "range|low_vol", "range|high_vol"}
            if regime_label not in allowed:
                regime_scale = 0.0
        elif regime_mode == "sizing":
            regime_scale = float((regime_size_map or {}).get(regime_label, 1.0))
        regime_total_bars[regime_label] = int(regime_total_bars.get(regime_label, 0)) + 1

        bars_elapsed = max((idx - prev_attempt_idx), 1) if prev_attempt_idx is not None else max(int(params.rebalance_bars), 1)
        prev_attempt_idx = idx
        if enable_symbol_shock_filters and np.any(symbol_shock_cooldown_left > 0):
            symbol_shock_cooldown_left = np.maximum(symbol_shock_cooldown_left - bars_elapsed, 0)
            for sym_i in range(s):
                if symbol_shock_cooldown_left[sym_i] <= 0:
                    symbol_shock_reason[sym_i] = ""
        halt_left_before = int(trading_halt_left)
        if trading_halt_left > 0 and not liquidation_triggered:
            trading_halt_left = max(trading_halt_left - bars_elapsed, 0)
        if halt_left_before > 0 and trading_halt_left == 0:
            post_halt_cooldown_left = max(post_halt_cooldown_bars, 0)
            post_halt_reentry_step = 0
            last_halt_release_idx = idx
        if post_halt_cooldown_left > 0 and trading_halt_left == 0:
            post_halt_cooldown_left = max(post_halt_cooldown_left - bars_elapsed, 0)
            post_halt_reentry_step += 1

        peak_equity = max(peak_equity, float(equity))
        if rolling_peak_window_bars is None:
            signal_peak = peak_equity
            peak_type = "absolute"
        else:
            rolling_peak_records.append((idx, float(equity)))
            window_bars = int(rolling_peak_window_bars)
            while rolling_peak_records and (idx - rolling_peak_records[0][0]) > window_bars:
                rolling_peak_records.pop(0)
            signal_peak = max((v for _, v in rolling_peak_records), default=float(equity))
            peak_type = "rolling"
        signal_peak = max(float(signal_peak), 1e-9)
        drawdown_pre = max(0.0, min(1.0, 1.0 - (equity / signal_peak)))
        max_dd_stage = len(dd_gross_mults) - 1
        desired_dd_stage = 0
        for i, thr in enumerate(dd_thresholds, start=1):
            if drawdown_pre >= float(thr):
                desired_dd_stage = i
        desired_dd_stage = min(max(desired_dd_stage, 0), max_dd_stage)

        if desired_dd_stage > dd_stage:
            for lvl in range(dd_stage + 1, desired_dd_stage + 1):
                dd_trigger_counts[f"stage_{lvl}"] = int(dd_trigger_counts.get(f"stage_{lvl}", 0)) + 1
            stage_transitions_up += int(desired_dd_stage - dd_stage)
            dd_stage = desired_dd_stage
            stage_down_confirm_accum_bars = 0
            reentry_ramp_active = False
            if dd_stage >= max_dd_stage:
                kill_switch_count += 1
                if kill_switch_first_ts is None:
                    kill_switch_first_ts = str(timestamps[idx])
                kill_cooldown_left = max(int(kill_cooldown_bars), 0)
        elif desired_dd_stage < dd_stage:
            can_down = True
            if dd_stage >= max_dd_stage and kill_cooldown_left > 0:
                can_down = False
            if can_down:
                candidate_stage = dd_stage - 1
                recover_idx = min(max(dd_stage - 1, 0), len(dd_recover_thresholds) - 1)
                recover_thr = float(dd_recover_thresholds[recover_idx])
                need_confirm = stage3_down_confirm_bars if dd_stage == 3 else stage_down_confirm_bars
                if drawdown_pre <= recover_thr:
                    stage_down_confirm_accum_bars += bars_elapsed
                else:
                    stage_down_confirm_accum_bars = 0
                if stage_down_confirm_accum_bars >= need_confirm and candidate_stage >= desired_dd_stage:
                    prev_mult = float(dd_mult_last)
                    dd_stage = candidate_stage
                    stage_transitions_down += 1
                    stage_down_confirm_accum_bars = 0
                    tgt_mult = float(dd_gross_mults[min(dd_stage, max_dd_stage)])
                    if tgt_mult > prev_mult and reentry_ramp_steps > 1:
                        reentry_ramp_active = True
                        reentry_ramp_from = prev_mult
                        reentry_ramp_to = tgt_mult
                        reentry_ramp_progress = 0
        else:
            stage_down_confirm_accum_bars = 0

        if dd_stage >= max_dd_stage:
            kill_bars += bars_elapsed
            if kill_cooldown_left > 0:
                kill_cooldown_left = max(kill_cooldown_left - bars_elapsed, 0)

        target_dd_mult = float(dd_gross_mults[min(dd_stage, max_dd_stage)])
        if reentry_ramp_active:
            reentry_ramp_progress += 1
            frac = min(reentry_ramp_progress / max(reentry_ramp_steps, 1), 1.0)
            dd_gross_mult = reentry_ramp_from + (reentry_ramp_to - reentry_ramp_from) * frac
            if frac >= 1.0:
                reentry_ramp_active = False
        else:
            dd_gross_mult = target_dd_mult
        dd_mult_last = float(dd_gross_mult)
        time_in_dd_stage[f"stage_{dd_stage}"] = int(time_in_dd_stage.get(f"stage_{dd_stage}", 0)) + bars_elapsed
        if dd_stage == 3:
            stage3_streak_bars += bars_elapsed
            stage3_longest_streak_bars = max(stage3_longest_streak_bars, stage3_streak_bars)
        else:
            stage3_streak_bars = 0

        # OFF->ON transition control: grace on OFF and phased re-entry on ON.
        effective_scale = regime_scale
        off_reduce_only = False
        decay_factor = 0.0
        if regime_scale <= 0.0:
            off_bars += 1
            on_bars = 0
            regime_off_bars[regime_label] = int(regime_off_bars.get(regime_label, 0)) + 1
            if off_bars <= max(params.off_grace_bars, 0):
                effective_scale = max(prev_effective_scale, 0.0)
                off_decay_progress = 0
            else:
                off_reduce_only = True
                steps = max(int(gross_decay_steps), 1)
                off_decay_progress = min(off_decay_progress + 1, steps)
                decay_factor = max((steps - off_decay_progress) / steps, 0.0)
                effective_scale = decay_factor
                phased_progress = 0
        else:
            on_bars += 1
            off_bars = 0
            off_decay_progress = 0
            steps = max(params.phased_entry_steps, 1)
            if prev_effective_scale <= 0.0 and steps > 1:
                phased_progress = 1
                effective_scale = regime_scale * (phased_progress / steps)
            elif 0 < phased_progress < steps:
                phased_progress += 1
                effective_scale = regime_scale * (phased_progress / steps)
            else:
                phased_progress = steps
                effective_scale = regime_scale
        if prev_effective_scale > 0.0 and effective_scale <= 0.0:
            on_to_off_count += 1
        elif prev_effective_scale <= 0.0 and effective_scale > 0.0:
            off_to_on_count += 1

        target_effective_scale = max(effective_scale * dd_gross_mult, 0.0)
        if transition_smoother_enabled and post_halt_cooldown_left > 0 and trading_halt_left == 0:
            post_steps = max(max(params.phased_entry_steps, 5), 1)
            post_ramp = min(post_halt_reentry_step / max(post_steps, 1), 1.0)
            post_cap_scale = (post_halt_max_gross * post_ramp) / max(params.gross_exposure, 1e-9)
            target_effective_scale = min(target_effective_scale, max(post_cap_scale, 0.0))
        if transition_smoother_enabled and liquidation_lookback_bars > 0:
            lookback_start = idx - liquidation_lookback_bars
            liquidation_indices = [x for x in liquidation_indices if x >= lookback_start]
            if liquidation_indices:
                liq_cap_scale = liquidation_lookback_max_gross / max(params.gross_exposure, 1e-9)
                target_effective_scale = min(target_effective_scale, max(liq_cap_scale, 0.0))
        if transition_smoother_enabled and not liquidation_triggered:
            delta_scale = target_effective_scale - prev_effective_scale
            if delta_scale > gross_step_up:
                delta_scale = gross_step_up
            elif delta_scale < -gross_step_down:
                delta_scale = -gross_step_down
            effective_scale = max(prev_effective_scale + delta_scale, 0.0)
        else:
            effective_scale = max(target_effective_scale, 0.0)
        if off_reduce_only and transition_smoother_enabled:
            if prev_effective_scale > 1e-9:
                decay_factor = min(max(effective_scale / prev_effective_scale, 0.0), 1.0)
            else:
                decay_factor = 0.0

        target_gross = float(params.gross_exposure * target_effective_scale)
        applied_gross = float(params.gross_exposure * effective_scale)
        gross_transition_mag = abs(applied_gross - float(params.gross_exposure * prev_effective_scale))
        gross_transition_magnitude_sum += gross_transition_mag
        gross_transition_max = max(gross_transition_max, gross_transition_mag)
        gross_error = abs(target_gross - applied_gross)
        gross_error_sum += gross_error
        gross_error_max = max(gross_error_max, gross_error)
        applied_gross_sum += applied_gross
        applied_gross_sq_sum += applied_gross * applied_gross
        applied_gross_count += 1
        gross_exposure_time_sum += applied_gross * bars_elapsed
        gross_rows.append(
            {
                "timestamp": str(timestamps[idx]),
                "target_gross": target_gross,
                "applied_gross": applied_gross,
                "dd_stage": int(dd_stage),
                "regime": regime_label,
            }
        )

        dd_reduce_only = dd_stage >= max_dd_stage
        if dd_reduce_only:
            off_reduce_only = True
        if liquidation_triggered:
            dd_reduce_only = True
            off_reduce_only = True
        halt_reduce_only = False
        if enable_liquidation and trading_halt_left > 0:
            if float(np.sum(np.abs(current_weights))) > 1e-9:
                halt_reduce_only = True
                off_reduce_only = True
        turnover_threshold = float((regime_turnover_threshold_map or {}).get(regime_label, params.turnover_threshold))
        if dd_stage >= 2:
            turnover_threshold *= dd_turnover_threshold_mult
        if transition_smoother_enabled and (dd_stage >= 2 or "high_vol" in regime_label):
            turnover_threshold *= 2.0

        if liquidation_triggered:
            target_weights = np.zeros(s, dtype=float)
            long_idx = np.asarray([], dtype=int)
            short_idx = np.asarray([], dtype=int)
            target_status = "liquidation_reduce_only"
        elif dd_reduce_only:
            target_weights = np.zeros(s, dtype=float)
            long_idx = np.asarray([], dtype=int)
            short_idx = np.asarray([], dtype=int)
            target_status = "dd_kill_reduce_only"
        elif halt_reduce_only:
            target_weights = np.zeros(s, dtype=float)
            long_idx = np.asarray([], dtype=int)
            short_idx = np.asarray([], dtype=int)
            target_status = "trading_halt_reduce_only"
        elif enable_liquidation and trading_halt_left > 0:
            target_weights = current_weights.copy()
            long_idx = np.where(target_weights > 1e-12)[0].astype(int)
            short_idx = np.where(target_weights < -1e-12)[0].astype(int)
            target_status = "trading_halt"
        elif off_reduce_only:
            target_weights = current_weights * decay_factor
            long_idx = np.where(target_weights > 1e-12)[0].astype(int)
            short_idx = np.where(target_weights < -1e-12)[0].astype(int)
            target_status = "off_reduce_only"
        else:
            target_weights, long_idx, short_idx, target_status = _portfolio_target_weights(
                close=close,
                open_=open_,
                high=high,
                low=low,
                idx=idx,
                lookback_bars=params.lookback_bars,
                vol_lookback=params.vol_lookback,
                k=params.k,
                rank_buffer=params.rank_buffer,
                prev_long_idx=prev_long_idx,
                prev_short_idx=prev_short_idx,
                gross_exposure=params.gross_exposure * effective_scale,
                signal_model=params.signal_model,
                lookback_score_mode=lookback_score_mode,  # type: ignore[arg-type]
            )
            if target_status != "ok":
                skip_reasons[target_status] = int(skip_reasons.get(target_status, 0)) + 1
                if target_status in {"warmup", "insufficient_valid_symbols"}:
                    target_weights = np.zeros(s, dtype=float)
        entry_block_stage = max(int(disable_new_entry_stage), 1)
        if disable_new_entry_when_dd and dd_stage >= entry_block_stage and target_weights.size == current_weights.size and not dd_reduce_only:
            clipped = target_weights.copy()
            for sym_i in range(s):
                cw = float(current_weights[sym_i])
                tw = float(clipped[sym_i])
                if abs(cw) <= 1e-9:
                    clipped[sym_i] = 0.0
                    continue
                if cw * tw < 0.0:
                    clipped[sym_i] = 0.0
                    continue
                if abs(tw) > abs(cw):
                    clipped[sym_i] = cw
            target_weights = clipped

        raw_weights = target_weights.copy()
        capped_weights = target_weights.copy()
        atr_ratio_vals = np.full(s, np.nan, dtype=float)
        shock_flags_vals = [""] * s
        if idx >= 1:
            prev_px = np.maximum(close[idx - 1], 1e-12)
            ret_1bar = (close[idx] / prev_px) - 1.0
        else:
            ret_1bar = np.zeros(s, dtype=float)
        atr_short = np.full(s, np.nan, dtype=float)
        atr_long = np.full(s, np.nan, dtype=float)
        if idx >= 24:
            atr_short = np.nanmean(atr[idx - 24 + 1 : idx + 1], axis=0)
        if idx >= 24 * 14:
            atr_long = np.nanmean(atr[idx - (24 * 14) + 1 : idx + 1], axis=0)
        valid_atr_ratio = np.isfinite(atr_short) & np.isfinite(atr_long) & (atr_long > 1e-9)
        atr_ratio_vals[valid_atr_ratio] = atr_short[valid_atr_ratio] / atr_long[valid_atr_ratio]
        any_shock_active_this_bar = False
        shocked_symbol_count_this_bar = 0
        for sym_i, symbol in enumerate(market.symbols):
            flags: list[str] = []
            applied_mult = 1.0
            missing_data = (not math.isfinite(float(close[idx, sym_i]))) or float(close[idx, sym_i]) <= 1e-9
            if not missing_data and (not math.isfinite(float(open_[idx, sym_i])) or float(open_[idx, sym_i]) <= 1e-9):
                missing_data = True
            if missing_data:
                flags.append("missing_data")
            if enable_symbol_shock_filters:
                ratio_i = float(atr_ratio_vals[sym_i]) if math.isfinite(float(atr_ratio_vals[sym_i])) else float("nan")
                ret_i = float(ret_1bar[sym_i]) if math.isfinite(float(ret_1bar[sym_i])) else 0.0
                trigger_reason = ""
                if math.isfinite(ratio_i) and ratio_i >= atr_shock_threshold and atr_shock_threshold > 0.0:
                    trigger_reason = "atr_shock"
                if abs(ret_i) >= gap_shock_threshold and gap_shock_threshold > 0.0:
                    trigger_reason = "gap_shock"
                if trigger_reason:
                    symbol_shock_cooldown_left[sym_i] = max(int(symbol_shock_cooldown_left[sym_i]), int(shock_cooldown_bars))
                    symbol_shock_reason[sym_i] = trigger_reason
                if int(symbol_shock_cooldown_left[sym_i]) > 0:
                    cool_reason = str(symbol_shock_reason[sym_i]) if str(symbol_shock_reason[sym_i]) else "atr_shock"
                    any_shock_active_this_bar = True
                    shocked_symbol_count_this_bar += 1
                    flags.append(cool_reason)
                    if shock_mode == "exclude":
                        applied_mult = 0.0
                    else:
                        applied_mult = shock_weight_mult_gap if cool_reason == "gap_shock" else shock_weight_mult_atr
                    shocked_counts_by_reason[cool_reason] = int(shocked_counts_by_reason.get(cool_reason, 0)) + 1
                    shocked_symbol_counts[symbol] = int(shocked_symbol_counts.get(symbol, 0)) + 1
                    shocked_mult_sum_by_reason[cool_reason] = float(shocked_mult_sum_by_reason.get(cool_reason, 0.0)) + float(applied_mult)
                    shocked_mult_count_by_reason[cool_reason] = int(shocked_mult_count_by_reason.get(cool_reason, 0)) + 1
                    excluded_rows.append(
                        {
                            "timestamp": str(timestamps[idx]),
                            "symbol": symbol,
                            "reason": cool_reason,
                            "applied_mult": float(applied_mult),
                            "cooldown_remaining": int(symbol_shock_cooldown_left[sym_i]),
                        }
                    )
            if "missing_data" in flags:
                applied_mult = 0.0
                shocked_counts_by_reason["missing_data"] = int(shocked_counts_by_reason.get("missing_data", 0)) + 1
                shocked_symbol_counts[symbol] = int(shocked_symbol_counts.get(symbol, 0)) + 1
                shocked_mult_sum_by_reason["missing_data"] = float(shocked_mult_sum_by_reason.get("missing_data", 0.0))
                shocked_mult_count_by_reason["missing_data"] = int(shocked_mult_count_by_reason.get("missing_data", 0)) + 1
                excluded_rows.append(
                    {
                        "timestamp": str(timestamps[idx]),
                        "symbol": symbol,
                        "reason": "missing_data",
                        "applied_mult": float(applied_mult),
                        "cooldown_remaining": int(symbol_shock_cooldown_left[sym_i]) if enable_symbol_shock_filters else 0,
                    }
                )
            capped_weights[sym_i] = float(capped_weights[sym_i]) * float(applied_mult)
            if applied_mult <= 1e-12 and flags:
                for reason in flags:
                    if reason in excluded_counts_by_reason:
                        excluded_counts_by_reason[reason] = int(excluded_counts_by_reason.get(reason, 0)) + 1
            cap_hit = False
            if max_abs_weight_per_symbol > 0.0 and abs(float(capped_weights[sym_i])) > max_abs_weight_per_symbol:
                capped_weights[sym_i] = math.copysign(max_abs_weight_per_symbol, float(capped_weights[sym_i]))
                cap_hit = True
                cap_hit_counts_by_symbol[symbol] = int(cap_hit_counts_by_symbol.get(symbol, 0)) + 1
            flags_str = "|".join(sorted(set(flags)))
            shock_flags_vals[sym_i] = flags_str
            risk_cap_rows.append(
                {
                    "timestamp": str(timestamps[idx]),
                    "symbol": symbol,
                    "raw_weight": float(raw_weights[sym_i]),
                    "capped_weight": float(capped_weights[sym_i]),
                    "cap_hit": bool(cap_hit),
                    "atr_ratio": float(atr_ratio_vals[sym_i]) if math.isfinite(float(atr_ratio_vals[sym_i])) else np.nan,
                    "shock_flags": flags_str,
                }
            )
        if any_shock_active_this_bar:
            any_shock_active_bar_count += 1
        shock_fraction_this_bar = float(shocked_symbol_count_this_bar / max(s, 1))
        shock_active_this_bar = bool(shock_fraction_this_bar >= shock_freeze_min_fraction)
        if shock_active_this_bar:
            shock_active_bars_count += 1
        target_weights = capped_weights
        long_idx = np.where(target_weights > 1e-12)[0].astype(int)
        short_idx = np.where(target_weights < -1e-12)[0].astype(int)

        turnover_ratio = float(np.sum(np.abs(target_weights - current_weights)))
        threshold = max(turnover_threshold, 0.0)
        force_rebalance = drift_threshold is not None and turnover_ratio >= max(float(drift_threshold), 0.0)
        force_liquidation_rebalance = bool(liquidation_triggered or halt_reduce_only)
        rebalance_applied = (turnover_ratio >= threshold) or bool(force_rebalance) or force_liquidation_rebalance
        rebalance_skipped_due_to_shock = False
        if force_rebalance and turnover_ratio < threshold:
            drift_force_count += 1
        dd_rebalance_effective = dd_rebalance_mult
        if transition_smoother_enabled and dd_rebalance_effective is None and (dd_stage >= 2 or "high_vol" in regime_label):
            dd_rebalance_effective = 2.0
        if dd_stage >= 2 and dd_rebalance_effective is not None and not force_liquidation_rebalance:
            dd_stride = max(int(round(float(dd_rebalance_effective))), 1)
            dd_rebalance_counter += 1
            if dd_rebalance_counter % dd_stride != 0:
                rebalance_applied = False
                target_weights = current_weights.copy()
                skip_reasons["dd_rebalance_sparsified"] = int(skip_reasons.get("dd_rebalance_sparsified", 0)) + 1
        else:
            dd_rebalance_counter = 0
        safety_rebalance = bool(force_liquidation_rebalance or dd_reduce_only or halt_reduce_only)
        if (
            shock_freeze_rebalance
            and shock_active_this_bar
            and rebalance_applied
            and not safety_rebalance
        ):
            rebalance_applied = False
            target_weights = current_weights.copy()
            rebalance_skipped_due_to_shock = True
            rebalance_skipped_due_to_shock_count += 1
        if not rebalance_applied:
            target_weights = current_weights.copy()
            if rebalance_skipped_due_to_shock:
                skip_reasons["shock_rebalance_freeze"] = int(skip_reasons.get("shock_rebalance_freeze", 0)) + 1
            elif enable_liquidation and trading_halt_left > 0:
                skip_reasons["trading_halt"] = int(skip_reasons.get("trading_halt", 0)) + 1
            else:
                skip_reasons["turnover_below_threshold"] = int(skip_reasons.get("turnover_below_threshold", 0)) + 1

        exec_idx = min(idx + 1 + max(int(cost_cfg.latency_bars), 0), n - 1)
        delta_qty = np.zeros(s, dtype=float)
        target_qty_ref = qty.copy()
        tradable_equity = max(equity, 0.0)
        backlog_ratio_pre = float(prev_backlog_notional / max(tradable_equity, 1e-9)) if tradable_equity > 0 else 0.0
        cap_used = _resolve_turnover_cap_used(
            cap_mode=cap_mode,
            backlog_ratio=backlog_ratio_pre,
            regime_label=regime_label,
            base_cap=effective_base_cap,
            cap_min=cap_min,
            cap_max=cap_max,
            backlog_thresholds=backlog_thresholds,
            cap_steps=cap_steps,
            high_vol_cap_max=high_vol_cap_max,
        )
        if cap_used is not None:
            cap_used_sum += cap_used
            cap_used_count += 1
            cap_key = f"{cap_used:.2f}"
            cap_histogram[cap_key] = int(cap_histogram.get(cap_key, 0)) + 1
        else:
            cap_histogram["off"] = int(cap_histogram.get("off", 0)) + 1
        planned_turnover_notional = 0.0
        turnover_cap_notional = -1.0
        turnover_executed_fraction = 0.0
        if tradable_equity > 1e-9:
            target_notional = target_weights * tradable_equity
            base_exec_price = np.asarray(open_[exec_idx], dtype=float)
            invalid_px = (~np.isfinite(base_exec_price)) | (base_exec_price <= 1e-9)
            if np.any(invalid_px):
                target_notional[invalid_px] = 0.0
                skip_reasons["invalid_exec_price"] = int(skip_reasons.get("invalid_exec_price", 0)) + int(np.sum(invalid_px))
            base_exec_price = np.where(invalid_px, 1.0, base_exec_price)
            target_qty = target_notional / base_exec_price
            target_qty_ref = target_qty.copy()
            delta_qty_full = target_qty - qty
            planned_turnover_notional = float(np.sum(np.abs(delta_qty_full * base_exec_price)))
            delta_qty = delta_qty_full.copy()
            if cap_used is not None:
                turnover_cap_notional = max(float(cap_used), 0.0) * tradable_equity
                if planned_turnover_notional > max(turnover_cap_notional, 1e-9):
                    scale = turnover_cap_notional / max(planned_turnover_notional, 1e-9)
                    delta_qty = delta_qty_full * max(scale, 0.0)
                    cap_hit_count += 1
                    skip_reasons["turnover_cap_scaled"] = int(skip_reasons.get("turnover_cap_scaled", 0)) + 1
            if off_reduce_only:
                # OFF regime unwind should only reduce existing exposure, not rotate into new risk.
                mask_increase = np.abs(qty + delta_qty) > np.abs(qty) + 1e-12
                delta_qty[mask_increase] = 0.0
                if np.any(mask_increase):
                    skip_reasons["off_reduce_only_clip"] = int(skip_reasons.get("off_reduce_only_clip", 0)) + int(np.sum(mask_increase))
        else:
            delta_qty = -qty
            if np.any(np.abs(delta_qty) > 0.0):
                planned_turnover_notional = float(np.sum(np.abs(delta_qty * np.maximum(open_[exec_idx], 1e-9))))
                target_qty_ref = np.zeros(s, dtype=float)
                turnover_executed_fraction = 1.0
            if tradable_equity <= 1e-9:
                if not enable_liquidation:
                    skip_reasons["equity_zero_or_negative"] = int(skip_reasons.get("equity_zero_or_negative", 0)) + 1

        if rebalance_applied and planned_turnover_notional > 0.0 and tradable_equity > 0.0:
            taker_fee_rate = _portfolio_fee_rate(order_model="market", base_config=base_config, fee_multiplier=cost_cfg.fee_multiplier)
            slip_frac_est = max(float(cost_cfg.slippage_bps), 0.0) / 10_000.0
            if cost_cfg.slippage_mode in {"atr", "mixed"}:
                slip_frac_est += max(float(cost_cfg.atr_slippage_mult), 0.0) * 0.01
            penalty_frac_est = max(float(cost_cfg.limit_unfilled_penalty_bps), 0.0) / 10_000.0 if cost_cfg.order_model == "limit" else 0.0
            conservative_cost_est = planned_turnover_notional * (taker_fee_rate + slip_frac_est + penalty_frac_est)
            cost_skip_ratio = float(skip_trades_if_cost_exceeds_equity_ratio)
            if dd_stage >= 2 or "high_vol" in regime_label:
                cost_skip_ratio *= 0.75
            if cost_skip_ratio > 0.0 and conservative_cost_est > (tradable_equity * cost_skip_ratio):
                delta_qty[:] = 0.0
                planned_turnover_notional = 0.0
                target_qty_ref = qty.copy()
                skip_reasons["insufficient_equity_for_cost"] = int(skip_reasons.get("insufficient_equity_for_cost", 0)) + 1

        scenario_fee = 0.0
        scenario_slip = 0.0
        scenario_penalty = 0.0
        turnover_notional = 0.0
        trades_this_bar = 0
        bar_blocked = False
        equity_frac = max(min(tradable_equity / max(initial_equity, 1e-9), 1.0), 0.0)
        dynamic_cost_ratio = min(max(max_cost_ratio_per_bar, 0.0), 0.02 * max(equity_frac, 0.1))
        bar_cost_budget = dynamic_cost_ratio * max(tradable_equity, 0.0)

        trade_legs: list[tuple[int, float, bool]] = []
        for sym_i in range(s):
            dqty = float(delta_qty[sym_i])
            if (not math.isfinite(dqty)) or abs(dqty) <= 1e-9:
                continue
            current_qty = float(qty[sym_i])
            target_qty_sym = current_qty + dqty
            if abs(current_qty) > 1e-9 and abs(target_qty_sym) > 1e-9 and (current_qty * target_qty_sym) < 0:
                close_leg = -current_qty
                open_leg = target_qty_sym
                if abs(close_leg) > 1e-9:
                    trade_legs.append((sym_i, close_leg, True))
                if abs(open_leg) > 1e-9:
                    trade_legs.append((sym_i, open_leg, False))
            else:
                is_reduce = abs(target_qty_sym) < abs(current_qty) - 1e-12
                trade_legs.append((sym_i, dqty, bool(is_reduce)))
        reduce_first_candidates += sum(1 for _, _, is_reduce in trade_legs if is_reduce)
        trade_legs.sort(key=lambda x: (0 if x[2] else 1, -abs(x[1])))

        for sym_i, planned_dqty, is_reduce in trade_legs:
            dqty = float(planned_dqty)
            if (not math.isfinite(dqty)) or abs(dqty) <= 1e-12:
                continue
            side_buy = dqty > 0
            side_sign = 1.0 if side_buy else -1.0
            base_price = float(open_[exec_idx, sym_i])
            if (not math.isfinite(base_price)) or base_price <= 1e-9:
                skip_reasons["invalid_base_price"] = int(skip_reasons.get("invalid_base_price", 0)) + 1
                skipped_trade_count += 1
                continue
            if turnover_cap_notional > 0:
                remaining_notional = turnover_cap_notional - turnover_notional
                if remaining_notional <= 1e-9:
                    skip_reasons["turnover_cap_block"] = int(skip_reasons.get("turnover_cap_block", 0)) + 1
                    break
                max_qty_by_cap = remaining_notional / max(base_price, 1e-9)
                if abs(dqty) > max_qty_by_cap:
                    dqty = math.copysign(max_qty_by_cap, dqty)
                    if abs(dqty) <= 1e-12:
                        skip_reasons["turnover_cap_block"] = int(skip_reasons.get("turnover_cap_block", 0)) + 1
                        continue

            fill_price = 0.0
            fee_rate = 0.0
            penalty = 0.0
            slippage_cost = 0.0
            abs_qty = abs(dqty)
            atr_value = float(atr[exec_idx, sym_i])
            slippage_frac = _portfolio_slippage_fraction(
                base_price=base_price,
                atr_value=atr_value,
                slippage_mode=cost_cfg.slippage_mode,
                slippage_bps=cost_cfg.slippage_bps,
                atr_slippage_mult=cost_cfg.atr_slippage_mult,
            )

            if cost_cfg.order_model == "limit":
                ref = float(close[exec_idx, sym_i])
                offset = max(float(cost_cfg.limit_price_offset_bps), 0.0) / 10_000.0
                limit_price = ref * (1.0 - offset) if side_buy else ref * (1.0 + offset)
                timeout = max(int(cost_cfg.limit_timeout_bars), 0)
                fill_prob = min(max(float(cost_cfg.limit_fill_probability), 0.0), 1.0)
                end_idx = min(exec_idx + timeout, n - 1)
                filled = False
                for probe in range(exec_idx, end_idx + 1):
                    touched = bool(low[probe, sym_i] <= limit_price) if side_buy else bool(high[probe, sym_i] >= limit_price)
                    if not touched:
                        continue
                    if rng.random() <= fill_prob:
                        fill_price = max(limit_price, 1e-12)
                        fee_rate = _portfolio_fee_rate(order_model="limit", base_config=base_config, fee_multiplier=cost_cfg.fee_multiplier)
                        filled = True
                        fill_count += 1
                        break
                if not filled:
                    reject_count += 1
                    fallback_idx = end_idx
                    base_fb = float(open_[fallback_idx, sym_i])
                    atr_fb = float(atr[fallback_idx, sym_i])
                    slip_fb = _portfolio_slippage_fraction(
                        base_price=base_fb,
                        atr_value=atr_fb,
                        slippage_mode=cost_cfg.slippage_mode,
                        slippage_bps=cost_cfg.slippage_bps,
                        atr_slippage_mult=cost_cfg.atr_slippage_mult,
                    )
                    fill_price = base_fb * (1.0 + slip_fb * side_sign)
                    fee_rate = _portfolio_fee_rate(order_model="market", base_config=base_config, fee_multiplier=cost_cfg.fee_multiplier)
                    penalty = abs_qty * fill_price * max(float(cost_cfg.limit_unfilled_penalty_bps), 0.0) / 10_000.0
                    slippage_cost = abs_qty * abs(fill_price - base_fb)
                    fill_count += 1
            else:
                fill_price = base_price * (1.0 + slippage_frac * side_sign)
                fee_rate = _portfolio_fee_rate(order_model="market", base_config=base_config, fee_multiplier=cost_cfg.fee_multiplier)
                slippage_cost = abs_qty * abs(fill_price - base_price)
                fill_count += 1

            if (not math.isfinite(fill_price)) or fill_price <= 1e-9:
                skip_reasons["invalid_fill_price"] = int(skip_reasons.get("invalid_fill_price", 0)) + 1
                skipped_trade_count += 1
                continue

            notional = abs_qty * fill_price
            if (not math.isfinite(notional)) or notional <= 0.0:
                skip_reasons["invalid_notional"] = int(skip_reasons.get("invalid_notional", 0)) + 1
                skipped_trade_count += 1
                continue
            if penalty > 0.0 and tradable_equity > 0.0 and tradable_equity <= (initial_equity * 0.10):
                max_penalty = tradable_equity * max(skip_trades_if_cost_exceeds_equity_ratio * 0.5, 0.0)
                if penalty > max_penalty:
                    skip_reasons["penalty_block_low_equity"] = int(skip_reasons.get("penalty_block_low_equity", 0)) + 1
                    skipped_trade_count += 1
                    continue
            fee = notional * fee_rate
            est_cost = fee + slippage_cost + penalty
            if bar_cost_budget > 0 and (scenario_fee + scenario_slip + scenario_penalty + est_cost) > bar_cost_budget:
                skip_reasons["bar_cost_budget_block"] = int(skip_reasons.get("bar_cost_budget_block", 0)) + 1
                skipped_trade_count += 1
                bar_blocked = True
                continue

            if side_buy:
                cash -= notional + fee + penalty
            else:
                cash += notional - fee - penalty
            qty[sym_i] += dqty
            trade_count += 1
            trades_this_bar += 1
            reduce_first_total_execs += 1
            if is_reduce:
                reduce_first_execs += 1

            turnover_notional += notional
            scenario_fee += fee
            scenario_slip += slippage_cost
            scenario_penalty += penalty

        if bar_blocked:
            skip_reasons["bar_blocked"] = int(skip_reasons.get("bar_blocked", 0)) + 1

        cost_fee_total += scenario_fee
        cost_slippage_total += scenario_slip
        cost_penalty_total += scenario_penalty

        post_equity = float(cash + np.dot(qty, close[idx]))
        px_ref = np.asarray(open_[exec_idx], dtype=float)
        px_ref = np.where((~np.isfinite(px_ref)) | (px_ref <= 1e-9), 1.0, px_ref)
        backlog_notional = float(np.sum(np.abs((target_qty_ref - qty) * px_ref)))
        backlog_notional_sum += backlog_notional
        backlog_notional_max = max(backlog_notional_max, backlog_notional)
        backlog_ratio = float(backlog_notional / max(post_equity if math.isfinite(post_equity) else tradable_equity, 1e-9))
        backlog_ratio_sum += backlog_ratio
        backlog_ratio_max = max(backlog_ratio_max, backlog_ratio)
        prev_backlog_notional = backlog_notional
        if planned_turnover_notional > 1e-9:
            turnover_executed_fraction = float(turnover_notional / max(planned_turnover_notional, 1e-9))
            turnover_executed_fraction = min(max(turnover_executed_fraction, 0.0), 1.0)
            executed_fraction_sum += turnover_executed_fraction
            executed_fraction_count += 1
        anomaly_reason = None
        liquidation_breach = (not math.isfinite(post_equity)) or (post_equity < (equity_floor - 1e-9))
        liquidation_forced = liquidation_triggered and (turnover_notional > 0.0 or had_open_position)
        if enable_liquidation and (liquidation_breach or liquidation_forced):
            if scenario_fee >= scenario_slip and scenario_fee >= scenario_penalty:
                liquid_reason = "negative_equity_due_to_fee"
            elif scenario_slip >= scenario_fee and scenario_slip >= scenario_penalty:
                liquid_reason = "negative_equity_due_to_slippage"
            elif scenario_penalty >= scenario_fee and scenario_penalty >= scenario_slip:
                liquid_reason = "negative_equity_due_to_penalty"
            else:
                liquid_reason = "negative_equity_due_to_other"
            if gross_transition_mag > 0.05:
                liquid_reason = "negative_equity_due_to_gross_transition"
            elif cap_hit_count > 0 and backlog_notional > max(tradable_equity, 1e-9):
                liquid_reason = "negative_equity_due_to_backlog_execution"
            elif turnover_notional > 0.0 and (scenario_slip / max(turnover_notional, 1e-9)) > 0.05:
                liquid_reason = "negative_equity_due_to_price_gap"
            liquidation_reason_counts[liquid_reason] = int(liquidation_reason_counts.get(liquid_reason, 0)) + 1
            liquidation_rows.append(
                {
                    "timestamp": str(timestamps[idx]),
                    "equity_before": float(equity),
                    "equity_after": float(max(post_equity, equity_floor)),
                    "gross": float(np.sum(np.abs(current_weights_post))) if "current_weights_post" in locals() else float(np.sum(np.abs(current_weights))),
                    "dd_stage": int(dd_stage),
                    "regime": regime_label,
                    "turnover_notional": float(turnover_notional),
                    "fee": float(scenario_fee),
                    "slippage": float(scenario_slip),
                    "penalty": float(scenario_penalty),
                    "reason": liquid_reason,
                }
            )
            post_equity = max(float(post_equity) if math.isfinite(post_equity) else equity_floor, equity_floor)
            qty[:] = 0.0
            cash = post_equity
            trading_halt_left = max(trading_halt_left, trading_halt_bars)
            liquidation_indices.append(idx)
            if last_halt_release_idx is not None and post_halt_cooldown_bars > 0:
                if (idx - last_halt_release_idx) <= post_halt_cooldown_bars:
                    liquidation_after_halt_count += 1
            anomaly_reason = "liquidation_event"
        elif post_equity <= 0.0 or not math.isfinite(post_equity):
            post_equity = 0.0
            qty[:] = 0.0
            cash = 0.0
            anomaly_reason = "equity_liquidated"
        if turnover_notional > max(max_notional_to_equity_mult, 1.0) * max(tradable_equity, 1.0) * 5.0:
            anomaly_reason = anomaly_reason or "turnover_notional_anomaly"
        if (scenario_fee + scenario_slip + scenario_penalty) > max(bar_cost_budget, 1.0) * 3.0 and tradable_equity > 0:
            anomaly_reason = anomaly_reason or "cost_spike_anomaly"

        if anomaly_reason is not None:
            event = {
                "timestamp": str(timestamps[idx]),
                "reason": anomaly_reason,
                "equity_before": equity,
                "equity_after": post_equity,
                "tradable_equity": tradable_equity,
                "turnover_notional": turnover_notional,
                "bar_cost": scenario_fee + scenario_slip + scenario_penalty,
                "regime": regime_label,
                "rebalance_applied": bool(rebalance_applied),
                "trades_this_bar": trades_this_bar,
            }
            debug_events.append(event)
            if len(debug_events) > 3:
                debug_events = debug_events[-3:]
            if stop_on_anomaly and debug_mode:
                raise RuntimeError(f"portfolio debug anomaly: {event}")

        post_equity_abs = max(abs(post_equity), 1e-9)
        current_weights_post = (qty * close[idx]) / post_equity_abs if math.isfinite(post_equity_abs) else np.zeros(s, dtype=float)
        last_weights = current_weights_post.copy()
        if trades_this_bar > 0:
            rebalance_execs += 1
            prev_long_idx = set(int(x) for x in long_idx.tolist())
            prev_short_idx = set(int(x) for x in short_idx.tolist())
            if abs(effective_scale - prev_effective_scale) > 1e-9:
                gross_change_turnover += turnover_notional
        prev_effective_scale = effective_scale
        peak_equity = max(peak_equity, float(post_equity))
        signal_peak_post = max(float(signal_peak), 1e-9)
        drawdown_post = max(0.0, min(1.0, 1.0 - (post_equity / signal_peak_post)))
        stage_key = f"stage_{dd_stage}"
        effective_gross_now = float(params.gross_exposure * effective_scale)
        gross_sum_by_stage[stage_key] = float(gross_sum_by_stage.get(stage_key, 0.0)) + effective_gross_now
        gross_cnt_by_stage[stage_key] = int(gross_cnt_by_stage.get(stage_key, 0)) + 1
        equity_points.append(
            {
                "timestamp": str(timestamps[idx]),
                "equity": post_equity,
                "cash": cash,
                "gross_exposure": float(np.sum(np.abs(current_weights_post))),
                "net_exposure": float(np.sum(current_weights_post)),
                "rebalance_applied": bool(trades_this_bar > 0),
                "regime": regime_label,
            }
        )
        dd_rows.append(
            {
                "timestamp": str(timestamps[idx]),
                "equity": float(post_equity),
                "peak_equity": float(peak_equity),
                "rolling_peak": float(signal_peak),
                "peak_type": peak_type,
                "drawdown": float(drawdown_post),
                "dd_stage": int(dd_stage),
                "effective_gross": effective_gross_now,
                "regime": regime_label,
            }
        )
        turnover_rows.append(
            {
                "timestamp": str(timestamps[idx]),
                "turnover_ratio": turnover_ratio,
                "turnover_notional": turnover_notional,
                "turnover_cap_notional": turnover_cap_notional,
                "turnover_executed_fraction": turnover_executed_fraction,
                "backlog_notional": backlog_notional,
                "backlog_ratio": backlog_ratio,
                "cap_used": cap_used if cap_used is not None else -1.0,
                "rebalance_applied": bool(trades_this_bar > 0),
                "regime": regime_label,
                "skip_reason": anomaly_reason or ("executed" if trades_this_bar > 0 else "no_execution"),
                "turnover_threshold_used": turnover_threshold,
                "force_rebalance": bool(force_rebalance),
                "trades_this_bar": trades_this_bar,
                "regime_scale": effective_scale,
                "rebalance_skipped_due_to_shock": bool(rebalance_skipped_due_to_shock),
            }
        )
        cost_rows.append(
            {
                "timestamp": str(timestamps[idx]),
                "fee_cost": scenario_fee,
                "slippage_cost": scenario_slip,
                "penalty_cost": scenario_penalty,
                "total_cost": scenario_fee + scenario_slip + scenario_penalty,
                "turnover_notional": turnover_notional,
                "trades_this_bar": trades_this_bar,
                "cost_budget": bar_cost_budget,
            }
        )
        for sym_i, symbol in enumerate(market.symbols):
            position_rows.append(
                {
                    "timestamp": str(timestamps[idx]),
                    "symbol": symbol,
                    "target_weight": float(target_weights[sym_i]),
                    "weight": float(current_weights_post[sym_i]),
                    "qty": float(qty[sym_i]),
                    "price": float(close[idx, sym_i]),
                    "regime": regime_label,
                }
            )

    final_equity = float(cash + np.dot(qty, close[-1]))
    if enable_liquidation:
        final_equity = max(final_equity, equity_floor)
    else:
        final_equity = max(final_equity, 0.0)
    if not math.isfinite(final_equity):
        final_equity = 0.0
    equity_points.append(
        {
            "timestamp": str(timestamps[-1]),
            "equity": final_equity,
            "cash": cash,
            "gross_exposure": float(np.sum(np.abs(last_weights))),
            "net_exposure": float(np.sum(last_weights)),
            "rebalance_applied": False,
            "regime": regime_by_ts.get(_ts_key(timestamps[-1]), "all") if regime_by_ts else "all",
        }
    )
    if prev_rebalance_equity is not None:
        interval_pnls.append(final_equity - prev_rebalance_equity)

    equity_df = pd.DataFrame(equity_points)
    dd_df = pd.DataFrame(dd_rows)
    gross_df = pd.DataFrame(gross_rows)
    excluded_df = pd.DataFrame(
        excluded_rows,
        columns=["timestamp", "symbol", "reason", "applied_mult", "cooldown_remaining"],
    )
    risk_cap_df = pd.DataFrame(
        risk_cap_rows,
        columns=["timestamp", "symbol", "raw_weight", "capped_weight", "cap_hit", "atr_ratio", "shock_flags"],
    )
    turnover_df = pd.DataFrame(turnover_rows)
    cost_df = pd.DataFrame(cost_rows)
    pos_df = pd.DataFrame(position_rows)
    liquidation_df = pd.DataFrame(
        liquidation_rows,
        columns=[
            "timestamp",
            "equity_before",
            "equity_after",
            "gross",
            "dd_stage",
            "regime",
            "turnover_notional",
            "fee",
            "slippage",
            "penalty",
            "reason",
        ],
    )

    equity_arr = equity_df["equity"].to_numpy(dtype=float) if not equity_df.empty else np.asarray([base_config.initial_equity], dtype=float)
    equity_arr = np.nan_to_num(equity_arr, nan=0.0, posinf=0.0, neginf=0.0)
    equity_arr = np.maximum(equity_arr, 0.0)
    initial = float(base_config.initial_equity)
    net_pnl = final_equity - initial

    start_ts = _to_utc_timestamp(timestamps[0])
    end_ts = _to_utc_timestamp(timestamps[-1])
    years = max((end_ts - start_ts).total_seconds() / (365.25 * 24 * 3600), 1e-9)
    cagr = (final_equity / initial) ** (1.0 / years) - 1.0 if initial > 0 and final_equity > 0 else -1.0
    max_dd = _max_drawdown(equity_arr)
    rets = np.diff(equity_arr) / np.maximum(np.abs(equity_arr[:-1]), 1e-9) if equity_arr.size > 1 else np.asarray([], dtype=float)
    sharpe_like = float(np.mean(rets) / max(float(np.std(rets)), 1e-9)) if rets.size else 0.0
    interval_pnls = [x for x in interval_pnls if math.isfinite(x)]
    win_rate, profit_factor = _portfolio_interval_metrics(interval_pnls)
    avg_turn_attempt = float(turnover_df["turnover_ratio"].mean()) if not turnover_df.empty else 0.0
    exec_turn_df = turnover_df[turnover_df["rebalance_applied"] == True] if not turnover_df.empty else pd.DataFrame()
    avg_turn_exec = float(exec_turn_df["turnover_ratio"].mean()) if not exec_turn_df.empty else 0.0
    fee_cost_per_gross_time = float(cost_fee_total / max(gross_exposure_time_sum, 1e-9))

    metrics: dict[str, float] = {
        "final_equity": final_equity,
        "net_pnl": net_pnl,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "profit_factor": profit_factor,
        "win_rate": win_rate,
        "avg_trade": (net_pnl / trade_count) if trade_count > 0 else 0.0,
        "trade_count": float(trade_count),
        "sharpe_like": sharpe_like,
        "rebalance_count": float(rebalance_attempts),
        "rebalance_attempt_count": float(rebalance_attempts),
        "rebalance_exec_count": float(rebalance_execs),
        "avg_turnover_ratio": avg_turn_exec,
        "avg_turnover_ratio_attempts": avg_turn_attempt,
        "fill_rate": float(fill_count / max(fill_count + reject_count, 1)),
        "reject_rate": float(reject_count / max(fill_count + reject_count, 1)),
        "skipped_trade_count": float(skipped_trade_count),
        "off_to_on_count": float(off_to_on_count),
        "on_to_off_count": float(on_to_off_count),
        "gross_change_turnover": float(gross_change_turnover),
        "cost_fee_total": cost_fee_total,
        "cost_slippage_total": cost_slippage_total,
        "cost_penalty_total": cost_penalty_total,
        "cost_total": cost_fee_total + cost_slippage_total + cost_penalty_total,
        "gross_exposure_time": float(gross_exposure_time_sum),
        "fee_cost_per_gross_time": fee_cost_per_gross_time,
        "cap_hit_count": float(cap_hit_count),
        "avg_executed_fraction": float(executed_fraction_sum / max(executed_fraction_count, 1)),
        "avg_backlog_notional": float(backlog_notional_sum / max(rebalance_attempts, 1)),
        "max_backlog_notional": float(backlog_notional_max),
        "avg_backlog_ratio": float(backlog_ratio_sum / max(rebalance_attempts, 1)),
        "max_backlog_ratio": float(backlog_ratio_max),
        "avg_cap_used": float(cap_used_sum / max(cap_used_count, 1)),
        "reduce_first_execution_ratio": float(reduce_first_execs / max(reduce_first_total_execs, 1)),
        "equity_zero_or_negative_count": float(skip_reasons.get("equity_zero_or_negative", 0)),
        "drift_force_count": float(drift_force_count),
        "kill_switch_events": float(kill_switch_count),
        "kill_switch_total_bars": float(kill_bars),
        "stage_3_share": float(time_in_dd_stage.get("stage_3", 0) / max(sum(time_in_dd_stage.values()), 1)),
        "stage_3_longest_streak_bars": float(stage3_longest_streak_bars),
        "stage_transitions_up": float(stage_transitions_up),
        "stage_transitions_down": float(stage_transitions_down),
        "liquidation_count": float(len(liquidation_df)),
        "rebalance_skipped_due_to_shock_count": float(rebalance_skipped_due_to_shock_count),
        "rebalance_skipped_due_to_shock_ratio": float(rebalance_skipped_due_to_shock_count / max(rebalance_attempts, 1)),
        "shock_active_bars_count": float(shock_active_bars_count),
    }
    regime_off_ratio = {
        rg: (float(regime_off_bars.get(rg, 0)) / max(float(regime_total_bars.get(rg, 0)), 1.0))
        for rg in sorted(regime_total_bars.keys())
    }
    diagnostics = {
        "total_bars": int(n),
        "warmup_bars": int(warmup),
        "rebalance_step_bars": int(params.rebalance_bars),
        "rebalance_attempts": int(rebalance_attempts),
        "rebalance_execs": int(rebalance_execs),
        "off_to_on_count": int(off_to_on_count),
        "on_to_off_count": int(on_to_off_count),
        "gross_change_turnover_contrib": float(gross_change_turnover),
        "regime_off_ratio": regime_off_ratio,
        "skip_reasons": {k: int(v) for k, v in sorted(skip_reasons.items())},
        "anomaly_events": int(len(debug_events)),
        "max_cost_ratio_per_bar": float(max_cost_ratio_per_bar),
        "cap_mode": str(cap_mode),
        "base_cap": float(effective_base_cap),
        "cap_min": float(cap_min),
        "cap_max": float(cap_max),
        "backlog_thresholds": [float(x) for x in backlog_thresholds],
        "cap_steps": [float(x) for x in cap_steps],
        "high_vol_cap_max": float(high_vol_cap_max),
        "rolling_peak_window_bars": None if rolling_peak_window_bars is None else int(rolling_peak_window_bars),
        "peak_type": "absolute" if rolling_peak_window_bars is None else "rolling",
        "stage_down_confirm_bars": int(stage_down_confirm_bars),
        "stage3_down_confirm_bars": int(stage3_down_confirm_bars),
        "reentry_ramp_steps": int(reentry_ramp_steps),
        "disable_new_entry_stage": int(disable_new_entry_stage),
        "dd_turnover_threshold_mult": float(dd_turnover_threshold_mult),
        "dd_rebalance_mult": None if dd_rebalance_mult is None else float(dd_rebalance_mult),
        "max_turnover_notional_to_equity": None if max_turnover_notional_to_equity is None else float(max_turnover_notional_to_equity),
        "drift_threshold": None if drift_threshold is None else float(drift_threshold),
        "gross_decay_steps": int(max(int(gross_decay_steps), 1)),
        "max_notional_to_equity_mult": float(max_notional_to_equity_mult),
        "enable_liquidation": bool(enable_liquidation),
        "equity_floor_ratio": float(equity_floor_ratio),
        "equity_floor": float(equity_floor),
        "trading_halt_bars": int(trading_halt_bars),
        "skip_trades_if_cost_exceeds_equity_ratio": float(skip_trades_if_cost_exceeds_equity_ratio),
        "transition_smoother_enabled": bool(transition_smoother_enabled),
        "gross_step_up": float(gross_step_up),
        "gross_step_down": float(gross_step_down),
        "post_halt_cooldown_bars": int(post_halt_cooldown_bars),
        "post_halt_max_gross": float(post_halt_max_gross),
        "liquidation_lookback_bars": int(liquidation_lookback_bars),
        "liquidation_lookback_max_gross": float(liquidation_lookback_max_gross),
        "enable_symbol_shock_filters": bool(enable_symbol_shock_filters),
        "max_abs_weight_per_symbol": float(max_abs_weight_per_symbol),
        "atr_shock_threshold": float(atr_shock_threshold),
        "gap_shock_threshold": float(gap_shock_threshold),
        "shock_cooldown_bars": int(shock_cooldown_bars),
        "shock_mode": str(shock_mode),
        "shock_weight_mult_atr": float(shock_weight_mult_atr),
        "shock_weight_mult_gap": float(shock_weight_mult_gap),
        "shock_freeze_rebalance": bool(shock_freeze_rebalance),
        "shock_freeze_min_fraction": float(shock_freeze_min_fraction),
        "lookback_score_mode": str(lookback_score_mode),
        "count_cap_hits": int(cap_hit_count),
        "avg_executed_fraction": float(executed_fraction_sum / max(executed_fraction_count, 1)),
        "avg_backlog_notional": float(backlog_notional_sum / max(rebalance_attempts, 1)),
        "max_backlog_notional": float(backlog_notional_max),
        "avg_backlog_ratio": float(backlog_ratio_sum / max(rebalance_attempts, 1)),
        "max_backlog_ratio": float(backlog_ratio_max),
        "avg_cap_used": float(cap_used_sum / max(cap_used_count, 1)),
        "cap_histogram": {k: int(v) for k, v in sorted(cap_histogram.items())},
        "reduce_first_execution_ratio": float(reduce_first_execs / max(reduce_first_total_execs, 1)),
        "reduce_first_exec_count": int(reduce_first_execs),
        "reduce_first_candidate_count": int(reduce_first_candidates),
        "equity_zero_or_negative_count": int(skip_reasons.get("equity_zero_or_negative", 0)),
        "drift_force_count": int(drift_force_count),
        "dd_trigger_counts": {k: int(v) for k, v in sorted(dd_trigger_counts.items())},
        "time_in_dd_stage": {k: int(v) for k, v in sorted(time_in_dd_stage.items())},
        "stage_transitions_up": int(stage_transitions_up),
        "stage_transitions_down": int(stage_transitions_down),
        "stage_3_longest_streak_bars": int(stage3_longest_streak_bars),
        "stage_3_share": float(time_in_dd_stage.get("stage_3", 0) / max(sum(time_in_dd_stage.values()), 1)),
        "avg_effective_gross_by_stage": {
            k: (float(gross_sum_by_stage.get(k, 0.0)) / max(float(gross_cnt_by_stage.get(k, 0)), 1.0))
            for k in sorted(gross_sum_by_stage.keys())
        },
        "gross_transition_magnitude_sum": float(gross_transition_magnitude_sum),
        "gross_transition_max": float(gross_transition_max),
        "applied_gross_mean": float(applied_gross_sum / max(applied_gross_count, 1)),
        "applied_gross_var": float((applied_gross_sq_sum / max(applied_gross_count, 1)) - (applied_gross_sum / max(applied_gross_count, 1)) ** 2),
        "backlog_gross_error_mean": float(gross_error_sum / max(applied_gross_count, 1)),
        "backlog_gross_error_max": float(gross_error_max),
        "liquidation_after_halt_count": int(liquidation_after_halt_count),
        "kill_switch_events": {
            "count": int(kill_switch_count),
            "first_ts": kill_switch_first_ts,
            "total_bars_in_kill": int(kill_bars),
        },
        "liquidation_events": {
            "count": int(len(liquidation_df)),
            "first_ts": None if liquidation_df.empty else str(liquidation_df.iloc[0]["timestamp"]),
        },
        "negative_equity_cause_counts": {k: int(v) for k, v in sorted(liquidation_reason_counts.items())},
        "negative_equity_cause_top3": sorted(
            (
                {"reason": str(k), "count": int(v)}
                for k, v in liquidation_reason_counts.items()
                if int(v) > 0
            ),
            key=lambda x: x["count"],
            reverse=True,
        )[:3],
        "excluded_counts_by_reason": {k: int(v) for k, v in sorted(excluded_counts_by_reason.items())},
        "shocked_counts_by_reason": {k: int(v) for k, v in sorted(shocked_counts_by_reason.items())},
        "avg_effective_mult_by_reason": {
            k: (
                float(shocked_mult_sum_by_reason.get(k, 0.0))
                / max(float(shocked_mult_count_by_reason.get(k, 0)), 1.0)
            )
            for k in sorted(shocked_mult_sum_by_reason.keys())
        },
        "fraction_of_time_any_shock_active": float(any_shock_active_bar_count / max(rebalance_attempts, 1)),
        "shock_active_bars_count": int(shock_active_bars_count),
        "rebalance_skipped_due_to_shock_count": int(rebalance_skipped_due_to_shock_count),
        "rebalance_skipped_due_to_shock_ratio": float(rebalance_skipped_due_to_shock_count / max(rebalance_attempts, 1)),
        "cap_hit_counts_by_symbol": {k: int(v) for k, v in sorted(cap_hit_counts_by_symbol.items())},
        "top5_shocked_symbols": [
            {"symbol": str(k), "count": int(v)}
            for k, v in sorted(shocked_symbol_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        ],
        "top5_excluded_symbols": [
            {"symbol": str(k), "count": int(v)}
            for k, v in sorted(shocked_symbol_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        ],
        "top5_cap_hit_symbols": [
            {"symbol": str(k), "count": int(v)}
            for k, v in sorted(cap_hit_counts_by_symbol.items(), key=lambda kv: kv[1], reverse=True)[:5]
        ],
    }
    return PortfolioSimResult(
        metrics=metrics,
        equity_curve=equity_df,
        dd_timeline=dd_df,
        gross_target_applied=gross_df,
        excluded_symbols=excluded_df,
        symbol_risk_caps=risk_cap_df,
        positions=pos_df,
        turnover=turnover_df,
        cost_breakdown=cost_df,
        liquidation_events=liquidation_df,
        diagnostics=diagnostics,
        debug_dump=debug_events,
    )


def _portfolio_btc_benchmark(
    *,
    market: PortfolioMarketData,
    initial_equity: float,
) -> pd.DataFrame:
    if "BTC/USDT" in market.symbols:
        btc_idx = market.symbols.index("BTC/USDT")
    else:
        btc_idx = 0
    close = market.close[:, btc_idx]
    if close.size == 0:
        return pd.DataFrame(columns=["timestamp", "btc_equity"])
    base = max(float(close[0]), 1e-12)
    curve = initial_equity * (close / base)
    return pd.DataFrame({"timestamp": market.timestamps.astype(str), "btc_equity": curve})


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


def _resolve_gross_profile(profile_name: str) -> dict[str, float]:
    name = str(profile_name).strip().lower()
    return dict(REGIME_GROSS_PROFILES.get(name, REGIME_GROSS_PROFILES["balanced"]))


def _nearest_regime_map(
    regime_maps_by_pct: dict[float, dict[str, str]],
    pct: float,
) -> dict[str, str]:
    if not regime_maps_by_pct:
        return {}
    target = float(pct)
    key = min(regime_maps_by_pct.keys(), key=lambda x: abs(float(x) - target))
    return regime_maps_by_pct[key]


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


def _portfolio_params_to_dict(params: PortfolioParams) -> dict[str, Any]:
    return {
        "signal_model": params.signal_model,
        "lookback_bars": int(params.lookback_bars),
        "rebalance_bars": int(params.rebalance_bars),
        "k": int(params.k),
        "gross_exposure": float(params.gross_exposure),
        "turnover_threshold": float(params.turnover_threshold),
        "vol_lookback": int(params.vol_lookback),
        "rank_buffer": int(params.rank_buffer),
        "high_vol_percentile": float(params.high_vol_percentile),
        "gross_map": str(params.gross_map),
        "off_grace_bars": int(params.off_grace_bars),
        "phased_entry_steps": int(params.phased_entry_steps),
    }


def _portfolio_params_from_dict(payload: dict[str, Any]) -> PortfolioParams:
    model = str(payload.get("signal_model", "momentum")).strip().lower()
    if model not in {"momentum", "mean_reversion"}:
        model = "momentum"
    return PortfolioParams(
        signal_model=model,  # type: ignore[arg-type]
        lookback_bars=max(int(payload.get("lookback_bars", 168)), 1),
        rebalance_bars=max(int(payload.get("rebalance_bars", 4)), 1),
        k=max(int(payload.get("k", 3)), 1),
        gross_exposure=max(float(payload.get("gross_exposure", 1.0)), 0.0),
        turnover_threshold=max(float(payload.get("turnover_threshold", 0.0)), 0.0),
        vol_lookback=max(int(payload.get("vol_lookback", 96)), 5),
        rank_buffer=max(int(payload.get("rank_buffer", 0)), 0),
        high_vol_percentile=min(max(float(payload.get("high_vol_percentile", 0.65)), 0.10), 0.99),
        gross_map=str(payload.get("gross_map", "balanced")).strip().lower() or "balanced",
        off_grace_bars=max(int(payload.get("off_grace_bars", 0)), 0),
        phased_entry_steps=max(int(payload.get("phased_entry_steps", 1)), 1),
    )


def _build_portfolio_param_grid(
    *,
    signal_models: list[str],
    lookback_bars: list[int],
    rebalance_bars: list[int],
    k_values: list[int],
    gross_values: list[float],
    turnover_threshold: float,
    vol_lookback: int,
    rank_buffers: list[int],
    high_vol_percentiles: list[float],
    gross_maps: list[str],
    off_grace_bars_list: list[int],
    phased_entry_steps_list: list[int],
) -> list[PortfolioParams]:
    grid: list[PortfolioParams] = []
    for model in signal_models:
        norm_model = model.strip().lower()
        if norm_model not in {"momentum", "mean_reversion"}:
            continue
        for lb, rb, k, gross, rank_buffer, hv_pct, gross_map, off_grace, phased_steps in itertools.product(
            lookback_bars,
            rebalance_bars,
            k_values,
            gross_values,
            rank_buffers,
            high_vol_percentiles,
            gross_maps,
            off_grace_bars_list,
            phased_entry_steps_list,
        ):
            grid.append(
                PortfolioParams(
                    signal_model=norm_model,  # type: ignore[arg-type]
                    lookback_bars=max(int(lb), 1),
                    rebalance_bars=max(int(rb), 1),
                    k=max(int(k), 1),
                    gross_exposure=max(float(gross), 0.0),
                    turnover_threshold=max(float(turnover_threshold), 0.0),
                    vol_lookback=max(int(vol_lookback), 5),
                    rank_buffer=max(int(rank_buffer), 0),
                    high_vol_percentile=min(max(float(hv_pct), 0.10), 0.99),
                    gross_map=str(gross_map).strip().lower() or "balanced",
                    off_grace_bars=max(int(off_grace), 0),
                    phased_entry_steps=max(int(phased_steps), 1),
                )
            )
    uniq: dict[str, PortfolioParams] = {}
    for p in grid:
        key = json.dumps(_portfolio_params_to_dict(p), sort_keys=True)
        uniq[key] = p
    return list(uniq.values())


def run_portfolio_walk_forward(
    *,
    market: PortfolioMarketData,
    param_grid: list[PortfolioParams],
    base_config: BacktestConfig,
    baseline_cost: PortfolioCostConfig,
    metric: str,
    train_days: int,
    test_days: int,
    step_days: int,
    top_pct: float,
    max_candidates: int,
    seed: int,
    start: str,
    end: str,
    regime_maps_by_pct: dict[float, dict[str, str]],
    regime_turnover_threshold_map: dict[str, float] | None = None,
    debug_mode: bool = False,
    max_cost_ratio_per_bar: float = 0.05,
    dd_controller_enabled: bool = False,
    dd_thresholds: tuple[float, float, float, float] = (0.10, 0.20, 0.30, 0.40),
    dd_gross_mults: tuple[float, float, float, float, float] = (1.0, 0.70, 0.50, 0.30, 0.0),
    dd_recover_thresholds: tuple[float, float, float, float] = (0.08, 0.16, 0.24, 0.32),
    kill_cooldown_bars: int = 168,
    disable_new_entry_when_dd: bool = True,
    rolling_peak_window_bars: int | None = None,
    stage_down_confirm_bars: int = 48,
    stage3_down_confirm_bars: int = 96,
    reentry_ramp_steps: int = 3,
    disable_new_entry_stage: int = 3,
    dd_turnover_threshold_mult: float = 1.5,
    dd_rebalance_mult: float | None = None,
    cap_mode: Literal["fixed", "adaptive"] = "fixed",
    base_cap: float = 0.25,
    cap_min: float = 0.20,
    cap_max: float = 0.40,
    backlog_thresholds: tuple[float, float, float] = (0.25, 0.50, 0.75),
    cap_steps: tuple[float, float, float, float] = (0.25, 0.30, 0.35, 0.40),
    high_vol_cap_max: float = 0.30,
    max_turnover_notional_to_equity: float | None = 0.25,
    drift_threshold: float | None = 0.35,
    gross_decay_steps: int = 3,
    max_notional_to_equity_mult: float = 3.0,
    enable_liquidation: bool = True,
    equity_floor_ratio: float = 0.01,
    trading_halt_bars: int = 168,
    skip_trades_if_cost_exceeds_equity_ratio: float = 0.02,
    transition_smoother_enabled: bool = False,
    gross_step_up: float = 0.10,
    gross_step_down: float = 0.25,
    post_halt_cooldown_bars: int = 168,
    post_halt_max_gross: float = 0.15,
    liquidation_lookback_bars: int = 720,
    liquidation_lookback_max_gross: float = 0.15,
    enable_symbol_shock_filters: bool = True,
    max_abs_weight_per_symbol: float = 0.12,
    atr_shock_threshold: float = 2.5,
    gap_shock_threshold: float = 0.10,
    shock_cooldown_bars: int = 72,
    shock_mode: Literal["exclude", "downweight"] = "downweight",
    shock_weight_mult_atr: float = 0.25,
    shock_weight_mult_gap: float = 0.10,
    shock_freeze_rebalance: bool | None = None,
    shock_freeze_min_fraction: float = 0.30,
    lookback_score_mode: Literal["single", "median_3"] = "single",
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    if not param_grid:
        return pd.DataFrame(), pd.DataFrame(), {"oos_positive_ratio": 0.0, "param_stability_score": 0.0}
    rng = np.random.default_rng(seed)
    candidates = list(param_grid)
    if len(candidates) > max_candidates:
        idxs = rng.choice(len(candidates), size=max_candidates, replace=False)
        candidates = [candidates[int(i)] for i in idxs]

    windows = _window_iter(start=start, end=end, train_days=train_days, test_days=test_days, step_days=step_days)
    if not windows:
        return pd.DataFrame(), pd.DataFrame(), {"oos_positive_ratio": 0.0, "param_stability_score": 0.0}

    window_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    best_params: list[dict[str, Any]] = []
    w_index = 0
    for w in windows:
        train_market = _slice_portfolio_market(market, w["train_start"], w["train_end"])
        test_market = _slice_portfolio_market(market, w["test_start"], w["test_end"])
        if train_market.bars < 80 or test_market.bars < 30:
            continue

        train_scores: list[dict[str, Any]] = []
        for p in candidates:
            candidate_regime_map = _nearest_regime_map(regime_maps_by_pct, p.high_vol_percentile)
            size_map = _resolve_gross_profile(p.gross_map)
            try:
                sim = _simulate_portfolio(
                    market=train_market,
                    params=p,
                    base_config=base_config,
                    cost_cfg=baseline_cost,
                    seed=seed + w_index,
                    regime_by_ts=candidate_regime_map,
                    regime_mode="sizing",
                    regime_size_map=size_map,
                    regime_turnover_threshold_map=regime_turnover_threshold_map,
                    debug_mode=debug_mode,
                    max_cost_ratio_per_bar=max_cost_ratio_per_bar,
                    dd_controller_enabled=dd_controller_enabled,
                    dd_thresholds=dd_thresholds,
                    dd_gross_mults=dd_gross_mults,
                    dd_recover_thresholds=dd_recover_thresholds,
                    kill_cooldown_bars=kill_cooldown_bars,
                    disable_new_entry_when_dd=disable_new_entry_when_dd,
                    rolling_peak_window_bars=rolling_peak_window_bars,
                    stage_down_confirm_bars=stage_down_confirm_bars,
                    stage3_down_confirm_bars=stage3_down_confirm_bars,
                    reentry_ramp_steps=reentry_ramp_steps,
                    disable_new_entry_stage=disable_new_entry_stage,
                    dd_turnover_threshold_mult=dd_turnover_threshold_mult,
                    dd_rebalance_mult=dd_rebalance_mult,
                    cap_mode=cap_mode,
                    base_cap=base_cap,
                    cap_min=cap_min,
                    cap_max=cap_max,
                    backlog_thresholds=backlog_thresholds,
                    cap_steps=cap_steps,
                    high_vol_cap_max=high_vol_cap_max,
                    max_turnover_notional_to_equity=max_turnover_notional_to_equity,
                    drift_threshold=drift_threshold,
                    gross_decay_steps=gross_decay_steps,
                    max_notional_to_equity_mult=max_notional_to_equity_mult,
                    enable_liquidation=enable_liquidation,
                    equity_floor_ratio=equity_floor_ratio,
                    trading_halt_bars=trading_halt_bars,
                    skip_trades_if_cost_exceeds_equity_ratio=skip_trades_if_cost_exceeds_equity_ratio,
                    transition_smoother_enabled=transition_smoother_enabled,
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
                    shock_mode=shock_mode,
                    shock_weight_mult_atr=shock_weight_mult_atr,
                    shock_weight_mult_gap=shock_weight_mult_gap,
                    shock_freeze_rebalance=shock_freeze_rebalance,
                    shock_freeze_min_fraction=shock_freeze_min_fraction,
                    lookback_score_mode=lookback_score_mode,
                )
            except Exception:
                continue
            m = sim.metrics
            item = {
                "window_index": w_index,
                "role": "train",
                "params_json": json.dumps(_portfolio_params_to_dict(p), sort_keys=True),
                "metric_value": float(m.get(metric, m.get("sharpe_like", 0.0))),
                "net_pnl": float(m.get("net_pnl", 0.0)),
                "sharpe_like": float(m.get("sharpe_like", 0.0)),
                "max_drawdown": float(m.get("max_drawdown", 0.0)),
                "trade_count": float(m.get("trade_count", 0.0)),
                "rebalance_count": float(m.get("rebalance_count", 0.0)),
            }
            train_scores.append(item)
            candidate_rows.append(item)
        if not train_scores:
            continue

        train_scores = sorted(train_scores, key=lambda x: x["metric_value"], reverse=True)
        top_k = max(1, int(math.ceil(len(train_scores) * top_pct)))
        top_cluster = train_scores[:top_k]
        top_params = [json.loads(x["params_json"]) for x in top_cluster]
        best_payload = json.loads(top_cluster[0]["params_json"])
        best_params.append(best_payload)

        test_rows: list[dict[str, Any]] = []
        for j, payload in enumerate(top_params):
            p = _portfolio_params_from_dict(payload)
            candidate_regime_map = _nearest_regime_map(regime_maps_by_pct, p.high_vol_percentile)
            size_map = _resolve_gross_profile(p.gross_map)
            try:
                sim = _simulate_portfolio(
                    market=test_market,
                    params=p,
                    base_config=base_config,
                    cost_cfg=baseline_cost,
                    seed=seed + w_index + j + 1,
                    regime_by_ts=candidate_regime_map,
                    regime_mode="sizing",
                    regime_size_map=size_map,
                    regime_turnover_threshold_map=regime_turnover_threshold_map,
                    debug_mode=debug_mode,
                    max_cost_ratio_per_bar=max_cost_ratio_per_bar,
                    dd_controller_enabled=dd_controller_enabled,
                    dd_thresholds=dd_thresholds,
                    dd_gross_mults=dd_gross_mults,
                    dd_recover_thresholds=dd_recover_thresholds,
                    kill_cooldown_bars=kill_cooldown_bars,
                    disable_new_entry_when_dd=disable_new_entry_when_dd,
                    rolling_peak_window_bars=rolling_peak_window_bars,
                    stage_down_confirm_bars=stage_down_confirm_bars,
                    stage3_down_confirm_bars=stage3_down_confirm_bars,
                    reentry_ramp_steps=reentry_ramp_steps,
                    disable_new_entry_stage=disable_new_entry_stage,
                    dd_turnover_threshold_mult=dd_turnover_threshold_mult,
                    dd_rebalance_mult=dd_rebalance_mult,
                    cap_mode=cap_mode,
                    base_cap=base_cap,
                    cap_min=cap_min,
                    cap_max=cap_max,
                    backlog_thresholds=backlog_thresholds,
                    cap_steps=cap_steps,
                    high_vol_cap_max=high_vol_cap_max,
                    max_turnover_notional_to_equity=max_turnover_notional_to_equity,
                    drift_threshold=drift_threshold,
                    gross_decay_steps=gross_decay_steps,
                    max_notional_to_equity_mult=max_notional_to_equity_mult,
                    enable_liquidation=enable_liquidation,
                    equity_floor_ratio=equity_floor_ratio,
                    trading_halt_bars=trading_halt_bars,
                    skip_trades_if_cost_exceeds_equity_ratio=skip_trades_if_cost_exceeds_equity_ratio,
                    transition_smoother_enabled=transition_smoother_enabled,
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
                    shock_mode=shock_mode,
                    shock_weight_mult_atr=shock_weight_mult_atr,
                    shock_weight_mult_gap=shock_weight_mult_gap,
                    shock_freeze_rebalance=shock_freeze_rebalance,
                    shock_freeze_min_fraction=shock_freeze_min_fraction,
                    lookback_score_mode=lookback_score_mode,
                )
            except Exception:
                continue
            m = sim.metrics
            row = {
                "window_index": w_index,
                "role": "test",
                "params_json": json.dumps(payload, sort_keys=True),
                "metric_value": float(m.get(metric, m.get("sharpe_like", 0.0))),
                "net_pnl": float(m.get("net_pnl", 0.0)),
                "sharpe_like": float(m.get("sharpe_like", 0.0)),
                "max_drawdown": float(m.get("max_drawdown", 0.0)),
                "trade_count": float(m.get("trade_count", 0.0)),
                "rebalance_count": float(m.get("rebalance_count", 0.0)),
            }
            test_rows.append(row)
            candidate_rows.append(row)
        test_rows = sorted(test_rows, key=lambda x: x["metric_value"], reverse=True)
        best_test = test_rows[0] if test_rows else {"metric_value": float("-inf"), "net_pnl": 0.0, "sharpe_like": 0.0}
        test_pos_ratio = float(np.mean([1.0 if x["net_pnl"] > 0 else 0.0 for x in test_rows])) if test_rows else 0.0
        window_rows.append(
            {
                "window_index": w_index,
                "train_start": w["train_start"].isoformat(),
                "train_end": w["train_end"].isoformat(),
                "test_start": w["test_start"].isoformat(),
                "test_end": w["test_end"].isoformat(),
                "train_candidates": len(train_scores),
                "top_cluster_size": len(top_cluster),
                "best_train_metric": float(top_cluster[0]["metric_value"]),
                "best_test_metric": float(best_test["metric_value"]),
                "best_test_net_pnl": float(best_test["net_pnl"]),
                "best_test_sharpe_like": float(best_test["sharpe_like"]),
                "test_positive_ratio_top_cluster": test_pos_ratio,
                "top_cluster_param_dispersion": _param_dispersion(top_params),
                "best_params_json": json.dumps(best_payload, sort_keys=True),
            }
        )
        w_index += 1

    wf_df = pd.DataFrame(window_rows)
    cand_df = pd.DataFrame(candidate_rows)
    if wf_df.empty:
        return wf_df, cand_df, {"oos_positive_ratio": 0.0, "param_stability_score": 0.0}
    summary = {
        "window_count": float(len(wf_df)),
        "oos_positive_ratio": float(np.mean((wf_df["best_test_net_pnl"] > 0).astype(float))),
        "oos_median_best_test_metric": float(wf_df["best_test_metric"].median()),
        "oos_median_best_test_sharpe_like": float(wf_df["best_test_sharpe_like"].median()),
        "oos_mean_top_cluster_positive_ratio": float(wf_df["test_positive_ratio_top_cluster"].mean()),
        "param_stability_score": float(_param_stability(best_params)),
        "median_top_cluster_dispersion": float(wf_df["top_cluster_param_dispersion"].median()),
    }
    return wf_df, cand_df, summary


def _select_portfolio_params(*, wf_df: pd.DataFrame, fallback: PortfolioParams) -> PortfolioParams:
    if wf_df.empty or "best_params_json" not in wf_df.columns:
        return fallback
    counts = wf_df["best_params_json"].value_counts()
    if counts.empty:
        return fallback
    payload = json.loads(str(counts.index[0]))
    return _portfolio_params_from_dict(payload)


def run_portfolio_cost_stress(
    *,
    market: PortfolioMarketData,
    params: PortfolioParams,
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
    regime_by_ts: dict[str, str],
    regime_size_map: dict[str, float],
    regime_turnover_threshold_map: dict[str, float] | None = None,
    debug_mode: bool = False,
    max_cost_ratio_per_bar: float = 0.05,
    dd_controller_enabled: bool = False,
    dd_thresholds: tuple[float, float, float, float] = (0.10, 0.20, 0.30, 0.40),
    dd_gross_mults: tuple[float, float, float, float, float] = (1.0, 0.70, 0.50, 0.30, 0.0),
    dd_recover_thresholds: tuple[float, float, float, float] = (0.08, 0.16, 0.24, 0.32),
    kill_cooldown_bars: int = 168,
    disable_new_entry_when_dd: bool = True,
    rolling_peak_window_bars: int | None = None,
    stage_down_confirm_bars: int = 48,
    stage3_down_confirm_bars: int = 96,
    reentry_ramp_steps: int = 3,
    disable_new_entry_stage: int = 3,
    dd_turnover_threshold_mult: float = 1.5,
    dd_rebalance_mult: float | None = None,
    cap_mode: Literal["fixed", "adaptive"] = "fixed",
    base_cap: float = 0.25,
    cap_min: float = 0.20,
    cap_max: float = 0.40,
    backlog_thresholds: tuple[float, float, float] = (0.25, 0.50, 0.75),
    cap_steps: tuple[float, float, float, float] = (0.25, 0.30, 0.35, 0.40),
    high_vol_cap_max: float = 0.30,
    max_turnover_notional_to_equity: float | None = 0.25,
    drift_threshold: float | None = 0.35,
    gross_decay_steps: int = 3,
    max_notional_to_equity_mult: float = 3.0,
    enable_liquidation: bool = True,
    equity_floor_ratio: float = 0.01,
    trading_halt_bars: int = 168,
    skip_trades_if_cost_exceeds_equity_ratio: float = 0.02,
    transition_smoother_enabled: bool = False,
    gross_step_up: float = 0.10,
    gross_step_down: float = 0.25,
    post_halt_cooldown_bars: int = 168,
    post_halt_max_gross: float = 0.15,
    liquidation_lookback_bars: int = 720,
    liquidation_lookback_max_gross: float = 0.15,
    enable_symbol_shock_filters: bool = True,
    max_abs_weight_per_symbol: float = 0.12,
    atr_shock_threshold: float = 2.5,
    gap_shock_threshold: float = 0.10,
    shock_cooldown_bars: int = 72,
    shock_mode: Literal["exclude", "downweight"] = "downweight",
    shock_weight_mult_atr: float = 0.25,
    shock_weight_mult_gap: float = 0.10,
    shock_freeze_rebalance: bool | None = None,
    shock_freeze_min_fraction: float = 0.30,
    lookback_score_mode: Literal["single", "median_3"] = "single",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    scenario_idx = 0
    if slippage_mode == "fixed":
        slip_grid = [("fixed", x, 0.0) for x in fixed_slippage_bps]
    elif slippage_mode == "atr":
        slip_grid = [("atr", 0.0, x) for x in atr_slippage_mults]
    else:
        slip_grid = [("mixed", b, a) for b in fixed_slippage_bps for a in atr_slippage_mults]

    for fee_mult in fee_multipliers:
        for latency in latency_bars:
            for order_model in order_models:
                for mode, bps, atr_mult in slip_grid:
                    scenario_idx += 1
                    cost_cfg = PortfolioCostConfig(
                        order_model=order_model,
                        fee_multiplier=fee_mult,
                        slippage_mode=mode,  # type: ignore[arg-type]
                        slippage_bps=bps,
                        atr_slippage_mult=atr_mult,
                        latency_bars=latency,
                        limit_timeout_bars=limit_timeout_bars,
                        limit_fill_probability=limit_fill_probability,
                        limit_unfilled_penalty_bps=limit_unfilled_penalty_bps,
                    )
                    sim = _simulate_portfolio(
                        market=market,
                        params=params,
                        base_config=base_config,
                        cost_cfg=cost_cfg,
                        seed=seed + scenario_idx,
                        regime_by_ts=regime_by_ts,
                        regime_mode="sizing",
                        regime_size_map=regime_size_map,
                        regime_turnover_threshold_map=regime_turnover_threshold_map,
                        debug_mode=debug_mode,
                        max_cost_ratio_per_bar=max_cost_ratio_per_bar,
                        dd_controller_enabled=dd_controller_enabled,
                        dd_thresholds=dd_thresholds,
                        dd_gross_mults=dd_gross_mults,
                        dd_recover_thresholds=dd_recover_thresholds,
                        kill_cooldown_bars=kill_cooldown_bars,
                        disable_new_entry_when_dd=disable_new_entry_when_dd,
                        rolling_peak_window_bars=rolling_peak_window_bars,
                        stage_down_confirm_bars=stage_down_confirm_bars,
                        stage3_down_confirm_bars=stage3_down_confirm_bars,
                        reentry_ramp_steps=reentry_ramp_steps,
                        disable_new_entry_stage=disable_new_entry_stage,
                        dd_turnover_threshold_mult=dd_turnover_threshold_mult,
                        dd_rebalance_mult=dd_rebalance_mult,
                        cap_mode=cap_mode,
                        base_cap=base_cap,
                        cap_min=cap_min,
                        cap_max=cap_max,
                        backlog_thresholds=backlog_thresholds,
                        cap_steps=cap_steps,
                        high_vol_cap_max=high_vol_cap_max,
                        max_turnover_notional_to_equity=max_turnover_notional_to_equity,
                        drift_threshold=drift_threshold,
                        gross_decay_steps=gross_decay_steps,
                        max_notional_to_equity_mult=max_notional_to_equity_mult,
                        enable_liquidation=enable_liquidation,
                        equity_floor_ratio=equity_floor_ratio,
                        trading_halt_bars=trading_halt_bars,
                        skip_trades_if_cost_exceeds_equity_ratio=skip_trades_if_cost_exceeds_equity_ratio,
                        transition_smoother_enabled=transition_smoother_enabled,
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
                        shock_mode=shock_mode,
                        shock_weight_mult_atr=shock_weight_mult_atr,
                        shock_weight_mult_gap=shock_weight_mult_gap,
                    shock_freeze_rebalance=shock_freeze_rebalance,
                    shock_freeze_min_fraction=shock_freeze_min_fraction,
                    lookback_score_mode=lookback_score_mode,
                    )
                    rows.append(
                        {
                            "scenario_id": f"pcost_{scenario_idx:04d}",
                            "fee_multiplier": fee_mult,
                            "slippage_mode": mode,
                            "slippage_bps": bps,
                            "atr_slippage_mult": atr_mult,
                            "latency_bars": latency,
                            "order_model": order_model,
                            "limit_timeout_bars": limit_timeout_bars if order_model == "limit" else 0,
                            "limit_fill_probability": limit_fill_probability if order_model == "limit" else 1.0,
                            "limit_unfilled_penalty_bps": limit_unfilled_penalty_bps if order_model == "limit" else 0.0,
                            **sim.metrics,
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


def run_portfolio_regime_gating(
    *,
    market: PortfolioMarketData,
    params: PortfolioParams,
    base_config: BacktestConfig,
    baseline_cost: PortfolioCostConfig,
    seed: int,
    regime_by_ts: dict[str, str],
    base_regime_size_map: dict[str, float],
    regime_turnover_threshold_map: dict[str, float] | None = None,
    debug_mode: bool = False,
    max_cost_ratio_per_bar: float = 0.05,
    dd_controller_enabled: bool = False,
    dd_thresholds: tuple[float, float, float, float] = (0.10, 0.20, 0.30, 0.40),
    dd_gross_mults: tuple[float, float, float, float, float] = (1.0, 0.70, 0.50, 0.30, 0.0),
    dd_recover_thresholds: tuple[float, float, float, float] = (0.08, 0.16, 0.24, 0.32),
    kill_cooldown_bars: int = 168,
    disable_new_entry_when_dd: bool = True,
    rolling_peak_window_bars: int | None = None,
    stage_down_confirm_bars: int = 48,
    stage3_down_confirm_bars: int = 96,
    reentry_ramp_steps: int = 3,
    disable_new_entry_stage: int = 3,
    dd_turnover_threshold_mult: float = 1.5,
    dd_rebalance_mult: float | None = None,
    cap_mode: Literal["fixed", "adaptive"] = "fixed",
    base_cap: float = 0.25,
    cap_min: float = 0.20,
    cap_max: float = 0.40,
    backlog_thresholds: tuple[float, float, float] = (0.25, 0.50, 0.75),
    cap_steps: tuple[float, float, float, float] = (0.25, 0.30, 0.35, 0.40),
    high_vol_cap_max: float = 0.30,
    max_turnover_notional_to_equity: float | None = 0.25,
    drift_threshold: float | None = 0.35,
    gross_decay_steps: int = 3,
    max_notional_to_equity_mult: float = 3.0,
    enable_liquidation: bool = True,
    equity_floor_ratio: float = 0.01,
    trading_halt_bars: int = 168,
    skip_trades_if_cost_exceeds_equity_ratio: float = 0.02,
    transition_smoother_enabled: bool = False,
    gross_step_up: float = 0.10,
    gross_step_down: float = 0.25,
    post_halt_cooldown_bars: int = 168,
    post_halt_max_gross: float = 0.15,
    liquidation_lookback_bars: int = 720,
    liquidation_lookback_max_gross: float = 0.15,
    enable_symbol_shock_filters: bool = True,
    max_abs_weight_per_symbol: float = 0.12,
    atr_shock_threshold: float = 2.5,
    gap_shock_threshold: float = 0.10,
    shock_cooldown_bars: int = 72,
    shock_mode: Literal["exclude", "downweight"] = "downweight",
    shock_weight_mult_atr: float = 0.25,
    shock_weight_mult_gap: float = 0.10,
    shock_freeze_rebalance: bool | None = None,
    shock_freeze_min_fraction: float = 0.30,
    lookback_score_mode: Literal["single", "median_3"] = "single",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    regime_names = sorted(set(regime_by_ts.values()))
    scenarios: list[tuple[str, Literal["none", "on_off", "sizing"], set[str], dict[str, float]]] = []
    scenarios.append(("baseline", "none", set(regime_names), {}))
    for rg in regime_names:
        scenarios.append((f"onoff_{rg.replace('|', '_')}", "on_off", {rg}, {}))
    scenarios.append(
        (
            "sizing_base_map",
            "sizing",
            set(regime_names),
            dict(base_regime_size_map),
        )
    )
    scenarios.append(
        (
            "sizing_range_highvol_off",
            "sizing",
            set(regime_names),
            {
                "trend|low_vol": 1.0,
                "trend|high_vol": min(float(base_regime_size_map.get("trend|high_vol", 0.25)), 0.25),
                "range|low_vol": 1.0,
                "range|high_vol": 0.0,
            },
        )
    )

    rows: list[dict[str, Any]] = []
    regime_rows: list[dict[str, Any]] = []
    for idx, (scenario_id, mode, allowed, size_map) in enumerate(scenarios):
        sim = _simulate_portfolio(
            market=market,
            params=params,
            base_config=base_config,
            cost_cfg=baseline_cost,
            seed=seed + idx,
            regime_by_ts=regime_by_ts,
            regime_mode=mode,
            allowed_regimes=allowed,
            regime_size_map=size_map,
            regime_turnover_threshold_map=regime_turnover_threshold_map,
            debug_mode=debug_mode,
            max_cost_ratio_per_bar=max_cost_ratio_per_bar,
            dd_controller_enabled=dd_controller_enabled,
            dd_thresholds=dd_thresholds,
            dd_gross_mults=dd_gross_mults,
            dd_recover_thresholds=dd_recover_thresholds,
            kill_cooldown_bars=kill_cooldown_bars,
            disable_new_entry_when_dd=disable_new_entry_when_dd,
            rolling_peak_window_bars=rolling_peak_window_bars,
            stage_down_confirm_bars=stage_down_confirm_bars,
            stage3_down_confirm_bars=stage3_down_confirm_bars,
            reentry_ramp_steps=reentry_ramp_steps,
            disable_new_entry_stage=disable_new_entry_stage,
            dd_turnover_threshold_mult=dd_turnover_threshold_mult,
            dd_rebalance_mult=dd_rebalance_mult,
            cap_mode=cap_mode,
            base_cap=base_cap,
            cap_min=cap_min,
            cap_max=cap_max,
            backlog_thresholds=backlog_thresholds,
            cap_steps=cap_steps,
            high_vol_cap_max=high_vol_cap_max,
            max_turnover_notional_to_equity=max_turnover_notional_to_equity,
            drift_threshold=drift_threshold,
            gross_decay_steps=gross_decay_steps,
            max_notional_to_equity_mult=max_notional_to_equity_mult,
            enable_liquidation=enable_liquidation,
            equity_floor_ratio=equity_floor_ratio,
            trading_halt_bars=trading_halt_bars,
            skip_trades_if_cost_exceeds_equity_ratio=skip_trades_if_cost_exceeds_equity_ratio,
            transition_smoother_enabled=transition_smoother_enabled,
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
            shock_mode=shock_mode,
            shock_weight_mult_atr=shock_weight_mult_atr,
            shock_weight_mult_gap=shock_weight_mult_gap,
                    shock_freeze_rebalance=shock_freeze_rebalance,
                    shock_freeze_min_fraction=shock_freeze_min_fraction,
                    lookback_score_mode=lookback_score_mode,
        )
        m = sim.metrics
        rows.append(
            {
                "scenario_id": scenario_id,
                "gate_mode": mode,
                "allowed_regimes": "|".join(sorted(allowed)),
                "size_map_json": json.dumps(size_map, sort_keys=True),
                **m,
            }
        )
        if scenario_id.startswith("onoff_"):
            regime_name = scenario_id.replace("onoff_", "").replace("_", "|", 1)
            regime_rows.append(
                {
                    "regime": regime_name,
                    "win_rate": m["win_rate"],
                    "profit_factor": m["profit_factor"],
                    "max_drawdown": m["max_drawdown"],
                    "sharpe_like": m["sharpe_like"],
                    "trade_count": m["trade_count"],
                    "avg_trade": m["avg_trade"],
                    "net_pnl": m["net_pnl"],
                    "rebalance_count": m["rebalance_count"],
                }
            )
    return (
        pd.DataFrame(rows).sort_values("net_pnl", ascending=False).reset_index(drop=True),
        pd.DataFrame(regime_rows).sort_values("net_pnl", ascending=False).reset_index(drop=True),
    )


def _portfolio_verdict(summary: dict[str, Any]) -> str:
    gate_wfo = bool(float(summary.get("oos_positive_ratio", 0.0)) >= 0.60)
    gate_cost = bool(float(summary.get("cost_positive_ratio", 0.0)) >= 0.30)
    gate_mdd = bool(summary.get("mdd_better_or_equal_btc", False))
    gate_reb = bool(float(summary.get("rebalance_count", 0.0)) >= 200.0)
    gate_regime = bool(summary.get("regime_pf_mdd_flag", False))
    if gate_wfo and gate_cost and gate_mdd and gate_reb and gate_regime:
        return "PASS"
    if (gate_wfo and gate_cost) or (gate_cost and gate_reb and gate_regime):
        return "MIXED"
    return "FAIL"


def _portfolio_report(
    *,
    run_dir: Path,
    run_id: str,
    config_dump: dict[str, Any],
    summary: dict[str, Any],
    diagnostics: dict[str, Any],
    cost_df: pd.DataFrame,
    wf_df: pd.DataFrame,
    regime_table_df: pd.DataFrame,
    regime_exposure_df: pd.DataFrame,
    rate_limit_compare_df: pd.DataFrame,
    transition_compare_df: pd.DataFrame,
    peak_mode_compare_df: pd.DataFrame,
    liquidation_df: pd.DataFrame,
) -> None:
    def block_csv(df: pd.DataFrame, head: int = 12) -> str:
        if df.empty:
            return "_(no rows)_"
        return "```csv\n" + df.head(head).to_csv(index=False) + "```"

    lines: list[str] = []
    lines.append(f"# Portfolio Edge Report ({run_id})")
    lines.append("")
    lines.append("## Verdict")
    lines.append(f"- verdict: **{summary.get('verdict', 'UNKNOWN')}**")
    lines.append(f"- oos_positive_ratio: `{float(summary.get('oos_positive_ratio', 0.0)):.4f}` (gate >= 0.60)")
    lines.append(f"- cost_positive_ratio: `{float(summary.get('cost_positive_ratio', 0.0)):.4f}` (gate >= 0.30)")
    lines.append(
        f"- mdd_compare_to_btc: `{float(summary.get('portfolio_max_drawdown', 0.0)):.4f}` vs `{float(summary.get('btc_long_max_drawdown', 0.0)):.4f}`"
    )
    lines.append(f"- rebalance_count: `{int(float(summary.get('rebalance_count', 0.0)))}` (gate >= 200)")
    lines.append(f"- rebalance_attempts: `{int(float(summary.get('rebalance_attempt_count', 0.0)))}`")
    lines.append(f"- rebalance_execs: `{int(float(summary.get('rebalance_exec_count', 0.0)))}`")
    lines.append("")
    lines.append("## Interpretation")
    if float(summary.get("cost_positive_ratio", 0.0)) < 0.30:
        lines.append("- Cost gate failed: turnover/execution cost still dominates signal edge under stress scenarios.")
    else:
        lines.append("- Cost gate passed: strategy keeps positive outcomes in a meaningful portion of stress scenarios.")
    if float(summary.get("oos_positive_ratio", 0.0)) < 0.60:
        lines.append("- WFO gate failed: time-split out-of-sample consistency is insufficient.")
    else:
        lines.append("- WFO gate passed: rolling windows preserve positive OOS distribution.")
    if not bool(summary.get("regime_pf_mdd_flag", False)):
        lines.append("- Regime consistency weak: no regime cell shows PF>1.1 with relatively improved MDD.")
    else:
        lines.append("- Regime consistency present: at least one regime cell has PF>1.1 with improved drawdown profile.")
    lines.append("")
    lines.append("## Core Summary")
    for k, v in summary.items():
        if k == "verdict":
            continue
        if isinstance(v, float):
            lines.append(f"- {k}: `{v:.6f}`")
        else:
            lines.append(f"- {k}: `{v}`")
    lines.append("")
    lines.append("## Rebalance Diagnostics")
    lines.append(f"- attempts: `{diagnostics.get('rebalance_attempts', 0)}`")
    lines.append(f"- executions: `{diagnostics.get('rebalance_execs', 0)}`")
    lines.append(f"- warmup_bars: `{diagnostics.get('warmup_bars', 0)}`")
    lines.append(f"- step_bars: `{diagnostics.get('rebalance_step_bars', 0)}`")
    skip_reasons = diagnostics.get("skip_reasons", {})
    if isinstance(skip_reasons, dict) and skip_reasons:
        lines.append("- skip_reasons:")
        for k, v in sorted(skip_reasons.items()):
            lines.append(f"  - {k}: {v}")
    else:
        lines.append("- skip_reasons: _(none)_")
    dd_counts = diagnostics.get("dd_trigger_counts", {})
    if isinstance(dd_counts, dict) and dd_counts:
        lines.append("- dd_trigger_counts:")
        for k, v in sorted(dd_counts.items()):
            lines.append(f"  - {k}: {v}")
    dd_times = diagnostics.get("time_in_dd_stage", {})
    if isinstance(dd_times, dict) and dd_times:
        lines.append("- time_in_dd_stage:")
        for k, v in sorted(dd_times.items()):
            lines.append(f"  - {k}: {v}")
    kill_info = diagnostics.get("kill_switch_events", {})
    if isinstance(kill_info, dict):
        lines.append(f"- kill_switch_events: `{json.dumps(kill_info, ensure_ascii=True)}`")
    liq_info = diagnostics.get("liquidation_events", {})
    if isinstance(liq_info, dict):
        lines.append(f"- liquidation_events: `{json.dumps(liq_info, ensure_ascii=True)}`")
    liq_top3 = diagnostics.get("negative_equity_cause_top3", [])
    if isinstance(liq_top3, list) and liq_top3:
        lines.append("- liquidation_cause_top3:")
        for item in liq_top3[:3]:
            reason = str(item.get("reason", "unknown")) if isinstance(item, dict) else str(item)
            count = int(item.get("count", 0)) if isinstance(item, dict) else 0
            lines.append(f"  - {reason}: {count}")
    exc_reason = diagnostics.get("excluded_counts_by_reason", {})
    if isinstance(exc_reason, dict) and exc_reason:
        lines.append(f"- excluded_counts_by_reason: `{json.dumps(exc_reason, ensure_ascii=True)}`")
    shocked_reason = diagnostics.get("shocked_counts_by_reason", {})
    if isinstance(shocked_reason, dict) and shocked_reason:
        lines.append(f"- shocked_counts_by_reason: `{json.dumps(shocked_reason, ensure_ascii=True)}`")
    avg_mult = diagnostics.get("avg_effective_mult_by_reason", {})
    if isinstance(avg_mult, dict) and avg_mult:
        lines.append(f"- avg_effective_mult_by_reason: `{json.dumps(avg_mult, ensure_ascii=True)}`")
    lines.append(
        f"- fraction_of_time_any_shock_active: `{float(diagnostics.get('fraction_of_time_any_shock_active', 0.0)):.6f}`"
    )
    lines.append(f"- shock_active_bars_count: `{int(diagnostics.get('shock_active_bars_count', 0))}`")
    lines.append(
        f"- rebalance_skipped_due_to_shock: `{int(diagnostics.get('rebalance_skipped_due_to_shock_count', 0))}`"
        f" (`{float(diagnostics.get('rebalance_skipped_due_to_shock_ratio', 0.0)):.6f}`)"
    )
    lines.append("")
    lines.append("## Fee Spike Cause (Shock Freeze)")
    fee_total = float(summary.get("fee_cost_total", 0.0))
    attempt_count = int(diagnostics.get("rebalance_attempts", 0))
    exec_count = int(diagnostics.get("rebalance_execs", 0))
    fee_diag_df = pd.DataFrame(
        [
            {
                "fee_cost_total": fee_total,
                "rebalance_attempts": attempt_count,
                "rebalance_execs": exec_count,
                "shock_active_bars_count": int(diagnostics.get("shock_active_bars_count", 0)),
                "rebalance_skipped_due_to_shock_count": int(diagnostics.get("rebalance_skipped_due_to_shock_count", 0)),
                "rebalance_skipped_due_to_shock_ratio": float(diagnostics.get("rebalance_skipped_due_to_shock_ratio", 0.0)),
                "fee_per_rebalance_attempt": fee_total / max(attempt_count, 1),
                "fee_per_rebalance_exec": fee_total / max(exec_count, 1),
            }
        ]
    )
    lines.append(block_csv(fee_diag_df, head=1))
    if int(diagnostics.get("rebalance_skipped_due_to_shock_count", 0)) > 0:
        lines.append("- interpretation: shock-active bars skipped rebalances, reducing fee churn from micro-adjustment trades.")
    else:
        lines.append("- interpretation: no shock-freeze skip was triggered, so fee reduction from freeze could not materialize.")
    lines.append("")
    lines.append("## Config")
    lines.append("```json")
    lines.append(json.dumps(config_dump, indent=2, ensure_ascii=True, default=str))
    lines.append("```")
    lines.append("")
    lines.append("## Rebalance Rate Limit (fixed vs adaptive)")
    lines.append(block_csv(rate_limit_compare_df))
    lines.append("")
    lines.append("## Transition Smoother Comparison (off vs on)")
    lines.append(block_csv(transition_compare_df))
    lines.append("")
    lines.append("## Peak Mode Comparison (absolute vs rolling)")
    lines.append(block_csv(peak_mode_compare_df))
    lines.append("")
    lines.append("## Backlog Ratio Diagnostics")
    lines.append(f"- avg_backlog_ratio: `{float(summary.get('avg_backlog_ratio', 0.0)):.6f}`")
    lines.append(f"- max_backlog_ratio: `{float(summary.get('max_backlog_ratio', 0.0)):.6f}`")
    lines.append(f"- avg_cap_used: `{float(summary.get('avg_cap_used', 0.0)):.6f}`")
    lines.append(f"- fee_cost_per_gross_time: `{float(summary.get('fee_cost_per_gross_time', 0.0)):.8f}`")
    lines.append("")
    lines.append("## Cost Stress (head)")
    lines.append(block_csv(cost_df))
    lines.append("")
    lines.append("## Walk-forward Windows (head)")
    lines.append(block_csv(wf_df))
    lines.append("")
    lines.append("## Regime Table (head)")
    lines.append(block_csv(regime_table_df))
    lines.append("")
    lines.append("## Regime Exposure Heatmap Table")
    lines.append(block_csv(regime_exposure_df))
    lines.append("")
    lines.append("## Liquidation Events (head)")
    lines.append(block_csv(liquidation_df))
    lines.append("")
    top_excluded = diagnostics.get("top5_excluded_symbols", [])
    top_shocked = diagnostics.get("top5_shocked_symbols", [])
    top_caps = diagnostics.get("top5_cap_hit_symbols", [])
    lines.append("## Symbol Shocked Top5")
    lines.append(block_csv(pd.DataFrame(top_shocked)))
    lines.append("")
    lines.append("## Symbol Exclusions Top5")
    lines.append(block_csv(pd.DataFrame(top_excluded)))
    lines.append("")
    lines.append("## Symbol Cap Hits Top5")
    lines.append(block_csv(pd.DataFrame(top_caps)))
    lines.append("")
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _build_regime_exposure_table(sim: PortfolioSimResult) -> pd.DataFrame:
    if sim.turnover.empty or sim.equity_curve.empty:
        return pd.DataFrame(
            columns=[
                "regime",
                "gross_mean",
                "profit_factor",
                "max_drawdown",
                "trade_count",
                "cost_total",
                "fee_cost",
                "slippage_cost",
                "penalty_cost",
                "off_ratio",
            ]
        )
    turn = sim.turnover.copy()
    eq = sim.equity_curve.copy()
    cost = sim.cost_breakdown.copy()
    eq["equity_prev"] = eq["equity"].shift(1)
    eq["pnl_step"] = eq["equity"] - eq["equity_prev"]
    eq["pnl_step"] = eq["pnl_step"].fillna(0.0)

    core = turn.merge(cost, on="timestamp", how="left").merge(eq[["timestamp", "pnl_step"]], on="timestamp", how="left")
    core["pnl_step"] = core["pnl_step"].fillna(0.0)
    gross_by_ts = eq[["timestamp", "gross_exposure"]].copy()
    core = core.merge(gross_by_ts, on="timestamp", how="left")

    rows: list[dict[str, Any]] = []
    for regime, g in core.groupby("regime"):
        pnl = g["pnl_step"].to_numpy(dtype=float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        pf = float(np.sum(wins) / max(abs(np.sum(losses)), 1e-9)) if losses.size else float("inf")
        eq_reg = eq[eq["regime"] == regime]["equity"].to_numpy(dtype=float)
        mdd = _max_drawdown(eq_reg) if eq_reg.size else 0.0
        off_ratio = float(np.mean(g["regime_scale"] <= 1e-12)) if "regime_scale" in g.columns else 0.0
        rows.append(
            {
                "regime": regime,
                "gross_mean": float(g["gross_exposure"].mean()) if "gross_exposure" in g.columns else 0.0,
                "profit_factor": pf,
                "max_drawdown": mdd,
                "trade_count": float(g["trades_this_bar"].sum()) if "trades_this_bar" in g.columns else 0.0,
                "cost_total": float(g["total_cost"].sum()) if "total_cost" in g.columns else 0.0,
                "fee_cost": float(g["fee_cost"].sum()) if "fee_cost" in g.columns else 0.0,
                "slippage_cost": float(g["slippage_cost"].sum()) if "slippage_cost" in g.columns else 0.0,
                "penalty_cost": float(g["penalty_cost"].sum()) if "penalty_cost" in g.columns else 0.0,
                "off_ratio": off_ratio,
            }
        )
    return pd.DataFrame(rows).sort_values("regime").reset_index(drop=True)


def run_portfolio_validation(
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
    signal_models: list[str],
    lookback_bars: list[int],
    rebalance_bars: list[int],
    k_values: list[int],
    gross_values: list[float],
    rank_buffers: list[int],
    high_vol_percentiles: list[float],
    gross_maps: list[str],
    off_grace_bars_list: list[int],
    phased_entry_steps_list: list[int],
    turnover_threshold: float,
    turnover_threshold_high_vol: float | None,
    turnover_threshold_low_vol: float | None,
    vol_lookback: int,
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
    trend_ema_span: int,
    trend_slope_lookback: int,
    trend_slope_threshold: float,
    regime_atr_period: int,
    regime_vol_lookback: int,
    regime_vol_percentile: float,
    high_vol_gross_mult: float,
    debug_mode: bool,
    max_cost_ratio_per_bar: float,
    dd_controller_enabled: bool = False,
    dd_thresholds: tuple[float, float, float, float] = (0.10, 0.20, 0.30, 0.40),
    dd_gross_mults: tuple[float, float, float, float, float] = (1.0, 0.70, 0.50, 0.30, 0.0),
    dd_recover_thresholds: tuple[float, float, float, float] = (0.08, 0.16, 0.24, 0.32),
    kill_cooldown_bars: int = 168,
    disable_new_entry_when_dd: bool = True,
    rolling_peak_window_bars: int | None = None,
    stage_down_confirm_bars: int = 48,
    stage3_down_confirm_bars: int = 96,
    reentry_ramp_steps: int = 3,
    disable_new_entry_stage: int = 3,
    dd_turnover_threshold_mult: float = 1.5,
    dd_rebalance_mult: float | None = None,
    cap_mode: Literal["fixed", "adaptive"] = "fixed",
    base_cap: float = 0.25,
    cap_min: float = 0.20,
    cap_max: float = 0.40,
    backlog_thresholds: tuple[float, float, float] = (0.25, 0.50, 0.75),
    cap_steps: tuple[float, float, float, float] = (0.25, 0.30, 0.35, 0.40),
    high_vol_cap_max: float = 0.30,
    max_turnover_notional_to_equity: float | None,
    drift_threshold: float | None,
    gross_decay_steps: int,
    max_notional_to_equity_mult: float,
    enable_liquidation: bool = True,
    equity_floor_ratio: float = 0.01,
    trading_halt_bars: int = 168,
    skip_trades_if_cost_exceeds_equity_ratio: float = 0.02,
    transition_smoother_enabled: bool = False,
    gross_step_up: float = 0.10,
    gross_step_down: float = 0.25,
    post_halt_cooldown_bars: int = 168,
    post_halt_max_gross: float = 0.15,
    liquidation_lookback_bars: int = 720,
    liquidation_lookback_max_gross: float = 0.15,
    enable_symbol_shock_filters: bool = True,
    max_abs_weight_per_symbol: float = 0.12,
    atr_shock_threshold: float = 2.5,
    gap_shock_threshold: float = 0.10,
    shock_cooldown_bars: int = 72,
    shock_mode: Literal["exclude", "downweight"] = "downweight",
    shock_weight_mult_atr: float = 0.25,
    shock_weight_mult_gap: float = 0.10,
    shock_freeze_rebalance: bool | None = None,
    shock_freeze_min_fraction: float = 0.30,
    lookback_score_mode: Literal["single", "median_3"] = "single",
    stop_on_anomaly: bool,
) -> PortfolioRunOutput:
    run_id = f"portfolio_{pd.Timestamp.now(tz='UTC').strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if shock_freeze_rebalance is None:
        shock_freeze_rebalance = bool(str(shock_mode).strip().lower() == "downweight")
    else:
        shock_freeze_rebalance = bool(shock_freeze_rebalance)
    shock_freeze_min_fraction = min(max(float(shock_freeze_min_fraction), 0.0), 1.0)
    lookback_score_mode = str(lookback_score_mode).strip().lower()  # type: ignore[assignment]
    if lookback_score_mode not in {"single", "median_3"}:
        lookback_score_mode = "single"  # type: ignore[assignment]

    candles_by_symbol = load_multi_candles(
        data_source=data_source,
        symbols=symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        seed=seed,
        csv_path=csv_path,
        testnet=testnet,
    )
    market = _build_portfolio_market(candles_by_symbol, atr_period=max(regime_atr_period, base_config.atr_period))
    if market.bars < 100:
        raise ValueError("insufficient aligned bars for portfolio suite")

    btc_idx = market.symbols.index("BTC/USDT") if "BTC/USDT" in market.symbols else 0
    btc_df = pd.DataFrame(
        {
            "timestamp": market.timestamps,
            "open": market.open[:, btc_idx],
            "high": market.high[:, btc_idx],
            "low": market.low[:, btc_idx],
            "close": market.close[:, btc_idx],
            "volume": np.ones(market.bars, dtype=float),
        }
    )
    pct_list = sorted({round(float(x), 4) for x in (high_vol_percentiles or [regime_vol_percentile])})
    regime_maps_by_pct: dict[float, dict[str, str]] = {}
    for pct in pct_list:
        regimes = _label_regimes(
            btc_df,
            trend_ema_span=trend_ema_span,
            trend_slope_lookback=trend_slope_lookback,
            trend_slope_threshold=trend_slope_threshold,
            atr_period=regime_atr_period,
            vol_lookback=regime_vol_lookback,
            vol_percentile=min(max(float(pct), 0.10), 0.99),
        )
        regime_maps_by_pct[float(pct)] = {_ts_key(ts): str(rg) for ts, rg in zip(market.timestamps, regimes)}
    low_thr = float(turnover_threshold if turnover_threshold_low_vol is None else turnover_threshold_low_vol)
    high_thr = float(turnover_threshold if turnover_threshold_high_vol is None else turnover_threshold_high_vol)
    regime_turnover_threshold_map = {
        "trend|low_vol": low_thr,
        "range|low_vol": low_thr,
        "trend|high_vol": high_thr,
        "range|high_vol": high_thr,
    }

    param_grid = _build_portfolio_param_grid(
        signal_models=signal_models,
        lookback_bars=lookback_bars,
        rebalance_bars=rebalance_bars,
        k_values=k_values,
        gross_values=gross_values,
        turnover_threshold=turnover_threshold,
        vol_lookback=vol_lookback,
        rank_buffers=rank_buffers,
        high_vol_percentiles=pct_list,
        gross_maps=gross_maps,
        off_grace_bars_list=off_grace_bars_list,
        phased_entry_steps_list=phased_entry_steps_list,
    )
    if not param_grid:
        raise ValueError("portfolio parameter grid is empty")

    preferred_order = "limit" if "limit" in order_models else order_models[0]
    baseline_latency = 1 if 1 in latency_bars else latency_bars[0]
    baseline_cost = PortfolioCostConfig(
        order_model=preferred_order,
        fee_multiplier=1.0,
        slippage_mode=slippage_mode,
        slippage_bps=fixed_slippage_bps[0],
        atr_slippage_mult=atr_slippage_mults[0],
        latency_bars=baseline_latency,
        limit_timeout_bars=limit_timeout_bars,
        limit_fill_probability=limit_fill_probability,
        limit_unfilled_penalty_bps=limit_unfilled_penalty_bps,
    )

    wf_df, wf_candidates_df, wf_summary = run_portfolio_walk_forward(
        market=market,
        param_grid=param_grid,
        base_config=base_config,
        baseline_cost=baseline_cost,
        metric=walk_metric,
        train_days=walk_train_days,
        test_days=walk_test_days,
        step_days=walk_step_days,
        top_pct=walk_top_pct,
        max_candidates=walk_max_candidates,
        seed=seed,
        start=start,
        end=end,
        regime_maps_by_pct=regime_maps_by_pct,
        regime_turnover_threshold_map=regime_turnover_threshold_map,
        debug_mode=debug_mode,
        max_cost_ratio_per_bar=max_cost_ratio_per_bar,
        dd_controller_enabled=dd_controller_enabled,
        dd_thresholds=dd_thresholds,
        dd_gross_mults=dd_gross_mults,
        dd_recover_thresholds=dd_recover_thresholds,
        kill_cooldown_bars=kill_cooldown_bars,
        disable_new_entry_when_dd=disable_new_entry_when_dd,
        rolling_peak_window_bars=rolling_peak_window_bars,
        stage_down_confirm_bars=stage_down_confirm_bars,
        stage3_down_confirm_bars=stage3_down_confirm_bars,
        reentry_ramp_steps=reentry_ramp_steps,
        disable_new_entry_stage=disable_new_entry_stage,
        dd_turnover_threshold_mult=dd_turnover_threshold_mult,
        dd_rebalance_mult=dd_rebalance_mult,
        cap_mode=cap_mode,
        base_cap=base_cap,
        cap_min=cap_min,
        cap_max=cap_max,
        backlog_thresholds=backlog_thresholds,
        cap_steps=cap_steps,
        high_vol_cap_max=high_vol_cap_max,
        max_turnover_notional_to_equity=max_turnover_notional_to_equity,
        drift_threshold=drift_threshold,
        gross_decay_steps=gross_decay_steps,
        max_notional_to_equity_mult=max_notional_to_equity_mult,
        enable_liquidation=enable_liquidation,
        equity_floor_ratio=equity_floor_ratio,
        trading_halt_bars=trading_halt_bars,
        skip_trades_if_cost_exceeds_equity_ratio=skip_trades_if_cost_exceeds_equity_ratio,
        transition_smoother_enabled=transition_smoother_enabled,
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
        shock_mode=shock_mode,
        shock_weight_mult_atr=shock_weight_mult_atr,
        shock_weight_mult_gap=shock_weight_mult_gap,
                    shock_freeze_rebalance=shock_freeze_rebalance,
                    shock_freeze_min_fraction=shock_freeze_min_fraction,
                    lookback_score_mode=lookback_score_mode,
    )
    selected_params = _select_portfolio_params(wf_df=wf_df, fallback=param_grid[0])
    selected_regime_map = _nearest_regime_map(regime_maps_by_pct, selected_params.high_vol_percentile)
    selected_size_map = _resolve_gross_profile(selected_params.gross_map)
    selected_size_map["trend|high_vol"] = min(selected_size_map.get("trend|high_vol", 0.25), float(high_vol_gross_mult))
    selected_size_map["range|high_vol"] = min(selected_size_map.get("range|high_vol", 0.10), float(high_vol_gross_mult))

    baseline_sim = _simulate_portfolio(
        market=market,
        params=selected_params,
        base_config=base_config,
        cost_cfg=baseline_cost,
        seed=seed,
        regime_by_ts=selected_regime_map,
        regime_mode="sizing",
        regime_size_map=selected_size_map,
        regime_turnover_threshold_map=regime_turnover_threshold_map,
        debug_mode=debug_mode,
        max_cost_ratio_per_bar=max_cost_ratio_per_bar,
        dd_controller_enabled=dd_controller_enabled,
        dd_thresholds=dd_thresholds,
        dd_gross_mults=dd_gross_mults,
        dd_recover_thresholds=dd_recover_thresholds,
        kill_cooldown_bars=kill_cooldown_bars,
        disable_new_entry_when_dd=disable_new_entry_when_dd,
        rolling_peak_window_bars=rolling_peak_window_bars,
        stage_down_confirm_bars=stage_down_confirm_bars,
        stage3_down_confirm_bars=stage3_down_confirm_bars,
        reentry_ramp_steps=reentry_ramp_steps,
        disable_new_entry_stage=disable_new_entry_stage,
        dd_turnover_threshold_mult=dd_turnover_threshold_mult,
        dd_rebalance_mult=dd_rebalance_mult,
        cap_mode=cap_mode,
        base_cap=base_cap,
        cap_min=cap_min,
        cap_max=cap_max,
        backlog_thresholds=backlog_thresholds,
        cap_steps=cap_steps,
        high_vol_cap_max=high_vol_cap_max,
        max_turnover_notional_to_equity=max_turnover_notional_to_equity,
        drift_threshold=drift_threshold,
        gross_decay_steps=gross_decay_steps,
        max_notional_to_equity_mult=max_notional_to_equity_mult,
        enable_liquidation=enable_liquidation,
        equity_floor_ratio=equity_floor_ratio,
        trading_halt_bars=trading_halt_bars,
        skip_trades_if_cost_exceeds_equity_ratio=skip_trades_if_cost_exceeds_equity_ratio,
        transition_smoother_enabled=transition_smoother_enabled,
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
        shock_mode=shock_mode,
        shock_weight_mult_atr=shock_weight_mult_atr,
        shock_weight_mult_gap=shock_weight_mult_gap,
                    shock_freeze_rebalance=shock_freeze_rebalance,
                    shock_freeze_min_fraction=shock_freeze_min_fraction,
                    lookback_score_mode=lookback_score_mode,
        stop_on_anomaly=stop_on_anomaly,
    )
    compare_base_cap = float(base_cap if max_turnover_notional_to_equity is None else max_turnover_notional_to_equity)
    fixed_sim = baseline_sim if cap_mode == "fixed" else _simulate_portfolio(
        market=market,
        params=selected_params,
        base_config=base_config,
        cost_cfg=baseline_cost,
        seed=seed + 91_001,
        regime_by_ts=selected_regime_map,
        regime_mode="sizing",
        regime_size_map=selected_size_map,
        regime_turnover_threshold_map=regime_turnover_threshold_map,
        debug_mode=debug_mode,
        max_cost_ratio_per_bar=max_cost_ratio_per_bar,
        dd_controller_enabled=dd_controller_enabled,
        dd_thresholds=dd_thresholds,
        dd_gross_mults=dd_gross_mults,
        dd_recover_thresholds=dd_recover_thresholds,
        kill_cooldown_bars=kill_cooldown_bars,
        disable_new_entry_when_dd=disable_new_entry_when_dd,
        rolling_peak_window_bars=rolling_peak_window_bars,
        stage_down_confirm_bars=stage_down_confirm_bars,
        stage3_down_confirm_bars=stage3_down_confirm_bars,
        reentry_ramp_steps=reentry_ramp_steps,
        disable_new_entry_stage=disable_new_entry_stage,
        dd_turnover_threshold_mult=dd_turnover_threshold_mult,
        dd_rebalance_mult=dd_rebalance_mult,
        cap_mode="fixed",
        base_cap=compare_base_cap,
        cap_min=cap_min,
        cap_max=cap_max,
        backlog_thresholds=backlog_thresholds,
        cap_steps=cap_steps,
        high_vol_cap_max=high_vol_cap_max,
        max_turnover_notional_to_equity=max_turnover_notional_to_equity,
        drift_threshold=drift_threshold,
        gross_decay_steps=gross_decay_steps,
        max_notional_to_equity_mult=max_notional_to_equity_mult,
        enable_liquidation=enable_liquidation,
        equity_floor_ratio=equity_floor_ratio,
        trading_halt_bars=trading_halt_bars,
        skip_trades_if_cost_exceeds_equity_ratio=skip_trades_if_cost_exceeds_equity_ratio,
        transition_smoother_enabled=transition_smoother_enabled,
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
        shock_mode=shock_mode,
        shock_weight_mult_atr=shock_weight_mult_atr,
        shock_weight_mult_gap=shock_weight_mult_gap,
                    shock_freeze_rebalance=shock_freeze_rebalance,
                    shock_freeze_min_fraction=shock_freeze_min_fraction,
                    lookback_score_mode=lookback_score_mode,
        stop_on_anomaly=stop_on_anomaly,
    )
    adaptive_sim = baseline_sim if cap_mode == "adaptive" else _simulate_portfolio(
        market=market,
        params=selected_params,
        base_config=base_config,
        cost_cfg=baseline_cost,
        seed=seed + 91_002,
        regime_by_ts=selected_regime_map,
        regime_mode="sizing",
        regime_size_map=selected_size_map,
        regime_turnover_threshold_map=regime_turnover_threshold_map,
        debug_mode=debug_mode,
        max_cost_ratio_per_bar=max_cost_ratio_per_bar,
        dd_controller_enabled=dd_controller_enabled,
        dd_thresholds=dd_thresholds,
        dd_gross_mults=dd_gross_mults,
        dd_recover_thresholds=dd_recover_thresholds,
        kill_cooldown_bars=kill_cooldown_bars,
        disable_new_entry_when_dd=disable_new_entry_when_dd,
        rolling_peak_window_bars=rolling_peak_window_bars,
        stage_down_confirm_bars=stage_down_confirm_bars,
        stage3_down_confirm_bars=stage3_down_confirm_bars,
        reentry_ramp_steps=reentry_ramp_steps,
        disable_new_entry_stage=disable_new_entry_stage,
        dd_turnover_threshold_mult=dd_turnover_threshold_mult,
        dd_rebalance_mult=dd_rebalance_mult,
        cap_mode="adaptive",
        base_cap=compare_base_cap,
        cap_min=cap_min,
        cap_max=cap_max,
        backlog_thresholds=backlog_thresholds,
        cap_steps=cap_steps,
        high_vol_cap_max=high_vol_cap_max,
        max_turnover_notional_to_equity=max_turnover_notional_to_equity,
        drift_threshold=drift_threshold,
        gross_decay_steps=gross_decay_steps,
        max_notional_to_equity_mult=max_notional_to_equity_mult,
        enable_liquidation=enable_liquidation,
        equity_floor_ratio=equity_floor_ratio,
        trading_halt_bars=trading_halt_bars,
        skip_trades_if_cost_exceeds_equity_ratio=skip_trades_if_cost_exceeds_equity_ratio,
        transition_smoother_enabled=transition_smoother_enabled,
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
        shock_mode=shock_mode,
        shock_weight_mult_atr=shock_weight_mult_atr,
        shock_weight_mult_gap=shock_weight_mult_gap,
                    shock_freeze_rebalance=shock_freeze_rebalance,
                    shock_freeze_min_fraction=shock_freeze_min_fraction,
                    lookback_score_mode=lookback_score_mode,
        stop_on_anomaly=stop_on_anomaly,
    )
    rate_limit_compare_df = pd.DataFrame(
        [
            {
                "scenario": "fixed_cap",
                "cap_mode": "fixed",
                "base_cap": compare_base_cap,
                **fixed_sim.metrics,
            },
            {
                "scenario": "adaptive_cap",
                "cap_mode": "adaptive",
                "base_cap": compare_base_cap,
                **adaptive_sim.metrics,
            },
        ]
    )
    no_smoother_sim = baseline_sim if not transition_smoother_enabled else _simulate_portfolio(
        market=market,
        params=selected_params,
        base_config=base_config,
        cost_cfg=baseline_cost,
        seed=seed + 91_003,
        regime_by_ts=selected_regime_map,
        regime_mode="sizing",
        regime_size_map=selected_size_map,
        regime_turnover_threshold_map=regime_turnover_threshold_map,
        debug_mode=debug_mode,
        max_cost_ratio_per_bar=max_cost_ratio_per_bar,
        dd_controller_enabled=dd_controller_enabled,
        dd_thresholds=dd_thresholds,
        dd_gross_mults=dd_gross_mults,
        dd_recover_thresholds=dd_recover_thresholds,
        kill_cooldown_bars=kill_cooldown_bars,
        disable_new_entry_when_dd=disable_new_entry_when_dd,
        rolling_peak_window_bars=rolling_peak_window_bars,
        stage_down_confirm_bars=stage_down_confirm_bars,
        stage3_down_confirm_bars=stage3_down_confirm_bars,
        reentry_ramp_steps=reentry_ramp_steps,
        disable_new_entry_stage=disable_new_entry_stage,
        dd_turnover_threshold_mult=dd_turnover_threshold_mult,
        dd_rebalance_mult=dd_rebalance_mult,
        cap_mode=cap_mode,
        base_cap=base_cap,
        cap_min=cap_min,
        cap_max=cap_max,
        backlog_thresholds=backlog_thresholds,
        cap_steps=cap_steps,
        high_vol_cap_max=high_vol_cap_max,
        max_turnover_notional_to_equity=max_turnover_notional_to_equity,
        drift_threshold=drift_threshold,
        gross_decay_steps=gross_decay_steps,
        max_notional_to_equity_mult=max_notional_to_equity_mult,
        enable_liquidation=enable_liquidation,
        equity_floor_ratio=equity_floor_ratio,
        trading_halt_bars=trading_halt_bars,
        skip_trades_if_cost_exceeds_equity_ratio=skip_trades_if_cost_exceeds_equity_ratio,
        transition_smoother_enabled=False,
        gross_step_up=1.0,
        gross_step_down=1.0,
        post_halt_cooldown_bars=0,
        post_halt_max_gross=max(selected_params.gross_exposure, 1.0),
        liquidation_lookback_bars=0,
        liquidation_lookback_max_gross=max(selected_params.gross_exposure, 1.0),
        enable_symbol_shock_filters=enable_symbol_shock_filters,
        max_abs_weight_per_symbol=max_abs_weight_per_symbol,
        atr_shock_threshold=atr_shock_threshold,
        gap_shock_threshold=gap_shock_threshold,
        shock_cooldown_bars=shock_cooldown_bars,
        shock_mode=shock_mode,
        shock_weight_mult_atr=shock_weight_mult_atr,
        shock_weight_mult_gap=shock_weight_mult_gap,
                    shock_freeze_rebalance=shock_freeze_rebalance,
                    shock_freeze_min_fraction=shock_freeze_min_fraction,
                    lookback_score_mode=lookback_score_mode,
        stop_on_anomaly=stop_on_anomaly,
    )
    off_transition_cause = int((no_smoother_sim.diagnostics.get("negative_equity_cause_counts", {}) or {}).get("negative_equity_due_to_gross_transition", 0))
    on_transition_cause = int((baseline_sim.diagnostics.get("negative_equity_cause_counts", {}) or {}).get("negative_equity_due_to_gross_transition", 0))
    transition_compare_df = pd.DataFrame(
        [
            {
                "scenario": "smoother_off",
                "liquidation_count": float(no_smoother_sim.metrics.get("liquidation_count", 0.0)),
                "gross_transition_cause_count": float(off_transition_cause),
                "max_drawdown": float(no_smoother_sim.metrics.get("max_drawdown", 0.0)),
                "fee_cost_total": float(no_smoother_sim.metrics.get("cost_fee_total", 0.0)),
                "fee_cost_per_gross_time": float(no_smoother_sim.metrics.get("fee_cost_per_gross_time", 0.0)),
            },
            {
                "scenario": "smoother_on" if transition_smoother_enabled else "current",
                "liquidation_count": float(baseline_sim.metrics.get("liquidation_count", 0.0)),
                "gross_transition_cause_count": float(on_transition_cause),
                "max_drawdown": float(baseline_sim.metrics.get("max_drawdown", 0.0)),
                "fee_cost_total": float(baseline_sim.metrics.get("cost_fee_total", 0.0)),
                "fee_cost_per_gross_time": float(baseline_sim.metrics.get("fee_cost_per_gross_time", 0.0)),
            },
        ]
    )
    dd_curve = baseline_sim.dd_timeline.copy()
    if dd_curve.empty:
        peak_mode_compare_df = pd.DataFrame(columns=["peak_mode", "window_bars", "signal_drawdown_max"])
    else:
        controller_mode = "rolling" if rolling_peak_window_bars is not None else "absolute"
        controller_max = float(dd_curve["drawdown"].max()) if "drawdown" in dd_curve.columns else 0.0
        eq_vals = dd_curve["equity"].to_numpy(dtype=float)
        abs_peak_vals = np.maximum.accumulate(np.maximum(eq_vals, 1e-9))
        abs_dd_vals = 1.0 - (np.maximum(eq_vals, 0.0) / np.maximum(abs_peak_vals, 1e-9))
        abs_max = float(np.max(abs_dd_vals)) if abs_dd_vals.size else 0.0
        peak_mode_compare_df = pd.DataFrame(
            [
                {"peak_mode": controller_mode, "window_bars": rolling_peak_window_bars, "signal_drawdown_max": controller_max},
                {"peak_mode": "absolute", "window_bars": None, "signal_drawdown_max": abs_max},
            ]
        )
    cost_df, cost_sens_df = run_portfolio_cost_stress(
        market=market,
        params=selected_params,
        base_config=base_config,
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
        regime_by_ts=selected_regime_map,
        regime_size_map=selected_size_map,
        regime_turnover_threshold_map=regime_turnover_threshold_map,
        debug_mode=debug_mode,
        max_cost_ratio_per_bar=max_cost_ratio_per_bar,
        dd_controller_enabled=dd_controller_enabled,
        dd_thresholds=dd_thresholds,
        dd_gross_mults=dd_gross_mults,
        dd_recover_thresholds=dd_recover_thresholds,
        kill_cooldown_bars=kill_cooldown_bars,
        disable_new_entry_when_dd=disable_new_entry_when_dd,
        rolling_peak_window_bars=rolling_peak_window_bars,
        stage_down_confirm_bars=stage_down_confirm_bars,
        stage3_down_confirm_bars=stage3_down_confirm_bars,
        reentry_ramp_steps=reentry_ramp_steps,
        disable_new_entry_stage=disable_new_entry_stage,
        dd_turnover_threshold_mult=dd_turnover_threshold_mult,
        dd_rebalance_mult=dd_rebalance_mult,
        cap_mode=cap_mode,
        base_cap=base_cap,
        cap_min=cap_min,
        cap_max=cap_max,
        backlog_thresholds=backlog_thresholds,
        cap_steps=cap_steps,
        high_vol_cap_max=high_vol_cap_max,
        max_turnover_notional_to_equity=max_turnover_notional_to_equity,
        drift_threshold=drift_threshold,
        gross_decay_steps=gross_decay_steps,
        max_notional_to_equity_mult=max_notional_to_equity_mult,
        enable_liquidation=enable_liquidation,
        equity_floor_ratio=equity_floor_ratio,
        trading_halt_bars=trading_halt_bars,
        skip_trades_if_cost_exceeds_equity_ratio=skip_trades_if_cost_exceeds_equity_ratio,
        transition_smoother_enabled=transition_smoother_enabled,
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
        shock_mode=shock_mode,
        shock_weight_mult_atr=shock_weight_mult_atr,
        shock_weight_mult_gap=shock_weight_mult_gap,
                    shock_freeze_rebalance=shock_freeze_rebalance,
                    shock_freeze_min_fraction=shock_freeze_min_fraction,
                    lookback_score_mode=lookback_score_mode,
    )
    regime_df, regime_table_df = run_portfolio_regime_gating(
        market=market,
        params=selected_params,
        base_config=base_config,
        baseline_cost=baseline_cost,
        seed=seed,
        regime_by_ts=selected_regime_map,
        base_regime_size_map=selected_size_map,
        regime_turnover_threshold_map=regime_turnover_threshold_map,
        debug_mode=debug_mode,
        max_cost_ratio_per_bar=max_cost_ratio_per_bar,
        dd_controller_enabled=dd_controller_enabled,
        dd_thresholds=dd_thresholds,
        dd_gross_mults=dd_gross_mults,
        dd_recover_thresholds=dd_recover_thresholds,
        kill_cooldown_bars=kill_cooldown_bars,
        disable_new_entry_when_dd=disable_new_entry_when_dd,
        rolling_peak_window_bars=rolling_peak_window_bars,
        stage_down_confirm_bars=stage_down_confirm_bars,
        stage3_down_confirm_bars=stage3_down_confirm_bars,
        reentry_ramp_steps=reentry_ramp_steps,
        disable_new_entry_stage=disable_new_entry_stage,
        dd_turnover_threshold_mult=dd_turnover_threshold_mult,
        dd_rebalance_mult=dd_rebalance_mult,
        cap_mode=cap_mode,
        base_cap=base_cap,
        cap_min=cap_min,
        cap_max=cap_max,
        backlog_thresholds=backlog_thresholds,
        cap_steps=cap_steps,
        high_vol_cap_max=high_vol_cap_max,
        max_turnover_notional_to_equity=max_turnover_notional_to_equity,
        drift_threshold=drift_threshold,
        gross_decay_steps=gross_decay_steps,
        max_notional_to_equity_mult=max_notional_to_equity_mult,
        enable_liquidation=enable_liquidation,
        equity_floor_ratio=equity_floor_ratio,
        trading_halt_bars=trading_halt_bars,
        skip_trades_if_cost_exceeds_equity_ratio=skip_trades_if_cost_exceeds_equity_ratio,
        transition_smoother_enabled=transition_smoother_enabled,
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
        shock_mode=shock_mode,
        shock_weight_mult_atr=shock_weight_mult_atr,
        shock_weight_mult_gap=shock_weight_mult_gap,
                    shock_freeze_rebalance=shock_freeze_rebalance,
                    shock_freeze_min_fraction=shock_freeze_min_fraction,
                    lookback_score_mode=lookback_score_mode,
    )

    bench_df = _portfolio_btc_benchmark(market=market, initial_equity=float(base_config.initial_equity))
    equity_compare_df = baseline_sim.equity_curve[["timestamp", "equity"]].merge(
        bench_df.rename(columns={"btc_equity": "btc_equity"}),
        on="timestamp",
        how="left",
    )
    regime_exposure_df = _build_regime_exposure_table(baseline_sim)
    regime_code_map = {"trend|low_vol": 0.0, "trend|high_vol": 1.0, "range|low_vol": 2.0, "range|high_vol": 3.0}
    regime_timeline_df = pd.DataFrame(
        {
            "timestamp": market.timestamps.astype(str),
            "regime": [selected_regime_map.get(_ts_key(ts), "range|low_vol") for ts in market.timestamps],
        }
    )
    regime_timeline_df["regime_code"] = regime_timeline_df["regime"].map(regime_code_map).fillna(0.0)
    btc_mdd = _max_drawdown(bench_df["btc_equity"].to_numpy(dtype=float)) if not bench_df.empty else 0.0
    baseline_metrics = baseline_sim.metrics
    portfolio_mdd = float(baseline_metrics.get("max_drawdown", 0.0))
    regime_pf_mdd_flag = False
    if not regime_table_df.empty:
        pf_cond = regime_table_df["profit_factor"] > 1.1
        mdd_cond = regime_table_df["max_drawdown"] >= regime_table_df["max_drawdown"].median()
        regime_pf_mdd_flag = bool((pf_cond & mdd_cond).any())

    summary: dict[str, Any] = {
        "bars": float(market.bars),
        "symbols": len(market.symbols),
        "signal_model": selected_params.signal_model,
        "lookback_bars": float(selected_params.lookback_bars),
        "rebalance_bars": float(selected_params.rebalance_bars),
        "k": float(selected_params.k),
        "rank_buffer": float(selected_params.rank_buffer),
        "high_vol_percentile": float(selected_params.high_vol_percentile),
        "gross_map": selected_params.gross_map,
        "off_grace_bars": float(selected_params.off_grace_bars),
        "phased_entry_steps": float(selected_params.phased_entry_steps),
        "gross_exposure": float(selected_params.gross_exposure),
        "turnover_threshold": float(selected_params.turnover_threshold),
        "cap_mode": str(cap_mode),
        "base_cap": float(compare_base_cap),
        "cap_min": float(cap_min),
        "cap_max": float(cap_max),
        "high_vol_cap_max": float(high_vol_cap_max),
        "dd_controller_enabled": bool(dd_controller_enabled),
        "net_pnl": float(baseline_metrics.get("net_pnl", 0.0)),
        "cagr": float(baseline_metrics.get("cagr", 0.0)),
        "portfolio_max_drawdown": portfolio_mdd,
        "profit_factor": float(baseline_metrics.get("profit_factor", 0.0)),
        "win_rate": float(baseline_metrics.get("win_rate", 0.0)),
        "avg_trade": float(baseline_metrics.get("avg_trade", 0.0)),
        "trade_count": float(baseline_metrics.get("trade_count", 0.0)),
        "rebalance_count": float(baseline_metrics.get("rebalance_count", 0.0)),
        "rebalance_attempt_count": float(baseline_metrics.get("rebalance_attempt_count", 0.0)),
        "rebalance_exec_count": float(baseline_metrics.get("rebalance_exec_count", 0.0)),
        "avg_turnover_ratio": float(baseline_metrics.get("avg_turnover_ratio", 0.0)),
        "avg_turnover_ratio_attempts": float(baseline_metrics.get("avg_turnover_ratio_attempts", 0.0)),
        "oos_positive_ratio": float(wf_summary.get("oos_positive_ratio", 0.0)),
        "wfo_param_stability_score": float(wf_summary.get("param_stability_score", 0.0)),
        "cost_positive_ratio": float(np.mean((cost_df["net_pnl"] > 0).astype(float))) if not cost_df.empty else 0.0,
        "cost_median_net_pnl": float(cost_df["net_pnl"].median()) if not cost_df.empty else 0.0,
        "cost_min_net_pnl": float(cost_df["net_pnl"].min()) if not cost_df.empty else 0.0,
        "regime_best_profit_factor": float(regime_table_df["profit_factor"].max()) if not regime_table_df.empty else 0.0,
        "regime_best_max_drawdown": float(regime_table_df["max_drawdown"].max()) if not regime_table_df.empty else 0.0,
        "regime_pf_mdd_flag": regime_pf_mdd_flag,
        "btc_long_max_drawdown": btc_mdd,
        "mdd_better_or_equal_btc": bool(portfolio_mdd >= btc_mdd),
        "skip_reasons_json": json.dumps(baseline_sim.diagnostics.get("skip_reasons", {}), sort_keys=True),
        "off_to_on_count": float(baseline_metrics.get("off_to_on_count", 0.0)),
        "on_to_off_count": float(baseline_metrics.get("on_to_off_count", 0.0)),
        "gross_change_turnover": float(baseline_metrics.get("gross_change_turnover", 0.0)),
        "fee_cost_total": float(baseline_metrics.get("cost_fee_total", 0.0)),
        "fee_cost_per_gross_time": float(baseline_metrics.get("fee_cost_per_gross_time", 0.0)),
        "rebalance_skipped_due_to_shock_count": float(baseline_metrics.get("rebalance_skipped_due_to_shock_count", 0.0)),
        "rebalance_skipped_due_to_shock_ratio": float(baseline_metrics.get("rebalance_skipped_due_to_shock_ratio", 0.0)),
        "shock_active_bars_count": float(baseline_metrics.get("shock_active_bars_count", 0.0)),
        "cap_hit_count": float(baseline_metrics.get("cap_hit_count", 0.0)),
        "avg_executed_fraction": float(baseline_metrics.get("avg_executed_fraction", 0.0)),
        "avg_backlog_notional": float(baseline_metrics.get("avg_backlog_notional", 0.0)),
        "max_backlog_notional": float(baseline_metrics.get("max_backlog_notional", 0.0)),
        "avg_backlog_ratio": float(baseline_metrics.get("avg_backlog_ratio", 0.0)),
        "max_backlog_ratio": float(baseline_metrics.get("max_backlog_ratio", 0.0)),
        "avg_cap_used": float(baseline_metrics.get("avg_cap_used", 0.0)),
        "reduce_first_execution_ratio": float(baseline_metrics.get("reduce_first_execution_ratio", 0.0)),
        "equity_zero_or_negative_count": float(baseline_metrics.get("equity_zero_or_negative_count", 0.0)),
        "drift_force_count": float(baseline_metrics.get("drift_force_count", 0.0)),
        "kill_switch_events": float(baseline_metrics.get("kill_switch_events", 0.0)),
        "kill_switch_total_bars": float(baseline_metrics.get("kill_switch_total_bars", 0.0)),
        "stage_3_share": float(baseline_metrics.get("stage_3_share", 0.0)),
        "stage_3_longest_streak_bars": float(baseline_metrics.get("stage_3_longest_streak_bars", 0.0)),
        "stage_transitions_up": float(baseline_metrics.get("stage_transitions_up", 0.0)),
        "stage_transitions_down": float(baseline_metrics.get("stage_transitions_down", 0.0)),
        "liquidation_count": float(baseline_metrics.get("liquidation_count", 0.0)),
        "first_liquidation_ts": (
            None
            if baseline_sim.liquidation_events.empty
            else str(baseline_sim.liquidation_events.iloc[0].get("timestamp"))
        ),
        "transition_cause_count": float((baseline_sim.diagnostics.get("negative_equity_cause_counts", {}) or {}).get("negative_equity_due_to_gross_transition", 0)),
        "liquidation_after_halt_count": float(baseline_sim.diagnostics.get("liquidation_after_halt_count", 0)),
    }
    summary["gate_wfo"] = bool(float(summary["oos_positive_ratio"]) >= 0.60)
    summary["gate_cost"] = bool(float(summary["cost_positive_ratio"]) >= 0.30)
    summary["gate_mdd_vs_btc"] = bool(summary["mdd_better_or_equal_btc"])
    summary["gate_rebalance_count"] = bool(float(summary["rebalance_count"]) >= 200.0)
    summary["gate_regime_consistency"] = bool(summary["regime_pf_mdd_flag"])
    summary["verdict"] = _portfolio_verdict(summary)

    config_dump = {
        "run_id": run_id,
        "suite": "portfolio",
        "symbols": market.symbols,
        "timeframe": timeframe,
        "start": str(_to_utc_timestamp(start)),
        "end": str(_to_utc_timestamp(end)),
        "seed": seed,
        "data_source": data_source,
        "generated_at": pd.Timestamp.now(tz=timezone.utc).isoformat(),
        "selected_params": _portfolio_params_to_dict(selected_params),
        "grid": [_portfolio_params_to_dict(p) for p in param_grid],
        "safety": {
            "debug_mode": debug_mode,
            "max_cost_ratio_per_bar": max_cost_ratio_per_bar,
            "dd_controller_enabled": dd_controller_enabled,
            "dd_thresholds": list(dd_thresholds),
            "dd_gross_mults": list(dd_gross_mults),
            "dd_recover_thresholds": list(dd_recover_thresholds),
            "kill_cooldown_bars": kill_cooldown_bars,
            "disable_new_entry_when_dd": disable_new_entry_when_dd,
            "rolling_peak_window_bars": rolling_peak_window_bars,
            "stage_down_confirm_bars": stage_down_confirm_bars,
            "stage3_down_confirm_bars": stage3_down_confirm_bars,
            "reentry_ramp_steps": reentry_ramp_steps,
            "disable_new_entry_stage": disable_new_entry_stage,
            "dd_turnover_threshold_mult": dd_turnover_threshold_mult,
            "dd_rebalance_mult": dd_rebalance_mult,
            "cap_mode": cap_mode,
            "base_cap": base_cap,
            "cap_min": cap_min,
            "cap_max": cap_max,
            "backlog_thresholds": list(backlog_thresholds),
            "cap_steps": list(cap_steps),
            "high_vol_cap_max": high_vol_cap_max,
            "max_turnover_notional_to_equity": max_turnover_notional_to_equity,
            "drift_threshold": drift_threshold,
            "gross_decay_steps": gross_decay_steps,
            "max_notional_to_equity_mult": max_notional_to_equity_mult,
            "enable_liquidation": enable_liquidation,
            "equity_floor_ratio": equity_floor_ratio,
            "trading_halt_bars": trading_halt_bars,
            "skip_trades_if_cost_exceeds_equity_ratio": skip_trades_if_cost_exceeds_equity_ratio,
            "transition_smoother_enabled": transition_smoother_enabled,
            "gross_step_up": gross_step_up,
            "gross_step_down": gross_step_down,
            "post_halt_cooldown_bars": post_halt_cooldown_bars,
            "post_halt_max_gross": post_halt_max_gross,
            "liquidation_lookback_bars": liquidation_lookback_bars,
            "liquidation_lookback_max_gross": liquidation_lookback_max_gross,
            "enable_symbol_shock_filters": enable_symbol_shock_filters,
            "max_abs_weight_per_symbol": max_abs_weight_per_symbol,
            "atr_shock_threshold": atr_shock_threshold,
            "gap_shock_threshold": gap_shock_threshold,
            "shock_cooldown_bars": shock_cooldown_bars,
            "shock_mode": shock_mode,
            "shock_weight_mult_atr": shock_weight_mult_atr,
            "shock_weight_mult_gap": shock_weight_mult_gap,
            "shock_freeze_rebalance": shock_freeze_rebalance,
            "shock_freeze_min_fraction": shock_freeze_min_fraction,
            "lookback_score_mode": lookback_score_mode,
            "stop_on_anomaly": stop_on_anomaly,
            "regime_turnover_threshold_map": regime_turnover_threshold_map,
        },
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
        },
        "regime": {
            "trend_ema_span": trend_ema_span,
            "trend_slope_lookback": trend_slope_lookback,
            "trend_slope_threshold": trend_slope_threshold,
            "atr_period": regime_atr_period,
            "vol_lookback": regime_vol_lookback,
            "vol_percentile": regime_vol_percentile,
            "high_vol_gross_mult": high_vol_gross_mult,
            "high_vol_percentile_candidates": pct_list,
            "gross_map_candidates": gross_maps,
            "selected_regime_size_map": selected_size_map,
        },
    }

    save_json(config_dump, run_dir / "config.json")
    save_json(summary, run_dir / "summary.json")
    save_json(baseline_sim.diagnostics, run_dir / "diagnostics.json")
    save_json({"events": baseline_sim.debug_dump}, run_dir / "debug_dump.json")
    save_dataframe_csv(pd.DataFrame({"metric": list(summary.keys()), "value": list(summary.values())}), run_dir / "summary.csv")
    save_dataframe_csv(baseline_sim.equity_curve, run_dir / "portfolio_equity_curve.csv")
    save_dataframe_csv(baseline_sim.dd_timeline, run_dir / "dd_timeline.csv")
    save_dataframe_csv(baseline_sim.gross_target_applied, run_dir / "gross_target_vs_applied.csv")
    save_dataframe_csv(baseline_sim.excluded_symbols, run_dir / "excluded_symbols.csv")
    save_dataframe_csv(baseline_sim.symbol_risk_caps, run_dir / "symbol_risk_caps.csv")
    save_dataframe_csv(baseline_sim.positions, run_dir / "portfolio_positions.csv")
    save_dataframe_csv(baseline_sim.turnover, run_dir / "turnover.csv")
    save_dataframe_csv(baseline_sim.cost_breakdown, run_dir / "cost_breakdown.csv")
    save_dataframe_csv(baseline_sim.liquidation_events, run_dir / "liquidation_events.csv")
    save_dataframe_csv(cost_df, run_dir / "cost_stress.csv")
    save_dataframe_csv(cost_sens_df, run_dir / "cost_sensitivity.csv")
    save_dataframe_csv(rate_limit_compare_df, run_dir / "rate_limit_comparison.csv")
    save_dataframe_csv(transition_compare_df, run_dir / "transition_smoother_comparison.csv")
    save_dataframe_csv(peak_mode_compare_df, run_dir / "peak_mode_comparison.csv")
    save_dataframe_csv(wf_df, run_dir / "walk_forward_windows.csv")
    save_dataframe_csv(wf_candidates_df, run_dir / "walk_forward_candidates.csv")
    save_dataframe_csv(regime_df, run_dir / "regime_scenarios.csv")
    save_dataframe_csv(regime_table_df, run_dir / "regime_table.csv")
    save_dataframe_csv(regime_exposure_df, run_dir / "regime_exposure_table.csv")
    save_dataframe_csv(regime_timeline_df, run_dir / "regime_timeline.csv")
    save_dataframe_csv(bench_df, run_dir / "benchmark_btc_buyhold.csv")
    save_dataframe_csv(equity_compare_df, run_dir / "equity_vs_btc.csv")

    plots_dir = run_dir / "plots"
    save_line_chart(plots_dir / "portfolio_equity_curve.png", baseline_sim.equity_curve["equity"].tolist() if not baseline_sim.equity_curve.empty else [])
    save_line_chart(
        plots_dir / "drawdown_line.png",
        baseline_sim.dd_timeline["drawdown"].tolist() if not baseline_sim.dd_timeline.empty and "drawdown" in baseline_sim.dd_timeline.columns else [],
    )
    save_line_chart(
        plots_dir / "effective_gross_line.png",
        baseline_sim.dd_timeline["effective_gross"].tolist() if not baseline_sim.dd_timeline.empty and "effective_gross" in baseline_sim.dd_timeline.columns else [],
    )
    save_dual_line_chart(
        plots_dir / "gross_target_vs_applied.png",
        baseline_sim.gross_target_applied["target_gross"].tolist() if not baseline_sim.gross_target_applied.empty and "target_gross" in baseline_sim.gross_target_applied.columns else [],
        baseline_sim.gross_target_applied["applied_gross"].tolist() if not baseline_sim.gross_target_applied.empty and "applied_gross" in baseline_sim.gross_target_applied.columns else [],
    )
    save_dual_line_chart(
        plots_dir / "equity_vs_btc.png",
        equity_compare_df["equity"].tolist() if not equity_compare_df.empty else [],
        equity_compare_df["btc_equity"].tolist() if not equity_compare_df.empty else [],
    )
    save_line_chart(plots_dir / "cost_net_pnl_line.png", cost_df["net_pnl"].tolist() if not cost_df.empty else [])
    save_histogram(plots_dir / "walk_forward_oos_hist.png", wf_df["best_test_net_pnl"].tolist() if not wf_df.empty else [])
    save_bar_chart(plots_dir / "regime_net_pnl_bar.png", regime_table_df["net_pnl"].tolist() if not regime_table_df.empty else [])
    save_bar_chart(plots_dir / "regime_timeline.png", regime_timeline_df["regime_code"].tolist() if not regime_timeline_df.empty else [])
    save_histogram(
        plots_dir / "turnover_hist.png",
        baseline_sim.turnover["turnover_ratio"].tolist() if not baseline_sim.turnover.empty else [],
    )
    save_line_chart(
        plots_dir / "backlog_ratio_line.png",
        baseline_sim.turnover["backlog_ratio"].tolist() if not baseline_sim.turnover.empty and "backlog_ratio" in baseline_sim.turnover.columns else [],
    )
    _portfolio_report(
        run_dir=run_dir,
        run_id=run_id,
        config_dump=config_dump,
        summary=summary,
        diagnostics=baseline_sim.diagnostics,
        cost_df=cost_df,
        wf_df=wf_df,
        regime_table_df=regime_table_df,
        regime_exposure_df=regime_exposure_df,
        rate_limit_compare_df=rate_limit_compare_df,
        transition_compare_df=transition_compare_df,
        peak_mode_compare_df=peak_mode_compare_df,
        liquidation_df=baseline_sim.liquidation_events,
    )

    files = {
        "config_json": str(run_dir / "config.json"),
        "summary_csv": str(run_dir / "summary.csv"),
        "summary_json": str(run_dir / "summary.json"),
        "diagnostics_json": str(run_dir / "diagnostics.json"),
        "debug_dump_json": str(run_dir / "debug_dump.json"),
        "report_md": str(run_dir / "report.md"),
        "portfolio_equity_curve_csv": str(run_dir / "portfolio_equity_curve.csv"),
        "dd_timeline_csv": str(run_dir / "dd_timeline.csv"),
        "gross_target_vs_applied_csv": str(run_dir / "gross_target_vs_applied.csv"),
        "excluded_symbols_csv": str(run_dir / "excluded_symbols.csv"),
        "symbol_risk_caps_csv": str(run_dir / "symbol_risk_caps.csv"),
        "portfolio_positions_csv": str(run_dir / "portfolio_positions.csv"),
        "turnover_csv": str(run_dir / "turnover.csv"),
        "cost_breakdown_csv": str(run_dir / "cost_breakdown.csv"),
        "liquidation_events_csv": str(run_dir / "liquidation_events.csv"),
        "cost_stress_csv": str(run_dir / "cost_stress.csv"),
        "rate_limit_comparison_csv": str(run_dir / "rate_limit_comparison.csv"),
        "transition_smoother_comparison_csv": str(run_dir / "transition_smoother_comparison.csv"),
        "peak_mode_comparison_csv": str(run_dir / "peak_mode_comparison.csv"),
        "walk_forward_csv": str(run_dir / "walk_forward_windows.csv"),
        "regime_table_csv": str(run_dir / "regime_table.csv"),
        "regime_exposure_csv": str(run_dir / "regime_exposure_table.csv"),
        "regime_timeline_csv": str(run_dir / "regime_timeline.csv"),
        "equity_vs_btc_csv": str(run_dir / "equity_vs_btc.csv"),
        "btc_benchmark_csv": str(run_dir / "benchmark_btc_buyhold.csv"),
    }
    return PortfolioRunOutput(run_id=run_id, run_dir=run_dir, summary=summary, files=files)


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
            notes="Track A: direction dependency mitigation with BTC beta hedge proxy checks.",
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
            notes="Track B: split trend/range operation with reduced sizing in high-vol regimes.",
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
            notes="Track C: fixed risk template plus limit/market execution robustness checks.",
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
            "verdict": "FAIL",
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
        verdict = "PASS"
    elif (gate_wfo and gate_regime) or (gate_cost and gate_trade):
        verdict = "MIXED"
    else:
        verdict = "FAIL"

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
    "PortfolioRunOutput",
    "PortfolioParams",
    "PortfolioCostConfig",
    "PortfolioMarketData",
    "SystemCandidate",
    "SystemBatchOutput",
    "run_system_batch",
    "default_system_candidates",
    "run_edge_validation",
    "run_portfolio_validation",
    "load_candles",
    "load_multi_candles",
    "run_cost_stress",
    "run_portfolio_cost_stress",
    "run_walk_forward",
    "run_portfolio_walk_forward",
    "run_regime_gating",
    "run_portfolio_regime_gating",
    "_parse_duration_list",
    "_parse_float_list",
    "_parse_int_list",
]


