"""Titan Markets LLC — Titan Formation Breakout System (TFBS) algorithmic trading engine.

The package is organised in layers:

    data/        market data feeds (CSV, synthetic, live adapter interface)
    core/        candles, indicators, sessions, market structure primitives
    strategy/    HTF bias, formation detection, the TFBS signal engine
    risk/        position sizing + the firm drawdown / compliance state machine
    execution/   order model, broker interface, paper broker
    journal/     trade journal (per the guide's "journal every trade" rule)
    backtest/    event-driven backtester and performance metrics
    bot.py       the live orchestration loop

Risk parameters are implemented directly from the Titan Markets *Beginner's
Complete Guide to Risk Management in Prop Trading (2026 Edition)*.  Entry logic
follows the structural principles that guide describes; see
``config/titan.yaml`` for which parameters are firm-specified and which are
inferred defaults awaiting the full 18-page TFBS document.
"""

__version__ = "1.0.0"
__firm__ = "Titan Markets LLC"

from titan_tfbs.config import TitanConfig, load_config
from titan_tfbs.strategy.signals import TradeSignal, Direction, Conviction

__all__ = [
    "TitanConfig",
    "load_config",
    "TradeSignal",
    "Direction",
    "Conviction",
    "__version__",
    "__firm__",
]
