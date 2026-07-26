import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from titan_tfbs.config import TitanConfig  # noqa: E402
from titan_tfbs.core.candles import Candle  # noqa: E402

START = datetime(2026, 1, 5, tzinfo=timezone.utc)


@pytest.fixture
def config() -> TitanConfig:
    cfg = TitanConfig()
    cfg.journal.enabled = False
    return cfg


@pytest.fixture
def start() -> datetime:
    return START


def make_candle(ts, o, h, l, c, v=1000.0) -> Candle:
    return Candle(ts, o, h, l, c, v)
