"""Event-driven backtester.

Replays base-timeframe candles through the live :class:`~titan_tfbs.bot.TFBSBot`
— the same pipeline, the same risk gate, the same trade management — so a
backtest measures the system that would actually trade, not a parallel
implementation of it.

Multi-symbol runs are interleaved in strict timestamp order, which matters:
the Ch VIII-A aggregate-risk and correlated-exposure caps are portfolio-level
rules and only bind correctly if symbols compete for the same headroom in the
order the market delivered them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from titan_tfbs.backtest.metrics import PerformanceMetrics, compute_metrics
from titan_tfbs.bot import BotEvent, TFBSBot
from titan_tfbs.config import TitanConfig
from titan_tfbs.core.candles import Candle
from titan_tfbs.data.feed import DataFeed
from titan_tfbs.data.news import EconomicCalendar
from titan_tfbs.execution.orders import ExitReason, Position
from titan_tfbs.journal.journal import TradeJournal


@dataclass
class BacktestResult:
    """Outcome of a run."""

    metrics: PerformanceMetrics
    positions: List[Position] = field(default_factory=list)
    equity_curve: List[Tuple[datetime, float]] = field(default_factory=list)
    events: List[BotEvent] = field(default_factory=list)
    bars_processed: int = 0
    symbols: List[str] = field(default_factory=list)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    account_report: Dict[str, object] = field(default_factory=dict)
    signals_evaluated: int = 0
    signals_accepted: int = 0

    def summary(self) -> Dict[str, object]:
        return {
            "symbols": self.symbols,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "bars": self.bars_processed,
            "setups_evaluated": self.signals_evaluated,
            "setups_executed": self.signals_accepted,
            "metrics": self.metrics.to_dict(),
            "account": self.account_report,
        }

    def render(self) -> str:  # pragma: no cover - reporting
        head = (
            f"TFBS BACKTEST  {', '.join(self.symbols)}\n"
            f"  Period       {self.start:%Y-%m-%d} -> {self.end:%Y-%m-%d}\n"
            f"  Bars         {self.bars_processed:,}\n"
            f"  Setups       {self.signals_evaluated} evaluated, "
            f"{self.signals_accepted} executed\n"
        )
        return head + "\n" + self.metrics.render()


class BacktestEngine:
    """Runs the bot over historical data."""

    def __init__(
        self,
        config: Optional[TitanConfig] = None,
        symbols: Optional[Sequence[str]] = None,
        journal: Optional[TradeJournal] = None,
        calendar: Optional[EconomicCalendar] = None,
        warmup_bars: int = 0,
        base_timeframe: str = "5M",
    ) -> None:
        self.config = config or TitanConfig()
        self.symbols = [s.upper() for s in (symbols or self.config.symbols)]
        self.journal = journal
        self.calendar = calendar
        self.warmup_bars = warmup_bars
        self.base_timeframe = base_timeframe

    def run(
        self,
        feed: DataFeed,
        symbols: Optional[Sequence[str]] = None,
        equity_sample_bars: int = 288,
    ) -> BacktestResult:
        """Replay ``feed`` and return the results.

        ``equity_sample_bars`` controls how often an equity point is recorded;
        288 five-minute bars is one calendar day.
        """
        universe = [s.upper() for s in (symbols or self.symbols or feed.symbols())]
        if not universe:
            raise ValueError("no symbols to backtest")

        data: Dict[str, List[Candle]] = {s: list(feed.history(s)) for s in universe}
        data = {s: c for s, c in data.items() if c}
        if not data:
            raise ValueError("feed returned no candles for the requested symbols")

        # Warm up each symbol's higher-timeframe screens before trading.
        warm = max(0, self.warmup_bars)
        first_live_ts = min(
            (c[warm].ts if len(c) > warm else c[-1].ts) for c in data.values()
        )

        bot = TFBSBot(
            self.config,
            symbols=list(data),
            start_time=first_live_ts,
            journal=self.journal,
            calendar=self.calendar,
            base_timeframe=self.base_timeframe,
        )
        if warm:
            for symbol, candles in data.items():
                bot.warmup(symbol, candles[:warm])

        # Interleave every symbol's remaining candles in timestamp order.
        stream: List[Tuple[datetime, str, Candle]] = []
        for symbol, candles in data.items():
            for candle in candles[warm:]:
                stream.append((candle.ts, symbol, candle))
        stream.sort(key=lambda row: (row[0], row[1]))

        equity_curve: List[Tuple[datetime, float]] = []
        evaluated = accepted = 0
        counter = 0

        for ts, symbol, candle in stream:
            evaluations = bot.on_candle(symbol, candle)
            evaluated += len(evaluations)
            accepted += sum(1 for e in evaluations if e.accepted)
            counter += 1
            if counter % max(1, equity_sample_bars) == 0:
                equity_curve.append((ts, bot.risk.state.equity))
                bot.log_equity_point()

        bot.close_all(ExitReason.END_OF_DATA)
        end_ts = stream[-1][0] if stream else first_live_ts
        equity_curve.append((end_ts, bot.risk.state.equity))

        metrics = compute_metrics(
            bot.closed_positions, equity_curve, self.config.account.starting_balance
        )
        return BacktestResult(
            metrics=metrics,
            positions=list(bot.closed_positions),
            equity_curve=equity_curve,
            events=list(bot.events),
            bars_processed=len(stream),
            symbols=list(data),
            start=stream[0][0] if stream else None,
            end=end_ts,
            account_report=bot.risk.report(end_ts),
            signals_evaluated=evaluated,
            signals_accepted=accepted,
        )
