"""Candles, timeframes and multi-timeframe aggregation.

The firm trades the 5M, 15M, 1H and 4H charts, with research and bias taken
from the higher timeframes (TFBS Ch IX).  This module ingests a single base
series (5M by default) and derives every higher timeframe from it, so all
screens are guaranteed to be consistent with one another.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Iterator, List, Optional, Sequence


@dataclass(frozen=True)
class Candle:
    """One OHLCV bar. ``ts`` is the bar's OPEN time, in UTC."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"candle {self.ts}: high {self.high} < low {self.low}")

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0

    def body_frac(self) -> float:
        return self.body / self.range if self.range > 0 else 0.0


_TF_PATTERN = re.compile(r"^(\d+)\s*([MHDW])$", re.IGNORECASE)
_TF_UNIT_MINUTES = {"M": 1, "H": 60, "D": 1440, "W": 10080}


@dataclass(frozen=True, order=True)
class TimeFrame:
    """A timeframe such as ``5M``, ``1H``, ``4H``, ``1D``, ``1W``."""

    minutes: int
    label: str = ""

    @classmethod
    def parse(cls, text: str) -> "TimeFrame":
        m = _TF_PATTERN.match(text.strip())
        if not m:
            raise ValueError(f"unrecognised timeframe '{text}' (use e.g. 5M, 1H, 4H, 1D)")
        qty, unit = int(m.group(1)), m.group(2).upper()
        if qty <= 0:
            raise ValueError(f"timeframe quantity must be positive: '{text}'")
        return cls(minutes=qty * _TF_UNIT_MINUTES[unit], label=f"{qty}{unit}")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.label or f"{self.minutes}M"

    @property
    def delta(self) -> timedelta:
        return timedelta(minutes=self.minutes)

    def floor(self, ts: datetime) -> datetime:
        """Snap a timestamp down to this timeframe's bucket start (UTC)."""
        ts = _as_utc(ts)
        if self.minutes >= _TF_UNIT_MINUTES["W"]:
            # Weekly buckets start Monday 00:00 UTC.
            day = ts.replace(hour=0, minute=0, second=0, microsecond=0)
            return day - timedelta(days=day.weekday())
        if self.minutes >= _TF_UNIT_MINUTES["D"]:
            days = self.minutes // _TF_UNIT_MINUTES["D"]
            day = ts.replace(hour=0, minute=0, second=0, microsecond=0)
            if days == 1:
                return day
            epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
            n = (day - epoch).days // days
            return epoch + timedelta(days=n * days)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        elapsed = int((ts - epoch).total_seconds() // 60)
        return epoch + timedelta(minutes=(elapsed // self.minutes) * self.minutes)


def _as_utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


TF_5M = TimeFrame.parse("5M")
TF_15M = TimeFrame.parse("15M")
TF_1H = TimeFrame.parse("1H")
TF_4H = TimeFrame.parse("4H")
TF_1D = TimeFrame.parse("1D")
TF_1W = TimeFrame.parse("1W")


class CandleSeries:
    """An append-only series of candles for a single symbol/timeframe."""

    __slots__ = ("symbol", "timeframe", "_candles", "max_length")

    def __init__(
        self,
        symbol: str,
        timeframe: TimeFrame,
        candles: Optional[Iterable[Candle]] = None,
        max_length: int = 5000,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.max_length = max_length
        self._candles: List[Candle] = []
        for c in candles or ():
            self.append(c)

    # -- container protocol ------------------------------------------------

    def __len__(self) -> int:
        return len(self._candles)

    def __iter__(self) -> Iterator[Candle]:
        return iter(self._candles)

    def __getitem__(self, item):
        return self._candles[item]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CandleSeries {self.symbol} {self.timeframe} n={len(self)}>"

    # -- mutation ----------------------------------------------------------

    def append(self, candle: Candle) -> None:
        """Append a closed candle. Out-of-order timestamps are rejected."""
        if self._candles:
            last = self._candles[-1]
            if candle.ts < last.ts:
                raise ValueError(
                    f"{self.symbol} {self.timeframe}: out-of-order candle "
                    f"{candle.ts} after {last.ts}"
                )
            if candle.ts == last.ts:
                self._candles[-1] = candle
                return
        self._candles.append(candle)
        if len(self._candles) > self.max_length:
            del self._candles[: len(self._candles) - self.max_length]

    def extend(self, candles: Iterable[Candle]) -> None:
        for c in candles:
            self.append(c)

    # -- access ------------------------------------------------------------

    @property
    def last(self) -> Optional[Candle]:
        return self._candles[-1] if self._candles else None

    def closes(self, n: Optional[int] = None) -> List[float]:
        src = self._candles if n is None else self._candles[-n:]
        return [c.close for c in src]

    def highs(self, n: Optional[int] = None) -> List[float]:
        src = self._candles if n is None else self._candles[-n:]
        return [c.high for c in src]

    def lows(self, n: Optional[int] = None) -> List[float]:
        src = self._candles if n is None else self._candles[-n:]
        return [c.low for c in src]

    def volumes(self, n: Optional[int] = None) -> List[float]:
        src = self._candles if n is None else self._candles[-n:]
        return [c.volume for c in src]

    def slice(self, start: int, end: Optional[int] = None) -> List[Candle]:
        return self._candles[start : end if end is not None else len(self._candles)]

    def index_at_or_before(self, ts: datetime) -> Optional[int]:
        """Index of the last candle opening at or before ``ts``."""
        if not self._candles:
            return None
        ts = _as_utc(ts)
        i = bisect_right([c.ts for c in self._candles], ts) - 1
        return i if i >= 0 else None

    def window(self, ts: datetime, lookback: int) -> List[Candle]:
        """The ``lookback`` candles ending at or before ``ts``."""
        i = self.index_at_or_before(ts)
        if i is None:
            return []
        return self._candles[max(0, i - lookback + 1) : i + 1]


class Resampler:
    """Aggregates a base timeframe into a higher one, emitting closed bars."""

    def __init__(self, target: TimeFrame) -> None:
        self.target = target
        self._bucket_ts: Optional[datetime] = None
        self._open = self._high = self._low = self._close = 0.0
        self._volume = 0.0

    def push(self, candle: Candle) -> Optional[Candle]:
        """Feed a base candle; returns the higher-TF candle that just closed."""
        bucket = self.target.floor(candle.ts)
        completed: Optional[Candle] = None
        if self._bucket_ts is None:
            self._start(bucket, candle)
            return None
        if bucket > self._bucket_ts:
            completed = self._flush()
            self._start(bucket, candle)
            return completed
        if bucket < self._bucket_ts:
            raise ValueError(
                f"resampler received out-of-order candle {candle.ts} "
                f"(current bucket {self._bucket_ts})"
            )
        self._high = max(self._high, candle.high)
        self._low = min(self._low, candle.low)
        self._close = candle.close
        self._volume += candle.volume
        return None

    def _start(self, bucket: datetime, candle: Candle) -> None:
        self._bucket_ts = bucket
        self._open = candle.open
        self._high = candle.high
        self._low = candle.low
        self._close = candle.close
        self._volume = candle.volume

    def _flush(self) -> Optional[Candle]:
        if self._bucket_ts is None:
            return None
        return Candle(
            ts=self._bucket_ts,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=self._volume,
        )

    def pending(self) -> Optional[Candle]:
        """The still-forming bar. Never fed to the strategy (Ch V: close only)."""
        return self._flush()


class MultiTimeframeStore:
    """Holds every screen for one symbol, derived from a single base feed.

    TFBS Ch IX requires three screens to agree.  Deriving them all from one
    base series guarantees they never disagree because of vendor differences.
    """

    def __init__(
        self,
        symbol: str,
        base_timeframe: TimeFrame,
        timeframes: Sequence[str],
        max_length: int = 5000,
    ) -> None:
        self.symbol = symbol
        self.base_timeframe = base_timeframe
        self.series: Dict[str, CandleSeries] = {}
        self._resamplers: Dict[str, Resampler] = {}

        wanted: List[TimeFrame] = []
        for label in timeframes:
            tf = TimeFrame.parse(label)
            if tf.minutes % base_timeframe.minutes != 0:
                raise ValueError(
                    f"{tf} is not a whole multiple of base timeframe {base_timeframe}"
                )
            wanted.append(tf)
        if base_timeframe not in wanted:
            wanted.insert(0, base_timeframe)

        for tf in sorted(set(wanted)):
            self.series[str(tf)] = CandleSeries(symbol, tf, max_length=max_length)
            if tf != base_timeframe:
                self._resamplers[str(tf)] = Resampler(tf)

    @property
    def base(self) -> CandleSeries:
        return self.series[str(self.base_timeframe)]

    def get(self, timeframe: str) -> Optional[CandleSeries]:
        return self.series.get(str(TimeFrame.parse(timeframe)))

    def require(self, timeframe: str) -> CandleSeries:
        s = self.get(timeframe)
        if s is None:
            raise KeyError(f"{self.symbol}: timeframe {timeframe} not tracked")
        return s

    def push(self, candle: Candle) -> Dict[str, Candle]:
        """Feed one base candle; returns every higher-TF bar that just closed.

        The returned mapping is what the strategy evaluates: only *closed*
        bars are ever acted upon, per TFBS Ch V-A criterion 1.
        """
        self.base.append(candle)
        closed: Dict[str, Candle] = {str(self.base_timeframe): candle}
        for label, resampler in self._resamplers.items():
            done = resampler.push(candle)
            if done is not None:
                self.series[label].append(done)
                closed[label] = done
        return closed

    def warmup(self, candles: Iterable[Candle]) -> None:
        for c in candles:
            self.push(c)

    def finalize(self) -> None:
        """Close any partially-formed higher-TF bars (end of a backtest)."""
        for label, resampler in self._resamplers.items():
            pending = resampler.pending()
            if pending is not None:
                self.series[label].append(pending)
