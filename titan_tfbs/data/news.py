"""Economic calendar and the Ch XII-A6 news blackout.

    "No Trading During News: No entries within 15 min before or 5 min after
     high-impact releases."                                  — TFBS Ch XII-A6
    "No high-impact news within 30 minutes?"                 — Appendix A

An event applies to a symbol when its currency or its explicit symbol list
matches: a USD release blacks out EURUSD, XAUUSD, ES, NQ and every other
USD-quoted instrument the firm trades.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from titan_tfbs.instruments import CATALOG, get_instrument


class Impact:
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class NewsEvent:
    """One scheduled economic release."""

    ts: datetime
    name: str
    impact: str = Impact.HIGH
    #: Currency the release belongs to, e.g. USD, EUR, GBP, JPY.
    currency: str = "USD"
    #: Optional explicit symbol scope; empty means "every symbol touching the
    #: currency".
    symbols: tuple = ()

    @property
    def is_high_impact(self) -> bool:
        return self.impact.lower() == Impact.HIGH

    def affects(self, symbol: str) -> bool:
        key = symbol.upper()
        if self.symbols:
            return key in {s.upper() for s in self.symbols}
        instrument = CATALOG.get(key)
        if instrument is None:
            return False
        ccy = self.currency.upper()
        if instrument.quote_currency.upper() == ccy:
            return True
        if instrument.base_currency and instrument.base_currency.upper() == ccy:
            return True
        return ccy in {g.upper() for g in instrument.correlation_groups}


@dataclass
class EconomicCalendar:
    """A sorted set of releases, queryable for blackout windows."""

    events: List[NewsEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.events.sort(key=lambda e: e.ts)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_file(cls, path: str | Path) -> "EconomicCalendar":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"economic calendar not found: {p}")
        if p.suffix.lower() == ".json":
            data = json.loads(p.read_text())
            return cls([_event_from_dict(d) for d in data])
        events: List[NewsEvent] = []
        with open(p, newline="") as handle:
            for row in csv.DictReader(handle):
                events.append(_event_from_dict({k.lower(): v for k, v in row.items()}))
        return cls(events)

    def add(self, event: NewsEvent) -> None:
        self.events.append(event)
        self.events.sort(key=lambda e: e.ts)

    # -- queries -----------------------------------------------------------

    def blackout(
        self,
        ts: datetime,
        symbol: str,
        before_min: int = 30,
        after_min: int = 5,
    ) -> Optional[str]:
        """Return a description when ``ts`` falls inside a blackout window."""
        ts = _utc(ts)
        window_start = ts - timedelta(minutes=after_min)
        window_end = ts + timedelta(minutes=before_min)
        for event in self.events:
            if not event.is_high_impact:
                continue
            if event.ts < window_start or event.ts > window_end:
                continue
            if not event.affects(symbol):
                continue
            delta = (event.ts - ts).total_seconds() / 60.0
            when = f"in {delta:.0f} min" if delta >= 0 else f"{-delta:.0f} min ago"
            return (
                f"high-impact {event.currency} release '{event.name}' {when} "
                f"— blackout -{before_min}/+{after_min} min (Ch XII-A6)"
            )
        return None

    def imminent(
        self, ts: datetime, symbol: str, within_min: int = 30
    ) -> Optional[NewsEvent]:
        """Ch X-C — a high-impact release approaching an open position."""
        ts = _utc(ts)
        for event in self.events:
            if not event.is_high_impact or not event.affects(symbol):
                continue
            delta = (event.ts - ts).total_seconds() / 60.0
            if 0 <= delta <= within_min:
                return event
        return None

    def between(self, start: datetime, end: datetime) -> List[NewsEvent]:
        return [e for e in self.events if _utc(start) <= e.ts <= _utc(end)]


def _event_from_dict(data: Dict[str, object]) -> NewsEvent:
    raw_ts = data.get("ts") or data.get("time") or data.get("datetime") or data.get("date")
    if raw_ts is None:
        raise ValueError(f"calendar row has no timestamp: {data}")
    if isinstance(raw_ts, datetime):
        ts = _utc(raw_ts)
    else:
        text = str(raw_ts).strip().replace("Z", "+00:00")
        ts = _utc(datetime.fromisoformat(text))
    symbols = data.get("symbols") or ()
    if isinstance(symbols, str):
        symbols = tuple(s.strip() for s in symbols.split("|") if s.strip())
    return NewsEvent(
        ts=ts,
        name=str(data.get("name") or data.get("event") or "unnamed release"),
        impact=str(data.get("impact") or Impact.HIGH).lower(),
        currency=str(data.get("currency") or "USD").upper(),
        symbols=tuple(symbols),
    )


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
