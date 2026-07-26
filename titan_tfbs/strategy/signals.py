"""Signal, grade and score types produced by the TFBS pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from titan_tfbs.config import EntryMethod
from titan_tfbs.core.candles import Candle
from titan_tfbs.patterns.base import Pattern


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1

    @property
    def is_long(self) -> bool:
        return self is Direction.LONG

    @classmethod
    def from_sign(cls, sign: int) -> "Direction":
        return cls.LONG if sign > 0 else cls.SHORT


class Grade(str, Enum):
    """TFBS Ch XI — score to grade to action."""

    ELITE = "ELITE"            # 9-10  full position size (up to 2% risk)
    APPROVED = "APPROVED"      # 7-8   standard position size (1-1.5% risk)
    WATCHLIST = "WATCHLIST"    # 5-6   monitor only
    DISCARD = "DISCARD"        # 0-4   no trade — do not revisit

    @property
    def tradeable(self) -> bool:
        return self in (Grade.ELITE, Grade.APPROVED)


#: The manual speaks of "conviction"; the mechanism is the confluence grade.
Conviction = Grade


@dataclass
class ConfluenceScore:
    """TFBS Ch XI — the mandatory 10-point pre-trade score."""

    pattern_quality: int = 0        # 0-2
    breakout_strength: int = 0      # 0-2
    retest_confirm: int = 0         # 0-1
    htf_alignment: int = 0          # 0-2
    sr_confluence: int = 0          # 0-1
    rr_ratio: int = 0               # 0-1
    clean_path: int = 0             # 0-1
    triple_bonus: int = 0           # Ch IV-D
    dual_pattern_bonus: int = 0     # Ch VI-B A+
    reasons: Dict[str, str] = field(default_factory=dict)

    @property
    def base_total(self) -> int:
        return (
            self.pattern_quality
            + self.breakout_strength
            + self.retest_confirm
            + self.htf_alignment
            + self.sr_confluence
            + self.rr_ratio
            + self.clean_path
        )

    @property
    def raw_total(self) -> int:
        return self.base_total + self.triple_bonus + self.dual_pattern_bonus

    @property
    def total(self) -> int:
        """The score out of 10 (bonuses can only lift a setup to the cap)."""
        return min(10, self.raw_total)

    def grade(
        self, elite: int = 9, approved: int = 7, watchlist: int = 5
    ) -> Grade:
        # Ch XI: "R:R Ratio — 0 = Below 2:1 (auto-skip)".
        if self.rr_ratio == 0:
            return Grade.DISCARD
        t = self.total
        if t >= elite:
            return Grade.ELITE
        if t >= approved:
            return Grade.APPROVED
        if t >= watchlist:
            return Grade.WATCHLIST
        return Grade.DISCARD

    def breakdown(self) -> Dict[str, object]:
        return {
            "pattern_quality": self.pattern_quality,
            "breakout_strength": self.breakout_strength,
            "retest_confirm": self.retest_confirm,
            "htf_alignment": self.htf_alignment,
            "sr_confluence": self.sr_confluence,
            "rr_ratio": self.rr_ratio,
            "clean_path": self.clean_path,
            "triple_bonus": self.triple_bonus,
            "dual_pattern_bonus": self.dual_pattern_bonus,
            "total": self.total,
            "reasons": dict(self.reasons),
        }

    def __str__(self) -> str:  # pragma: no cover - reporting
        return f"{self.total}/10"


@dataclass
class BreakoutEvent:
    """TFBS Ch V — the confirming break of the neckline / confirmation line."""

    pattern: Pattern
    ts: datetime
    candle: Candle
    timeframe: str
    #: The key level's price at the moment of the break — the manual's
    #: "Neckline Break Price" used for the measured move (Ch III-D).
    level: float
    close: float
    atr: float
    volume_ratio: Optional[float] = None
    volume_surge: bool = False
    wick_only: bool = False
    gapped: bool = False
    gap_size: float = 0.0
    divergence: bool = False
    choppy_context: bool = False
    #: True when the pattern-TF bar has also closed beyond the level, the
    #: strongest form of the Ch V-A criterion 1 break.
    pattern_tf_confirmed: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def is_bearish(self) -> bool:
        return self.pattern.is_bearish

    @property
    def direction(self) -> Direction:
        return Direction.SHORT if self.is_bearish else Direction.LONG


@dataclass
class RetestEvent:
    """TFBS Ch V-C — price returns to the broken level and it holds."""

    ts: datetime
    candle: Candle
    #: The extreme reached during the retest; the Method B stop sits beyond it.
    wick_price: float
    rejection_kind: str
    rejection_strength: float
    followthrough: bool = False
    followthrough_candle: Optional[Candle] = None


@dataclass
class TradeSignal:
    """A fully specified, checklist-complete TFBS trade.

    Nothing reaches this type until the pipeline in Ch VI-A has been completed
    end to end: validated formation, confirmed breakout, confluence score of at
    least 7/10, and a measured move paying at least 2:1.
    """

    symbol: str
    direction: Direction
    pattern: Pattern
    breakout: BreakoutEvent
    entry_method: EntryMethod
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: Optional[float]
    risk_reward: float
    score: ConfluenceScore
    grade: Grade
    created_ts: datetime
    trend_timeframe: str
    pattern_timeframe: str
    entry_timeframe: str
    retest: Optional[RetestEvent] = None
    #: Filled in by the risk layer (Ch VI-A step 5: SIZE).
    risk_pct: float = 0.0
    risk_amount: float = 0.0
    position_size: float = 0.0
    #: Appendix A outcome; a signal never executes with a failed mandatory item.
    checklist: Optional[object] = None
    notes: List[str] = field(default_factory=list)
    #: Set when the pipeline rejected the setup, for the Ch XII-A4 journal
    #: requirement that skipped trades are logged too.
    rejected_reason: Optional[str] = None

    @property
    def stop_distance(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    @property
    def is_long(self) -> bool:
        return self.direction is Direction.LONG

    def r_multiple_price(self, r: float) -> float:
        """The price ``r`` R away from entry in the trade's favour."""
        return self.entry_price + self.direction.sign * r * self.stop_distance

    def rr_to(self, price: float) -> float:
        d = self.stop_distance
        return abs(price - self.entry_price) / d if d > 0 else 0.0

    def summary(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "direction": self.direction.value,
            "pattern": self.pattern.type.value,
            "grade": self.grade.value,
            "score": self.score.total,
            "entry_method": self.entry_method.value,
            "entry": round(self.entry_price, 8),
            "stop_loss": round(self.stop_loss, 8),
            "tp1": round(self.take_profit_1, 8),
            "tp2": round(self.take_profit_2, 8),
            "tp3": round(self.take_profit_3, 8) if self.take_profit_3 else None,
            "rr": round(self.risk_reward, 2),
            "risk_pct": round(self.risk_pct, 4),
            "size": self.position_size,
            "screens": {
                "trend": self.trend_timeframe,
                "pattern": self.pattern_timeframe,
                "entry": self.entry_timeframe,
            },
            "created": self.created_ts.isoformat(),
            "score_breakdown": self.score.breakdown(),
            "notes": list(self.notes),
        }
