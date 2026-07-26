"""Pattern scanning and validation — TFBS Ch VI-A steps 1 (SCAN) and 2 (VALIDATE).

    "1. SCAN — Identify developing H&S or DT/DB formations on the primary
     timeframe.
     2. VALIDATE — Apply quality filters (Ch III-E for H&S, Ch IV-C for
     DT/DB). Fail = discard."                                 — TFBS Ch VI-A
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from titan_tfbs.config import TitanConfig
from titan_tfbs.core.candles import CandleSeries
from titan_tfbs.core.indicators import atr
from titan_tfbs.core.structure import Pivot, find_pivots
from titan_tfbs.patterns.base import Pattern, PatternType, overlap_bars
from titan_tfbs.patterns.double_top import detect_double_tops
from titan_tfbs.patterns.head_shoulders import detect_head_shoulders

#: DERIVED — how close (in bars) an H&S right shoulder and a Double Top second
#: peak must be to count as the Ch VI-B "H&S + DT/DB Overlap" A+ setup.
_DUAL_OVERLAP_TOLERANCE_BARS = 3


@dataclass
class ScanResult:
    """Everything the scan produced for one symbol/timeframe."""

    symbol: str
    timeframe: str
    patterns: List[Pattern]
    pivots: List[Pivot]
    atr: float

    @property
    def best(self) -> Optional[Pattern]:
        if not self.patterns:
            return None
        return max(self.patterns, key=lambda p: (p.quality_points, p.quality, p.end_index))


class PatternDetector:
    """Runs both pattern modules over a timeframe and cross-references them."""

    def __init__(self, config: TitanConfig) -> None:
        self.config = config

    def scan(self, series: CandleSeries, max_candidates: int = 4) -> ScanResult:
        """SCAN + VALIDATE one timeframe."""
        candles = list(series)
        cfg = self.config
        symbol, timeframe = series.symbol, str(series.timeframe)

        if len(candles) < max(
            cfg.head_shoulders.min_prior_trend_bars + cfg.head_shoulders.min_formation_bars,
            cfg.atr_period * 3,
        ):
            return ScanResult(symbol, timeframe, [], [], 0.0)

        atr_value = atr(candles, cfg.atr_period) or 0.0
        if atr_value <= 0:
            return ScanResult(symbol, timeframe, [], [], 0.0)

        pivots = find_pivots(
            candles,
            lookback=cfg.swing.lookback,
            min_swing=cfg.swing.min_swing_atr * atr_value,
        )

        hs = detect_head_shoulders(
            candles, pivots, cfg.head_shoulders, atr_value, symbol, timeframe, max_candidates
        )
        dt = detect_double_tops(
            candles, pivots, cfg.double_top, atr_value, symbol, timeframe, max_candidates
        )

        patterns = [*hs, *dt]
        # Re-express every neckline in wall-clock time so the entry screen can
        # evaluate it (Ch IX: Screen 2 finds the level, Screen 3 trades it).
        tf_minutes = series.timeframe.minutes
        for p in patterns:
            p.set_time_anchor(candles[p.end_index].ts, tf_minutes)
            self._assess_macro_context(candles, p)
        self._mark_dual_patterns(hs, dt)
        patterns.sort(key=lambda p: (p.end_index, p.quality), reverse=True)
        return ScanResult(symbol, timeframe, patterns[: max_candidates * 2], pivots, atr_value)

    def scan_screens(
        self, store, timeframes: Sequence[str], max_candidates: int = 4
    ) -> Dict[str, ScanResult]:
        """Scan every Screen 2 timeframe and cross-reference for fractals."""
        results: Dict[str, ScanResult] = {}
        for tf in timeframes:
            series = store.get(tf)
            if series is None or len(series) == 0:
                continue
            results[tf] = self.scan(series, max_candidates)
        self._mark_fractals(results)
        return results

    def _assess_macro_context(self, candles: Sequence, pattern: Pattern) -> None:
        """Ch V-B — "Breakout inside a wider range = low follow-through".

        Measured on the formation itself: a reversal pattern only carries
        information when its apex caps the enclosing range.  Only bars up to
        the formation's end are considered, so the post-break move — which
        necessarily extends the range — cannot make a good setup look choppy.
        """
        cfg = self.config.breakout
        lo = max(0, pattern.end_index - cfg.choppy_context_lookback)
        window = candles[lo : pattern.end_index + 1]
        if len(window) < 20:
            pattern.choppy_context = False
            return
        hi = max(c.high for c in window)
        low = min(c.low for c in window)
        span = hi - low
        if span <= 0:
            pattern.choppy_context = False
            return
        apex = pattern.apex_price
        distance = (hi - apex) / span if pattern.is_bearish else (apex - low) / span
        pattern.context_position = distance
        pattern.choppy_context = distance > cfg.choppy_apex_tolerance
        if pattern.choppy_context:
            pattern.notes.append(
                f"apex sits {distance:.0%} inside the enclosing range — choppy "
                f"macro context (Ch V-B)"
            )

    # -- cross-referencing -------------------------------------------------

    @staticmethod
    def _mark_dual_patterns(hs: Sequence[Pattern], dt: Sequence[Pattern]) -> None:
        """Ch VI-B: the A+ ELITE dual-pattern setup.

            "The A+ (ELITE) grade — where an H&S right shoulder coincides with
             the second peak of a Double Top — is the highest-conviction TFBS
             setup."
        """
        for h in hs:
            rs_index = h.end_index          # the right shoulder closes the H&S
            for d in dt:
                if d.is_bearish != h.is_bearish:
                    continue
                if abs(d.end_index - rs_index) > _DUAL_OVERLAP_TOLERANCE_BARS:
                    continue
                if overlap_bars(h, d) <= 0:
                    continue
                h.dual_pattern = d.type.value
                d.dual_pattern = h.type.value
                note = f"A+ dual-pattern overlap: {h.type.value} + {d.type.value} (Ch VI-B)"
                if note not in h.notes:
                    h.notes.append(note)
                if note not in d.notes:
                    d.notes.append(note)

    @staticmethod
    def _mark_fractals(results: Dict[str, ScanResult]) -> None:
        """Ch XIV-B fractal case — same-direction formations on two screens.

            "H&S within H&S (fractal): Small H&S forming right shoulder of
             larger H&S. Valid, high-confluence. Score independently."
        """
        timeframes = list(results.keys())
        for i, tf_a in enumerate(timeframes):
            for tf_b in timeframes[i + 1 :]:
                for pa in results[tf_a].patterns:
                    for pb in results[tf_b].patterns:
                        if pa.is_bearish != pb.is_bearish:
                            continue
                        pa.fractal_confluence = f"{pb.type.value}@{tf_b}"
                        pb.fractal_confluence = f"{pa.type.value}@{tf_a}"


def describe(patterns: Sequence[Pattern]) -> str:  # pragma: no cover - reporting
    if not patterns:
        return "no valid TFBS formations"
    return "; ".join(
        f"{p.type.value} {p.timeframe} q={p.quality_points}/2 "
        f"trigger={p.trigger_line.value_at(p.end_index):.5f}"
        for p in patterns
    )
