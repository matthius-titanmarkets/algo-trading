"""Trade Management & Exit Architecture — TFBS Ch X.

    A. Stop-Loss Protocol
       Initial:    Beyond structural invalidation
       Breakeven:  Move SL to BE when price moves 1R in your favor
       Trailing:   After 1.5R, trail using most recent swing or 20-EMA

    B. Take-Profit Architecture
       TP1  50%  Measured move target
       TP2  30%  Next significant S/R beyond TP1
       TP3  20%  Trailed — exits only when trend structure breaks

    C. Early Exit Triggers
       - Price stalls at major S/R not in the pre-trade plan
       - High-impact news imminent and not yet at breakeven
       - Trade open 3x expected duration without reaching TP1
       - Counter-pattern forming on lower TF
       - Technical invalidation — setup thesis no longer valid

The Risk Management Guide adds the discipline that pairs with this: *"Never
move a stop to breakeven prematurely — respect the original thesis."*  The
breakeven move therefore fires at 1R and not before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence

from titan_tfbs.config import TradeManagementConfig
from titan_tfbs.core.candles import Candle
from titan_tfbs.core.indicators import ema
from titan_tfbs.core.structure import Level, swing_stop_price
from titan_tfbs.execution.broker import Broker
from titan_tfbs.execution.orders import ExitReason, Fill, Position, TakeProfitLeg


@dataclass
class TradeContext:
    """Everything Ch X needs to manage a position on one bar."""

    candle: Candle
    atr: float
    #: Recent entry-TF candles, for the swing-based trail (Ch X-A).
    recent_candles: Sequence[Candle] = ()
    #: HTF levels, for the Ch X-C "stalls at major S/R" trigger.
    levels: Sequence[Level] = ()
    #: Ch XII-A6 / Ch X-C — a high-impact release is imminent.
    news_imminent: bool = False
    #: Ch X-C — a counter-pattern has formed against the position.
    counter_pattern: bool = False
    #: Ch X-C — the setup thesis has been technically invalidated.
    invalidated: bool = False
    invalidation_detail: str = ""
    #: True when this bar is an entry-screen close. Stops and targets are
    #: checked on every base bar for fill realism, but the Ch X-C duration
    #: rule counts entry-screen bars.
    entry_tf_close: bool = True


class TradeManager:
    """Applies the Ch X rules to every open position, bar by bar."""

    def __init__(
        self,
        config: TradeManagementConfig,
        broker: Broker,
        pessimistic_intrabar: bool = True,
        on_stop_moved: Optional[Callable[[Position, float], None]] = None,
    ) -> None:
        self.cfg = config
        self.broker = broker
        self.pessimistic = pessimistic_intrabar
        self.on_stop_moved = on_stop_moved

    # -- main entry point --------------------------------------------------

    def update(self, position: Position, ctx: TradeContext) -> List[Fill]:
        """Advance one position by one bar. Returns any fills generated."""
        if not position.is_open:
            return []

        candle = ctx.candle
        if ctx.entry_tf_close:
            position.bars_open += 1
        self._track_excursion(position, candle)

        fills: List[Fill] = []

        # Order matters. Assume the worst about intrabar sequencing: the stop
        # is tested before any target on the same bar (Ch VIII: risk first).
        if self.pessimistic:
            fills.extend(self._check_stop(position, ctx))
            if not position.is_open:
                return fills
            fills.extend(self._check_targets(position, ctx))
        else:
            fills.extend(self._check_targets(position, ctx))
            if position.is_open:
                fills.extend(self._check_stop(position, ctx))
        if not position.is_open:
            return fills

        # Ch X-C early exits are evaluated on the close, after the bar's
        # stop/target levels have had their chance.
        early = self._check_early_exits(position, ctx)
        if early is not None:
            fills.append(early)
            return fills

        # Ch X-A stop protocol runs last so a stop moved this bar cannot be
        # retroactively hit by the same bar's range.
        self._manage_stop(position, ctx)
        return fills

    # -- Ch X-A stop protocol ---------------------------------------------

    def _check_stop(self, position: Position, ctx: TradeContext) -> List[Fill]:
        candle = ctx.candle
        hit = (
            candle.low <= position.stop_loss
            if position.sign > 0
            else candle.high >= position.stop_loss
        )
        if not hit:
            return []
        reason = ExitReason.STOP_LOSS
        if position.trailing_active:
            reason = ExitReason.TRAILING_STOP
        elif position.breakeven_done:
            reason = ExitReason.BREAKEVEN_STOP
        fill = self.broker.close_position(
            position, position.size, position.stop_loss, candle.ts, reason
        )
        return [fill] if fill else []

    def _manage_stop(self, position: Position, ctx: TradeContext) -> None:
        r_now = position.max_favorable_r

        # Breakeven at 1R (Ch X-A) — and never before (RMG s.03).
        if not position.breakeven_done and r_now >= self.cfg.breakeven_at_r:
            be_price = position.price_at_r(self.cfg.breakeven_offset_r)
            if self._move_stop(position, be_price):
                position.breakeven_done = True
                position.notes.append(
                    f"stop to breakeven at {self.cfg.breakeven_at_r:.1f}R (Ch X-A)"
                )

        # Trail after 1.5R (Ch X-A) using the recent swing or the 20-EMA.
        if r_now >= self.cfg.trail_start_r:
            trail = self._trail_price(position, ctx)
            if trail is not None and self._move_stop(position, trail):
                if not position.trailing_active:
                    position.notes.append(
                        f"trailing engaged at {self.cfg.trail_start_r:.1f}R "
                        f"({self.cfg.trail_method}, Ch X-A)"
                    )
                position.trailing_active = True

    def _trail_price(self, position: Position, ctx: TradeContext) -> Optional[float]:
        if self.cfg.trail_method == "ema":
            closes = [c.close for c in ctx.recent_candles]
            value = ema(closes, self.cfg.trail_ema_period)
            if value is None:
                return None
            buffer = self.cfg.trail_swing_buffer_atr * ctx.atr
            return value - position.sign * buffer
        # Default: most recent swing (Ch X-A).
        lookback = max(5, self.cfg.trail_ema_period // 2)
        return swing_stop_price(
            ctx.recent_candles,
            bullish=position.sign > 0,
            lookback=lookback,
            buffer=self.cfg.trail_swing_buffer_atr * ctx.atr,
        )

    def _move_stop(self, position: Position, price: float) -> bool:
        moved = self.broker.modify_stop(position, price)
        if moved and self.on_stop_moved is not None:
            self.on_stop_moved(position, position.stop_loss)
        return moved

    # -- Ch X-B take-profit ladder ----------------------------------------

    def _check_targets(self, position: Position, ctx: TradeContext) -> List[Fill]:
        candle = ctx.candle
        fills: List[Fill] = []
        for leg in position.legs:
            if not leg.active or not position.is_open:
                continue
            reached = (
                candle.high >= leg.price if position.sign > 0 else candle.low <= leg.price
            )
            if not reached:
                continue
            size = self._leg_size(position, leg)
            if size <= 0:
                leg.filled = True
                continue
            fill = self.broker.close_position(
                position, size, leg.price, candle.ts, leg.reason, note=leg.name
            )
            if fill:
                leg.filled = True
                fills.append(fill)
        return fills

    def _leg_size(self, position: Position, leg: TakeProfitLeg) -> float:
        """Size for one rung, rounded to a tradeable increment.

        The final rung always takes whatever remains so no dust is stranded.
        """
        remaining_legs = [l for l in position.legs if l.active]
        if len(remaining_legs) <= 1 or leg.name == "TP3":
            return position.size
        raw = position.initial_size * leg.allocation
        size = position.instrument.round_size_down(raw)
        if size <= 0:
            # Position too small to split: run it to the final target instead.
            return 0.0
        return min(size, position.size)

    # -- Ch X-C early exit triggers ---------------------------------------

    def _check_early_exits(self, position: Position, ctx: TradeContext) -> Optional[Fill]:
        candle = ctx.candle
        tp1 = position.leg("TP1")
        tp1_hit = bool(tp1 and tp1.filled)

        # "Technical invalidation — setup thesis no longer valid."
        if self.cfg.exit_on_structural_invalidation and ctx.invalidated:
            return self.broker.close_position(
                position, position.size, candle.close, candle.ts,
                ExitReason.EARLY_INVALIDATION, note=ctx.invalidation_detail,
            )

        # "High-impact news imminent and not yet at breakeven."
        if (
            self.cfg.exit_on_news_if_not_breakeven
            and ctx.news_imminent
            and not position.breakeven_done
        ):
            return self.broker.close_position(
                position, position.size, candle.close, candle.ts, ExitReason.EARLY_NEWS
            )

        # "Counter-pattern forming on lower TF."
        if self.cfg.exit_on_counter_pattern and ctx.counter_pattern and not tp1_hit:
            return self.broker.close_position(
                position, position.size, candle.close, candle.ts,
                ExitReason.EARLY_COUNTER_PATTERN,
            )

        # "Trade open 3x expected duration without reaching TP1."
        max_bars = self.cfg.expected_duration_bars * self.cfg.max_duration_multiple
        if not tp1_hit and position.bars_open >= max_bars:
            return self.broker.close_position(
                position, position.size, candle.close, candle.ts,
                ExitReason.EARLY_DURATION,
                note=f"{position.bars_open} bars open vs {max_bars:.0f} allowed",
            )

        # "Price stalls at major S/R not in the pre-trade plan."
        if not tp1_hit and self._stalled_at_level(position, ctx):
            return self.broker.close_position(
                position, position.size, candle.close, candle.ts, ExitReason.EARLY_STALL
            )
        return None

    def _stalled_at_level(self, position: Position, ctx: TradeContext) -> bool:
        """Price parked against an unplanned level while in profit but pre-TP1."""
        if not ctx.levels or ctx.atr <= 0:
            return False
        if position.max_favorable_r < 0.5 or position.bars_open < 6:
            return False
        tp1 = position.leg("TP1")
        planned = tp1.price if tp1 and tp1.price is not None else None
        price = ctx.candle.close
        tolerance = 0.5 * ctx.atr
        for level in ctx.levels:
            ahead = (level.price - price) * position.sign > 0
            if not ahead or abs(level.price - price) > tolerance:
                continue
            if planned is not None and abs(level.price - planned) <= tolerance:
                continue   # this level is the plan
            opposing = level.is_resistance if position.sign > 0 else not level.is_resistance
            if opposing and level.touches >= 2:
                return True
        return False

    # -- bookkeeping -------------------------------------------------------

    @staticmethod
    def _track_excursion(position: Position, candle: Candle) -> None:
        if position.r_unit <= 0:
            return
        best = candle.high if position.sign > 0 else candle.low
        worst = candle.low if position.sign > 0 else candle.high
        position.max_favorable_r = max(position.max_favorable_r, position.r_at(best))
        position.max_adverse_r = min(position.max_adverse_r, position.r_at(worst))

    def force_close(
        self, position: Position, price: float, ts: datetime, reason: ExitReason
    ) -> Optional[Fill]:
        return self.broker.close_position(position, position.size, price, ts, reason)
