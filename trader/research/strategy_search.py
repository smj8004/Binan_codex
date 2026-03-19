from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trader.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from trader.strategy.base import Bar, Strategy, StrategyPosition
from trader.strategy.ema_cross import EMACrossStrategy
from trader.strategy.trend_family import TrendDonchianBreakout, TrendSuperTrendStrategy


ESTIMATED_SECONDS_PER_BACKTEST = 0.35
SUPPORTED_STRATEGIES = (
    "ema_cross",
    "donchian_breakout",
    "donchian_breakout_adx",
    "rsi_mean_reversion",
)
SUPPORTED_FAMILIES = (
    "ema_cross",
    "donchian_breakout",
    "supertrend",
    "price_adx_breakout",
    "rsi_mean_reversion",
    "bollinger",
    "macd",
    "stoch_rsi",
)
TREND_FAMILIES = frozenset({"ema_cross", "donchian_breakout", "supertrend", "price_adx_breakout", "macd"})
MEAN_REVERSION_FAMILIES = frozenset({"rsi_mean_reversion", "bollinger", "stoch_rsi"})


def _timeframe_seconds(timeframe: str) -> int:
    raw = timeframe.lower().strip()
    if raw.endswith("m"):
        return int(raw[:-1]) * 60
    if raw.endswith("h"):
        return int(raw[:-1]) * 3600
    if raw.endswith("d"):
        return int(raw[:-1]) * 86400
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _ensure_candles(df: pd.DataFrame) -> pd.DataFrame:
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing candle columns: {missing}")
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        out[col] = out[col].astype(float)
    out = out.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    return out


def _json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _compound_total_return(values: list[float]) -> float:
    if not values:
        return 0.0
    total = 1.0
    for value in values:
        total *= 1.0 + float(value)
    return total - 1.0


def _rolling_volatility(closes: list[float], window: int) -> float | None:
    if len(closes) < window + 1:
        return None
    returns = pd.Series(closes, dtype="float64").pct_change().dropna()
    if len(returns) < window:
        return None
    value = float(returns.iloc[-window:].std(ddof=0))
    return value if math.isfinite(value) else None


