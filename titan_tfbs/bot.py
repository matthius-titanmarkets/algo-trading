"""The TFBS bot — the full Ch VI-A pipeline wired to an account.

The bot owns the parts of the pipeline that require account state:

    step 5 SIZE     -> :class:`titan_tfbs.risk.RiskManager`
    Appendix A      -> :mod:`titan_tfbs.strategy.checklist`
    step 6 EXECUTE  -> :class:`titan_tfbs.execution.Broker`
    step 7 MANAGE   -> :class:`titan_tfbs.execution.TradeManager`
    Ch XIII         -> :class:`titan_tfbs.journal.TradeJournal`

Feed it base-timeframe candles with :meth:`on_candle` and it does the rest:
scans the higher timeframes for formations, waits for a confirmed break, scores
the setup, runs the pre-trade checklist, sizes the position against the firm's
limits, executes, and manages the trade to the Ch X exit architecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

from titan_tfbs.config import TitanConfig
from titan_tfbs.core.candles import Candle, MultiTimeframeStore, TimeFrame
from titan_tfbs.core.indicators import atr as atr_of
from titan_tfbs.data.news import EconomicCalendar
from titan_tfbs.execution.broker import Broker, PaperBroker
from titan_tfbs.execution.manager import TradeContext, TradeManager
from titan_tfbs.execution.orders import ExitReason, Fill, Position
from titan_tfbs.instruments import FIRM_PRIMARY_SYMBOLS, Instrument, get_instrument
from titan_tfbs.journal.journal import TradeJournal
from titan_tfbs.risk.compliance import ComplianceMonitor, Rule
from titan_tfbs.risk.manager import RiskManager
from titan_tfbs.strategy.breakout import BROKEN, READY, RETESTED
from titan_tfbs.strategy.checklist import ChecklistResult, build_checklist
from titan_tfbs.strategy.signals import TradeSignal
from titan_tfbs.strategy.tfbs import SetupEvaluation, TFBSStrategy

#: The engine's base timeframe. Everything else is derived from it, and 5M is
#: the fastest chart the firm trades.
DEFAULT_BASE_TIMEFRAME = "5M"


@dataclass
class BotEvent:
    """Something worth reporting from a single bar."""

    ts: datetime
    symbol: str
    kind: str            # setup | rejected | entry | exit | limit | compliance
    detail: str
    payload: Dict[str, object] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - reporting
        return f"[{self.ts:%Y-%m-%d %H:%M}] {self.symbol} {self.kind}: {self.detail}"


class TFBSBot:
    """Automated execution of the Titan Formation Breakout System."""

    def __init__(
        self,
        config: Optional[TitanConfig] = None,
        symbols: Optional[Sequence[str]] = None,
        *,
        start_time: Optional[datetime] = None,
        broker: Optional[Broker] = None,
        journal: Optional[TradeJournal] = None,
        calendar: Optional[EconomicCalendar] = None,
        base_timeframe: str = DEFAULT_BASE_TIMEFRAME,
        on_event: Optional[Callable[[BotEvent], None]] = None,
    ) -> None:
        self.config = config or TitanConfig()
        now = start_time or datetime.now(timezone.utc)
        self.base_timeframe = TimeFrame.parse(base_timeframe)

        chosen = list(symbols or self.config.symbols or FIRM_PRIMARY_SYMBOLS)
        self.instruments: Dict[str, Instrument] = {
            s.upper(): get_instrument(s) for s in chosen
        }
        self.stores: Dict[str, MultiTimeframeStore] = {
            symbol: MultiTimeframeStore(
                symbol, self.base_timeframe, instrument.timeframes.all_timeframes()
            )
            for symbol, instrument in self.instruments.items()
        }

        self.strategy = TFBSStrategy(self.config)
        self.compliance = ComplianceMonitor(self.config.compliance, calendar=calendar)
        self.risk = RiskManager(self.config, now, compliance=self.compliance)
        self.calendar = calendar
        self.journal = journal or TradeJournal(self.config.journal)
        self.broker = broker or PaperBroker(
            self.config.execution,
            tp_config=self.config.trade_management,
            account_currency=self.config.account.currency,
            atr_lookup=self._atr_for,
        )
        self.trades = TradeManager(
            self.config.trade_management,
            self.broker,
            pessimistic_intrabar=self.config.execution.pessimistic_intrabar,
            on_stop_moved=self._on_stop_moved,
        )
        self.on_event = on_event
        self.events: List[BotEvent] = []
        self.closed_positions: List[Position] = []
        self.last_price: Dict[str, float] = {}
        self._now: datetime = now

    # -- plumbing ----------------------------------------------------------

    def _emit(self, ts: datetime, symbol: str, kind: str, detail: str, **payload) -> None:
        event = BotEvent(ts, symbol, kind, detail, payload)
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def _atr_for(self, symbol: str) -> Optional[float]:
        store = self.stores.get(symbol.upper())
        if store is None:
            return None
        instrument = self.instruments[symbol.upper()]
        series = store.get(instrument.timeframes.primary_entry_tf)
        if series is None or len(series) < self.config.atr_period + 2:
            return None
        return atr_of(list(series), self.config.atr_period)

    def _on_stop_moved(self, position: Position, new_stop: float) -> None:
        self.risk.update_stop(position.id, new_stop, position.value_per_point)

    def warmup(self, symbol: str, candles: Sequence[Candle]) -> None:
        """Load history without trading it — builds the higher-TF screens."""
        store = self.stores[symbol.upper()]
        for candle in candles:
            store.push(candle)
            self.last_price[symbol.upper()] = candle.close
        self._now = candles[-1].ts if candles else self._now

    # -- main loop ---------------------------------------------------------

    def on_candle(self, symbol: str, candle: Candle) -> List[SetupEvaluation]:
        """Process one closed base-timeframe candle for one symbol."""
        key = symbol.upper()
        if key not in self.stores:
            raise KeyError(f"{symbol} is not in this bot's universe")

        store = self.stores[key]
        instrument = self.instruments[key]
        closed = store.push(candle)
        self.last_price[key] = candle.close
        self._now = candle.ts

        for period in self.risk.on_time(candle.ts):
            if period == "day":
                self._emit(
                    candle.ts, key, "limit",
                    f"new session — daily loss limit reset "
                    f"({self.config.risk.daily_loss_limit_pct:.0f}%, Ch VIII-A)",
                )

        # ---- step 7: MANAGE open positions first -------------------------
        self._manage(key, candle, closed)

        # ---- steps 1-4: SCAN, VALIDATE, CONFIRM, SCORE -------------------
        evaluations = self.strategy.on_candles(key, store, closed, candle.ts)

        for evaluation in evaluations:
            if not evaluation.accepted:
                self._record_rejection(evaluation)
                continue
            self._try_execute(evaluation, instrument, candle)

        self._mark_equity()
        return evaluations

    def on_candles(self, symbol: str, candles: Sequence[Candle]) -> List[SetupEvaluation]:
        out: List[SetupEvaluation] = []
        for candle in candles:
            out.extend(self.on_candle(symbol, candle))
        return out

    # -- step 7: MANAGE ----------------------------------------------------

    def _manage(
        self, symbol: str, candle: Candle, closed: Dict[str, Candle]
    ) -> None:
        positions = [
            p for p in self.broker.open_positions() if p.signal.symbol == symbol
        ]
        if not positions:
            return

        instrument = self.instruments[symbol]
        store = self.stores[symbol]
        entry_tf = instrument.timeframes.primary_entry_tf
        entry_series = store.get(entry_tf)
        recent = list(entry_series)[-120:] if entry_series else []
        atr_value = self._atr_for(symbol) or 0.0
        levels = self.strategy.state_for(symbol).levels.get(
            instrument.timeframes.primary_pattern_tf, []
        )

        news_imminent = False
        if self.calendar is not None:
            news_imminent = (
                self.calendar.imminent(
                    candle.ts, symbol, self.config.compliance.news_blackout_before_min
                )
                is not None
            )

        for position in positions:
            ctx = TradeContext(
                candle=candle,
                atr=atr_value,
                recent_candles=recent,
                levels=levels,
                news_imminent=news_imminent,
                counter_pattern=self._counter_pattern(symbol, position),
                invalidated=self._thesis_invalidated(position, candle, atr_value),
                invalidation_detail="price closed back through the broken level (Ch X-C)",
                entry_tf_close=entry_tf in closed,
            )
            for fill in self.trades.update(position, ctx):
                self._on_fill(position, fill)

    def _counter_pattern(self, symbol: str, position: Position) -> bool:
        """Ch X-C — "Counter-pattern forming on lower TF".

        A counter-pattern is one pointing the *other* way: a bearish formation
        threatens a long, a bullish formation threatens a short.  Three things
        do not count:

        * the formation that produced this position;
        * formations that merely exist — the manual says *forming*, so only a
          break confirmed after we were already in the trade qualifies;
        * unbroken formations, which Ch VI-B grades as watchlist only.
        """
        state = self.strategy.states.get(symbol)
        if state is None:
            return False
        own = position.signal.pattern.signature()
        for watch in state.tracker.active:
            if watch.state not in (BROKEN, RETESTED, READY):
                continue
            if watch.breakout is None or watch.breakout.ts <= position.opened_ts:
                continue
            if watch.pattern.signature() == own:
                continue
            # bearish pattern vs long position, or bullish vs short.
            if watch.pattern.is_bearish == (position.sign > 0):
                return True
        return False

    def _thesis_invalidated(
        self, position: Position, candle: Candle, atr_value: float
    ) -> bool:
        """Ch X-C — the broken level has been decisively reclaimed.

        A retest entry sits within a bar's noise of the level by construction,
        so a bare touch is not invalidation; the close has to travel back
        through it by the same margin that would have failed the flip.
        """
        pattern = position.signal.pattern
        level = pattern.trigger_price_at_ts(candle.ts)
        buffer = self.config.breakout.retest_invalidation_buffer_atr * atr_value
        if position.sign > 0:
            return candle.close < level - buffer
        return candle.close > level + buffer

    def _on_fill(self, position: Position, fill: Fill) -> None:
        symbol = position.signal.symbol
        self.risk.reduce_size(position.id, fill.size)
        self._emit(
            fill.ts, symbol, "exit",
            f"{fill.reason.value} {fill.size:g} @ {fill.price:.5f} "
            f"({fill.r_multiple:+.2f}R, {fill.pnl:+,.2f})",
            trade_id=position.id, reason=fill.reason.value,
        )
        if not position.is_open:
            # The broker has already dropped this position, so the floating
            # figure is exactly what remains open elsewhere on the book.
            self.risk.register_close(
                position.id, position.realized_pnl, floating=self._floating_pnl()
            )
            self.closed_positions.append(position)
            # Ch XII-A4: every closed trade is journalled.
            self.journal.log_trade(position)
            self._check_post_trade_limits(fill.ts, symbol)

    def _check_post_trade_limits(self, ts: datetime, symbol: str) -> None:
        verdict = self.risk.limits()
        for breach in verdict.breaches:
            self._emit(ts, symbol, "limit", breach)
        if verdict.breaches:
            # Reaching a hard loss limit is not itself a rule violation; it
            # becomes one only if trading continues (Ch XII-A5). The engine
            # stops, so we record the event without a compliance flag.
            self._emit(
                ts, symbol, "limit",
                f"account status {verdict.status.value}; "
                f"can_trade={verdict.can_trade}",
            )

    # -- steps 5 & 6: SIZE and EXECUTE -------------------------------------

    def _try_execute(
        self, evaluation: SetupEvaluation, instrument: Instrument, candle: Candle
    ) -> None:
        signal = evaluation.signal
        assert signal is not None
        symbol = signal.symbol
        alignment = evaluation.alignment

        # ---- step 5: SIZE ------------------------------------------------
        size_factor = alignment.size_factor(self.config.mtf) if alignment else 1.0
        decision = self.risk.evaluate(signal, instrument, candle.ts, size_factor)
        if not decision.approved:
            evaluation.accepted = False
            evaluation.reason = decision.reason
            self._record_rejection(evaluation)
            return

        signal.risk_pct = decision.risk_pct
        signal.risk_amount = decision.risk_amount
        signal.position_size = decision.size
        signal.notes.extend(decision.notes)

        # ---- Appendix A: the mandatory pre-trade checklist ----------------
        news_clear, news_detail = self.compliance.news_check(candle.ts, symbol)
        checklist = build_checklist(
            signal,
            self.config,
            alignment,
            news_clear=news_clear,
            news_detail=news_detail,
            aggregate_risk_pct=self.risk.aggregate_open_risk_pct,
            risk_pct=decision.risk_pct,
            journal_enabled=self.journal.enabled,
            pattern_timeframes=list(instrument.timeframes.pattern),
            entry_timeframes=list(instrument.timeframes.entry),
        )
        signal.checklist = checklist
        if not checklist.approved:
            evaluation.accepted = False
            evaluation.reason = f"pre-trade checklist blocked: {checklist.reason()}"
            if not news_clear:
                self._emit(candle.ts, symbol, "compliance", news_detail)
            self._record_rejection(evaluation, checklist)
            return

        # ---- step 6: EXECUTE ---------------------------------------------
        if self.config.execution.dry_run and not isinstance(self.broker, PaperBroker):
            self._emit(candle.ts, symbol, "setup", "dry run — order not sent")
            self.journal.log_signal(
                candle.ts, symbol, True, "dry run", signal=signal
            )
            return

        position = self.broker.open_position(
            signal, instrument, decision.size, candle.ts, signal.entry_price
        )
        if position is None:
            evaluation.accepted = False
            evaluation.reason = (
                "fill refused: slippage would break the 2:1 minimum (Ch VIII-A)"
            )
            self._record_rejection(evaluation, checklist)
            return

        self.risk.register_open(
            position.id,
            signal,
            instrument,
            decision.size,
            decision.risk_amount,
            decision.risk_pct,
            candle.ts,
            entry_price=position.entry_price,
            stop_loss=position.stop_loss,
        )
        self.journal.log_signal(candle.ts, symbol, True, "executed", signal=signal)
        self._emit(
            candle.ts, symbol, "entry",
            f"{signal.grade.value} {signal.pattern.type.value} "
            f"{signal.direction.value} {decision.size:g} @ {position.entry_price:.5f} "
            f"SL {position.stop_loss:.5f} TP1 {signal.take_profit_1:.5f} "
            f"({signal.score.total}/10, {signal.risk_reward:.2f}R, "
            f"{decision.risk_pct:.2f}% risk)",
            trade_id=position.id,
            signal=signal.summary(),
        )

    def _record_rejection(
        self, evaluation: SetupEvaluation, checklist: Optional[ChecklistResult] = None
    ) -> None:
        self.journal.log_signal(
            evaluation.ts,
            evaluation.symbol,
            False,
            evaluation.reason,
            signal=evaluation.signal,
            pattern_type=evaluation.pattern.type.value if evaluation.pattern else "",
            pattern_tf=evaluation.pattern.timeframe if evaluation.pattern else "",
            checklist_failures=[i.text for i in checklist.failures] if checklist else None,
        )
        self._emit(evaluation.ts, evaluation.symbol, "rejected", evaluation.reason)

    # -- account -----------------------------------------------------------

    def _floating_pnl(self) -> float:
        """Unrealized P&L across the open book, at last traded prices."""
        total = 0.0
        for position in self.broker.open_positions():
            price = self.last_price.get(position.signal.symbol)
            if price is not None:
                total += position.unrealized(price)
        return total

    def _mark_equity(self) -> None:
        self.risk.mark_equity(self.risk.balance + self._floating_pnl())

    def close_all(self, reason: ExitReason = ExitReason.MANUAL) -> None:
        """Flatten the book — end of a backtest, or a hard compliance stop."""
        for position in list(self.broker.open_positions()):
            price = self.last_price.get(position.signal.symbol, position.entry_price)
            fill = self.trades.force_close(position, price, self._now, reason)
            if fill:
                self._on_fill(position, fill)
        self._mark_equity()

    # -- reporting ---------------------------------------------------------

    def snapshot(self) -> Dict[str, object]:
        return {
            "time": self._now.isoformat(),
            "account": self.risk.report(self._now),
            "open_positions": [p.summary() for p in self.broker.open_positions()],
            "closed_trades": len(self.closed_positions),
            "watchlists": {
                symbol: self.strategy.watchlist(symbol) for symbol in self.stores
            },
        }

    def log_equity_point(self) -> None:
        verdict = self.risk.limits()
        self.journal.log_equity(
            self._now,
            self.risk.balance,
            self.risk.state.equity,
            self.risk.state.peak_equity,
            len(self.broker.open_positions()),
            self.risk.aggregate_open_risk_pct,
            verdict.daily_dd_pct,
            verdict.max_dd_pct,
            verdict.status.value,
        )
