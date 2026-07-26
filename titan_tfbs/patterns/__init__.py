"""TFBS pattern modules.

* :mod:`head_shoulders` — Ch III, Pattern Module A (H&S / Inverse H&S)
* :mod:`double_top`     — Ch IV, Pattern Module B (Double & Triple Top/Bottom)
* :mod:`detector`       — Ch VI-A step 1 (SCAN) and step 2 (VALIDATE)

Per Ch VI-B, a level break with no H&S or DT/DB context is graded **F —
"NOT a TFBS setup — skip"**.  Nothing outside these modules can produce a
tradeable formation.
"""

from titan_tfbs.patterns.base import Pattern, PatternType, FilterResult
from titan_tfbs.patterns.detector import PatternDetector

__all__ = ["Pattern", "PatternType", "FilterResult", "PatternDetector"]
