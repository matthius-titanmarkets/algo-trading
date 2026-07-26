"""Pattern Module C — the Breakout Confirmation Protocol (TFBS Ch V).

    "The breakout is the trigger mechanism of TFBS. Neither H&S nor DT/DB is
     tradeable until the key level (neckline or confirmation line) has been
     decisively broken. [...] Trading before confirmation = anticipation
     trading. TFBS does not permit anticipation entries."       — TFBS Ch V

This module owns the state machine that carries a validated formation from
*armed* to *ready to execute*::

    armed --(closing break)--> broken --(retest + rejection)--> retested
          |                          \\--(follow-through)-----> ready
          |
          \\--(expiry / invalidation)--> dead

Method A takes the trade at ``broken``; Method B at ``retested``; Method C at
``ready`` (Ch VII).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from titan_tfbs.config import BreakoutConfig, EntryMethod, TitanConfig
from titan_tfbs.core.candles import Candle
from titan_tfbs.core.indicators import atr as atr_of, rsi_series, volume_ratio
from titan_tfbs.core.structure import detect_rejection, is_followthrough
from titan_tfbs.patterns.base import Pattern
from titan_tfbs.strategy.signals import BreakoutEvent, RetestEvent


#: Watch states. The formation walks these in order; anything that fails a
#: Ch V-B filter or a Ch XIV-B invalidation goes straight to DEAD.
ARMED = "armed"
BROKEN = "broken"
RETESTED = "retested"
READY = "ready"
DEAD = "dead"


@dataclass
class PatternWatch:
    """One formation being carried through the Ch V protocol."""

    pattern: Pattern
    registered_ts: datetime
    state: str = ARMED
    bars_armed: int = 0
    bars_since_break: int = 0
    breakout: Optional[BreakoutEvent] = None
    retest: Optional[RetestEvent] = None
    #: Ch XIV-B gap handling — the pre-gap close; a full fill invalidates.
    gap_origin: Optional[float] = None
    dead_reason: Optional[str] = None
    #: Entry methods already consumed, so one formation yields one trade.
    consumed: bool = False

    @property
    def alive(self) -> bool:
        return self.state != DEAD and not self.consumed

    def kill(self, reason: str) -> None:
        self.state = DEAD
        self.dead_reason = reason
        self.pattern.state = "invalidated" if "invalid" in reason else "expired"


def evaluate_breakout(
    pattern: Pattern,
    candles: Sequence[Candle],
    atr_value: float,
    cfg: BreakoutConfig,
    timeframe: str,
    context_candles: Optional[Sequence[Candle]] = None,
) -> Tuple[Optional[BreakoutEvent], Optional[str]]:
    """Apply the Ch V-A criteria to the most recent closed candle.

    Returns ``(event, rejection_reason)``.  ``event`` is None when there is no
    confirmed break; ``rejection_reason`` explains a *false* breakout that was
    actively filtered out (Ch V-B) so it can be journalled.
    """
    if not candles or atr_value <= 0:
        return None, None

    candle = candles[-1]
    level = pattern.trigger_price_at_ts(candle.ts)
    buffer = cfg.min_close_beyond_atr * atr_value

    # ---- Criterion 1 (REQUIRED): candle CLOSE beyond the level ----------
    if not pattern.is_broken_by_ts(candle, buffer):
        if cfg.reject_wick_only and pattern.wick_only_at_ts(candle):
            # Ch V-B: "Wick-only penetration [...] No close = no trade."
            return None, "wick_only_penetration"
        return None, None

    event = BreakoutEvent(
        pattern=pattern,
        ts=candle.ts,
        candle=candle,
        timeframe=timeframe,
        level=level,
        close=candle.close,
        atr=atr_value,
    )

    # ---- Criterion 2 (STRONG PREF): volume surge ------------------------
    vr = volume_ratio(candles, cfg.volume_average_period)
    event.volume_ratio = vr
    if vr is not None:
        event.volume_surge = vr >= cfg.volume_surge_multiple
        if not event.volume_surge:
            # Ch V-B: "Low-volume break [...] institutional trap."
            event.notes.append(
                f"volume {vr:.2f}x average, below the {cfg.volume_surge_multiple:.1f}x "
                f"Ch V-A threshold"
            )
            if cfg.reject_low_volume_break:
                return None, "low_volume_breakout"
    else:
        event.notes.append("no volume data on this feed; Ch V-A criterion 2 unavailable")

    # ---- Ch XIV-B: gap through the neckline -----------------------------
    if len(candles) >= 2:
        prev = candles[-2]
        gap = (
            (prev.low - candle.open) if pattern.is_bearish else (candle.open - prev.high)
        )
        if gap >= cfg.gap_min_atr * atr_value:
            event.gapped = True
            event.gap_size = gap
            event.notes.append(
                "gapped through the level: enter on gap close or first pullback; "
                "a full gap fill invalidates (Ch XIV-B)"
            )

    # ---- Ch V-B: choppy macro context -----------------------------------
    # Assessed on the formation at detection time (see PatternDetector), not
    # on the break, so the post-break move cannot distort it.
    if pattern.choppy_context:
        event.choppy_context = True
        event.notes.append(
            f"formation apex sits {pattern.context_position:.0%} inside a wider "
            f"range — low follow-through (Ch V-B)"
        )
        if cfg.reject_choppy_context:
            return None, "choppy_macro_context"

    # ---- Ch V-B: momentum divergence against the break ------------------
    if _has_divergence(candles, pattern.is_bearish, cfg.divergence_rsi_period):
        event.divergence = True
        event.notes.append("RSI divergence against the breakout — elevated skepticism (Ch V-B)")

    return event, None


def _has_divergence(candles: Sequence[Candle], bearish: bool, period: int) -> bool:
    """Ch V-B — "RSI/MACD diverging against breakout direction".

    Price makes a new extreme while RSI fails to, measured across the two
    halves of a recent window.
    """
    lookback = max(period * 3, 30)
    window = candles[-lookback:]
    if len(window) < period * 2 + 4:
        return False
    closes = [c.close for c in window]
    rsis = rsi_series(closes, period)
    half = len(window) // 2
    first, second = range(period, half), range(half, len(window))
    first = [i for i in first if rsis[i] is not None]
    second = [i for i in second if rsis[i] is not None]
    if not first or not second:
        return False

    if bearish:
        i1 = min(first, key=lambda i: closes[i])
        i2 = min(second, key=lambda i: closes[i])
        made_new_low = closes[i2] < closes[i1]
        return made_new_low and (rsis[i2] or 0) > (rsis[i1] or 0)
    i1 = max(first, key=lambda i: closes[i])
    i2 = max(second, key=lambda i: closes[i])
    made_new_high = closes[i2] > closes[i1]
    return made_new_high and (rsis[i2] or 100) < (rsis[i1] or 100)


def evaluate_retest(
    watch: PatternWatch,
    candles: Sequence[Candle],
    atr_value: float,
    cfg: BreakoutConfig,
    entry_cfg,
) -> Optional[RetestEvent]:
    """Ch V-A criterion 3 / Ch V-C — the retest that flips the level.

        "Price returns to broken level (now flipped S/R) and holds —
         structural flip confirmed."                        — Ch V-A, HIGHEST
    """
    if not candles or watch.breakout is None:
        return None
    candle = candles[-1]
    pattern = watch.pattern
    level = pattern.trigger_price_at_ts(candle.ts)
    tolerance = cfg.retest_tolerance_atr * atr_value

    touched = (
        candle.high >= level - tolerance
        if pattern.is_bearish
        else candle.low <= level + tolerance
    )
    if not touched:
        return None

    # A short setup needs a bearish rejection at the retest, and vice versa.
    rejection = detect_rejection(
        candles,
        bullish=not pattern.is_bearish,
        pin_wick_ratio=entry_cfg.pin_bar_wick_ratio,
        pin_max_body_frac=entry_cfg.pin_bar_max_body_frac,
        engulf_min_body_ratio=entry_cfg.engulfing_min_body_ratio,
    )
    if rejection is None:
        return None

    wick = candle.high if pattern.is_bearish else candle.low
    return RetestEvent(
        ts=candle.ts,
        candle=candle,
        wick_price=wick,
        rejection_kind=rejection.kind,
        rejection_strength=rejection.strength,
    )


class BreakoutTracker:
    """Carries every armed formation for one symbol through the Ch V protocol."""

    def __init__(self, config: TitanConfig) -> None:
        self.config = config
        self.watches: Dict[str, PatternWatch] = {}

    # -- registration ------------------------------------------------------

    def register(self, pattern: Pattern, now: datetime) -> Optional[PatternWatch]:
        """Add a validated formation to the watchlist.

        Ch VI-B grades an unbroken formation as *watchlist only*.  Overlapping
        same-direction formations are one trade idea seen several ways — a
        complex H&S and the simple H&S inside it, or an Inverse H&S and the
        Double Bottom built from the same lows, or the same reversal read on
        both the 4H and the 1H.  Ch VI-B calls that overlap a single A+ setup,
        not two trades, so only one read is carried forward.

        Precedence follows Ch IX ("Higher TF trumps lower TF"), then pattern
        quality.  A formation that has already broken is never displaced.
        """
        sig = pattern.signature()
        if sig in self.watches:
            return None

        for existing in self.active:
            other = existing.pattern
            if other.is_bearish != pattern.is_bearish:
                continue
            overlaps = not (
                pattern.end_ts < other.start_ts or pattern.start_ts > other.end_ts
            )
            if not overlaps:
                continue
            if existing.state != ARMED:
                return None      # already live; do not double up on it
            if _precedence(other) >= _precedence(pattern):
                return None      # keep the incumbent
            existing.kill("superseded_by_higher_precedence_formation")

        watch = PatternWatch(pattern=pattern, registered_ts=now)
        self.watches[sig] = watch
        return watch

    def prune(self) -> List[PatternWatch]:
        """Drop dead and consumed watches; returns what was removed."""
        removed = [w for w in self.watches.values() if not w.alive]
        for w in removed:
            self.watches.pop(w.pattern.signature(), None)
        return removed

    @property
    def active(self) -> List[PatternWatch]:
        return [w for w in self.watches.values() if w.alive]

    # -- per-bar processing ------------------------------------------------

    def on_pattern_bar(self) -> None:
        """Age armed formations on the pattern screen; expire the stale ones.

        Ch VI-B grades an unbroken formation as watchlist-only; it cannot stay
        on the watchlist forever.
        """
        cfg = self.config.breakout
        for watch in list(self.watches.values()):
            if watch.state == ARMED:
                watch.bars_armed += 1
                if watch.bars_armed > cfg.pattern_expiry_bars:
                    watch.kill("expired_without_break")

    def on_entry_bar(
        self,
        candles: Sequence[Candle],
        timeframe: str,
        context_candles: Optional[Sequence[Candle]] = None,
    ) -> List[Tuple[PatternWatch, str]]:
        """Advance every watch against the newest closed entry-screen bar.

        Returns ``(watch, transition)`` pairs, where ``transition`` is one of
        ``broken`` / ``retested`` / ``ready`` / ``dead``.
        """
        cfg = self.config.breakout
        entry_cfg = self.config.entry
        transitions: List[Tuple[PatternWatch, str]] = []
        if not candles:
            return transitions

        atr_value = atr_of(candles, self.config.atr_period) or 0.0
        if atr_value <= 0:
            return transitions
        candle = candles[-1]

        for watch in list(self.watches.values()):
            if not watch.alive:
                continue
            pattern = watch.pattern

            if watch.state == ARMED:
                event, reason = evaluate_breakout(
                    pattern, candles, atr_value, cfg, timeframe, context_candles
                )
                if event is not None:
                    watch.breakout = event
                    watch.state = BROKEN
                    watch.bars_since_break = 0
                    pattern.state = "confirmed"
                    if event.gapped and len(candles) >= 2:
                        watch.gap_origin = candles[-2].close
                    transitions.append((watch, BROKEN))
                elif reason:
                    pattern.notes.append(f"false breakout filtered: {reason} (Ch V-B)")
                continue

            # Past the break: age it, and police the invalidation conditions.
            watch.bars_since_break += 1

            if self._invalidated(watch, candle, atr_value):
                transitions.append((watch, DEAD))
                continue

            if watch.state == BROKEN:
                if watch.bars_since_break > cfg.retest_max_bars:
                    # No retest arrived. Method A has already had its chance.
                    watch.kill("retest_window_expired")
                    transitions.append((watch, DEAD))
                    continue
                retest = evaluate_retest(watch, candles, atr_value, cfg, entry_cfg)
                if retest is not None:
                    watch.retest = retest
                    watch.state = RETESTED
                    transitions.append((watch, RETESTED))
                continue

            if watch.state == RETESTED and watch.retest is not None:
                # Ch VII Method C — wait for the follow-through candle.
                if is_followthrough(
                    watch.retest.candle, candle, bullish=not pattern.is_bearish
                ):
                    watch.retest.followthrough = True
                    watch.retest.followthrough_candle = candle
                    watch.state = READY
                    transitions.append((watch, READY))
                elif watch.bars_since_break > cfg.retest_max_bars:
                    watch.kill("followthrough_window_expired")
                    transitions.append((watch, DEAD))
                continue

        return transitions

    def _invalidated(
        self, watch: PatternWatch, candle: Candle, atr_value: float
    ) -> bool:
        """Ch V-B / Ch XIV-B — the structural flip failed."""
        cfg = self.config.breakout
        pattern = watch.pattern
        level = pattern.trigger_price_at_ts(candle.ts)
        # Never call the flip failed while price is still inside the retest
        # zone — see the note on retest_invalidation_buffer_atr.
        buffer = (
            max(cfg.retest_invalidation_buffer_atr, cfg.retest_tolerance_atr * 1.2)
            * atr_value
        )

        if cfg.retest_invalidate_on_close_back:
            back_inside = (
                candle.close > level + buffer
                if pattern.is_bearish
                else candle.close < level - buffer
            )
            if back_inside:
                watch.kill("invalidated_close_back_through_level")
                return True

        # Ch XIV-B: "Full gap fill = invalidation."
        if watch.gap_origin is not None:
            filled = (
                candle.high >= watch.gap_origin
                if pattern.is_bearish
                else candle.low <= watch.gap_origin
            )
            if filled:
                watch.kill("invalidated_full_gap_fill")
                return True

        # The formation itself failing is terminal regardless of the retest.
        beyond_structure = (
            candle.close > pattern.structural_invalidation
            if pattern.is_bearish
            else candle.close < pattern.structural_invalidation
        )
        if beyond_structure:
            watch.kill("invalidated_structural_level_broken")
            return True
        return False


def _precedence(pattern: Pattern) -> Tuple[int, float]:
    """Ch IX ordering for competing reads of the same price action."""
    from titan_tfbs.core.candles import TimeFrame

    try:
        minutes = TimeFrame.parse(pattern.timeframe).minutes
    except ValueError:      # pragma: no cover - defensive
        minutes = 0
    return minutes, pattern.quality


def method_ready_state(method: EntryMethod) -> str:
    """The watch state at which each Ch VII entry method fires."""
    return {
        EntryMethod.A_AGGRESSIVE: BROKEN,
        EntryMethod.B_STANDARD: RETESTED,
        EntryMethod.C_CONSERVATIVE: READY,
    }[method]
