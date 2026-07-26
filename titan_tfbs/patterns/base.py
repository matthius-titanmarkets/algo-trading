"""Shared pattern model for the TFBS pattern modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Sequence

from titan_tfbs.core.candles import Candle
from titan_tfbs.core.structure import Line, Pivot, Trend


class PatternType(str, Enum):
    """The only formations TFBS recognises (Ch III, Ch IV, Ch VI-B)."""

    HEAD_SHOULDERS = "H&S"
    INVERSE_HEAD_SHOULDERS = "InvH&S"
    DOUBLE_TOP = "DT"
    DOUBLE_BOTTOM = "DB"
    TRIPLE_TOP = "TT"
    TRIPLE_BOTTOM = "TB"

    @property
    def is_bearish(self) -> bool:
        """True when a confirmed break sends us short."""
        return self in (
            PatternType.HEAD_SHOULDERS,
            PatternType.DOUBLE_TOP,
            PatternType.TRIPLE_TOP,
        )

    @property
    def is_triple(self) -> bool:
        """TFBS Ch IV-D / Ch XIV-B — the +1 confluence variant."""
        return self in (PatternType.TRIPLE_TOP, PatternType.TRIPLE_BOTTOM)

    @property
    def family(self) -> str:
        if self in (PatternType.HEAD_SHOULDERS, PatternType.INVERSE_HEAD_SHOULDERS):
            return "head_shoulders"
        return "double_top"


class FilterStatus(str, Enum):
    """TFBS Ch III-E / Appendix A filter weighting."""

    MANDATORY = "M"
    PREFERRED = "P"


@dataclass(frozen=True)
class FilterResult:
    """One quality filter's outcome, retained for the journal and audit."""

    name: str
    status: FilterStatus
    passed: bool
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.status is FilterStatus.MANDATORY and not self.passed


