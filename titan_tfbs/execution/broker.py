"""Broker abstraction and the paper/backtest fill simulator.

``PaperBroker`` is used by the backtester and by the bot in dry-run mode.  A
live venue adapter only has to implement :class:`Broker`; nothing above this
layer knows whether fills are simulated or real.
"""

from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional

from titan_tfbs.config import ExecutionConfig, TradeManagementConfig
from titan_tfbs.execution.orders import (
    ExitReason,
    Fill,
    Position,
    PositionState,
    build_legs,
)
from titan_tfbs.instruments import Instrument
from titan_tfbs.risk.sizing import quote_conversion_factor
from titan_tfbs.strategy.signals import TradeSignal


class Broker(ABC):
    """Minimal venue interface used by the engine."""

    @abstractmethod
    def open_position(
        self,
        signal: TradeSignal,
        instrument: Instrument,
        size: float,
        ts: datetime,
        reference_price: float,
    ) -> Optional[Position]:
        """Enter a position. Returns None if the order could not be filled."""

    @abstractmethod
    def close_position(
        self,
        position: Position,
        size: float,
        price: float,
        ts: datetime,
        reason: ExitReason,
        note: str = "",
    ) -> Optional[Fill]:
        """Close all or part of a position at ``price``."""

    @abstractmethod
    def modify_stop(self, position: Position, new_stop: float) -> bool:
        """Move a position's protective stop."""


class PaperBroker(Broker):
    """Deterministic fill simulator: spread crossing, slippage and commission."""

    def __init__(
        self,
        config: ExecutionConfig,
        tp_config: Optional[TradeManagementConfig] = None,
        account_currency: str = "USD",
        atr_lookup=None,
    ) -> None:
        self.config = config
        #: Ch X-B allocations for the take-profit ladder.
        self.tp_config = tp_config or TradeManagementConfig()
        self.account_currency = account_currency
        #: Callable(symbol) -> ATR, used to scale slippage. Optional.
        self.atr_lookup = atr_lookup
        self.positions: Dict[str, Position] = {}
        self.closed: List[Position] = []
        self._ids = itertools.count(1)

    # -- helpers -----------------------------------------------------------

    def _slippage(self, instrument: Instrument) -> float:
        atr_value = self.atr_lookup(instrument.symbol) if self.atr_lookup else None
        if atr_value:
            return self.config.slippage_atr * atr_value
        return instrument.tick_size

    def _entry_price(self, instrument: Instrument, reference: float, sign: int) -> float:
        """Cross the spread and pay slippage on the way in."""
        adverse = instrument.typical_spread / 2.0 + self._slippage(instrument)
        return instrument.round_price(reference + sign * adverse)

    def _exit_price(self, instrument: Instrument, reference: float, sign: int) -> float:
        adverse = instrument.typical_spread / 2.0 + self._slippage(instrument)
        return instrument.round_price(reference - sign * adverse)

    def _value_per_point(self, instrument: Instrument, price: float) -> float:
        factor = quote_conversion_factor(instrument, self.account_currency, price) or 1.0
        return instrument.value_per_point * factor

    # -- Broker interface --------------------------------------------------

    def open_position(
        self,
        signal: TradeSignal,
        instrument: Instrument,
        size: float,
        ts: datetime,
        reference_price: float,
    ) -> Optional[Position]:
        if size <= 0:
            return None
        sign = signal.direction.sign
        fill_price = self._entry_price(instrument, reference_price, sign)

        # Ch VIII-A: the 2:1 minimum is a hard limit, so a fill that has
        # slipped enough to break it is refused rather than taken.
        if self.config.revalidate_rr_on_fill:
            risk = abs(fill_price - signal.stop_loss)
            reward = abs(signal.take_profit_1 - fill_price)
            if risk <= 0 or reward / risk < 2.0:
                return None

        r_unit = abs(fill_price - signal.stop_loss)
        if r_unit <= 0:
            return None

        pos = Position(
            id=f"T{next(self._ids):05d}",
            signal=signal,
            instrument=instrument,
            direction=signal.direction,
            entry_price=fill_price,
            initial_size=size,
            size=size,
            stop_loss=signal.stop_loss,
            initial_stop=signal.stop_loss,
            legs=build_legs(signal, self.tp_config),
            opened_ts=ts,
            r_unit=r_unit,
            value_per_point=self._value_per_point(instrument, fill_price),
        )
        pos.commission_paid += size * instrument.commission_per_contract / 2.0
        self.positions[pos.id] = pos
        return pos

    def close_position(
        self,
        position: Position,
        size: float,
        price: float,
        ts: datetime,
        reason: ExitReason,
        note: str = "",
    ) -> Optional[Fill]:
        if not position.is_open:
            return None
        size = min(size, position.size)
        if size <= 0:
            return None

        instrument = position.instrument
        fill_price = self._exit_price(instrument, price, position.sign)
        gross = (fill_price - position.entry_price) * position.sign * size * position.value_per_point
        commission = size * instrument.commission_per_contract / 2.0
        pnl = gross - commission
        r = position.r_at(fill_price)

        fill = Fill(
            ts=ts,
            price=fill_price,
            size=size,
            reason=reason,
            pnl=pnl,
            r_multiple=r,
            commission=commission,
            note=note,
        )
        position.register_fill(fill)
        if position.state is PositionState.CLOSED:
            self.positions.pop(position.id, None)
            self.closed.append(position)
        return fill

    def modify_stop(self, position: Position, new_stop: float) -> bool:
        return position.move_stop(
            position.instrument.round_price(new_stop),
            allow_widening=False,
        )

    # -- reporting ---------------------------------------------------------

    def open_positions(self) -> List[Position]:
        return list(self.positions.values())

    def floating_pnl(self, prices: Dict[str, float]) -> float:
        total = 0.0
        for pos in self.positions.values():
            price = prices.get(pos.signal.symbol)
            if price is not None:
                total += pos.unrealized(price)
        return total
