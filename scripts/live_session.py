#!/usr/bin/env python3
"""Thin wrapper kept for the documented path; the logic lives in the package.

    python scripts/live_session.py --symbols XAUUSD,NQ

Equivalent to `python main.py` and `titan-tfbs live`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from titan_tfbs.live import main

if __name__ == "__main__":
    raise SystemExit(main(prog="python scripts/live_session.py"))
