"""Order and position model.

The take-profit structure mirrors TFBS Ch X-B exactly::

    TP1  50%  Measured move target (pattern height from breakout)
    TP2  30%  Next significant S/R beyond TP1
    TP3  20%  Trailed — exits only when trend structure breaks
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from titan_tfbs.instruments import Instrument
from titan_tfbs.strategy.signals import Direction, TradeSignal


class PositionState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class ExitReason(str, Enum):
    """Every way a TFBS position can end."""

    STOP_LOSS = "stop_loss"
    BREAKEVEN_STOP = "breakeven_stop"
    TRAILING_STOP = "trailing_stop"
    TP1 = "tp1_measured_move"
    TP2 = "tp2_next_sr"
    TP3 = "tp3_trailed"
    # Ch X-C early exit triggers
    EARLY_STALL = "early_exit_stalled_at_sr"
    EARLY_NEWS = "early_exit_news_not_at_breakeven"
    EARLY_DURATION = "early_exit_3x_expected_duration"
    EARLY_COUNTER_PATTERN = "early_exit_counter_pattern"
    EARLY_INVALIDATION = "early_exit_technical_invalidation"
    MANUAL = "manual_close"
    END_OF_DATA = "end_of_backtest"

    @property
    def is_target(self) -> bool:
        return self in (ExitReason.TP1, ExitReason.TP2, ExitReason.TP3)

    @property
    def is_stop(self) -> bool:
        return self in (
            ExitReason.STOP_LOSS,
            ExitReason.BREAKEVEN_STOP,
            ExitReason.TRAILING_STOP,
        )


@dataclass
class TakeProfitLeg:
    """One rung of the Ch X-B ladder."""

    name: str
    price: Optional[float]
    allocation: float
    reason: ExitReason
    filled: bool = False

    @property
    def active(self) -> bool:
        return self.price is not None and not self.filled


@dataclass
class Fill:
    """A completed execution against a position."""

    ts: datetime
    price: float
    size: float
    reason: ExitReason
    pnl: float
    r_multiple: float
    commission: float = 0.0
    note: str = ""


@dataclass
class Position:
    """A live TFBS position with its full management state."""

    id: str
    signal: TradeSignal
    instrument: Instrument
    direction: Direction
    entry_price: float
    initial_size: float
    size: float
    stop_loss: float
    initial_stop: float
    legs: List[TakeProfitLeg]
    opened_ts: datetime
    #: One R in price terms, fixed at entry — every management rule in Ch X
    #: is expressed in R.
    r_unit: float
    value_per_point: float
    state: PositionState = PositionState.OPEN
    realized_pnl: float = 0.0
    commission_paid: float = 0.0
    fills: List[Fill] = field(default_factory=list)
    bars_open: int = 0
    max_favorable_r: float = 0.0
    max_adverse_r: float = 0.0
    breakeven_done: bool = False
    trailing_active: bool = False
    closed_ts: Optional[datetime] = None
    close_reason: Optional[ExitReason] = None
    notes: List[str] = field(default_factory=list)

    # -- geometry ----------------------------------------------------------

    @property
    def sign(self) -> int:
        return self.direction.sign

    @property
    def is_open(self) -> bool:
        return self.state is PositionState.OPEN and self.size > 0

    def r_at(self, price: float) -> float:
        if self.r_unit <= 0:
            return 0.0
        return (price - self.entry_price) * self.sign / self.r_unit

    def price_at_r(self, r: float) -> float:
        return self.entry_price + self.sign * r * self.r_unit

    def unrealized(self, price: float) -> float:
        return (price - self.entry_price) * self.sign * self.size * self.value_per_point

    def equity_contribution(self, price: float) -> float:
        return self.realized_pnl + self.unrealized(price)

    # -- mutation ----------------------------------------------------------

    def register_fill(self, fill: Fill) -> None:
        self.fills.append(fill)
        self.realized_pnl += fill.pnl
        self.commission_paid += fill.commission
        self.size = max(0.0, self.size - fill.size)
        if self.size <= 1e-12:
            self.state = PositionState.CLOSED
            self.closed_ts = fill.ts
            self.close_reason = fill.reason

    def move_stop(self, new_stop: float, allow_widening: bool = False) -> bool:
        """Ch XIV-A5: "Stop only moves in your favor. Never widen"."""
        improves = (new_stop > self.stop_loss) if self.sign > 0 else (new_stop < self.stop_loss)
        if not improves and not allow_widening:
            return False
        self.stop_loss = new_stop
        return True

    def leg(self, name: str) -> Optional[TakeProfitLeg]:
        return next((l for l in self.legs if l.name == name), None)

    @property
    def realized_r(self) -> float:
        """Total R banked, weighted by the size of each partial exit."""
        if self.initial_size <= 0 or self.r_unit <= 0:
            return 0.0
        return sum(f.r_multiple * (f.size / self.initial_size) for f in self.fills)

    def summary(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "symbol": self.signal.symbol,
            "direction": self.direction.value,
            "pattern": self.signal.pattern.type.value,
            "grade": self.signal.grade.value,
            "score": self.signal.score.total,
            "entry": round(self.entry_price, 8),
            "initial_stop": round(self.initial_stop, 8),
            "stop": round(self.stop_loss, 8),
            "size": self.initial_size,
            "opened": self.opened_ts.isoformat(),
            "closed": self.closed_ts.isoformat() if self.closed_ts else None,
            "realized_pnl": round(self.realized_pnl, 2),
            "realized_r": round(self.realized_r, 3),
            "max_favorable_r": round(self.max_favorable_r, 3),
            "max_adverse_r": round(self.max_adverse_r, 3),
            "bars_open": self.bars_open,
            "close_reason": self.close_reason.value if self.close_reason else None,
            "fills": [
                {
                    "ts": f.ts.isoformat(),
                    "price": round(f.price, 8),
                    "size": f.size,
                    "reason": f.reason.value,
                    "pnl": round(f.pnl, 2),
                    "r": round(f.r_multiple, 3),
                }
                for f in self.fills
            ],
        }


def build_legs(signal: TradeSignal, cfg) -> List[TakeProfitLeg]:
    """Construct the Ch X-B ladder from a signal's targets."""
    return [
        TakeProfitLeg("TP1", signal.take_profit_1, cfg.tp1_allocation, ExitReason.TP1),
        TakeProfitLeg("TP2", signal.take_profit_2, cfg.tp2_allocation, ExitReason.TP2),
        TakeProfitLeg("TP3", signal.take_profit_3, cfg.tp3_allocation, ExitReason.TP3),
    ]
