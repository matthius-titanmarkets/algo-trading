"""The TFBS decision pipeline (Ch VI-A).

    1. SCAN     -> :mod:`titan_tfbs.patterns.detector`
    2. VALIDATE -> :mod:`titan_tfbs.patterns` quality filters
    3. CONFIRM  -> :mod:`titan_tfbs.strategy.breakout`
    4. SCORE    -> :mod:`titan_tfbs.strategy.confluence`
    5. SIZE     -> :mod:`titan_tfbs.risk`
    6. EXECUTE  -> :mod:`titan_tfbs.execution`
    7. MANAGE   -> :mod:`titan_tfbs.execution.manager`

:mod:`titan_tfbs.strategy.tfbs` wires them together.
"""

from titan_tfbs.strategy.signals import (
    Conviction,
    Direction,
    Grade,
    TradeSignal,
)
from titan_tfbs.strategy.tfbs import TFBSStrategy

__all__ = ["Direction", "Grade", "Conviction", "TradeSignal", "TFBSStrategy"]
