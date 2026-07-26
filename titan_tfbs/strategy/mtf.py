"""Multi-Timeframe Alignment Protocol (TFBS Ch IX).

    Screen 1 — TREND    Daily / Weekly   macro directional bias
    Screen 2 — PATTERN  4H / 1H          formation identification
    Screen 3 — ENTRY    15M / 5M         precision trigger

    "Trade WITH the HTF trend. [...] Higher TF trumps lower TF. [...]
     Convergence = conviction. [...] Divergence = caution."     — TFBS Ch IX

The firm's research and bias work is done on the higher timeframes; the 5M and
15M charts exist only to time the entry.  That hierarchy is enforced here: a
counter-trend setup scores 0 on the Ch XI HTF factor, which on its own usually
drags the total below the 7/10 gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from titan_tfbs.config import MTFConfig, TitanConfig
from titan_tfbs.core.candles import MultiTimeframeStore
from titan_tfbs.core.indicators import atr
from titan_tfbs.core.structure import Trend, TrendState, classify_trend, find_pivots
from titan_tfbs.strategy.signals import Direction


@dataclass
class ScreenRead:
    """One timeframe's structural read."""

    timeframe: str
    trend: Trend
    strength: float
    bars: int

    @property
    def usable(self) -> bool:
        return self.bars > 0


@dataclass
class MTFAlignment:
    """The combined three-screen verdict for a symbol."""

    bias: Trend
    bias_strength: float
    screens: List[ScreenRead] = field(default_factory=list)
    pattern_screen: Optional[ScreenRead] = None
    #: True when the Screen 1 timeframes disagree with each other, or the
    #: pattern screen fights the macro bias (Ch IX: "Divergence = caution").
    conflict: bool = False
    detail: str = ""

    def alignment_points(self, direction: Direction, cfg: MTFConfig) -> int:
        """Ch XI HTF factor: 0 = counter-trend, 1 = neutral, 2 = fully aligned."""
        if self.bias is Trend.RANGE:
            return 1
        aligned = (self.bias is Trend.UP) == direction.is_long
        if not aligned:
            return 0
        # "Convergence = conviction" — the pattern screen must not be fighting
        # the macro read for full marks.
        if self.conflict:
            return 1
        return 2

    def is_counter_trend(self, direction: Direction) -> bool:
        if self.bias is Trend.RANGE:
            return False
        return (self.bias is Trend.UP) != direction.is_long

    def size_factor(self, cfg: MTFConfig) -> float:
        """Ch IX: "Conflicting screens = reduce size or wait"."""
        return cfg.conflicting_screen_size_factor if self.conflict else 1.0

    def summary(self) -> Dict[str, object]:
        return {
            "bias": self.bias.value,
            "strength": round(self.bias_strength, 3),
            "conflict": self.conflict,
            "screens": {s.timeframe: s.trend.value for s in self.screens},
            "pattern_screen": (
                {self.pattern_screen.timeframe: self.pattern_screen.trend.value}
                if self.pattern_screen
                else None
            ),
            "detail": self.detail,
        }


def read_screen(
    store: MultiTimeframeStore, timeframe: str, cfg: TitanConfig
) -> Optional[ScreenRead]:
    """Classify one timeframe's structure."""
    series = store.get(timeframe)
    if series is None or len(series) < cfg.mtf.min_bias_bars:
        return None
    candles = list(series)
    atr_value = atr(candles, cfg.atr_period) or 0.0
    pivots = find_pivots(
        candles,
        lookback=cfg.swing.lookback,
        min_swing=cfg.swing.min_swing_atr * atr_value,
    )
    state: TrendState = classify_trend(
        candles,
        pivots=pivots,
        fast_ema=cfg.mtf.bias_fast_ema,
        slow_ema=cfg.mtf.bias_slow_ema,
        swing_lookback=cfg.swing.lookback,
    )
    return ScreenRead(timeframe, state.trend, state.strength, len(candles))


def analyze(
    store: MultiTimeframeStore,
    cfg: TitanConfig,
    trend_timeframes: Sequence[str],
    pattern_timeframe: Optional[str] = None,
) -> MTFAlignment:
    """Build the Screen 1 bias and detect cross-screen divergence."""
    screens: List[ScreenRead] = []
    for tf in trend_timeframes:
        read = read_screen(store, tf, cfg)
        if read is not None:
            screens.append(read)

    if not screens:
        return MTFAlignment(
            bias=Trend.RANGE,
            bias_strength=0.0,
            detail="insufficient higher-timeframe history for a bias",
        )

    primary = screens[0]
    others = screens[1:]
    bias = primary.trend
    strength = primary.strength
    conflict = False
    notes: List[str] = [f"{s.timeframe}={s.trend.value}" for s in screens]

    for other in others:
        if other.trend is Trend.RANGE or primary.trend is Trend.RANGE:
            strength *= 0.85
            continue
        if other.trend is not primary.trend:
            # Ch IX: "Higher TF trumps lower TF." The longer timeframe wins,
            # and the disagreement is flagged as caution.
            conflict = True
            bias = other.trend if other.timeframe in _longer(primary.timeframe, other.timeframe) else primary.trend
            strength *= 0.5
        else:
            strength = min(1.0, strength * 1.15)

    pattern_read = (
        read_screen(store, pattern_timeframe, cfg) if pattern_timeframe else None
    )
    if pattern_read is not None and bias is not Trend.RANGE:
        if pattern_read.trend is not Trend.RANGE and pattern_read.trend is not bias:
            conflict = True
            notes.append(f"{pattern_read.timeframe} fights the macro bias")

    return MTFAlignment(
        bias=bias,
        bias_strength=max(0.0, min(1.0, strength)),
        screens=screens,
        pattern_screen=pattern_read,
        conflict=conflict,
        detail=", ".join(notes),
    )


def _longer(a: str, b: str) -> List[str]:
    from titan_tfbs.core.candles import TimeFrame

    ta, tb = TimeFrame.parse(a), TimeFrame.parse(b)
    return [b] if tb.minutes > ta.minutes else [a]