def _calc_rsi_from_closes(closes: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    series = pd.Series(closes, dtype="float64")
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(span=period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(span=period, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_adx(candles: pd.DataFrame, window: int = 14) -> pd.Series:
    if window < 2:
        raise ValueError("window must be >= 2")
    required = {"high", "low", "close"}
    missing = required.difference(candles.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns for ADX: {sorted(missing)}")

    highs = candles["high"].astype(float)
    lows = candles["low"].astype(float)
    closes = candles["close"].astype(float)
    prev_close = closes.shift(1)

    true_range = pd.concat(
        [
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    up_move = highs.diff()
    down_move = -lows.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0.0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0.0), 0.0)

    atr = true_range.rolling(window=window, min_periods=window).mean()
    atr_safe = atr.replace(0.0, np.nan)
    plus_di = 100.0 * plus_dm.rolling(window=window, min_periods=window).mean() / atr_safe
    minus_di = 100.0 * minus_dm.rolling(window=window, min_periods=window).mean() / atr_safe
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return dx.rolling(window=window, min_periods=window).mean().fillna(0.0).clip(lower=0.0, upper=100.0)


class _ADXMixin:
    def _adx_init(self, *, adx_window: int, adx_threshold: float) -> None:
        if adx_window < 2:
            raise ValueError("adx_window must be >= 2")
        self.adx_window = int(adx_window)
        self.adx_threshold = float(adx_threshold)
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._prev_close: float | None = None
        self._tr_values: list[float] = []
        self._plus_dm_values: list[float] = []
        self._minus_dm_values: list[float] = []
        self._dx_values: list[float] = []
        self._latest_adx: float = 0.0

    def _update_adx(self, bar: Bar) -> float:
        if self._prev_close is None or self._prev_high is None or self._prev_low is None:
            self._prev_high = float(bar.high)
            self._prev_low = float(bar.low)
            self._prev_close = float(bar.close)
            self._latest_adx = 0.0
            return self._latest_adx

        high = float(bar.high)
        low = float(bar.low)
        close = float(bar.close)
        true_range = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        up_move = high - self._prev_high
        down_move = self._prev_low - low
        plus_dm = up_move if up_move > down_move and up_move > 0.0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0.0 else 0.0

        self._tr_values.append(true_range)
        self._plus_dm_values.append(plus_dm)
        self._minus_dm_values.append(minus_dm)

        if len(self._tr_values) >= self.adx_window:
            atr = float(np.mean(self._tr_values[-self.adx_window :]))
            if atr > 0.0:
                plus_di = 100.0 * float(np.mean(self._plus_dm_values[-self.adx_window :])) / atr
                minus_di = 100.0 * float(np.mean(self._minus_dm_values[-self.adx_window :])) / atr
                denom = plus_di + minus_di
                dx = 100.0 * abs(plus_di - minus_di) / denom if denom > 0.0 else 0.0
                self._dx_values.append(dx)

        if len(self._dx_values) >= self.adx_window:
            self._latest_adx = float(np.mean(self._dx_values[-self.adx_window :]))
        else:
            self._latest_adx = 0.0

        self._prev_high = high
        self._prev_low = low
        self._prev_close = close
        return self._latest_adx


class DonchianBreakoutADXStrategy(_ADXMixin, TrendDonchianBreakout):
    def __init__(
        self,
        *,
        entry_period: int = 20,
        exit_period: int = 10,
        adx_window: int = 14,
        adx_threshold: float = 20.0,
        allow_short: bool = True,
        stop_loss_pct: float = 0.0,
        take_profit_pct: float = 0.0,
    ) -> None:
        super().__init__(
            entry_period=entry_period,
            exit_period=exit_period,
            allow_short=allow_short,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )
        self._adx_init(adx_window=adx_window, adx_threshold=adx_threshold)

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> str:
        adx = self._update_adx(bar)
        signal = super().on_bar(bar, position)
        if signal in {"long", "short"} and adx < self.adx_threshold:
            return "hold"
        return signal


class RSIMeanReversionStrategy(Strategy):
    def __init__(
        self,
        *,
        rsi_period: int = 14,
        lower: float = 30.0,
        upper: float = 70.0,
        exit_threshold: float = 50.0,
        allow_short: bool = True,
    ) -> None:
        if rsi_period < 2:
            raise ValueError("rsi_period must be >= 2")
        if not (0 < lower < exit_threshold < upper < 100):
            raise ValueError("Require 0 < lower < exit_threshold < upper < 100")
        self.rsi_period = int(rsi_period)
        self.lower = float(lower)
        self.upper = float(upper)
        self.exit_threshold = float(exit_threshold)
        self.allow_short = bool(allow_short)
        self._closes: list[float] = []
        self._prev_rsi: float | None = None

    def _calc_rsi(self) -> float | None:
        return _calc_rsi_from_closes(self._closes, self.rsi_period)

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> str:
        self._closes.append(bar.close)
        rsi = self._calc_rsi()
        if rsi is None:
            return "hold"

        signal = "hold"
        if position is not None and position.side == "long" and rsi >= self.exit_threshold:
            signal = "exit"
        elif position is not None and position.side == "short" and rsi <= self.exit_threshold:
            signal = "exit"
        elif self._prev_rsi is not None:
            if self._prev_rsi > self.lower and rsi <= self.lower:
                signal = "long"
            elif self.allow_short and self._prev_rsi < self.upper and rsi >= self.upper:
                signal = "short"

        self._prev_rsi = rsi
        return signal


class EmaCrossTrendFilterStrategy(Strategy):
    def __init__(
        self,
        *,
        fast_len: int = 12,
        slow_len: int = 26,
        trend_filter: bool = False,
        allow_short: bool = False,
    ) -> None:
        if slow_len <= fast_len:
            raise ValueError("slow_len must be greater than fast_len")
        self.fast_len = int(fast_len)
        self.slow_len = int(slow_len)
        self.trend_filter = bool(trend_filter)
        self.allow_short = bool(allow_short)
        self.trend_len = max(self.slow_len * 3, 80)
        self._closes: list[float] = []

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> str:
        self._closes.append(bar.close)
        min_bars = max(self.slow_len + 1, self.trend_len)
        if len(self._closes) < min_bars:
            return "hold"

        series = pd.Series(self._closes, dtype="float64")
        ema_fast = series.ewm(span=self.fast_len, adjust=False).mean()
        ema_slow = series.ewm(span=self.slow_len, adjust=False).mean()
        prev_fast = float(ema_fast.iloc[-2])
        prev_slow = float(ema_slow.iloc[-2])
        curr_fast = float(ema_fast.iloc[-1])
        curr_slow = float(ema_slow.iloc[-1])
        trend_ema = float(series.ewm(span=self.trend_len, adjust=False).mean().iloc[-1])

        cross_up = prev_fast <= prev_slow and curr_fast > curr_slow
        cross_down = prev_fast >= prev_slow and curr_fast < curr_slow
        uptrend_ok = (not self.trend_filter) or (bar.close > trend_ema)
        downtrend_ok = (not self.trend_filter) or (bar.close < trend_ema)

        if cross_up and uptrend_ok:
            return "long"
        if cross_down:
            if self.allow_short and downtrend_ok:
                return "short"
            if position is not None and position.side == "long":
                return "exit"
        if cross_up and position is not None and position.side == "short":
            return "exit"
        return "hold"


class PriceADXBreakoutStrategy(_ADXMixin, Strategy):
    def __init__(
        self,
        *,
        breakout_lookback: int = 20,
        exit_lookback: int = 10,
        adx_window: int = 14,
        adx_threshold: float = 20.0,
        allow_short: bool = True,
    ) -> None:
        if exit_lookback >= breakout_lookback:
            raise ValueError("exit_lookback must be less than breakout_lookback")
        self.breakout_lookback = int(breakout_lookback)
        self.exit_lookback = int(exit_lookback)
        self.allow_short = bool(allow_short)
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._adx_init(adx_window=adx_window, adx_threshold=adx_threshold)

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> str:
        adx = self._update_adx(bar)
        self._closes.append(bar.close)
        self._highs.append(bar.high)
        self._lows.append(bar.low)

        min_bars = max(self.breakout_lookback + 1, self.exit_lookback + 1, self.adx_window * 2)
        if len(self._closes) < min_bars:
            return "hold"

        entry_high = max(self._closes[-(self.breakout_lookback + 1) : -1])
        entry_low = min(self._closes[-(self.breakout_lookback + 1) : -1])
        exit_high = max(self._highs[-(self.exit_lookback + 1) : -1])
        exit_low = min(self._lows[-(self.exit_lookback + 1) : -1])

        if position is not None and position.side == "long" and bar.close < exit_low:
            return "exit"
        if position is not None and position.side == "short" and bar.close > exit_high:
            return "exit"

        if adx < self.adx_threshold:
            return "hold"
        if bar.close > entry_high and (position is None or position.side != "long"):
            return "long"
        if bar.close < entry_low and (position is None or position.side != "short"):
            return "short" if self.allow_short else "hold"
        return "hold"


class RSIMeanReversionVolFilterStrategy(RSIMeanReversionStrategy):
    def __init__(
        self,
        *,
        rsi_period: int = 14,
        lower: float = 30.0,
        upper: float = 70.0,
        exit_threshold: float = 50.0,
        allow_short: bool = True,
        volatility_filter: bool = False,
        vol_window: int = 20,
        vol_baseline_window: int = 60,
        vol_ratio_limit: float = 1.25,
    ) -> None:
        super().__init__(
            rsi_period=rsi_period,
            lower=lower,
            upper=upper,
            exit_threshold=exit_threshold,
            allow_short=allow_short,
        )
        self.volatility_filter = bool(volatility_filter)
        self.vol_window = int(vol_window)
        self.vol_baseline_window = int(vol_baseline_window)
        self.vol_ratio_limit = float(vol_ratio_limit)

    def _vol_ok(self) -> bool:
        if not self.volatility_filter:
            return True
        recent = _rolling_volatility(self._closes, self.vol_window)
        baseline = _rolling_volatility(self._closes, self.vol_baseline_window)
        if recent is None or baseline is None or baseline <= 0.0:
            return False
        return recent <= baseline * self.vol_ratio_limit

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> str:
        signal = super().on_bar(bar, position)
        if signal in {"long", "short"} and not self._vol_ok():
            return "hold"
        return signal


class BollingerMeanReversionStrategy(Strategy):
    def __init__(
        self,
        *,
        lookback: int = 20,
        std_mult: float = 2.0,
        exit_mode: str = "midband",
        allow_short: bool = True,
        volatility_filter: bool = False,
        vol_window: int = 20,
        vol_baseline_window: int = 60,
        vol_ratio_limit: float = 1.25,
    ) -> None:
        if lookback < 2:
            raise ValueError("lookback must be >= 2")
        if std_mult <= 0:
            raise ValueError("std_mult must be positive")
        if exit_mode not in {"midband", "threshold"}:
            raise ValueError("exit_mode must be midband or threshold")
        self.lookback = int(lookback)
        self.std_mult = float(std_mult)
        self.exit_mode = exit_mode
        self.allow_short = bool(allow_short)
        self.volatility_filter = bool(volatility_filter)
        self.vol_window = int(vol_window)
        self.vol_baseline_window = int(vol_baseline_window)
        self.vol_ratio_limit = float(vol_ratio_limit)
        self._closes: list[float] = []
        self._prev_close: float | None = None
        self._prev_upper: float | None = None
        self._prev_lower: float | None = None

    def _vol_ok(self) -> bool:
        if not self.volatility_filter:
            return True
        recent = _rolling_volatility(self._closes, self.vol_window)
        baseline = _rolling_volatility(self._closes, self.vol_baseline_window)
        if recent is None or baseline is None or baseline <= 0.0:
            return False
        return recent <= baseline * self.vol_ratio_limit

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> str:
        self._closes.append(bar.close)
        if len(self._closes) < self.lookback:
            return "hold"

        series = pd.Series(self._closes[-self.lookback :], dtype="float64")
        mid = float(series.mean())
        std = float(series.std(ddof=0))
        if std <= 0.0:
            return "hold"
        upper = mid + self.std_mult * std
        lower = mid - self.std_mult * std
        zscore = (bar.close - mid) / std

        if position is not None and position.side == "long":
            if self.exit_mode == "midband" and bar.close >= mid:
                return "exit"
            if self.exit_mode == "threshold" and zscore >= -0.25:
                return "exit"
        if position is not None and position.side == "short":
            if self.exit_mode == "midband" and bar.close <= mid:
                return "exit"
            if self.exit_mode == "threshold" and zscore <= 0.25:
                return "exit"

        signal = "hold"
        if self._prev_close is not None and self._prev_lower is not None and self._prev_upper is not None:
            crossed_below = self._prev_close >= self._prev_lower and bar.close < lower
            crossed_above = self._prev_close <= self._prev_upper and bar.close > upper
            if crossed_below and self._vol_ok():
                signal = "long"
            elif crossed_above and self._vol_ok():
                signal = "short" if self.allow_short else "hold"

        self._prev_close = bar.close
        self._prev_upper = upper
        self._prev_lower = lower
        return signal


class MACDMomentumFilterStrategy(_ADXMixin, Strategy):
    def __init__(
        self,
        *,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        use_histogram: bool = False,
        histogram_threshold: float = 0.0,
        adx_filter: bool = False,
        adx_window: int = 14,
        adx_threshold: float = 20.0,
        allow_short: bool = True,
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period must be less than slow_period")
        self.fast_period = int(fast_period)
        self.slow_period = int(slow_period)
        self.signal_period = int(signal_period)
        self.use_histogram = bool(use_histogram)
        self.histogram_threshold = float(histogram_threshold)
        self.adx_filter = bool(adx_filter)
        self.allow_short = bool(allow_short)
        self._closes: list[float] = []
        self._adx_init(adx_window=adx_window, adx_threshold=adx_threshold)

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> str:
        adx = self._update_adx(bar)
        self._closes.append(bar.close)
        min_bars = self.slow_period + self.signal_period + 2
        if len(self._closes) < min_bars:
            return "hold"

        series = pd.Series(self._closes, dtype="float64")
        fast = series.ewm(span=self.fast_period, adjust=False).mean()
        slow = series.ewm(span=self.slow_period, adjust=False).mean()
        macd_line = fast - slow
        signal_line = macd_line.ewm(span=self.signal_period, adjust=False).mean()
        histogram = macd_line - signal_line

        prev_macd = float(macd_line.iloc[-2])
        prev_signal = float(signal_line.iloc[-2])
        curr_macd = float(macd_line.iloc[-1])
        curr_signal = float(signal_line.iloc[-1])
        curr_hist = float(histogram.iloc[-1])

        cross_up = prev_macd <= prev_signal and curr_macd > curr_signal
        cross_down = prev_macd >= prev_signal and curr_macd < curr_signal
        regime_ok = (not self.adx_filter) or (adx >= self.adx_threshold)

        if cross_up and regime_ok:
            if self.use_histogram and curr_hist <= self.histogram_threshold:
                return "hold"
            return "long"
        if cross_down:
            if self.allow_short and regime_ok:
                if self.use_histogram and curr_hist >= -self.histogram_threshold:
                    return "hold"
                return "short"
            if position is not None and position.side == "long":
                return "exit"
        if cross_up and position is not None and position.side == "short":
            return "exit"
        return "hold"


class StochRSIHybridStrategy(Strategy):
    def __init__(
        self,
        *,
        rsi_period: int = 14,
        stoch_period: int = 14,
        oversold: float = 20.0,
        overbought: float = 80.0,
        trend_filter: bool = False,
        trend_window: int = 50,
        allow_short: bool = True,
    ) -> None:
        self.rsi_period = int(rsi_period)
        self.stoch_period = int(stoch_period)
        self.oversold = float(oversold)
        self.overbought = float(overbought)
        self.trend_filter = bool(trend_filter)
        self.trend_window = int(trend_window)
        self.allow_short = bool(allow_short)
        self._closes: list[float] = []
        self._rsi_values: list[float] = []
        self._prev_stoch: float | None = None

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> str:
        self._closes.append(bar.close)
        rsi = _calc_rsi_from_closes(self._closes, self.rsi_period)
        if rsi is None:
            return "hold"
        self._rsi_values.append(rsi)
        if len(self._rsi_values) < self.stoch_period:
            return "hold"

        rsi_window = pd.Series(self._rsi_values[-self.stoch_period :], dtype="float64")
        rsi_min = float(rsi_window.min())
        rsi_max = float(rsi_window.max())
        if rsi_max - rsi_min <= 0.0:
            stoch_rsi = 50.0
        else:
            stoch_rsi = 100.0 * ((rsi - rsi_min) / (rsi_max - rsi_min))

        trend_ok_long = True
        trend_ok_short = True
        if self.trend_filter and len(self._closes) >= self.trend_window:
            trend_ema = float(pd.Series(self._closes, dtype="float64").ewm(span=self.trend_window, adjust=False).mean().iloc[-1])
            trend_ok_long = bar.close > trend_ema
            trend_ok_short = bar.close < trend_ema

        if position is not None and position.side == "long" and stoch_rsi >= 60.0:
            self._prev_stoch = stoch_rsi
            return "exit"
        if position is not None and position.side == "short" and stoch_rsi <= 40.0:
            self._prev_stoch = stoch_rsi
            return "exit"

        signal = "hold"
        if self._prev_stoch is not None:
            crossed_up = self._prev_stoch <= self.oversold and stoch_rsi > self.oversold
            crossed_down = self._prev_stoch >= self.overbought and stoch_rsi < self.overbought
            if crossed_up and trend_ok_long:
                signal = "long"
            elif crossed_down and trend_ok_short:
                signal = "short" if self.allow_short else "hold"

        self._prev_stoch = stoch_rsi
        return signal


class RegimeConditionedStrategy(Strategy):
    def __init__(
        self,
        *,
        base_strategy: Strategy,
        allow_long_mask: list[bool],
        allow_short_mask: list[bool],
    ) -> None:
        self.base_strategy = base_strategy
        self.allow_long_mask = allow_long_mask
        self.allow_short_mask = allow_short_mask
        self._index = 0

    def _mask_value(self, values: list[bool]) -> bool:
        if not values:
            return False
        idx = min(self._index, len(values) - 1)
        return bool(values[idx])

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> str:
        signal = self.base_strategy.on_bar(bar, position)
        allow_long = self._mask_value(self.allow_long_mask)
        allow_short = self._mask_value(self.allow_short_mask)
        self._index += 1

        if signal in {"hold", "exit"}:
            return signal
        if signal in {"long", "buy"}:
            if allow_long:
                return signal
            if position is not None and position.side == "short":
                return "exit"
            return "hold"
        if signal in {"short", "sell"}:
            if allow_short:
                return signal
            if position is not None and position.side == "long":
                return "exit"
            return "hold"
        return signal


def _valid_bool_series(values: pd.Series) -> pd.Series:
    return values.fillna(False).astype(bool)


def _precompute_regime_masks(
    *,
    candles: pd.DataFrame,
    interval: str,
    strategy_family: str,
    regime_name: str,
    regime_params: dict[str, Any] | None,
) -> tuple[list[bool], list[bool], float]:
    if regime_name == "off" or not regime_params:
        size = len(candles)
        return [True] * size, [True] * size, 1.0 if size else 0.0

    closes = candles["close"].astype(float)
    pct_returns = closes.pct_change()

    adx_window = int(regime_params["adx_window"])
    low_adx_threshold = float(regime_params["low_adx_threshold"])
    high_adx_threshold = float(regime_params["high_adx_threshold"])
    vol_window = int(regime_params["vol_window"])
    vol_percentile_window = int(regime_params["vol_percentile_window"])
    low_vol_quantile = float(regime_params["low_vol_quantile"])
    high_vol_quantile = float(regime_params["high_vol_quantile"])
    trend_ema_span = int(regime_params["trend_ema_span"])
    trend_slope_lookback = int(regime_params["trend_slope_lookback"])
    trend_slope_threshold = float(regime_params["trend_slope_threshold"])
    trend_distance_threshold = float(regime_params.get("trend_distance_threshold", 0.0))

    adx_series = calculate_adx(candles, window=adx_window)
    low_adx = _valid_bool_series(adx_series <= low_adx_threshold)
    high_adx = _valid_bool_series(adx_series >= high_adx_threshold)

    realized_vol = pct_returns.rolling(window=vol_window, min_periods=vol_window).std(ddof=0)
    low_vol_cut = realized_vol.rolling(window=vol_percentile_window, min_periods=vol_percentile_window).quantile(low_vol_quantile)
    high_vol_cut = realized_vol.rolling(window=vol_percentile_window, min_periods=vol_percentile_window).quantile(high_vol_quantile)
    low_vol = _valid_bool_series(realized_vol <= low_vol_cut)
    high_vol = _valid_bool_series(realized_vol >= high_vol_cut)

    trend_ema = closes.ewm(span=trend_ema_span, adjust=False).mean()
    slope = trend_ema.pct_change(periods=trend_slope_lookback)
    ema_distance = ((closes / trend_ema) - 1.0).abs().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    trend_distance_ok = _valid_bool_series(ema_distance >= trend_distance_threshold)
    uptrend = _valid_bool_series((closes > trend_ema) & (slope >= trend_slope_threshold) & trend_distance_ok)
    downtrend = _valid_bool_series((closes < trend_ema) & (slope <= -trend_slope_threshold) & trend_distance_ok)
    flat = _valid_bool_series(~uptrend & ~downtrend)

    if strategy_family in TREND_FAMILIES:
        base_mask = _valid_bool_series(high_adx & ~low_vol)
        allow_long = _valid_bool_series(base_mask & uptrend)
        allow_short = _valid_bool_series(base_mask & downtrend)
        coverage_mask = _valid_bool_series(base_mask & (uptrend | downtrend))
    elif strategy_family in MEAN_REVERSION_FAMILIES:
        base_mask = _valid_bool_series(low_adx & low_vol & flat)
        allow_long = base_mask.copy()
        allow_short = base_mask.copy()
        coverage_mask = base_mask.copy()
    else:
        size = len(candles)
        return [True] * size, [True] * size, 1.0 if size else 0.0

    coverage_ratio = float(coverage_mask.mean()) if len(coverage_mask) else 0.0
    return allow_long.tolist(), allow_short.tolist(), coverage_ratio


def _default_regime_spec(strategy_family: str, _interval: str) -> _RegimeSpec:
    params = {
        "adx_window": 14,
        "low_adx_threshold": 18.0,
        "high_adx_threshold": 30.0,
        "vol_window": 20,
        "vol_percentile_window": 160,
        "low_vol_quantile": 0.20,
        "high_vol_quantile": 0.80,
        "trend_ema_span": 100,
        "trend_slope_lookback": 16,
        "trend_slope_threshold": 0.0030,
        "trend_distance_threshold": 0.0050,
        "min_coverage_ratio": 0.20,
    }
    if strategy_family in TREND_FAMILIES:
        return _RegimeSpec(name="trend_tight_high_adx_extreme_vol_strict_trend", params=params)
    if strategy_family in MEAN_REVERSION_FAMILIES:
        return _RegimeSpec(name="meanrev_tight_low_adx_extreme_low_vol_flat", params=params)
    return _RegimeSpec(name="off", params={})


@dataclass(frozen=True)
class StrategySearchConfig:
    interval: str = "1h"
    data_root: Path = Path("data/futures_historical")
    out_root: Path = Path("out/strategy_search")
    initial_equity: float = 10_000.0
    leverage: float = 1.0
    fixed_notional_usdt: float = 1_000.0
    taker_fee_bps: float = 5.0
    slippage_bps: float = 2.0
    train_days: int = 180
    test_days: int = 60
    step_days: int = 60
    min_trade_count: int = 3
    strategies: tuple[str, ...] | None = None


@dataclass(frozen=True)
class StrategySearchResult:
    summary_path: Path
    by_symbol_path: Path
    markdown_path: Path
    summary_df: pd.DataFrame
    by_symbol_df: pd.DataFrame


@dataclass(frozen=True)
class BroadSweepConfig:
    intervals: tuple[str, ...] = ("1h", "4h")
    data_root: Path = Path("data/futures_historical")
    out_root: Path = Path("out/strategy_search_matrix")
    initial_equity: float = 10_000.0
    leverage: float = 1.0
    fixed_notional_usdt: float = 1_000.0
    taker_fee_bps: float = 5.0
    slippage_bps: float = 2.0
    train_days: int = 180
    test_days: int = 60
    step_days: int = 60
    min_trade_count: int = 3
    families: tuple[str, ...] | None = None
    max_combos: int | None = 96
    time_budget_hours: float = 6.0
    jobs: int = max(1, min(os.cpu_count() or 1, 8))
    regime_mode: str = "off"


@dataclass(frozen=True)
class BroadSweepResult:
    summary_path: Path
    by_symbol_path: Path
    window_results_path: Path
    markdown_path: Path
    family_summary_path: Path
    summary_df: pd.DataFrame
    by_symbol_df: pd.DataFrame
    family_summary_df: pd.DataFrame


@dataclass(frozen=True)
class _Window:
    index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    train_df: pd.DataFrame
    test_df: pd.DataFrame


@dataclass(frozen=True)
class _BroadCandidate:
    strategy_family: str
    strategy_name: str
    params: dict[str, Any]
    regime_name: str = "off"
    regime_params: dict[str, Any] | None = None


@dataclass(frozen=True)
class _RegimeSpec:
    name: str
    params: dict[str, Any]


@dataclass(frozen=True)
class _BroadTask:
    candidate: _BroadCandidate
    symbols: tuple[str, ...]
    config: BroadSweepConfig


class _Accumulator:
    def __init__(self, *, initial_equity: float, timeframe: str) -> None:
        self.initial_equity = float(initial_equity)
        self.timeframe = timeframe
        self.current_equity = float(initial_equity)
        self.curve: list[float] = []
        self.trade_returns: list[float] = []
        self.trade_net_pnls: list[float] = []
        self.trade_gross_pnls: list[float] = []
        self.trade_fees: list[float] = []
        self.start_ts: pd.Timestamp | None = None
        self.end_ts: pd.Timestamp | None = None

    def add(self, *, result: BacktestResult, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> None:
        if result.equity_curve:
            scale = self.current_equity / max(float(result.initial_equity), 1e-12)
            scaled_curve = [float(value) * scale for value in result.equity_curve]
            self.curve.extend(scaled_curve)
            self.current_equity = scaled_curve[-1]
            for trade in result.trades:
                self.trade_returns.append(float(trade.return_pct))
                self.trade_net_pnls.append(float(trade.net_pnl) * scale)
                self.trade_gross_pnls.append(float(trade.gross_pnl) * scale)
                self.trade_fees.append(float(trade.fee_paid) * scale)
        self.start_ts = start_ts if self.start_ts is None else min(self.start_ts, start_ts)
        self.end_ts = end_ts if self.end_ts is None else max(self.end_ts, end_ts)

    def metrics(self) -> dict[str, float]:
        if not self.curve:
            return {
                "total_return": 0.0,
                "cagr": 0.0,
                "max_drawdown": 0.0,
                "sharpe_like": 0.0,
                "trade_count": 0.0,
                "win_rate": 0.0,
                "fee_cost_total": 0.0,
                "avg_trade_return": 0.0,
                "gross_pnl_total": 0.0,
                "net_pnl_total": 0.0,
                "fee_to_gross_ratio": 0.0,
            }

        arr = np.asarray(self.curve, dtype=float)
        peaks = np.maximum.accumulate(arr)
        drawdowns = np.where(peaks > 0, (arr - peaks) / peaks, 0.0)
        max_drawdown = float(drawdowns.min())
        total_return = float(arr[-1] / self.initial_equity - 1.0)

        if self.start_ts is None or self.end_ts is None or self.end_ts <= self.start_ts or total_return <= -1.0:
            cagr = 0.0
        else:
            years = max((self.end_ts - self.start_ts).total_seconds() / (365.25 * 24 * 3600), 1e-9)
            cagr = float((1.0 + total_return) ** (1.0 / years) - 1.0)

        bar_returns = np.diff(arr) / arr[:-1] if len(arr) > 1 else np.asarray([], dtype=float)
        if bar_returns.size > 1 and float(bar_returns.std()) > 0:
            bars_per_year = (365.25 * 24 * 3600) / _timeframe_seconds(self.timeframe)
            sharpe_like = float((bar_returns.mean() / bar_returns.std()) * np.sqrt(bars_per_year))
        else:
            sharpe_like = 0.0

        wins = [value for value in self.trade_net_pnls if value > 0]
        fee_cost_total = float(sum(self.trade_fees))
        gross_pnl_total = float(sum(self.trade_gross_pnls))
        return {
            "total_return": total_return,
            "cagr": cagr,
            "max_drawdown": max_drawdown,
            "sharpe_like": sharpe_like,
            "trade_count": float(len(self.trade_net_pnls)),
            "win_rate": float(len(wins) / len(self.trade_net_pnls)) if self.trade_net_pnls else 0.0,
            "fee_cost_total": fee_cost_total,
            "avg_trade_return": float(np.mean(self.trade_returns)) if self.trade_returns else 0.0,
            "gross_pnl_total": gross_pnl_total,
            "net_pnl_total": float(sum(self.trade_net_pnls)),
            "fee_to_gross_ratio": float(fee_cost_total / gross_pnl_total) if gross_pnl_total > 0 else float("inf" if fee_cost_total > 0 else 0.0),
        }


def _resolve_strategy_names(strategies: tuple[str, ...] | None) -> list[str]:
    if strategies is None:
        return list(SUPPORTED_STRATEGIES)
    normalized: list[str] = []
    for raw_name in strategies:
        name = str(raw_name).strip()
        if not name:
            continue
        if name not in SUPPORTED_STRATEGIES:
            raise ValueError(f"Unsupported strategy: {name}. Expected one of {', '.join(SUPPORTED_STRATEGIES)}")
        if name not in normalized:
            normalized.append(name)
    if not normalized:
        raise ValueError("At least one valid strategy is required")
    return normalized


def _resolve_family_names(families: tuple[str, ...] | None) -> list[str]:
    if families is None:
        return list(SUPPORTED_FAMILIES)
    normalized: list[str] = []
    for raw_name in families:
        name = str(raw_name).strip()
        if not name:
            continue
        if name not in SUPPORTED_FAMILIES:
            raise ValueError(f"Unsupported family: {name}. Expected one of {', '.join(SUPPORTED_FAMILIES)}")
        if name not in normalized:
            normalized.append(name)
    if not normalized:
        raise ValueError("At least one valid family is required")
    return normalized


def _resolve_regime_mode(mode: str | None) -> str:
    normalized = str(mode or "off").strip().lower()
    if normalized not in {"off", "family-default"}:
        raise ValueError("Unsupported regime_mode. Expected one of: off, family-default")
    return normalized


def _strategy_grid(strategies: tuple[str, ...] | None = None) -> dict[str, list[dict[str, Any]]]:
    selected = _resolve_strategy_names(strategies)
    grids: dict[str, list[dict[str, Any]]] = {name: [] for name in selected}

    if "ema_cross" in grids:
        for fast in [8, 21]:
            for slow in [55, 89]:
                for allow_short in [False, True]:
                    if slow <= fast:
                        continue
                    grids["ema_cross"].append(
                        {
                            "fast_len": fast,
                            "slow_len": slow,
                            "allow_short": allow_short,
                            "mode": "long_short" if allow_short else "long_flat",
                        }
                    )

    donchian_params: list[dict[str, Any]] = []
    for entry_period in [20, 55]:
        for exit_period in [10, 20]:
            for allow_short in [False, True]:
                if exit_period >= entry_period:
                    continue
                donchian_params.append(
                    {
                        "entry_period": entry_period,
                        "exit_period": exit_period,
                        "allow_short": allow_short,
                        "mode": "long_short" if allow_short else "long_flat",
                    }
                )
    if "donchian_breakout" in grids:
        grids["donchian_breakout"].extend(donchian_params)
    if "donchian_breakout_adx" in grids:
        for base_params in donchian_params:
            for adx_window in [10, 14, 20]:
                for adx_threshold in [15, 20, 25, 30]:
                    grids["donchian_breakout_adx"].append(
                        {
                            **base_params,
                            "adx_window": adx_window,
                            "adx_threshold": adx_threshold,
                        }
                    )

    if "rsi_mean_reversion" in grids:
        for rsi_period in [7, 14]:
            for lower in [20, 30]:
                for upper in [70]:
                    for exit_threshold in [50]:
                        for allow_short in [False, True]:
                            if not (0 < lower < exit_threshold < upper < 100):
                                continue
                            grids["rsi_mean_reversion"].append(
                                {
                                    "rsi_period": rsi_period,
                                    "lower": lower,
                                    "upper": upper,
                                    "exit_threshold": exit_threshold,
                                    "allow_short": allow_short,
                                    "mode": "long_short" if allow_short else "long_flat",
                                }
                            )

    return grids


def _build_broad_candidates(
    families: tuple[str, ...] | None = None,
    *,
    regime_mode: str = "off",
    intervals: tuple[str, ...] = ("1h", "4h"),
) -> list[_BroadCandidate]:
    selected = _resolve_family_names(families)
    resolved_regime_mode = _resolve_regime_mode(regime_mode)
    candidates: list[_BroadCandidate] = []

    if "ema_cross" in selected:
        for fast_len in [5, 8, 10, 12, 15]:
            for slow_len in [20, 30, 40, 50, 80]:
                if slow_len <= fast_len:
                    continue
                for trend_filter in [False, True]:
                    candidates.append(
                        _BroadCandidate(
                            strategy_family="ema_cross",
                            strategy_name="ema_cross",
                            params={
                                "fast_len": fast_len,
                                "slow_len": slow_len,
                                "trend_filter": trend_filter,
                                "allow_short": False,
                            },
                        )
                    )

    if "donchian_breakout" in selected:
        for entry_period in [10, 20, 30, 40]:
            for exit_period in [5, 10, 15, 20]:
                if exit_period >= entry_period:
                    continue
                candidates.append(
                    _BroadCandidate(
                        strategy_family="donchian_breakout",
                        strategy_name="donchian_breakout",
                        params={
                            "entry_period": entry_period,
                            "exit_period": exit_period,
                            "allow_short": False,
                        },
                    )
                )

    if "supertrend" in selected:
        for atr_period in [7, 10, 14, 21]:
            for multiplier in [2.0, 3.0, 4.0]:
                candidates.append(
                    _BroadCandidate(
                        strategy_family="supertrend",
                        strategy_name="supertrend",
                        params={
                            "atr_period": atr_period,
                            "multiplier": multiplier,
                            "allow_short": False,
                        },
                    )
                )

    if "price_adx_breakout" in selected:
        for breakout_lookback in [10, 20, 30]:
            for exit_lookback in [5, 10]:
                if exit_lookback >= breakout_lookback:
                    continue
                for adx_window in [14, 20]:
                    for adx_threshold in [20, 25, 30]:
                        candidates.append(
                            _BroadCandidate(
                                strategy_family="price_adx_breakout",
                                strategy_name="price_adx_breakout",
                                params={
                                    "breakout_lookback": breakout_lookback,
                                    "exit_lookback": exit_lookback,
                                    "adx_window": adx_window,
                                    "adx_threshold": adx_threshold,
                                    "allow_short": False,
                                },
                            )
                        )

    if "rsi_mean_reversion" in selected:
        for rsi_period in [7, 14, 21]:
            for lower in [20, 25, 30]:
                for exit_threshold in [40, 45, 50]:
                    if lower >= exit_threshold:
                        continue
                    for volatility_filter in [False, True]:
                        candidates.append(
                            _BroadCandidate(
                                strategy_family="rsi_mean_reversion",
                                strategy_name="rsi_mean_reversion",
                                params={
                                    "rsi_period": rsi_period,
                                    "lower": lower,
                                    "upper": 100 - lower,
                                    "exit_threshold": exit_threshold,
                                    "allow_short": True,
                                    "volatility_filter": volatility_filter,
                                },
                            )
                        )

    if "bollinger" in selected:
        for lookback in [10, 20, 30]:
            for std_mult in [1.5, 2.0, 2.5]:
                for exit_mode in ["midband", "threshold"]:
                    for volatility_filter in [False, True]:
                        candidates.append(
                            _BroadCandidate(
                                strategy_family="bollinger",
                                strategy_name="bollinger_mean_reversion",
                                params={
                                    "lookback": lookback,
                                    "std_mult": std_mult,
                                    "exit_mode": exit_mode,
                                    "allow_short": True,
                                    "volatility_filter": volatility_filter,
                                },
                            )
                        )

    if "macd" in selected:
        param_sets = [
            (8, 21, 5),
            (10, 30, 7),
            (12, 26, 9),
            (16, 32, 9),
        ]
        for fast_period, slow_period, signal_period in param_sets:
            for use_histogram in [False, True]:
                for adx_filter in [False, True]:
                    candidates.append(
                        _BroadCandidate(
                            strategy_family="macd",
                            strategy_name="macd_momentum",
                            params={
                                "fast_period": fast_period,
                                "slow_period": slow_period,
                                "signal_period": signal_period,
                                "use_histogram": use_histogram,
                                "histogram_threshold": 0.0,
                                "adx_filter": adx_filter,
                                "adx_window": 14,
                                "adx_threshold": 20.0,
                                "allow_short": True,
                            },
                        )
                    )

    if "stoch_rsi" in selected:
        for rsi_period in [7, 14]:
            for stoch_period in [7, 14]:
                for oversold in [15, 20, 25]:
                    for trend_filter in [False, True]:
                        candidates.append(
                            _BroadCandidate(
                                strategy_family="stoch_rsi",
                                strategy_name="stoch_rsi_hybrid",
                                params={
                                    "rsi_period": rsi_period,
                                    "stoch_period": stoch_period,
                                    "oversold": oversold,
                                    "overbought": 100 - oversold,
                                    "trend_filter": trend_filter,
                                    "trend_window": 50,
                                    "allow_short": True,
                                },
                            )
                        )

    if resolved_regime_mode == "off":
        return candidates

    conditioned: list[_BroadCandidate] = []
    for candidate in candidates:
        regime_spec = _default_regime_spec(candidate.strategy_family, intervals[0] if intervals else "1h")
        conditioned.append(
            _BroadCandidate(
                strategy_family=candidate.strategy_family,
                strategy_name=candidate.strategy_name,
                params=candidate.params,
                regime_name=regime_spec.name,
                regime_params=regime_spec.params,
            )
        )
    return conditioned


def _limit_broad_candidates(candidates: list[_BroadCandidate], max_combos: int | None) -> list[_BroadCandidate]:
    if max_combos is None or max_combos <= 0 or len(candidates) <= max_combos:
        return candidates

    grouped: dict[str, list[_BroadCandidate]] = defaultdict(list)
    ordered_families: list[str] = []
    for candidate in candidates:
        if candidate.strategy_family not in grouped:
            ordered_families.append(candidate.strategy_family)
        grouped[candidate.strategy_family].append(candidate)

    selected: list[_BroadCandidate] = []
    family_indices = {family: 0 for family in ordered_families}
    while len(selected) < max_combos:
        progressed = False
        for family in ordered_families:
            idx = family_indices[family]
            if idx >= len(grouped[family]):
                continue
            selected.append(grouped[family][idx])
            family_indices[family] += 1
            progressed = True
            if len(selected) >= max_combos:
                break
        if not progressed:
            break
    return selected


def _build_strategy(strategy_name: str, params: dict[str, Any]) -> Strategy:
    if strategy_name == "ema_cross":
        return EMACrossStrategy(
            short_window=int(params["fast_len"]),
            long_window=int(params["slow_len"]),
            allow_short=bool(params["allow_short"]),
            stop_loss_pct=0.0,
            take_profit_pct=0.0,
        )
    if strategy_name == "donchian_breakout":
        return TrendDonchianBreakout(
            entry_period=int(params["entry_period"]),
            exit_period=int(params["exit_period"]),
            allow_short=bool(params["allow_short"]),
            stop_loss_pct=0.0,
            take_profit_pct=0.0,
        )
    if strategy_name == "donchian_breakout_adx":
        return DonchianBreakoutADXStrategy(
            entry_period=int(params["entry_period"]),
            exit_period=int(params["exit_period"]),
            adx_window=int(params["adx_window"]),
            adx_threshold=float(params["adx_threshold"]),
            allow_short=bool(params["allow_short"]),
            stop_loss_pct=0.0,
            take_profit_pct=0.0,
        )
    if strategy_name == "rsi_mean_reversion":
        return RSIMeanReversionStrategy(
            rsi_period=int(params["rsi_period"]),
            lower=float(params["lower"]),
            upper=float(params["upper"]),
            exit_threshold=float(params["exit_threshold"]),
            allow_short=bool(params["allow_short"]),
        )
    raise ValueError(f"Unsupported strategy: {strategy_name}")


def _build_broad_base_strategy(candidate: _BroadCandidate) -> Strategy:
    params = candidate.params
    if candidate.strategy_name == "ema_cross":
        return EmaCrossTrendFilterStrategy(
            fast_len=int(params["fast_len"]),
            slow_len=int(params["slow_len"]),
            trend_filter=bool(params["trend_filter"]),
            allow_short=bool(params["allow_short"]),
        )
    if candidate.strategy_name == "donchian_breakout":
        return TrendDonchianBreakout(
            entry_period=int(params["entry_period"]),
            exit_period=int(params["exit_period"]),
            allow_short=bool(params["allow_short"]),
            stop_loss_pct=0.0,
            take_profit_pct=0.0,
        )
    if candidate.strategy_name == "supertrend":
        return TrendSuperTrendStrategy(
            atr_period=int(params["atr_period"]),
            multiplier=float(params["multiplier"]),
            allow_short=bool(params["allow_short"]),
            stop_loss_pct=0.0,
            take_profit_pct=0.0,
        )
    if candidate.strategy_name == "price_adx_breakout":
        return PriceADXBreakoutStrategy(
            breakout_lookback=int(params["breakout_lookback"]),
            exit_lookback=int(params["exit_lookback"]),
            adx_window=int(params["adx_window"]),
            adx_threshold=float(params["adx_threshold"]),
            allow_short=bool(params["allow_short"]),
        )
    if candidate.strategy_name == "rsi_mean_reversion":
        return RSIMeanReversionVolFilterStrategy(
            rsi_period=int(params["rsi_period"]),
            lower=float(params["lower"]),
            upper=float(params["upper"]),
            exit_threshold=float(params["exit_threshold"]),
            allow_short=bool(params["allow_short"]),
            volatility_filter=bool(params["volatility_filter"]),
        )
    if candidate.strategy_name == "bollinger_mean_reversion":
        return BollingerMeanReversionStrategy(
            lookback=int(params["lookback"]),
            std_mult=float(params["std_mult"]),
            exit_mode=str(params["exit_mode"]),
            allow_short=bool(params["allow_short"]),
            volatility_filter=bool(params["volatility_filter"]),
        )
    if candidate.strategy_name == "macd_momentum":
        return MACDMomentumFilterStrategy(
            fast_period=int(params["fast_period"]),
            slow_period=int(params["slow_period"]),
            signal_period=int(params["signal_period"]),
            use_histogram=bool(params["use_histogram"]),
            histogram_threshold=float(params["histogram_threshold"]),
            adx_filter=bool(params["adx_filter"]),
            adx_window=int(params["adx_window"]),
            adx_threshold=float(params["adx_threshold"]),
            allow_short=bool(params["allow_short"]),
        )
    if candidate.strategy_name == "stoch_rsi_hybrid":
        return StochRSIHybridStrategy(
            rsi_period=int(params["rsi_period"]),
            stoch_period=int(params["stoch_period"]),
            oversold=float(params["oversold"]),
            overbought=float(params["overbought"]),
            trend_filter=bool(params["trend_filter"]),
            trend_window=int(params["trend_window"]),
            allow_short=bool(params["allow_short"]),
        )
    raise ValueError(f"Unsupported broad strategy: {candidate.strategy_name}")


def _build_broad_strategy(*, candidate: _BroadCandidate, candles: pd.DataFrame, interval: str) -> tuple[Strategy, float]:
    strategy = _build_broad_base_strategy(candidate)
    allow_long_mask, allow_short_mask, coverage_ratio = _precompute_regime_masks(
        candles=candles,
        interval=interval,
        strategy_family=candidate.strategy_family,
        regime_name=candidate.regime_name,
        regime_params=candidate.regime_params,
    )
    if candidate.regime_name == "off":
        return strategy, coverage_ratio
    return (
        RegimeConditionedStrategy(
            base_strategy=strategy,
            allow_long_mask=allow_long_mask,
            allow_short_mask=allow_short_mask,
        ),
        coverage_ratio,
    )


def _make_backtest_config(*, symbol: str, interval: str, config: StrategySearchConfig | BroadSweepConfig) -> BacktestConfig:
    return BacktestConfig(
        symbol=symbol,
        timeframe=interval,
        initial_equity=config.initial_equity,
        leverage=config.leverage,
        order_type="MARKET",
        execution_price_source="next_open",
        slippage_bps=config.slippage_bps,
        taker_fee_bps=config.taker_fee_bps,
        maker_fee_bps=config.taker_fee_bps,
        fee_multiplier=1.0,
        sizing_mode="fixed_usdt",
        fixed_notional_usdt=config.fixed_notional_usdt,
        persist_to_db=False,
    )


def _run_backtest(
    *,
    candles: pd.DataFrame,
    symbol: str,
    strategy_name: str,
    params: dict[str, Any],
    config: StrategySearchConfig,
    engine: BacktestEngine,
) -> BacktestResult:
    strategy = _build_strategy(strategy_name, params)
    return engine.run(candles=candles, strategy=strategy, config=_make_backtest_config(symbol=symbol, interval=config.interval, config=config))


def _run_broad_backtest(
    *,
    candles: pd.DataFrame,
    symbol: str,
    interval: str,
    candidate: _BroadCandidate,
    config: BroadSweepConfig,
    engine: BacktestEngine,
) -> tuple[BacktestResult, float]:
    strategy, coverage_ratio = _build_broad_strategy(candidate=candidate, candles=candles, interval=interval)
    result = engine.run(candles=candles, strategy=strategy, config=_make_backtest_config(symbol=symbol, interval=interval, config=config))
    return result, coverage_ratio


def _window_rows(candles: pd.DataFrame, *, interval: str, train_days: int, test_days: int, step_days: int) -> list[_Window]:
    if candles.empty:
        return []
    start_ts = candles["timestamp"].min()
    end_ts = candles["timestamp"].max()
    windows: list[_Window] = []
    cursor = start_ts
    idx = 0
    while True:
        train_end = cursor + pd.Timedelta(days=train_days)
        test_end = train_end + pd.Timedelta(days=test_days)
        if test_end > end_ts + pd.Timedelta(seconds=_timeframe_seconds(interval)):
            break
        train_df = candles[(candles["timestamp"] >= cursor) & (candles["timestamp"] < train_end)].reset_index(drop=True)
        test_df = candles[(candles["timestamp"] >= train_end) & (candles["timestamp"] < test_end)].reset_index(drop=True)
        if not train_df.empty and not test_df.empty:
            windows.append(
                _Window(
                    index=idx,
                    train_start=cursor,
                    train_end=train_end,
                    test_start=train_end,
                    test_end=test_end,
                    train_df=train_df,
                    test_df=test_df,
                )
            )
            idx += 1
        cursor = cursor + pd.Timedelta(days=step_days)
    return windows


def _select_best_train_candidate(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    ranked = sorted(
        rows,
        key=lambda row: (
            row["trade_gate"],
            float(row["train_sharpe_like"]),
            float(row["train_total_return"]),
            float(row["train_max_drawdown"]),
            -float(row["train_fee_cost_total"]),
        ),
        reverse=True,
    )
    return ranked[0]


def _params_summary(values: list[str]) -> str:
    if not values:
        return ""
    counts = Counter(values)
    return " | ".join(f"{item} x{count}" for item, count in counts.most_common(3))


def _distinct_examples(values: list[str], *, limit: int = 3) -> str:
    examples: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in examples:
            continue
        examples.append(text)
        if len(examples) >= limit:
            break
    return " || ".join(examples)


def _evaluate_symbol_strategy(
    *,
    symbol: str,
    candles: pd.DataFrame,
    strategy_name: str,
    grid: list[dict[str, Any]],
    config: StrategySearchConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    engine = BacktestEngine()
    windows = _window_rows(
        candles,
        interval=config.interval,
        train_days=config.train_days,
        test_days=config.test_days,
        step_days=config.step_days,
    )
    train_acc = _Accumulator(initial_equity=config.initial_equity, timeframe=config.interval)
    test_acc = _Accumulator(initial_equity=config.initial_equity, timeframe=config.interval)
    selected_rows: list[dict[str, Any]] = []

    for window in windows:
        train_candidates: list[dict[str, Any]] = []
        for params in grid:
            train_result = _run_backtest(
                candles=window.train_df,
                symbol=symbol,
                strategy_name=strategy_name,
                params=params,
                config=config,
                engine=engine,
            )
            train_acc_for_window = _Accumulator(initial_equity=config.initial_equity, timeframe=config.interval)
            train_acc_for_window.add(result=train_result, start_ts=window.train_start, end_ts=window.train_end)
            train_summary = train_acc_for_window.metrics()
            train_candidates.append(
                {
                    "params": params,
                    "params_json": _json_dumps(params),
                    "train_result": train_result,
                    "train_total_return": train_summary["total_return"],
                    "train_cagr": train_summary["cagr"],
                    "train_max_drawdown": train_summary["max_drawdown"],
                    "train_sharpe_like": train_summary["sharpe_like"],
                    "train_trade_count": train_summary["trade_count"],
                    "train_win_rate": train_summary["win_rate"],
                    "train_fee_cost_total": train_summary["fee_cost_total"],
                    "trade_gate": float(train_summary["trade_count"]) >= float(config.min_trade_count),
                }
            )

        chosen = _select_best_train_candidate(train_candidates)
        if chosen is None:
            continue

        test_result = _run_backtest(
            candles=window.test_df,
            symbol=symbol,
            strategy_name=strategy_name,
            params=chosen["params"],
            config=config,
            engine=engine,
        )
        test_acc_for_window = _Accumulator(initial_equity=config.initial_equity, timeframe=config.interval)
        test_acc_for_window.add(result=test_result, start_ts=window.test_start, end_ts=window.test_end)
        test_summary = test_acc_for_window.metrics()

        train_acc.add(result=chosen["train_result"], start_ts=window.train_start, end_ts=window.train_end)
        test_acc.add(result=test_result, start_ts=window.test_start, end_ts=window.test_end)

        selected_rows.append(
            {
                "symbol": symbol,
                "strategy": strategy_name,
                "window_index": window.index,
                "train_start": window.train_start.isoformat(),
                "train_end": window.train_end.isoformat(),
                "test_start": window.test_start.isoformat(),
                "test_end": window.test_end.isoformat(),
                "params_json": chosen["params_json"],
                "train_total_return": chosen["train_total_return"],
                "train_cagr": chosen["train_cagr"],
                "train_max_drawdown": chosen["train_max_drawdown"],
                "train_sharpe_like": chosen["train_sharpe_like"],
                "train_trade_count": chosen["train_trade_count"],
                "train_win_rate": chosen["train_win_rate"],
                "train_fee_cost_total": chosen["train_fee_cost_total"],
                "test_total_return": test_summary["total_return"],
                "test_cagr": test_summary["cagr"],
                "test_max_drawdown": test_summary["max_drawdown"],
                "test_sharpe_like": test_summary["sharpe_like"],
                "test_trade_count": test_summary["trade_count"],
                "test_win_rate": test_summary["win_rate"],
                "test_fee_cost_total": test_summary["fee_cost_total"],
            }
        )

    train_summary = train_acc.metrics()
    test_summary = test_acc.metrics()
    window_returns = [float(row["test_total_return"]) for row in selected_rows]
    params_json_list = [row["params_json"] for row in selected_rows]

    by_symbol_row = {
        "symbol": symbol,
        "strategy": strategy_name,
        "interval": config.interval,
        "window_count": len(selected_rows),
        "date_start": candles["timestamp"].min().isoformat() if not candles.empty else "",
        "date_end": candles["timestamp"].max().isoformat() if not candles.empty else "",
        "train_total_return": train_summary["total_return"],
        "train_cagr": train_summary["cagr"],
        "train_max_drawdown": train_summary["max_drawdown"],
        "train_sharpe_like": train_summary["sharpe_like"],
        "train_trade_count": train_summary["trade_count"],
        "train_win_rate": train_summary["win_rate"],
        "oos_total_return": test_summary["total_return"],
        "oos_cagr": test_summary["cagr"],
        "oos_max_drawdown": test_summary["max_drawdown"],
        "oos_sharpe": test_summary["sharpe_like"],
        "trade_count": test_summary["trade_count"],
        "win_rate": test_summary["win_rate"],
        "fee_cost_total": test_summary["fee_cost_total"],
        "avg_trade_return": test_summary["avg_trade_return"],
        "gross_pnl_total": test_summary["gross_pnl_total"],
        "net_pnl_total": test_summary["net_pnl_total"],
        "fee_to_gross_ratio": test_summary["fee_to_gross_ratio"],
        "oos_positive": bool(test_summary["total_return"] > 0),
        "window_oos_positive_ratio": float(np.mean([value > 0 for value in window_returns])) if window_returns else 0.0,
        "window_oos_compound_return": _compound_total_return(window_returns),
        "most_common_params": _params_summary(params_json_list),
    }
    return by_symbol_row, selected_rows


def _evaluate_symbol_candidate(
    *,
    symbol: str,
    interval: str,
    candles: pd.DataFrame,
    candidate: _BroadCandidate,
    config: BroadSweepConfig,
    engine: BacktestEngine,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    windows = _window_rows(
        candles,
        interval=interval,
        train_days=config.train_days,
        test_days=config.test_days,
        step_days=config.step_days,
    )
    train_acc = _Accumulator(initial_equity=config.initial_equity, timeframe=interval)
    test_acc = _Accumulator(initial_equity=config.initial_equity, timeframe=interval)
    params_json = _json_dumps(candidate.params)
    regime_params_json = _json_dumps(candidate.regime_params or {})
    window_rows: list[dict[str, Any]] = []
    train_coverages: list[float] = []
    test_coverages: list[float] = []

    for window in windows:
        train_result, train_coverage_ratio = _run_broad_backtest(
            candles=window.train_df,
            symbol=symbol,
            interval=interval,
            candidate=candidate,
            config=config,
            engine=engine,
        )
        test_result, test_coverage_ratio = _run_broad_backtest(
            candles=window.test_df,
            symbol=symbol,
            interval=interval,
            candidate=candidate,
            config=config,
            engine=engine,
        )
        train_coverages.append(train_coverage_ratio)
        test_coverages.append(test_coverage_ratio)

        train_window_acc = _Accumulator(initial_equity=config.initial_equity, timeframe=interval)
        train_window_acc.add(result=train_result, start_ts=window.train_start, end_ts=window.train_end)
        train_summary = train_window_acc.metrics()

        test_window_acc = _Accumulator(initial_equity=config.initial_equity, timeframe=interval)
        test_window_acc.add(result=test_result, start_ts=window.test_start, end_ts=window.test_end)
        test_summary = test_window_acc.metrics()

        train_acc.add(result=train_result, start_ts=window.train_start, end_ts=window.train_end)
        test_acc.add(result=test_result, start_ts=window.test_start, end_ts=window.test_end)

        window_rows.append(
            {
                "strategy_family": candidate.strategy_family,
                "strategy_name": candidate.strategy_name,
                "interval": interval,
                "symbol": symbol,
                "params_json": params_json,
                "regime_name": candidate.regime_name,
                "regime_params_json": regime_params_json,
                "train_regime_coverage_ratio": train_coverage_ratio,
                "test_regime_coverage_ratio": test_coverage_ratio,
                "window_index": window.index,
                "train_start": window.train_start.isoformat(),
                "train_end": window.train_end.isoformat(),
                "test_start": window.test_start.isoformat(),
                "test_end": window.test_end.isoformat(),
                "train_total_return": train_summary["total_return"],
                "train_cagr": train_summary["cagr"],
                "train_max_drawdown": train_summary["max_drawdown"],
                "train_sharpe_like": train_summary["sharpe_like"],
                "train_trade_count": train_summary["trade_count"],
                "train_win_rate": train_summary["win_rate"],
                "train_fee_cost_total": train_summary["fee_cost_total"],
                "test_total_return": test_summary["total_return"],
                "test_cagr": test_summary["cagr"],
                "test_max_drawdown": test_summary["max_drawdown"],
                "test_sharpe_like": test_summary["sharpe_like"],
                "test_trade_count": test_summary["trade_count"],
                "test_win_rate": test_summary["win_rate"],
                "test_fee_cost_total": test_summary["fee_cost_total"],
            }
        )

    train_summary = train_acc.metrics()
    test_summary = test_acc.metrics()
    window_returns = [float(row["test_total_return"]) for row in window_rows]
    return (
        {
            "strategy_family": candidate.strategy_family,
            "strategy_name": candidate.strategy_name,
            "interval": interval,
            "symbol": symbol,
            "params_json": params_json,
            "regime_name": candidate.regime_name,
            "regime_params_json": regime_params_json,
            "window_count": len(window_rows),
            "date_start": candles["timestamp"].min().isoformat() if not candles.empty else "",
            "date_end": candles["timestamp"].max().isoformat() if not candles.empty else "",
            "train_total_return": train_summary["total_return"],
            "train_cagr": train_summary["cagr"],
            "train_max_drawdown": train_summary["max_drawdown"],
            "train_sharpe_like": train_summary["sharpe_like"],
            "train_trade_count": train_summary["trade_count"],
            "train_win_rate": train_summary["win_rate"],
            "oos_total_return": test_summary["total_return"],
            "oos_cagr": test_summary["cagr"],
            "oos_max_drawdown": test_summary["max_drawdown"],
            "oos_sharpe": test_summary["sharpe_like"],
            "trade_count": test_summary["trade_count"],
            "win_rate": test_summary["win_rate"],
            "fee_cost_total": test_summary["fee_cost_total"],
            "avg_trade_return": test_summary["avg_trade_return"],
            "gross_pnl_total": test_summary["gross_pnl_total"],
            "net_pnl_total": test_summary["net_pnl_total"],
            "fee_to_gross_ratio": test_summary["fee_to_gross_ratio"],
            "oos_positive": bool(test_summary["total_return"] > 0),
            "window_oos_positive_ratio": float(np.mean([value > 0 for value in window_returns])) if window_returns else 0.0,
            "window_oos_compound_return": _compound_total_return(window_returns),
            "train_regime_coverage_ratio": float(np.mean(train_coverages)) if train_coverages else 0.0,
            "regime_coverage_ratio": float(np.mean(test_coverages)) if test_coverages else 0.0,
        },
        window_rows,
    )


def _build_summary(by_symbol_df: pd.DataFrame, *, config: StrategySearchConfig) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for strategy_name, group in by_symbol_df.groupby("strategy", sort=False):
        oos_returns = group["oos_total_return"].astype(float)
        oos_max_drawdowns = group["oos_max_drawdown"].astype(float)
        fee_ratios = group["fee_to_gross_ratio"].replace([np.inf, -np.inf], np.nan).astype(float)
        symbol_consistency_count = int((oos_returns > 0).sum())
        hard_gate_count = 0
        hard_gate_count += int(float(oos_returns.mean()) > 0)
        hard_gate_count += int(float(oos_max_drawdowns.mean()) > -0.35)
        hard_gate_count += int(float(group["trade_count"].mean()) >= float(config.min_trade_count))
        hard_gate_count += int((float(fee_ratios.mean()) if not fee_ratios.dropna().empty else float("inf")) < 1.0)
        hard_gate_count += int(symbol_consistency_count >= 3)
        rows.append(
            {
                "strategy": strategy_name,
                "interval": config.interval,
                "symbol_count": int(len(group)),
                "window_count_total": int(group["window_count"].sum()),
                "train_total_return_mean": float(group["train_total_return"].mean()),
                "train_cagr_mean": float(group["train_cagr"].mean()),
                "train_max_drawdown_mean": float(group["train_max_drawdown"].mean()),
                "train_sharpe_like_mean": float(group["train_sharpe_like"].mean()),
                "oos_total_return_mean": float(oos_returns.mean()),
                "oos_total_return_median": float(oos_returns.median()),
                "oos_cagr_mean": float(group["oos_cagr"].mean()),
                "oos_max_drawdown_mean": float(oos_max_drawdowns.mean()),
                "oos_sharpe_mean": float(group["oos_sharpe"].mean()),
                "trade_count_mean": float(group["trade_count"].mean()),
                "trade_count_total": float(group["trade_count"].sum()),
                "win_rate_mean": float(group["win_rate"].mean()),
                "fee_cost_total": float(group["fee_cost_total"].sum()),
                "avg_trade_return_mean": float(group["avg_trade_return"].mean()),
                "symbol_consistency_count": symbol_consistency_count,
                "symbol_return_std": float(oos_returns.std(ddof=0)) if len(group) > 1 else 0.0,
                "window_oos_positive_ratio_mean": float(group["window_oos_positive_ratio"].mean()),
                "hard_gate_count": hard_gate_count,
                "hard_gate_pass": bool(hard_gate_count >= 4),
                "most_common_params": _distinct_examples(group["most_common_params"].tolist()),
            }
        )

    summary_df = pd.DataFrame(rows)
    if summary_df.empty:
        return summary_df
    summary_df = summary_df.sort_values(
        by=[
            "hard_gate_pass",
            "oos_total_return_mean",
            "oos_sharpe_mean",
            "symbol_consistency_count",
            "symbol_return_std",
        ],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)
    summary_df.insert(0, "rank", range(1, len(summary_df) + 1))
    return summary_df


def _major_alt_metrics(group: pd.DataFrame) -> tuple[float, float]:
    major = group[group["symbol"].isin(["BTCUSDT", "ETHUSDT", "BNBUSDT"])]
    alt = group[~group["symbol"].isin(["BTCUSDT", "ETHUSDT", "BNBUSDT"])]
    major_mean = float(major["oos_total_return"].mean()) if not major.empty else 0.0
    alt_mean = float(alt["oos_total_return"].mean()) if not alt.empty else 0.0
    return major_mean, alt_mean


def _hard_gate_count_for_summary(*, oos_return_mean: float, oos_sharpe_mean: float, oos_mdd_mean: float, positive_symbols: int, fee_ratio_mean: float) -> int:
    count = 0
    count += int(oos_return_mean > 0.0)
    count += int(oos_sharpe_mean > 0.0)
    count += int(oos_mdd_mean > -0.20)
    count += int(positive_symbols >= 3)
    count += int(fee_ratio_mean < 1.0)
    return count


def _rank_score(row: dict[str, Any], *, min_trade_count: int) -> float:
    trade_count_mean = float(row["trade_count_mean"])
    fee_cost_total = float(row["fee_cost_total"])
    fee_ratio_mean = float(row["fee_to_gross_ratio_mean"])
    trade_penalty = 0.0
    if trade_count_mean < min_trade_count:
        trade_penalty += (float(min_trade_count) - trade_count_mean) * 1.5
    if trade_count_mean > 120.0:
        trade_penalty += (trade_count_mean - 120.0) / 12.0
    if not math.isfinite(fee_ratio_mean):
        trade_penalty += 2.5
    return float(
        (120.0 * float(row["oos_total_return_mean"]))
        + (10.0 * float(row["oos_sharpe_mean"]))
        - (60.0 * abs(min(float(row["oos_max_drawdown_mean"]), 0.0)))
        + (2.5 * float(row["positive_symbols"]))
        + (1.5 * float(row["hard_gate_count"]))
        - (0.04 * fee_cost_total)
        - trade_penalty
    )


def _coverage_floor_from_regime_params_json(regime_params_json: str) -> float:
    try:
        params = json.loads(str(regime_params_json))
    except json.JSONDecodeError:
        return 0.0
    value = params.get("min_coverage_ratio", 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _build_broad_summary(by_symbol_df: pd.DataFrame, *, config: BroadSweepConfig) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    group_cols = ["strategy_family", "strategy_name", "interval", "params_json", "regime_name", "regime_params_json"]
    for keys, group in by_symbol_df.groupby(group_cols, sort=False):
        strategy_family, strategy_name, interval, params_json, regime_name, regime_params_json = keys
        oos_returns = group["oos_total_return"].astype(float)
        oos_sharpes = group["oos_sharpe"].astype(float)
        oos_drawdowns = group["oos_max_drawdown"].astype(float)
        fee_ratios = group["fee_to_gross_ratio"].replace([np.inf, -np.inf], np.nan).astype(float)
        positive_symbols = int(group["oos_positive"].sum())
        major_mean, alt_mean = _major_alt_metrics(group)
        fee_ratio_mean = float(fee_ratios.mean()) if not fee_ratios.dropna().empty else float("inf")
        row = {
            "strategy_family": strategy_family,
            "strategy_name": strategy_name,
            "interval": interval,
            "params_json": params_json,
            "regime_name": regime_name,
            "regime_params_json": regime_params_json,
            "symbol_count": int(len(group)),
            "window_count_total": int(group["window_count"].sum()),
            "train_total_return_mean": float(group["train_total_return"].mean()),
            "train_cagr_mean": float(group["train_cagr"].mean()),
            "train_max_drawdown_mean": float(group["train_max_drawdown"].mean()),
            "train_sharpe_like_mean": float(group["train_sharpe_like"].mean()),
            "oos_total_return_mean": float(oos_returns.mean()),
            "oos_total_return_median": float(oos_returns.median()),
            "oos_cagr_mean": float(group["oos_cagr"].mean()),
            "oos_sharpe_mean": float(oos_sharpes.mean()),
            "oos_max_drawdown_mean": float(oos_drawdowns.mean()),
            "trade_count_mean": float(group["trade_count"].mean()),
            "trade_count_total": float(group["trade_count"].sum()),
            "fee_cost_total": float(group["fee_cost_total"].sum()),
            "win_rate_mean": float(group["win_rate"].mean()),
            "avg_trade_return_mean": float(group["avg_trade_return"].mean()),
            "positive_symbols": positive_symbols,
            "positive_symbol_ratio": float(positive_symbols / max(len(group), 1)),
            "symbol_return_std": float(oos_returns.std(ddof=0)) if len(group) > 1 else 0.0,
            "window_oos_positive_ratio_mean": float(group["window_oos_positive_ratio"].mean()),
            "major_oos_return_mean": major_mean,
            "alt_oos_return_mean": alt_mean,
            "fee_to_gross_ratio_mean": fee_ratio_mean,
            "train_regime_coverage_ratio_mean": float(group["train_regime_coverage_ratio"].mean()),
            "regime_coverage_ratio": float(group["regime_coverage_ratio"].mean()),
        }
        row["hard_gate_count"] = _hard_gate_count_for_summary(
            oos_return_mean=row["oos_total_return_mean"],
            oos_sharpe_mean=row["oos_sharpe_mean"],
            oos_mdd_mean=row["oos_max_drawdown_mean"],
            positive_symbols=positive_symbols,
            fee_ratio_mean=fee_ratio_mean,
        )
        row["min_coverage_ratio"] = _coverage_floor_from_regime_params_json(regime_params_json)
        row["coverage_floor_pass"] = bool(float(row["regime_coverage_ratio"]) >= float(row["min_coverage_ratio"]))
        row["hard_gate_pass"] = bool(int(row["hard_gate_count"]) >= 4) and bool(row["coverage_floor_pass"])
        row["rank_score"] = _rank_score(row, min_trade_count=config.min_trade_count)
        rows.append(row)

    summary_df = pd.DataFrame(rows)
    if summary_df.empty:
        return summary_df
    if "coverage_floor_pass" in summary_df.columns:
        summary_df = summary_df[summary_df["coverage_floor_pass"]].reset_index(drop=True)
    if summary_df.empty:
        return summary_df
    summary_df = summary_df.sort_values(
        by=["hard_gate_pass", "rank_score", "oos_total_return_mean", "oos_sharpe_mean"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)
    summary_df.insert(0, "rank", range(1, len(summary_df) + 1))
    return summary_df


def _build_family_summary(summary_df: pd.DataFrame) -> pd.DataFrame:
    if summary_df.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for strategy_family, group in summary_df.groupby("strategy_family", sort=False):
        best = group.sort_values(["rank_score", "oos_total_return_mean"], ascending=[False, False]).iloc[0]
        rows.append(
            {
                "strategy_family": strategy_family,
                "interval": str(best["interval"]),
                "best_rank": int(best["rank"]),
                "strategy_name": best["strategy_name"],
                "params_json": best["params_json"],
                "regime_name": best["regime_name"],
                "regime_params_json": best["regime_params_json"],
                "oos_total_return_mean": float(best["oos_total_return_mean"]),
                "oos_sharpe_mean": float(best["oos_sharpe_mean"]),
                "oos_max_drawdown_mean": float(best["oos_max_drawdown_mean"]),
                "trade_count_mean": float(best["trade_count_mean"]),
                "fee_cost_total": float(best["fee_cost_total"]),
                "positive_symbols": int(best["positive_symbols"]),
                "symbol_return_std": float(best["symbol_return_std"]),
                "regime_coverage_ratio": float(best["regime_coverage_ratio"]),
                "hard_gate_pass": bool(best["hard_gate_pass"]),
                "rank_score": float(best["rank_score"]),
            }
        )
    family_summary_df = pd.DataFrame(rows)
    if family_summary_df.empty:
        return family_summary_df
    return family_summary_df.sort_values(["rank_score", "oos_total_return_mean"], ascending=[False, False]).reset_index(drop=True)


def _next_candidates(*, summary_df: pd.DataFrame) -> list[str]:
    if summary_df.empty:
        return ["No candidates were produced."]
    suggestions: list[str] = []
    for _, row in summary_df.head(3).iterrows():
        suggestions.append(
            f"`{row['strategy_family']}` @ `{row['interval']}` regime `{row['regime_name']}` "
            f"coverage `{float(row['regime_coverage_ratio']):.4f}` params `{row['params_json']}`"
        )
    while len(suggestions) < 3:
        suggestions.append("Add a narrower family-specific follow-up around the current best candidate.")
    return suggestions[:3]


def _build_broad_markdown(
    *,
    path: Path,
    summary_df: pd.DataFrame,
    family_summary_df: pd.DataFrame,
    by_symbol_df: pd.DataFrame,
    config: BroadSweepConfig,
    raw_combo_count: int,
    selected_combo_count: int,
    estimated_backtests: int,
    estimated_hours: float,
) -> None:
    families = ", ".join(_resolve_family_names(config.families))
    intervals = ", ".join(config.intervals)
    hard_gate_count = int(summary_df["hard_gate_pass"].sum()) if not summary_df.empty else 0
    lines = [
        "# Broad Sweep Strategy Discovery",
        "",
        f"- generated_at_utc: {pd.Timestamp.now(tz='UTC').isoformat()}",
        f"- intervals: `{intervals}`",
        f"- families: `{families}`",
        f"- regime_mode: `{config.regime_mode}`",
        f"- raw_combo_count: `{raw_combo_count}`",
        f"- selected_combo_count: `{selected_combo_count}`",
        f"- symbols: `{len(by_symbol_df['symbol'].unique()) if not by_symbol_df.empty else 0}`",
        f"- train_days: `{config.train_days}`",
        f"- test_days: `{config.test_days}`",
        f"- step_days: `{config.step_days}`",
        f"- taker_fee_bps: `{config.taker_fee_bps}`",
        f"- slippage_bps: `{config.slippage_bps}`",
        f"- jobs: `{config.jobs}`",
        f"- estimated_backtests: `{estimated_backtests}`",
        f"- estimated_wall_clock_hours: `{estimated_hours:.2f}`",
        f"- hard_gate_pass_count: `{hard_gate_count}`",
        "",
        "## Top 10 Strategies",
        "",
    ]

    if summary_df.empty:
        lines.append("_No results generated._")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    lines.append("| rank | family | strategy | interval | regime | coverage | oos_return | oos_sharpe | oos_mdd | positive_symbols | fee_cost_total | rank_score |")
    lines.append("|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for _, row in summary_df.head(10).iterrows():
        lines.append(
            f"| {int(row['rank'])} | {row['strategy_family']} | {row['strategy_name']} | {row['interval']} | "
            f"{row['regime_name']} | {float(row['regime_coverage_ratio']):.4f} | {float(row['oos_total_return_mean']):.4f} | "
            f"{float(row['oos_sharpe_mean']):.4f} | {float(row['oos_max_drawdown_mean']):.4f} | "
            f"{int(row['positive_symbols'])}/{int(row['symbol_count'])} | {float(row['fee_cost_total']):.4f} | "
            f"{float(row['rank_score']):.4f} |"
        )

    lines.extend(["", "## Best Per Family", ""])
    lines.append("| family | interval | strategy | regime | coverage | oos_return | oos_sharpe | oos_mdd | positive_symbols | hard_gate |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---|")
    for _, row in family_summary_df.iterrows():
        lines.append(
            f"| {row['strategy_family']} | {row['interval']} | {row['strategy_name']} | {row['regime_name']} | "
            f"{float(row['regime_coverage_ratio']):.4f} | {float(row['oos_total_return_mean']):.4f} | "
            f"{float(row['oos_sharpe_mean']):.4f} | {float(row['oos_max_drawdown_mean']):.4f} | "
            f"{int(row['positive_symbols'])} | {bool(row['hard_gate_pass'])} |"
        )

    lines.extend(["", "## 1h vs 4h", ""])
    interval_rows: list[str] = []
    for interval, group in summary_df.groupby("interval", sort=False):
        top = group.iloc[0]
        interval_rows.append(
            f"- `{interval}` top candidate: `{top['strategy_family']}/{top['strategy_name']}` "
            f"return `{float(top['oos_total_return_mean']):.4f}`, sharpe `{float(top['oos_sharpe_mean']):.4f}`, "
            f"hard_gate `{bool(top['hard_gate_pass'])}`"
        )
    lines.extend(interval_rows or ["_Only one interval executed._"])

    lines.extend(["", "## Major vs Alt Dispersion", ""])
    top = summary_df.iloc[0]
    lines.append(
        f"Best overall candidate `{top['strategy_family']}/{top['strategy_name']}` @ `{top['interval']}` had "
        f"`major_oos_return_mean={float(top['major_oos_return_mean']):.4f}` and "
        f"`alt_oos_return_mean={float(top['alt_oos_return_mean']):.4f}`."
    )

    lines.extend(["", "## Next 3 Candidates", ""])
    for suggestion in _next_candidates(summary_df=summary_df):
        lines.append(f"- {suggestion}")

    path.write_text("\n".join(lines), encoding="utf-8")


def _write_markdown(
    *,
    path: Path,
    summary_df: pd.DataFrame,
    by_symbol_df: pd.DataFrame,
    config: StrategySearchConfig,
) -> None:
    comparison_line = _build_donchian_adx_comparison(summary_df=summary_df)
    lines = [
        "# Strategy Search Summary",
        "",
        f"- generated_at_utc: {pd.Timestamp.now(tz='UTC').isoformat()}",
        f"- interval: `{config.interval}`",
        f"- train_days: `{config.train_days}`",
        f"- test_days: `{config.test_days}`",
        f"- step_days: `{config.step_days}`",
        f"- taker_fee_bps: `{config.taker_fee_bps}`",
        f"- slippage_bps: `{config.slippage_bps}`",
        "",
        "## Donchian ADX Comparison",
        "",
        comparison_line,
        "",
        "## Ranked Strategies",
        "",
    ]

    if summary_df.empty:
        lines.append("_No results generated._")
    else:
        for _, row in summary_df.head(3).iterrows():
            strategy = str(row["strategy"])
            lines.extend(
                [
                    f"### {int(row['rank'])}. {strategy}",
                    f"- OOS mean return: `{float(row['oos_total_return_mean']):.4f}`",
                    f"- OOS mean sharpe: `{float(row['oos_sharpe_mean']):.4f}`",
                    f"- OOS mean max drawdown: `{float(row['oos_max_drawdown_mean']):.4f}`",
                    f"- Positive symbols: `{int(row['symbol_consistency_count'])}` / `{int(row['symbol_count'])}`",
                    f"- Fee cost total: `{float(row['fee_cost_total']):.4f}`",
                    f"- Hard gate pass: `{bool(row['hard_gate_pass'])}` (`{int(row['hard_gate_count'])}` / 5)",
                    "",
                ]
            )
            subset = by_symbol_df[by_symbol_df["strategy"] == strategy].sort_values("oos_total_return", ascending=False)
            if not subset.empty:
                lines.append("| symbol | oos_total_return | oos_sharpe | max_drawdown | trade_count |")
                lines.append("|---|---:|---:|---:|---:|")
                for _, symbol_row in subset.iterrows():
                    lines.append(
                        f"| {symbol_row['symbol']} | {float(symbol_row['oos_total_return']):.4f} | "
                        f"{float(symbol_row['oos_sharpe']):.4f} | {float(symbol_row['oos_max_drawdown']):.4f} | "
                        f"{float(symbol_row['trade_count']):.0f} |"
                    )
                lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _build_donchian_adx_comparison(*, summary_df: pd.DataFrame) -> str:
    if summary_df.empty:
        return "_No results generated._"

    strategy_map = {str(row["strategy"]): row for _, row in summary_df.iterrows()}
    baseline = strategy_map.get("donchian_breakout")
    variant = strategy_map.get("donchian_breakout_adx")
    if baseline is None or variant is None:
        return "_Baseline/variant comparison unavailable because one of the strategies was not executed._"

    def _format_delta(variant_value: float, baseline_value: float) -> str:
        delta = float(variant_value) - float(baseline_value)
        return f"{delta:+.4f}"

    verdict = "improved" if float(variant["oos_total_return_mean"]) > float(baseline["oos_total_return_mean"]) else "degraded"
    return (
        "`donchian_breakout` vs `donchian_breakout_adx`: "
        f"OOS mean return `{float(baseline['oos_total_return_mean']):.4f}` -> `{float(variant['oos_total_return_mean']):.4f}` "
        f"(`{_format_delta(float(variant['oos_total_return_mean']), float(baseline['oos_total_return_mean']))}`), "
        f"sharpe `{float(baseline['oos_sharpe_mean']):.4f}` -> `{float(variant['oos_sharpe_mean']):.4f}`, "
        f"max drawdown `{float(baseline['oos_max_drawdown_mean']):.4f}` -> `{float(variant['oos_max_drawdown_mean']):.4f}`, "
        f"positive symbols `{int(baseline['symbol_consistency_count'])}` -> `{int(variant['symbol_consistency_count'])}` out of `{int(variant['symbol_count'])}`, "
        f"trade count mean `{float(baseline['trade_count_mean']):.2f}` -> `{float(variant['trade_count_mean']):.2f}`, "
        f"fee cost total `{float(baseline['fee_cost_total']):.4f}` -> `{float(variant['fee_cost_total']):.4f}`, "
        f"symbol return std `{float(baseline['symbol_return_std']):.4f}` -> `{float(variant['symbol_return_std']):.4f}`. "
        f"Variant verdict: `{verdict}`."
    )


def _load_candles_for_interval(*, symbol: str, interval: str, data_root: Path) -> pd.DataFrame:
    data_path = data_root / symbol / f"{interval}.csv"
    if not data_path.exists():
        raise FileNotFoundError(f"Historical data file not found: {data_path}")
    return _ensure_candles(pd.read_csv(data_path))


def _estimate_broad_runtime(
    *,
    candidate_count: int,
    symbol_count: int,
    windows_per_interval: dict[str, int],
    jobs: int,
) -> tuple[int, float]:
    backtests_per_candidate = sum(2 * symbol_count * count for count in windows_per_interval.values())
    estimated_backtests = candidate_count * backtests_per_candidate
    estimated_seconds = estimated_backtests * ESTIMATED_SECONDS_PER_BACKTEST / max(jobs, 1)
    return estimated_backtests, estimated_seconds / 3600.0


def _broad_task_worker(task: _BroadTask) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    engine = BacktestEngine()
    by_symbol_rows: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    for interval in task.config.intervals:
        for raw_symbol in task.symbols:
            symbol = str(raw_symbol).upper()
            candles = _load_candles_for_interval(symbol=symbol, interval=interval, data_root=task.config.data_root)
            row, per_window = _evaluate_symbol_candidate(
                symbol=symbol,
                interval=interval,
                candles=candles,
                candidate=task.candidate,
                config=task.config,
                engine=engine,
            )
            by_symbol_rows.append(row)
            window_rows.extend(per_window)
    return by_symbol_rows, window_rows


def run_strategy_search(*, symbols: list[str], config: StrategySearchConfig) -> StrategySearchResult:
    grids = _strategy_grid(config.strategies)
    by_symbol_rows: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []

    for raw_symbol in symbols:
        symbol = str(raw_symbol).upper()
        candles = _load_candles_for_interval(symbol=symbol, interval=config.interval, data_root=config.data_root)
        for strategy_name, grid in grids.items():
            by_symbol_row, selected_rows = _evaluate_symbol_strategy(
                symbol=symbol,
                candles=candles,
                strategy_name=strategy_name,
                grid=grid,
                config=config,
            )
            by_symbol_rows.append(by_symbol_row)
            window_rows.extend(selected_rows)

    by_symbol_df = pd.DataFrame(by_symbol_rows)
    summary_df = _build_summary(by_symbol_df, config=config)

    config.out_root.mkdir(parents=True, exist_ok=True)
    summary_path = config.out_root / "summary.csv"
    by_symbol_path = config.out_root / "by_symbol.csv"
    markdown_path = config.out_root / "top_strategies.md"
    window_path = config.out_root / "window_results.csv"

    summary_df.to_csv(summary_path, index=False)
    by_symbol_df.to_csv(by_symbol_path, index=False)
    pd.DataFrame(window_rows).to_csv(window_path, index=False)
    _write_markdown(path=markdown_path, summary_df=summary_df, by_symbol_df=by_symbol_df, config=config)

    return StrategySearchResult(
        summary_path=summary_path,
        by_symbol_path=by_symbol_path,
        markdown_path=markdown_path,
        summary_df=summary_df,
        by_symbol_df=by_symbol_df,
    )


def run_broad_sweep(*, symbols: list[str], config: BroadSweepConfig) -> BroadSweepResult:
    normalized_symbols = [str(symbol).upper() for symbol in symbols]
    raw_candidates = _build_broad_candidates(
        config.families,
        regime_mode=config.regime_mode,
        intervals=config.intervals,
    )
    windows_per_interval: dict[str, int] = {}
    for interval in config.intervals:
        sample_candles = _load_candles_for_interval(symbol=normalized_symbols[0], interval=interval, data_root=config.data_root)
        windows_per_interval[interval] = len(
            _window_rows(
                sample_candles,
                interval=interval,
                train_days=config.train_days,
                test_days=config.test_days,
                step_days=config.step_days,
            )
        )

    affordable_max: int | None = None
    if config.time_budget_hours > 0:
        backtests_per_candidate = sum(2 * len(normalized_symbols) * count for count in windows_per_interval.values())
        if backtests_per_candidate > 0:
            affordable_max = int(
                max(
                    1,
                    (config.time_budget_hours * 3600.0 * max(config.jobs, 1)) / (ESTIMATED_SECONDS_PER_BACKTEST * backtests_per_candidate),
                )
            )

    effective_max = config.max_combos
    if affordable_max is not None:
        effective_max = affordable_max if effective_max is None else min(effective_max, affordable_max)

    candidates = _limit_broad_candidates(raw_candidates, effective_max)
    return run_broad_sweep_candidates(
        symbols=normalized_symbols,
        config=config,
        candidates=candidates,
        raw_combo_count=len(raw_candidates),
    )


def run_broad_sweep_candidates(
    *,
    symbols: list[str],
    config: BroadSweepConfig,
    candidates: list[_BroadCandidate],
    raw_combo_count: int | None = None,
) -> BroadSweepResult:
    normalized_symbols = [str(symbol).upper() for symbol in symbols]
    windows_per_interval: dict[str, int] = {}
    for interval in config.intervals:
        sample_candles = _load_candles_for_interval(symbol=normalized_symbols[0], interval=interval, data_root=config.data_root)
        windows_per_interval[interval] = len(
            _window_rows(
                sample_candles,
                interval=interval,
                train_days=config.train_days,
                test_days=config.test_days,
                step_days=config.step_days,
            )
        )

    estimated_backtests, estimated_hours = _estimate_broad_runtime(
        candidate_count=len(candidates),
        symbol_count=len(normalized_symbols),
        windows_per_interval=windows_per_interval,
        jobs=config.jobs,
    )

    tasks = [_BroadTask(candidate=candidate, symbols=tuple(normalized_symbols), config=config) for candidate in candidates]
    by_symbol_rows: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []

    if config.jobs <= 1 or len(tasks) <= 1:
        for task in tasks:
            rows, per_window = _broad_task_worker(task)
            by_symbol_rows.extend(rows)
            window_rows.extend(per_window)
    else:
        with ProcessPoolExecutor(max_workers=config.jobs) as executor:
            futures = [executor.submit(_broad_task_worker, task) for task in tasks]
            for future in as_completed(futures):
                rows, per_window = future.result()
                by_symbol_rows.extend(rows)
                window_rows.extend(per_window)

    by_symbol_df = pd.DataFrame(by_symbol_rows)
    summary_df = _build_broad_summary(by_symbol_df, config=config)
    family_summary_df = _build_family_summary(summary_df)

    config.out_root.mkdir(parents=True, exist_ok=True)
    summary_path = config.out_root / "summary.csv"
    by_symbol_path = config.out_root / "by_symbol.csv"
    window_path = config.out_root / "window_results.csv"
    markdown_path = config.out_root / "top_strategies.md"
    family_summary_path = config.out_root / "strategy_family_summary.csv"

    summary_df.to_csv(summary_path, index=False)
    by_symbol_df.to_csv(by_symbol_path, index=False)
    pd.DataFrame(window_rows).to_csv(window_path, index=False)
    family_summary_df.to_csv(family_summary_path, index=False)
    _build_broad_markdown(
        path=markdown_path,
        summary_df=summary_df,
        family_summary_df=family_summary_df,
        by_symbol_df=by_symbol_df,
        config=config,
        raw_combo_count=int(raw_combo_count if raw_combo_count is not None else len(candidates)),
        selected_combo_count=len(candidates),
        estimated_backtests=estimated_backtests,
        estimated_hours=estimated_hours,
    )

    return BroadSweepResult(
        summary_path=summary_path,
        by_symbol_path=by_symbol_path,
        window_results_path=window_path,
        markdown_path=markdown_path,
        family_summary_path=family_summary_path,
        summary_df=summary_df,
        by_symbol_df=by_symbol_df,
        family_summary_df=family_summary_df,
    )


__all__ = [
    "BroadSweepConfig",
    "BroadSweepResult",
    "SUPPORTED_FAMILIES",
    "SUPPORTED_STRATEGIES",
    "StrategySearchConfig",
    "run_broad_sweep_candidates",
    "StrategySearchResult",
    "calculate_adx",
    "run_broad_sweep",
    "run_strategy_search",
]
