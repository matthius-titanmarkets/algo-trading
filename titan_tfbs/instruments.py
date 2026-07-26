"""Instrument catalog for every asset class Titan Markets LLC trades.

The universe and its optimal timeframes come straight from the TFBS Master
Strategy Manual, Ch I ("Applicable Markets"):

    Forex        EURUSD, GBPUSD, USDJPY, XAUUSD      1H, 4H, Daily
    Commodities  Gold (XAUUSD), Crude Oil (CL)       1H, 4H, Daily
    Indices      NQ (Nasdaq), ES (S&P 500), RTY      15M, 1H, 4H
    Equities     Large-cap / high-liquidity only     Daily, Weekly

Contract specifications (tick size, tick value, contract size) are exchange
facts, not strategy parameters, and are used by :mod:`titan_tfbs.risk.sizing`
to convert the manual's dollar-risk formula into a tradeable position size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class AssetClass(str, Enum):
    FOREX = "forex"
    METALS = "metals"
    COMMODITIES = "commodities"
    INDICES = "indices"
    EQUITIES = "equities"


@dataclass(frozen=True)
class TimeframeProfile:
    """TFBS Ch IX — the three screens, per instrument.

    Screen 1 TREND (macro bias), Screen 2 PATTERN (formation identification),
    Screen 3 ENTRY (precision trigger).
    """

    trend: List[str]
    pattern: List[str]
    entry: List[str]

    @property
    def primary_pattern_tf(self) -> str:
        return self.pattern[0]

    @property
    def primary_entry_tf(self) -> str:
        return self.entry[0]

    def all_timeframes(self) -> List[str]:
        seen: List[str] = []
        for tf in [*self.trend, *self.pattern, *self.entry]:
            if tf not in seen:
                seen.append(tf)
        return seen


#: The firm's standard three-screen stack for swing instruments: daily bias,
#: 4H/1H formations, 15M/5M triggers (TFBS Ch IX).
SWING_PROFILE = TimeframeProfile(
    trend=["1D", "1W"], pattern=["4H", "1H"], entry=["15M", "5M"]
)
#: Indices run one screen faster per Ch I ("15M, 1H, 4H").
INDEX_PROFILE = TimeframeProfile(
    trend=["1D"], pattern=["4H", "1H"], entry=["15M", "5M"]
)
#: Equities are a daily/weekly product per Ch I.
EQUITY_PROFILE = TimeframeProfile(
    trend=["1W", "1D"], pattern=["1D", "4H"], entry=["1H", "15M"]
)


@dataclass(frozen=True)
class Instrument:
    """Contract specification plus the firm metadata TFBS needs."""

    symbol: str
    asset_class: AssetClass
    description: str
    #: Smallest price increment the venue quotes.
    tick_size: float
    #: Account-currency value of one tick for one contract/lot.
    tick_value: float
    #: Units of the underlying per contract/lot (informational).
    contract_size: float
    #: Smallest tradeable increment: 0.01 lots for FX, 1 contract for futures.
    size_step: float
    min_size: float
    max_size: float
    #: Price move that constitutes "1 pip" in the manual's language.
    pip_size: float
    quote_currency: str
    base_currency: Optional[str] = None
    #: Round-turn commission per contract/lot in account currency.
    commission_per_contract: float = 0.0
    #: Typical spread expressed in price units.
    typical_spread: float = 0.0
    #: TFBS Ch VIII-A: "Max Correlated Exposure — 2 trades same
    #: currency/sector."  Instruments sharing a group count against that cap.
    correlation_groups: tuple = ()
    timeframes: TimeframeProfile = SWING_PROFILE
    price_precision: int = 5
    #: Venue trading hours as UTC (start_hour, end_hour); None = 24h.
    session_utc: Optional[tuple] = None

    # -- derived -----------------------------------------------------------

    @property
    def value_per_point(self) -> float:
        """Account-currency value of a 1.00 price move for one contract/lot.

        This is the denominator of the manual's sizing formula once the stop
        distance is expressed in price rather than pips (Ch VIII-B).
        """
        return self.tick_value / self.tick_size

    @property
    def value_per_pip(self) -> float:
        """Account-currency value of one pip for one contract/lot."""
        return self.value_per_point * self.pip_size

    def to_pips(self, price_distance: float) -> float:
        return price_distance / self.pip_size

    def round_price(self, price: float) -> float:
        steps = round(price / self.tick_size)
        return round(steps * self.tick_size, self.price_precision)

    def round_size_down(self, size: float) -> float:
        """Round a position size DOWN to a tradeable increment.

        Always down: rounding up would push realised risk above the cap the
        manual sets as a hard limit (Ch VIII-A).
        """
        if size <= 0:
            return 0.0
        steps = int(size / self.size_step + 1e-9)
        rounded = steps * self.size_step
        if rounded < self.min_size:
            return 0.0
        return round(min(rounded, self.max_size), 8)


def _fx(
    symbol: str,
    base: str,
    quote: str,
    pip: float,
    precision: int,
    spread: float,
    groups: tuple,
) -> Instrument:
    """Standard 100k-unit FX lot."""
    return Instrument(
        symbol=symbol,
        asset_class=AssetClass.FOREX,
        description=f"{base}/{quote} spot FX",
        tick_size=pip / 10.0,
        # 100,000 units * (pip/10) price move, valued in the quote currency.
        tick_value=100_000 * (pip / 10.0),
        contract_size=100_000,
        size_step=0.01,
        min_size=0.01,
        max_size=100.0,
        pip_size=pip,
        quote_currency=quote,
        base_currency=base,
        commission_per_contract=7.0,
        typical_spread=spread,
        correlation_groups=groups,
        timeframes=SWING_PROFILE,
        price_precision=precision,
    )


def _future(
    symbol: str,
    desc: str,
    tick_size: float,
    tick_value: float,
    asset_class: AssetClass,
    groups: tuple,
    profile: TimeframeProfile,
    precision: int = 2,
    commission: float = 4.0,
    session: Optional[tuple] = None,
) -> Instrument:
    return Instrument(
        symbol=symbol,
        asset_class=asset_class,
        description=desc,
        tick_size=tick_size,
        tick_value=tick_value,
        contract_size=1,
        size_step=1.0,
        min_size=1.0,
        max_size=500.0,
        pip_size=tick_size,
        quote_currency="USD",
        commission_per_contract=commission,
        typical_spread=tick_size,
        correlation_groups=groups,
        timeframes=profile,
        price_precision=precision,
        session_utc=session,
    )


#: The firm's tradeable universe (TFBS Ch I).
CATALOG: Dict[str, Instrument] = {
    # ---- Forex majors (Ch I: Forex) -------------------------------------
    "EURUSD": _fx("EURUSD", "EUR", "USD", 0.0001, 5, 0.00008, ("EUR", "USD", "fx_major")),
    "GBPUSD": _fx("GBPUSD", "GBP", "USD", 0.0001, 5, 0.00012, ("GBP", "USD", "fx_major")),
    "USDJPY": _fx("USDJPY", "USD", "JPY", 0.01, 3, 0.010, ("USD", "JPY", "fx_major")),
    # ---- Metals (Ch I: Forex + Commodities both list XAUUSD) ------------
    "XAUUSD": Instrument(
        symbol="XAUUSD",
        asset_class=AssetClass.METALS,
        description="Spot Gold vs US Dollar — firm's flagship instrument",
        tick_size=0.01,
        tick_value=1.0,          # 100 oz * $0.01
        contract_size=100,
        size_step=0.01,
        min_size=0.01,
        max_size=50.0,
        pip_size=0.10,           # firm convention: 1 pip = $0.10 on gold
        quote_currency="USD",
        base_currency="XAU",
        commission_per_contract=7.0,
        typical_spread=0.25,
        correlation_groups=("metals", "USD", "gold"),
        timeframes=SWING_PROFILE,
        price_precision=2,
    ),
    # ---- Commodities (Ch I: Crude Oil) ----------------------------------
    "CL": _future(
        "CL", "WTI Crude Oil futures (NYMEX)", 0.01, 10.0,
        AssetClass.COMMODITIES, ("energy", "commodities"), SWING_PROFILE, 2, 4.0,
    ),
    "MCL": _future(
        "MCL", "Micro WTI Crude Oil futures (NYMEX)", 0.01, 1.0,
        AssetClass.COMMODITIES, ("energy", "commodities"), SWING_PROFILE, 2, 1.0,
    ),
    "GC": _future(
        "GC", "Gold futures (COMEX)", 0.10, 10.0,
        AssetClass.COMMODITIES, ("metals", "gold", "commodities"), SWING_PROFILE, 2, 4.0,
    ),
    "MGC": _future(
        "MGC", "Micro Gold futures (COMEX)", 0.10, 1.0,
        AssetClass.COMMODITIES, ("metals", "gold", "commodities"), SWING_PROFILE, 2, 1.5,
    ),
    # ---- Equity index futures (Ch I: Indices) ---------------------------
    "ES": _future(
        "ES", "E-mini S&P 500 futures (CME)", 0.25, 12.50,
        AssetClass.INDICES, ("us_equity_index", "sp500"), INDEX_PROFILE, 2, 4.0,
    ),
    "MES": _future(
        "MES", "Micro E-mini S&P 500 futures (CME)", 0.25, 1.25,
        AssetClass.INDICES, ("us_equity_index", "sp500"), INDEX_PROFILE, 2, 1.0,
    ),
    "NQ": _future(
        "NQ", "E-mini Nasdaq-100 futures (CME)", 0.25, 5.00,
        AssetClass.INDICES, ("us_equity_index", "nasdaq"), INDEX_PROFILE, 2, 4.0,
    ),
    "MNQ": _future(
        "MNQ", "Micro E-mini Nasdaq-100 futures (CME)", 0.25, 0.50,
        AssetClass.INDICES, ("us_equity_index", "nasdaq"), INDEX_PROFILE, 2, 1.0,
    ),
    "RTY": _future(
        "RTY", "E-mini Russell 2000 futures (CME)", 0.10, 5.00,
        AssetClass.INDICES, ("us_equity_index", "russell"), INDEX_PROFILE, 2, 4.0,
    ),
    "M2K": _future(
        "M2K", "Micro E-mini Russell 2000 futures (CME)", 0.10, 0.50,
        AssetClass.INDICES, ("us_equity_index", "russell"), INDEX_PROFILE, 2, 1.0,
    ),
}


def _equity(symbol: str, name: str, sector: str) -> Instrument:
    """TFBS Ch I: equities are restricted to large-cap / high-liquidity names."""
    return Instrument(
        symbol=symbol,
        asset_class=AssetClass.EQUITIES,
        description=f"{name} common stock",
        tick_size=0.01,
        tick_value=0.01,
        contract_size=1,
        size_step=1.0,
        min_size=1.0,
        max_size=1_000_000.0,
        pip_size=0.01,
        quote_currency="USD",
        commission_per_contract=0.005,
        typical_spread=0.01,
        correlation_groups=("equities", sector),
        timeframes=EQUITY_PROFILE,
        price_precision=2,
        session_utc=(13, 20),  # 09:30-16:00 ET, approximated in whole UTC hours
    )


#: Large-cap equity names permitted under Ch I ("high-liquidity names only").
for _sym, _name, _sector in [
    ("AAPL", "Apple Inc.", "technology"),
    ("MSFT", "Microsoft Corp.", "technology"),
    ("NVDA", "NVIDIA Corp.", "technology"),
    ("AMZN", "Amazon.com Inc.", "consumer"),
    ("META", "Meta Platforms Inc.", "technology"),
    ("GOOGL", "Alphabet Inc.", "technology"),
    ("TSLA", "Tesla Inc.", "consumer"),
    ("JPM", "JPMorgan Chase & Co.", "financials"),
    ("XOM", "Exxon Mobil Corp.", "energy"),
    ("SPY", "SPDR S&P 500 ETF Trust", "us_equity_index"),
    ("QQQ", "Invesco QQQ Trust", "us_equity_index"),
]:
    CATALOG[_sym] = _equity(_sym, _name, _sector)


#: TFBS Ch I primary instruments — what the bot scans when no symbols are
#: configured explicitly.
FIRM_PRIMARY_SYMBOLS: List[str] = [
    "XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "CL", "NQ", "ES", "RTY",
]


def get_instrument(symbol: str) -> Instrument:
    key = symbol.upper()
    if key not in CATALOG:
        raise KeyError(
            f"{symbol} is not in the Titan Markets instrument catalog. "
            f"TFBS Ch I restricts trading to the firm's approved universe."
        )
    return CATALOG[key]


def register_instrument(instrument: Instrument) -> None:
    """Add an instrument to the catalog (e.g. an approved new large cap)."""
    CATALOG[instrument.symbol.upper()] = instrument


def symbols_for_asset_class(asset_class: AssetClass) -> List[str]:
    return [s for s, i in CATALOG.items() if i.asset_class is asset_class]


def shares_correlation_group(a: Instrument, b: Instrument) -> bool:
    """TFBS Ch VIII-A — same currency or sector."""
    return bool(set(a.correlation_groups) & set(b.correlation_groups))
