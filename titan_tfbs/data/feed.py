"""Market data feeds.

The engine consumes a single base timeframe per symbol (5M by default) and
derives every higher screen from it, so a feed only has to deliver one series.

Implement :class:`DataFeed` to wire a live vendor (the firm's stack runs
Bloomberg, Koyfin and TradingView per RMG s.07); nothing above this layer
cares where candles come from.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

from titan_tfbs.core.candles import Candle

#: Column name aliases accepted by the CSV loader.
_ALIASES = {
    "ts": {"ts", "time", "timestamp", "date", "datetime", "open_time"},
    "open": {"open", "o"},
    "high": {"high", "h"},
    "low": {"low", "l"},
    "close": {"close", "c", "last"},
    "volume": {"volume", "vol", "v", "tickvolume", "tick_volume"},
}


class DataFeed(ABC):
    """Source of base-timeframe candles for one or more symbols."""

    @abstractmethod
    def symbols(self) -> List[str]:
        ...

    @abstractmethod
    def history(self, symbol: str) -> Iterable[Candle]:
        """All candles currently available, oldest first."""

    def stream(self, symbol: str) -> Iterator[Candle]:
        """Yield candles as they close. The default replays history once."""
        yield from self.history(symbol)


class InMemoryFeed(DataFeed):
    """Feed backed by pre-loaded candles — used by tests and the backtester."""

    def __init__(self, data: Dict[str, Sequence[Candle]]) -> None:
        self._data = {s: list(c) for s, c in data.items()}

    def symbols(self) -> List[str]:
        return list(self._data)

    def history(self, symbol: str) -> List[Candle]:
        return self._data.get(symbol, [])

    def add(self, symbol: str, candles: Iterable[Candle]) -> None:
        self._data.setdefault(symbol, []).extend(candles)


class CSVFeed(DataFeed):
    """Loads ``SYMBOL.csv`` files from a directory.

    Accepts the usual column spellings (``time``/``timestamp``/``date``,
    ``o/h/l/c/v``) and both epoch seconds and ISO-8601 timestamps.
    """

    def __init__(
        self,
        directory: str | Path,
        symbols: Optional[Sequence[str]] = None,
        suffix: str = ".csv",
    ) -> None:
        self.directory = Path(directory)
        if not self.directory.exists():
            raise FileNotFoundError(f"data directory not found: {self.directory}")
        self.suffix = suffix
        self._symbols = (
            list(symbols)
            if symbols
            else sorted(p.stem.upper() for p in self.directory.glob(f"*{suffix}"))
        )
        self._cache: Dict[str, List[Candle]] = {}

    def symbols(self) -> List[str]:
        return list(self._symbols)

    def path_for(self, symbol: str) -> Path:
        return self.directory / f"{symbol}{self.suffix}"

    def history(self, symbol: str) -> List[Candle]:
        key = symbol.upper()
        if key in self._cache:
            return self._cache[key]
        path = self.path_for(key)
        if not path.exists():
            raise FileNotFoundError(f"no data file for {symbol}: {path}")
        candles = load_csv(path)
        self._cache[key] = candles
        return candles


def load_csv(path: str | Path) -> List[Candle]:
    """Read an OHLCV CSV into candles, sorted and de-duplicated by timestamp."""
    rows: List[Candle] = []
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return rows
        mapping = _resolve_columns(reader.fieldnames)
        missing = [k for k in ("ts", "open", "high", "low", "close") if k not in mapping]
        if missing:
            raise ValueError(f"{path}: missing required column(s) {missing}")
        for line in reader:
            try:
                candle = Candle(
                    ts=_parse_ts(line[mapping["ts"]]),
                    open=float(line[mapping["open"]]),
                    high=float(line[mapping["high"]]),
                    low=float(line[mapping["low"]]),
                    close=float(line[mapping["close"]]),
                    volume=(
                        float(line[mapping["volume"]] or 0.0)
                        if "volume" in mapping
                        else 0.0
                    ),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}: bad row {line}: {exc}") from exc
            rows.append(candle)
    rows.sort(key=lambda c: c.ts)
    deduped: List[Candle] = []
    for c in rows:
        if deduped and deduped[-1].ts == c.ts:
            deduped[-1] = c
            continue
        deduped.append(c)
    return deduped


def _resolve_columns(fieldnames: Sequence[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for name in fieldnames:
        key = name.strip().lower().replace(" ", "_")
        for canonical, aliases in _ALIASES.items():
            if key in aliases and canonical not in mapping:
                mapping[canonical] = name
    return mapping


def _parse_ts(raw: str) -> datetime:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        value = int(text)
        if value > 10_000_000_000:      # milliseconds
            value //= 1000
        return datetime.fromtimestamp(value, tz=timezone.utc)
    normalised = text.replace("Z", "+00:00").replace("/", "-")
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d-%m-%Y %H:%M"):
            try:
                dt = datetime.strptime(normalised, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"unrecognised timestamp '{raw}'")
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def write_csv(path: str | Path, candles: Iterable[Candle]) -> None:
    """Write candles back out — handy for capturing a live session."""
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in candles:
            writer.writerow(
                [c.ts.isoformat(), c.open, c.high, c.low, c.close, c.volume]
            )
