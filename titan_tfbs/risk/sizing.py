"""Position sizing — TFBS Ch VIII-B and Appendix B.

    Position Size = (Account Balance x Risk %) / |Entry - Stop Loss|

    "Example: $500K account | 1% risk ($5,000) | Entry 1.3050 | SL 1.3100 |
     Distance 50 pips | Size = $5,000 / 0.0050 = 1,000,000 units (10 lots)."
                                                          — TFBS Ch VIII-B

The manual's formula assumes a quote currency equal to the account currency
and a unit value of 1.  Real sizing across the firm's asset classes needs the
instrument's value per price point (a gold lot is 100 oz, an ES contract is
$50 per index point) and a quote-to-account conversion.  Both are applied
here; for a USD-quoted instrument in a USD account the result reduces exactly
to the manual's formula.

The RMG's variant of the same rule — ``Position Size = (Account Balance x Risk
%) / (Stop Loss in Pips x Pip Value)`` — is the identical calculation with the
stop expressed in pips.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from titan_tfbs.instruments import Instrument


@dataclass
class PositionSizeResult:
    """Outcome of the Ch VIII-B calculation, including what rounding cost."""

    size: float
    #: The dollar risk the manual's formula budgets: balance x risk %.
    risk_budget: float
    #: What the rounded position actually risks if the stop is hit.
    risk_amount: float
    risk_pct: float
    stop_distance: float
    stop_pips: float
    value_per_point: float
    commission: float = 0.0
    ok: bool = True
    reason: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def tradeable(self) -> bool:
        return self.ok and self.size > 0


def quote_conversion_factor(
    instrument: Instrument,
    account_currency: str,
    price: float,
    external_rate: Optional[float] = None,
) -> Optional[float]:
    """Convert one unit of the instrument's quote currency into the account's.

    * quote == account (EURUSD, XAUUSD, ES in a USD account) -> 1.0
    * base  == account (USDJPY in a USD account)             -> 1 / price
    * anything else needs an external rate (e.g. EURGBP in USD)
    """
    quote = instrument.quote_currency.upper()
    account = account_currency.upper()
    if quote == account:
        return 1.0
    if instrument.base_currency and instrument.base_currency.upper() == account:
        return (1.0 / price) if price > 0 else None
    return external_rate


def calculate_position_size(
    instrument: Instrument,
    account_balance: float,
    risk_pct: float,
    entry_price: float,
    stop_loss: float,
    account_currency: str = "USD",
    include_commission: bool = True,
    external_rate: Optional[float] = None,
) -> PositionSizeResult:
    """Size a position so a stop-out costs exactly the budgeted risk, or less.

    Rounding is always DOWN to a tradeable increment: rounding up would push
    realised risk above the Ch VIII-A hard limit.
    """
    stop_distance = abs(entry_price - stop_loss)
    stop_pips = instrument.to_pips(stop_distance)
    risk_budget = account_balance * (risk_pct / 100.0)

    if account_balance <= 0:
        return PositionSizeResult(
            0.0, 0.0, 0.0, 0.0, stop_distance, stop_pips, 0.0,
            ok=False, reason="account balance is zero or negative",
        )
    if risk_pct <= 0:
        return PositionSizeResult(
            0.0, risk_budget, 0.0, 0.0, stop_distance, stop_pips, 0.0,
            ok=False, reason="risk percentage must be positive",
        )
    if stop_distance <= 0:
        return PositionSizeResult(
            0.0, risk_budget, 0.0, 0.0, 0.0, 0.0, 0.0,
            ok=False,
            reason="entry and stop are identical — no defined invalidation "
                   "(Ch II pillar 3)",
        )

    factor = quote_conversion_factor(
        instrument, account_currency, entry_price, external_rate
    )
    if factor is None:
        return PositionSizeResult(
            0.0, risk_budget, 0.0, 0.0, stop_distance, stop_pips, 0.0,
            ok=False,
            reason=(
                f"no {instrument.quote_currency}->{account_currency} rate available "
                f"for {instrument.symbol}"
            ),
        )

    value_per_point = instrument.value_per_point * factor
    if value_per_point <= 0:
        return PositionSizeResult(
            0.0, risk_budget, 0.0, 0.0, stop_distance, stop_pips, 0.0,
            ok=False, reason=f"{instrument.symbol} has no positive point value",
        )

    # Cost per unit of size if the stop is hit, including the round turn.
    per_unit_cost = stop_distance * value_per_point
    if include_commission:
        per_unit_cost += instrument.commission_per_contract

    raw_size = risk_budget / per_unit_cost
    size = instrument.round_size_down(raw_size)

    notes: List[str] = []
    if size <= 0:
        return PositionSizeResult(
            0.0, risk_budget, 0.0, 0.0, stop_distance, stop_pips, value_per_point,
            ok=False,
            reason=(
                f"risk budget {risk_budget:,.2f} is below the cost of the minimum "
                f"{instrument.min_size} {instrument.symbol} position "
                f"({per_unit_cost * instrument.min_size:,.2f}) — "
                f"skip rather than over-risk (Ch VIII-A)"
            ),
        )
    if raw_size >= instrument.max_size:
        notes.append(
            f"size capped at the {instrument.max_size} venue maximum "
            f"(wanted {raw_size:.2f})"
        )

    commission = size * instrument.commission_per_contract if include_commission else 0.0
    risk_amount = size * stop_distance * value_per_point + commission
    return PositionSizeResult(
        size=size,
        risk_budget=risk_budget,
        risk_amount=risk_amount,
        risk_pct=risk_amount / account_balance * 100.0,
        stop_distance=stop_distance,
        stop_pips=stop_pips,
        value_per_point=value_per_point,
        commission=commission,
        notes=notes,
    )


def position_value(instrument: Instrument, size: float, price: float) -> float:
    """Notional value of a position, for exposure reporting."""
    return size * instrument.contract_size * price


def pnl_for(
    instrument: Instrument,
    size: float,
    entry: float,
    exit_price: float,
    direction_sign: int,
    account_currency: str = "USD",
    external_rate: Optional[float] = None,
    include_commission: bool = True,
) -> float:
    """Realised P&L in account currency for a closed position."""
    factor = quote_conversion_factor(
        instrument, account_currency, exit_price, external_rate
    ) or 1.0
    gross = (exit_price - entry) * direction_sign * size * instrument.value_per_point * factor
    commission = size * instrument.commission_per_contract if include_commission else 0.0
    return gross - commission
