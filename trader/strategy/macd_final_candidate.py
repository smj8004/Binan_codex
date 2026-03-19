from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from .base import Bar, Signal, Strategy, StrategyPosition
from .macd import MACDStrategy


FINAL_CANDIDATE_PROFILE = "macd_final_candidate"
FINAL_CANDIDATE_REGIME_NAME = "trend_tight_high_adx_extreme_vol_strict_trend"


@dataclass(frozen=True)
class FinalCandidateRegime:
    adx_window: int = 14
    low_adx_threshold: float = 18.0
    high_adx_threshold: float = 30.0
    vol_window: int = 20
    vol_percentile_window: int = 160
    low_vol_quantile: float = 0.20
    high_vol_quantile: float = 0.80
    trend_ema_span: int = 100
    trend_slope_lookback: int = 16
    trend_slope_threshold: float = 0.0030
    trend_distance_threshold: float = 0.0050
    min_coverage_ratio: float = 0.20


FINAL_CANDIDATE_MACD_PARAMS: dict[str, Any] = {
    "fast_period": 12,
    "slow_period": 26,
    "signal_period": 9,
    "allow_short": True,
    "use_histogram": False,
    "histogram_threshold": 0.0,
    "adx_filter": False,
}


def _safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _calculate_adx_latest(*, highs: list[float], lows: list[float], closes: list[float], window: int) -> float | None:
    if len(highs) < window * 2 or len(lows) < window * 2 or len(closes) < window * 2:
        return None
    frame = pd.DataFrame({"high": highs, "low": lows, "close": closes}, dtype="float64")
    prev_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    up_move = frame["high"].diff()
    down_move = -frame["low"].diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0.0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0.0), 0.0)
    atr = true_range.rolling(window=window, min_periods=window).mean()
    atr_safe = atr.replace(0.0, pd.NA)
    plus_di = 100.0 * plus_dm.rolling(window=window, min_periods=window).mean() / atr_safe
    minus_di = 100.0 * minus_dm.rolling(window=window, min_periods=window).mean() / atr_safe
    di_sum = (plus_di + minus_di).replace(0.0, pd.NA)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    value = _safe_float(dx.rolling(window=window, min_periods=window).mean().iloc[-1])
    return max(min(value, 100.0), 0.0) if value is not None else None


