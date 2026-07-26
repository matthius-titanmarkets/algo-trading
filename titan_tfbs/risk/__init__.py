"""Risk management — TFBS Ch VIII and the Titan Risk Management Guide.

    "Risk management is the structural foundation of TFBS. No setup, however
     high-conviction, justifies violating these parameters."   — TFBS Ch VIII

* :mod:`sizing`     — Ch VIII-B / Appendix B position sizing across asset classes
* :mod:`limits`     — Ch VIII-A limits + the Risk Guide drawdown ladder
* :mod:`compliance` — Ch XII mandatory rules and the escalation ladder
* :mod:`manager`    — the gate every signal passes through before execution
"""

from titan_tfbs.risk.limits import AccountState, AccountStatus, DrawdownMonitor
from titan_tfbs.risk.manager import RiskDecision, RiskManager
from titan_tfbs.risk.sizing import PositionSizeResult, calculate_position_size

__all__ = [
    "AccountState",
    "AccountStatus",
    "DrawdownMonitor",
    "RiskManager",
    "RiskDecision",
    "PositionSizeResult",
    "calculate_position_size",
]