@dataclass
class Pattern:
    """A validated TFBS formation, awaiting breakout confirmation.

    A ``Pattern`` is never tradeable on its own — Ch VI-B grades "Pattern, No
    Break" as *watchlist only*.  It becomes actionable when
    :mod:`titan_tfbs.strategy.breakout` confirms a close through
    :attr:`trigger_line`.
    """

    type: PatternType
    symbol: str
    timeframe: str
    #: Pivots that define the formation, in chronological order.
    pivots: List[Pivot]
    #: The neckline (H&S) or confirmation line (DT/DB) — the Ch V key level.
    trigger_line: Line
    #: Pattern height used for the Ch III-D / Ch IV-D measured move.
    measured_height: float
    #: Structural invalidation price (Ch VII / Ch X-A): right shoulder for
    #: H&S, second peak/trough for DT/DB.
    structural_invalidation: float
    start_index: int
    end_index: int
    start_ts: datetime
    end_ts: datetime
    filters: List[FilterResult] = field(default_factory=list)
    #: 0-2, the Ch XI "Pattern Quality" confluence factor.
    quality_points: int = 0
    #: 0.0-1.0 continuous quality used for ranking competing formations.
    quality: float = 0.0
    #: Ch XIV-B complex H&S: shoulders beyond the canonical three peaks.
    extra_shoulders: int = 0
    #: Ch III-C / Ch IV-C volume profile satisfied.
    volume_profile_ok: bool = False
    #: Ch V-B "choppy macro context" — the formation's apex is buried inside a
    #: wider range instead of capping it.
    choppy_context: bool = False
    #: How far the apex sits from the enclosing range's extreme, 0.0 = at it.
    context_position: float = 0.0
    prior_trend: Trend = Trend.RANGE
    prior_trend_size: float = 0.0
    atr: float = 0.0
    #: Ch VI-B A+ ELITE — set when this formation overlaps a second one (an
    #: H&S right shoulder coinciding with a Double Top's second peak).
    dual_pattern: Optional[str] = None
    #: Ch XIV-B fractal case — a same-direction formation on another screen.
    fractal_confluence: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    #: Set once the formation has been confirmed, expired or invalidated.
    state: str = "armed"        # armed | confirmed | expired | invalidated
    #: Trigger line re-expressed in wall-clock time (see :meth:`set_time_anchor`).
    _anchor_ts: Optional[datetime] = field(default=None, repr=False)
    _anchor_price: float = field(default=0.0, repr=False)
    _slope_per_min: float = field(default=0.0, repr=False)

    # -- geometry ----------------------------------------------------------

    @property
    def is_bearish(self) -> bool:
        return self.type.is_bearish

    @property
    def direction_sign(self) -> int:
        """+1 for a long setup, -1 for a short setup."""
        return -1 if self.is_bearish else 1

    @property
    def apex_price(self) -> float:
        """The head (H&S) or the tested level (DT/DB)."""
        extremes = [p.price for p in self.pivots if p.is_high == self.is_bearish]
        if not extremes:
            return self.pivots[0].price
        return max(extremes) if self.is_bearish else min(extremes)

    def trigger_price_at(self, index: int) -> float:
        """The key level's price at a given bar index (necklines can slope)."""
        return self.trigger_line.value_at(index)

    def set_time_anchor(self, anchor_ts: datetime, timeframe_minutes: int) -> None:
        """Re-express the trigger line in wall-clock time.

        The neckline is discovered in pattern-TF bar space (Ch IX Screen 2) but
        the breakout is confirmed on the entry TF (Screen 3), so the line has
        to be evaluable at any timestamp.
        """
        self._anchor_ts = anchor_ts
        self._anchor_price = self.trigger_line.value_at(self.end_index)
        self._slope_per_min = self.trigger_line.slope / max(1, timeframe_minutes)

    def trigger_price_at_ts(self, ts: datetime) -> float:
        """The key level's price at an arbitrary timestamp."""
        if self._anchor_ts is None:
            return self.trigger_line.value_at(self.end_index)
        minutes = (ts - self._anchor_ts).total_seconds() / 60.0
        return self._anchor_price + self._slope_per_min * minutes

    def is_broken_by_ts(self, candle: Candle, buffer: float = 0.0) -> bool:
        """TFBS Ch V-A criterion 1 — closing break, evaluated in time space."""
        level = self.trigger_price_at_ts(candle.ts)
        if self.is_bearish:
            return candle.close < level - buffer
        return candle.close > level + buffer

    def wick_only_at_ts(self, candle: Candle) -> bool:
        """TFBS Ch V-B — wick pierces the level but the body closes inside."""
        level = self.trigger_price_at_ts(candle.ts)
        if self.is_bearish:
            return candle.low < level <= candle.close
        return candle.high > level >= candle.close

    def signature(self) -> str:
        """Stable identity so the same formation is not registered twice.

        Keyed on timestamps rather than bar indices: indices shift as the
        rolling series drops old candles, timestamps never do.
        """
        return (
            f"{self.symbol}|{self.timeframe}|{self.type.value}|"
            f"{self.start_ts.isoformat()}|{self.end_ts.isoformat()}"
        )

    def is_broken_by(self, candle: Candle, index: int, buffer: float = 0.0) -> bool:
        """TFBS Ch V-A criterion 1 — break on a CLOSING basis only."""
        level = self.trigger_price_at(index)
        if self.is_bearish:
            return candle.close < level - buffer
        return candle.close > level + buffer

    def wick_only_penetration(self, candle: Candle, index: int) -> bool:
        """TFBS Ch V-B — "No close = no trade"."""
        level = self.trigger_price_at(index)
        if self.is_bearish:
            return candle.low < level <= candle.close
        return candle.high > level >= candle.close

    def measured_target(self, break_price: float) -> float:
        """TFBS Ch III-D / Ch IV-D — the measured move.

        ``Target = Break Price ± |Head/Peak − Neckline|``
        """
        return break_price + self.direction_sign * self.measured_height

    # -- reporting ---------------------------------------------------------

    @property
    def mandatory_failures(self) -> List[FilterResult]:
        return [f for f in self.filters if f.blocking]

    @property
    def preferred_passed(self) -> int:
        return sum(
            1 for f in self.filters if f.status is FilterStatus.PREFERRED and f.passed
        )

    @property
    def preferred_total(self) -> int:
        return sum(1 for f in self.filters if f.status is FilterStatus.PREFERRED)

    @property
    def is_valid(self) -> bool:
        return not self.mandatory_failures

    def label(self) -> str:
        return f"{self.type.value}@{self.timeframe}"

    def summary(self) -> Dict[str, object]:
        return {
            "type": self.type.value,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": "short" if self.is_bearish else "long",
            "start": self.start_ts.isoformat(),
            "end": self.end_ts.isoformat(),
            "bars": self.end_index - self.start_index,
            "trigger_price": round(self.trigger_line.value_at(self.end_index), 8),
            "measured_height": round(self.measured_height, 8),
            "structural_invalidation": round(self.structural_invalidation, 8),
            "quality": round(self.quality, 3),
            "quality_points": self.quality_points,
            "extra_shoulders": self.extra_shoulders,
            "volume_profile_ok": self.volume_profile_ok,
            "prior_trend": self.prior_trend.value,
            "filters": {f.name: f.passed for f in self.filters},
            "notes": list(self.notes),
            "state": self.state,
        }


def grade_quality(filters: Sequence[FilterResult]) -> tuple:
    """Map filter outcomes onto the Ch XI "Pattern Quality" factor (0-2).

    Ch XI defines the scale as ``0 = Ambiguous | 1 = Acceptable | 2 = Textbook``.
    A formation that fails a MANDATORY filter is not a formation at all, so the
    scale is applied to the PREFERRED filters: all (or all but a rounding
    fraction) satisfied is textbook, a majority is acceptable.
    """
    preferred = [f for f in filters if f.status is FilterStatus.PREFERRED]
    if not preferred:
        return 1, 0.5
    ratio = sum(1 for f in preferred if f.passed) / len(preferred)
    if ratio >= 0.80:
        return 2, ratio
    if ratio >= 0.50:
        return 1, ratio
    return 0, ratio


def overlap_bars(a: Pattern, b: Pattern) -> int:
    """Bars shared by two formations — used to spot the Ch VI-B A+ overlap."""
    return max(0, min(a.end_index, b.end_index) - max(a.start_index, b.start_index))