class MACDFinalCandidateStrategy(Strategy):
    def __init__(self) -> None:
        self.profile_name = FINAL_CANDIDATE_PROFILE
        self.regime = FinalCandidateRegime()
        self.base_strategy = MACDStrategy(
            fast_period=int(FINAL_CANDIDATE_MACD_PARAMS["fast_period"]),
            slow_period=int(FINAL_CANDIDATE_MACD_PARAMS["slow_period"]),
            signal_period=int(FINAL_CANDIDATE_MACD_PARAMS["signal_period"]),
            allow_short=bool(FINAL_CANDIDATE_MACD_PARAMS["allow_short"]),
            use_histogram=bool(FINAL_CANDIDATE_MACD_PARAMS["use_histogram"]),
            histogram_threshold=float(FINAL_CANDIDATE_MACD_PARAMS["histogram_threshold"]),
        )
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._closes: list[float] = []
        self._bars_seen = 0
        self._coverage_hits = 0
        self._last_state: dict[str, Any] = {
            "profile_name": self.profile_name,
            "regime_name": FINAL_CANDIDATE_REGIME_NAME,
            "regime_params": asdict(self.regime),
            "fixed_params": dict(FINAL_CANDIDATE_MACD_PARAMS),
            "base_signal": "hold",
            "gated_signal": "hold",
            "allow_long": False,
            "allow_short": False,
            "coverage_ratio": 0.0,
        }

    def _latest_regime(self) -> tuple[bool, bool, dict[str, Any]]:
        if len(self._closes) < max(
            int(self.regime.vol_window + self.regime.vol_percentile_window),
            int(self.regime.trend_ema_span + self.regime.trend_slope_lookback + 1),
            int(self.regime.adx_window * 2),
        ):
            return False, False, {
                "adx": None,
                "realized_vol": None,
                "low_vol_cut": None,
                "high_vol_cut": None,
                "trend_ema": None,
                "trend_slope": None,
                "ema_distance": None,
                "base_mask": False,
                "uptrend": False,
                "downtrend": False,
            }

        closes = pd.Series(self._closes, dtype="float64")
        pct_returns = closes.pct_change()
        realized_vol = pct_returns.rolling(window=self.regime.vol_window, min_periods=self.regime.vol_window).std(ddof=0)
        low_vol_cut = realized_vol.rolling(
            window=self.regime.vol_percentile_window,
            min_periods=self.regime.vol_percentile_window,
        ).quantile(self.regime.low_vol_quantile)
        high_vol_cut = realized_vol.rolling(
            window=self.regime.vol_percentile_window,
            min_periods=self.regime.vol_percentile_window,
        ).quantile(self.regime.high_vol_quantile)
        trend_ema = closes.ewm(span=self.regime.trend_ema_span, adjust=False).mean()
        trend_slope = trend_ema.pct_change(periods=self.regime.trend_slope_lookback)
        current_close = float(closes.iloc[-1])
        current_ema = _safe_float(trend_ema.iloc[-1]) or 0.0
        ema_distance = abs((current_close / current_ema) - 1.0) if current_ema > 0 else 0.0
        adx = _calculate_adx_latest(
            highs=self._highs,
            lows=self._lows,
            closes=self._closes,
            window=self.regime.adx_window,
        )
        current_realized_vol = _safe_float(realized_vol.iloc[-1])
        current_low_vol_cut = _safe_float(low_vol_cut.iloc[-1])
        current_high_vol_cut = _safe_float(high_vol_cut.iloc[-1])
        current_slope = _safe_float(trend_slope.iloc[-1])

        low_vol = (
            current_realized_vol is not None
            and current_low_vol_cut is not None
            and current_realized_vol <= current_low_vol_cut
        )
        high_adx = adx is not None and adx >= self.regime.high_adx_threshold
        trend_distance_ok = ema_distance >= self.regime.trend_distance_threshold
        uptrend = (
            current_ema > 0
            and current_close > current_ema
            and current_slope is not None
            and current_slope >= self.regime.trend_slope_threshold
            and trend_distance_ok
        )
        downtrend = (
            current_ema > 0
            and current_close < current_ema
            and current_slope is not None
            and current_slope <= -self.regime.trend_slope_threshold
            and trend_distance_ok
        )
        base_mask = bool(high_adx and not low_vol)
        allow_long = bool(base_mask and uptrend)
        allow_short = bool(base_mask and downtrend)
        return allow_long, allow_short, {
            "adx": adx,
            "realized_vol": current_realized_vol,
            "low_vol_cut": current_low_vol_cut,
            "high_vol_cut": current_high_vol_cut,
            "trend_ema": current_ema,
            "trend_slope": current_slope,
            "ema_distance": ema_distance,
            "base_mask": base_mask,
            "uptrend": uptrend,
            "downtrend": downtrend,
        }

    def on_bar(self, bar: Bar, position: StrategyPosition | None = None) -> Signal:
        self._bars_seen += 1
        self._highs.append(float(bar.high))
        self._lows.append(float(bar.low))
        self._closes.append(float(bar.close))
        base_signal = self.base_strategy.on_bar(bar, position)
        allow_long, allow_short, regime_state = self._latest_regime()
        if bool(regime_state.get("base_mask", False)):
            self._coverage_hits += 1
        coverage_ratio = float(self._coverage_hits / self._bars_seen) if self._bars_seen else 0.0

        gated_signal = base_signal
        if base_signal in {"long", "buy"} and not allow_long:
            gated_signal = "exit" if position is not None and position.side == "short" else "hold"
        elif base_signal in {"short", "sell"} and not allow_short:
            gated_signal = "exit" if position is not None and position.side == "long" else "hold"

        self._last_state = {
            "profile_name": self.profile_name,
            "regime_name": FINAL_CANDIDATE_REGIME_NAME,
            "regime_params": asdict(self.regime),
            "fixed_params": dict(FINAL_CANDIDATE_MACD_PARAMS),
            "base_signal": base_signal,
            "gated_signal": gated_signal,
            "allow_long": allow_long,
            "allow_short": allow_short,
            "coverage_ratio": coverage_ratio,
            **regime_state,
            "bars_seen": self._bars_seen,
        }
        return gated_signal

    def get_state(self) -> dict[str, Any]:
        base_state = self.base_strategy.get_state()
        return {
            **self._last_state,
            "base_strategy_state": base_state,
        }
