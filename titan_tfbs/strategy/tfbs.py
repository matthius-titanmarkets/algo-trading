"""The Unified TFBS Framework — the decision pipeline of Ch VI-A.

    1. SCAN     Identify developing H&S or DT/DB formations on the primary TF
    2. VALIDATE Apply quality filters. Fail = discard.
    3. CONFIRM  Wait for breakout confirmation. Do not anticipate.
    4. SCORE    Apply the Confluence Scoring System. Minimum: 7/10.
    5. SIZE     Calculate position size. Define SL, TP1, TP2, TP3.
    6. EXECUTE  Enter using an approved entry method.
    7. MANAGE   Follow the exit architecture. Log the trade.

This module owns steps 1-4 and the level construction for step 5.  Sizing,
the checklist and execution are applied by :class:`titan_tfbs.bot.TFBSBot`,
which owns the account.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from titan_tfbs.config import EntryMethod, TitanConfig
from titan_tfbs.core.candles import Candle, MultiTimeframeStore
from titan_tfbs.core.indicators import atr as atr_of
from titan_tfbs.core.structure import Level, find_levels, nearest_level_beyond
from titan_tfbs.instruments import Instrument, get_instrument
from titan_tfbs.patterns.base import Pattern
from titan_tfbs.patterns.detector import PatternDetector
from titan_tfbs.strategy import mtf
from titan_tfbs.strategy.breakout import (
    BROKEN,
    DEAD,
    READY,
    RETESTED,
    BreakoutTracker,
    PatternWatch,
    method_ready_state,
)
from titan_tfbs.strategy.confluence import score_setup
from titan_tfbs.strategy.mtf import MTFAlignment
from titan_tfbs.strategy.signals import (
    BreakoutEvent,
    ConfluenceScore,
    Direction,
    Grade,
    TradeSignal,
)


@dataclass
class SetupEvaluation:
    """One setup that reached at least the CONFIRM stage.

    Ch XII-A4 requires that skipped trades are journalled too, so rejected
    setups are returned rather than silently dropped.
    """

    symbol: str
    ts: datetime
    accepted: bool
    reason: str = ""
    signal: Optional[TradeSignal] = None
    pattern: Optional[Pattern] = None
    score: Optional[ConfluenceScore] = None
    alignment: Optional[MTFAlignment] = None
    grade: Optional[Grade] = None

    def summary(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "ts": self.ts.isoformat(),
            "accepted": self.accepted,
            "reason": self.reason,
            "grade": self.grade.value if self.grade else None,
            "score": self.score.total if self.score else None,
            "pattern": self.pattern.type.value if self.pattern else None,
            "signal": self.signal.summary() if self.signal else None,
        }


@dataclass
class SymbolState:
    """Per-symbol working state carried between bars."""

    symbol: str
    instrument: Instrument
    tracker: BreakoutTracker
    alignment: Optional[MTFAlignment] = None
    levels: Dict[str, List[Level]] = field(default_factory=dict)
    last_pattern_scan: Dict[str, Optional[datetime]] = field(default_factory=dict)
    watchlist_notes: List[str] = field(default_factory=list)


class TFBSStrategy:
    """The firm's sole approved strategy (Ch XII-A7: "One Strategy")."""

    def __init__(self, config: TitanConfig) -> None:
        self.config = config
        self.detector = PatternDetector(config)
        self.states: Dict[str, SymbolState] = {}

    # -- state -------------------------------------------------------------

    def state_for(self, symbol: str) -> SymbolState:
        if symbol not in self.states:
            self.states[symbol] = SymbolState(
                symbol=symbol,
                instrument=get_instrument(symbol),
                tracker=BreakoutTracker(self.config),
            )
        return self.states[symbol]

    def entry_method(self) -> EntryMethod:
        """Ch VII / Ch XII-B — the method this account is required to use."""
        return self.config.required_entry_method()

    # -- main pipeline -----------------------------------------------------

    def on_candles(
        self,
        symbol: str,
        store: MultiTimeframeStore,
        closed: Dict[str, Candle],
        now: datetime,
    ) -> List[SetupEvaluation]:
        """Process the timeframes that just closed and return any setups."""
        state = self.state_for(symbol)
        instrument = state.instrument
        profile = instrument.timeframes
        results: List[SetupEvaluation] = []

        # ---- Screens 1 & 2 refresh (higher timeframes drive the research) --
        pattern_tf_closed = [tf for tf in profile.pattern if tf in closed]
        trend_tf_closed = [tf for tf in profile.trend if tf in closed]

        if pattern_tf_closed or trend_tf_closed or state.alignment is None:
            state.alignment = mtf.analyze(
                store, self.config, profile.trend, profile.primary_pattern_tf
            )

        for tf in pattern_tf_closed:
            self._rescan(state, store, tf, now)
        if pattern_tf_closed:
            state.tracker.on_pattern_bar()

        # ---- Screen 3: the precision trigger -------------------------------
        entry_tf = profile.primary_entry_tf
        if entry_tf not in closed:
            state.tracker.prune()
            return results

        entry_series = store.get(entry_tf)
        if entry_series is None or len(entry_series) < self.config.atr_period + 2:
            return results

        entry_candles = list(entry_series)
        pattern_series = store.get(profile.primary_pattern_tf)
        context = list(pattern_series) if pattern_series is not None else None

        transitions = state.tracker.on_entry_bar(entry_candles, entry_tf, context)
        required_state = method_ready_state(self.entry_method())

        for watch, transition in transitions:
            if transition == DEAD:
                results.append(
                    SetupEvaluation(
                        symbol=symbol,
                        ts=now,
                        accepted=False,
                        reason=watch.dead_reason or "formation invalidated",
                        pattern=watch.pattern,
                    )
                )
                continue
            if transition != required_state:
                # A valid step forward, but not this account's entry trigger.
                continue
            evaluation = self._evaluate(state, store, watch, entry_candles, entry_tf, now)
            results.append(evaluation)
            if evaluation.accepted:
                watch.consumed = True

        state.tracker.prune()
        return results

    # -- SCAN + VALIDATE ---------------------------------------------------

    def _rescan(
        self,
        state: SymbolState,
        store: MultiTimeframeStore,
        timeframe: str,
        now: datetime,
    ) -> None:
        series = store.get(timeframe)
        if series is None:
            return
        result = self.detector.scan(series)
        state.levels[timeframe] = find_levels(
            list(series),
            pivots=result.pivots,
            atr_period=self.config.atr_period,
            swing_lookback=self.config.swing.lookback,
        )
        state.last_pattern_scan[timeframe] = now
        for pattern in result.patterns:
            registered = state.tracker.register(pattern, now)
            if registered is not None:
                # Ch VI-B: a formation with an intact neckline is watchlist only.
                state.watchlist_notes.append(
                    f"{now.isoformat()} watchlist: {pattern.type.value} on {timeframe}"
                )

    # -- CONFIRM + SCORE + level construction ------------------------------

    def _evaluate(
        self,
        state: SymbolState,
        store: MultiTimeframeStore,
        watch: PatternWatch,
        entry_candles: Sequence[Candle],
        entry_tf: str,
        now: datetime,
    ) -> SetupEvaluation:
        cfg = self.config
        pattern = watch.pattern
        breakout = watch.breakout
        assert breakout is not None  # guaranteed by the tracker state machine

        direction = Direction.SHORT if pattern.is_bearish else Direction.LONG
        alignment = state.alignment or mtf.analyze(
            store, cfg, state.instrument.timeframes.trend
        )

        atr_entry = atr_of(entry_candles, cfg.atr_period) or 0.0
        if atr_entry <= 0:
            return SetupEvaluation(
                state.symbol, now, False, "no ATR on the entry screen", pattern=pattern
            )

        method = self.entry_method()
        entry_price = self._entry_price(watch, method, entry_candles)
        stop_loss, stop_note = self._stop_loss(
            watch, method, entry_price, atr_entry, state.instrument
        )
        if stop_loss is None:
            return SetupEvaluation(state.symbol, now, False, stop_note, pattern=pattern)

        stop_distance = abs(entry_price - stop_loss)
        if stop_distance > cfg.entry.max_stop_atr * atr_entry:
            return SetupEvaluation(
                state.symbol,
                now,
                False,
                f"stop distance {stop_distance / atr_entry:.2f} ATR exceeds the "
                f"{cfg.entry.max_stop_atr:.1f} ATR ceiling — measured move cannot pay 2R",
                pattern=pattern,
            )

        levels = state.levels.get(pattern.timeframe, [])
        tp1, tp2, tp3 = self._targets(
            pattern, breakout, direction, entry_price, stop_distance, levels, atr_entry
        )

        rr = abs(tp1 - entry_price) / stop_distance if stop_distance > 0 else 0.0

        score = score_setup(
            pattern=pattern,
            breakout=breakout,
            retest=watch.retest,
            direction=direction,
            htf_points=alignment.alignment_points(direction, cfg.mtf),
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            levels=levels,
            atr_value=atr_entry,
            cfg=cfg.confluence,
            risk_cfg=cfg.risk,
        )
        grade = score.grade(
            cfg.confluence.elite_score,
            cfg.confluence.approved_score,
            cfg.confluence.watchlist_score,
        )

        signal = TradeSignal(
            symbol=state.symbol,
            direction=direction,
            pattern=pattern,
            breakout=breakout,
            entry_method=method,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            risk_reward=rr,
            score=score,
            grade=grade,
            created_ts=now,
            trend_timeframe=state.instrument.timeframes.trend[0],
            pattern_timeframe=pattern.timeframe,
            entry_timeframe=entry_tf,
            retest=watch.retest,
            notes=[stop_note] if stop_note else [],
        )
        signal.notes.extend(breakout.notes)

        # ---- Ch VIII-A hard limit: below 2:1 is an auto-skip ---------------
        if rr < cfg.risk.min_reward_risk:
            return SetupEvaluation(
                state.symbol,
                now,
                False,
                f"R:R {rr:.2f}:1 is below the firm minimum of "
                f"{cfg.risk.min_reward_risk:.0f}:1 (Ch VIII-A) — skip the trade",
                signal=signal,
                pattern=pattern,
                score=score,
                alignment=alignment,
                grade=Grade.DISCARD,
            )

        # ---- Ch XI gate: 7/10 or higher ------------------------------------
        if not grade.tradeable:
            return SetupEvaluation(
                state.symbol,
                now,
                False,
                f"confluence {score.total}/10 -> {grade.value}; the firm approves "
                f"{cfg.confluence.min_score_to_trade}/10 or higher (Ch XI)",
                signal=signal,
                pattern=pattern,
                score=score,
                alignment=alignment,
                grade=grade,
            )

        # ---- Ch IX: counter-trend setups are blocked by Appendix A ---------
        if cfg.mtf.block_counter_trend and alignment.is_counter_trend(direction):
            return SetupEvaluation(
                state.symbol,
                now,
                False,
                f"counter-trend to the {alignment.bias.value} higher-timeframe bias "
                f"(Ch IX / Appendix A)",
                signal=signal,
                pattern=pattern,
                score=score,
                alignment=alignment,
                grade=grade,
            )

        return SetupEvaluation(
            symbol=state.symbol,
            ts=now,
            accepted=True,
            signal=signal,
            pattern=pattern,
            score=score,
            alignment=alignment,
            grade=grade,
        )

    # -- Ch VII entry and stop construction --------------------------------

    def _entry_price(
        self, watch: PatternWatch, method: EntryMethod, candles: Sequence[Candle]
    ) -> float:
        """Ch VII — where each method takes its fill."""
        if method is EntryMethod.A_AGGRESSIVE:
            # "Enter immediately on the candle close that confirms the breakout."
            return watch.breakout.candle.close
        if method is EntryMethod.B_STANDARD and watch.retest is not None:
            # "Enter on rejection signal (pin bar, engulfing, strong wick)."
            return watch.retest.candle.close
        if method is EntryMethod.C_CONSERVATIVE and watch.retest is not None:
            candle = watch.retest.followthrough_candle or watch.retest.candle
            return candle.close
        return candles[-1].close

    def _stop_loss(
        self,
        watch: PatternWatch,
        method: EntryMethod,
        entry_price: float,
        atr_value: float,
        instrument: Instrument,
    ) -> tuple:
        """Ch VII / Ch X-A — the structural stop for each entry method."""
        cfg = self.config.entry
        pattern = watch.pattern
        sign = -1 if pattern.is_bearish else 1
        buffer = cfg.stop_buffer_atr * atr_value
        note = ""

        # Method A: beyond the last swing point — the right shoulder for H&S,
        # the second peak/trough for DT/DB.
        structural = pattern.structural_invalidation - sign * buffer

        if method is EntryMethod.A_AGGRESSIVE or watch.retest is None:
            stop = structural
            note = "stop beyond the formation's structural invalidation (Ch VII Method A)"
        else:
            # Methods B and C: "just beyond retest wick".
            stop = watch.retest.wick_price - sign * buffer
            note = "stop just beyond the retest wick (Ch VII Method B/C)"
            if cfg.enforce_structural_stop_floor:
                stop = min(stop, structural) if sign > 0 else max(stop, structural)
                note = "stop forced out to structural invalidation (config)"

        # A stop on the wrong side of entry is not a stop.
        if (sign > 0 and stop >= entry_price) or (sign < 0 and stop <= entry_price):
            return None, "stop resolved to the wrong side of the entry price"

        distance = abs(entry_price - stop)
        floor = max(
            cfg.min_stop_atr * atr_value,
            cfg.min_stop_spread_multiple * instrument.typical_spread,
            cfg.min_stop_pattern_atr_frac * pattern.atr,
        )
        if distance < floor:
            stop = entry_price - sign * floor
            note += (
                f"; widened to the {floor / atr_value:.2f} ATR / "
                f"{cfg.min_stop_spread_multiple:.0f}x spread minimum"
            )
        return stop, note

    # -- Ch X-B target construction ----------------------------------------

    def _targets(
        self,
        pattern: Pattern,
        breakout: BreakoutEvent,
        direction: Direction,
        entry_price: float,
        stop_distance: float,
        levels: Sequence[Level],
        atr_value: float,
    ) -> tuple:
        """Build the Ch X-B ladder.

        TP1 is the measured move, anchored to the break price exactly as
        Ch III-D and Ch IV-D specify; TP2 is the next significant S/R beyond
        it; TP3 is the trailed runner, projected at twice the pattern height
        for reporting.
        """
        cfg = self.config.trade_management
        sign = direction.sign

        tp1 = pattern.measured_target(breakout.level)

        # TP2 — "next significant S/R beyond TP1".
        beyond = nearest_level_beyond(
            levels, tp1, sign, min_distance=0.5 * atr_value
        )
        if beyond is not None:
            tp2 = beyond.price
        else:
            tp2 = entry_price + sign * cfg.tp2_fallback_r * stop_distance
        # Never let TP2 sit behind TP1.
        if (tp2 - tp1) * sign <= 0:
            tp2 = entry_price + sign * max(
                cfg.tp2_fallback_r * stop_distance,
                abs(tp1 - entry_price) + atr_value,
            )

        # TP3 — trailed; the projection is a reporting aid only.
        tp3 = breakout.level + sign * 2.0 * pattern.measured_height
        if (tp3 - tp2) * sign <= 0:
            tp3 = tp2 + sign * atr_value
        return tp1, tp2, tp3

    # -- reporting ---------------------------------------------------------

    def watchlist(self, symbol: str) -> List[Dict[str, object]]:
        """Ch VI-B — formations awaiting a break."""
        state = self.states.get(symbol)
        if state is None:
            return []
        return [
            {**w.pattern.summary(), "watch_state": w.state}
            for w in state.tracker.active
        ]
