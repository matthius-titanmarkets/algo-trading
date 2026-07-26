"""Trade management and execution — TFBS Ch X, Ch VII and Appendix A."""

from datetime import datetime, timedelta, timezone

import pytest

from titan_tfbs.config import EntryMethod, TitanConfig
from titan_tfbs.core.candles import Candle
from titan_tfbs.execution.broker import PaperBroker
from titan_tfbs.execution.manager import TradeContext, TradeManager
from titan_tfbs.execution.orders import ExitReason, Position
from titan_tfbs.instruments import get_instrument
from titan_tfbs.patterns.base import Pattern, PatternType
from titan_tfbs.core.structure import Line, Pivot, PivotType, Trend
from titan_tfbs.strategy.signals import (
    BreakoutEvent,
    ConfluenceScore,
    Direction,
    Grade,
    TradeSignal,
)

UTC = timezone.utc
NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def make_signal(
    symbol="XAUUSD",
    direction=Direction.LONG,
    entry=2600.0,
    stop=2590.0,
    # 2.2R to TP1: the fixture must clear the Ch VIII-A 2:1 minimum *after*
    # spread and slippage, which the paper broker revalidates at the fill.
    tp1=2622.0,
    tp2=2632.0,
    tp3=2642.0,
):
    pivot = Pivot(0, NOW, entry, PivotType.HIGH, 1000.0)
    pattern = Pattern(
        type=PatternType.INVERSE_HEAD_SHOULDERS
        if direction is Direction.LONG
        else PatternType.HEAD_SHOULDERS,
        symbol=symbol,
        timeframe="1H",
        pivots=[pivot],
        trigger_line=Line(0, entry, 10, entry),
        measured_height=abs(tp1 - entry),
        structural_invalidation=stop,
        start_index=0,
        end_index=10,
        start_ts=NOW - timedelta(hours=10),
        end_ts=NOW,
        quality_points=2,
        quality=1.0,
        prior_trend=Trend.UP,
        atr=5.0,
        state="confirmed",
    )
    pattern.set_time_anchor(NOW, 60)
    breakout = BreakoutEvent(
        pattern=pattern,
        ts=NOW,
        candle=Candle(NOW, entry, entry, entry, entry, 1000),
        timeframe="15M",
        level=entry,
        close=entry,
        atr=5.0,
        volume_ratio=2.0,
        volume_surge=True,
    )
    score = ConfluenceScore(
        pattern_quality=2, breakout_strength=2, retest_confirm=1,
        htf_alignment=2, sr_confluence=0, rr_ratio=1, clean_path=1,
    )
    return TradeSignal(
        symbol=symbol,
        direction=direction,
        pattern=pattern,
        breakout=breakout,
        entry_method=EntryMethod.B_STANDARD,
        entry_price=entry,
        stop_loss=stop,
        take_profit_1=tp1,
        take_profit_2=tp2,
        take_profit_3=tp3,
        risk_reward=abs(tp1 - entry) / abs(entry - stop),
        score=score,
        grade=Grade.ELITE,
        created_ts=NOW,
        trend_timeframe="1D",
        pattern_timeframe="1H",
        entry_timeframe="15M",
    )


def bar(ts, o, h, l, c, v=1000.0):
    return Candle(ts, o, h, l, c, v)


@pytest.fixture
def setup():
    cfg = TitanConfig()
    inst = get_instrument("XAUUSD")
    broker = PaperBroker(cfg.execution, tp_config=cfg.trade_management)
    manager = TradeManager(cfg.trade_management, broker)
    signal = make_signal()
    position = broker.open_position(signal, inst, 10.0, NOW, signal.entry_price)
    return cfg, broker, manager, position


class TestPositionMechanics:
    def test_r_unit_is_fixed_at_entry(self, setup):
        _, _, _, pos = setup
        assert pos.r_unit == pytest.approx(abs(pos.entry_price - pos.stop_loss))
        assert pos.r_at(pos.price_at_r(2.0)) == pytest.approx(2.0)

    def test_entry_crosses_the_spread(self):
        cfg = TitanConfig()
        broker = PaperBroker(cfg.execution, tp_config=cfg.trade_management)
        signal = make_signal()
        pos = broker.open_position(
            signal, get_instrument("XAUUSD"), 1.0, NOW, signal.entry_price
        )
        assert pos.entry_price > signal.entry_price   # a buyer pays the offer

    def test_a_fill_that_breaks_the_two_to_one_minimum_is_refused(self):
        """Ch VIII-A is a hard limit at the fill, not just on paper."""
        cfg = TitanConfig()
        broker = PaperBroker(cfg.execution, tp_config=cfg.trade_management)
        signal = make_signal(entry=2600.0, stop=2590.0, tp1=2605.0)  # only 0.5R
        assert broker.open_position(
            signal, get_instrument("XAUUSD"), 1.0, NOW, signal.entry_price
        ) is None

    def test_stops_only_move_in_your_favour(self, setup):
        """Ch XIV-A5: 'Stop only moves in your favor. Never widen.'"""
        _, broker, _, pos = setup
        original = pos.stop_loss
        assert not broker.modify_stop(pos, original - 5)
        assert pos.stop_loss == original
        assert broker.modify_stop(pos, original + 5)
        assert pos.stop_loss > original


class TestStopProtocol:
    """Ch X-A."""

    def test_breakeven_moves_at_one_r_and_not_before(self, setup):
        cfg, _, manager, pos = setup
        ctx = TradeContext(
            candle=bar(NOW, 2600, pos.price_at_r(0.9), 2599, 2604),
            atr=5.0, recent_candles=[],
        )
        manager.update(pos, ctx)
        assert not pos.breakeven_done, "moved to BE before 1R (RMG s.03)"

        ctx = TradeContext(
            candle=bar(NOW + timedelta(minutes=15), 2604, pos.price_at_r(1.1), 2603, 2609),
            atr=5.0, recent_candles=[],
        )
        manager.update(pos, ctx)
        assert pos.breakeven_done
        assert pos.stop_loss >= pos.entry_price

    def test_trailing_engages_after_one_and_a_half_r(self, setup):
        cfg, _, manager, pos = setup
        recent = [
            bar(NOW + timedelta(minutes=15 * i), 2600 + i, 2602 + i, 2598 + i, 2601 + i)
            for i in range(20)
        ]
        ctx = TradeContext(
            candle=bar(NOW, 2610, pos.price_at_r(1.6), 2609, 2615),
            atr=5.0, recent_candles=recent,
        )
        manager.update(pos, ctx)
        assert pos.trailing_active

    def test_stop_hit_closes_the_position(self, setup):
        _, _, manager, pos = setup
        fills = manager.update(
            pos,
            TradeContext(candle=bar(NOW, 2600, 2601, 2580, 2585), atr=5.0),
        )
        assert fills and fills[0].reason is ExitReason.STOP_LOSS
        assert not pos.is_open

    def test_stop_is_assumed_to_hit_before_the_target_on_the_same_bar(self, setup):
        """Risk first: a bar spanning both is treated as a loss."""
        _, _, manager, pos = setup
        fills = manager.update(
            pos,
            TradeContext(candle=bar(NOW, 2600, 2625, 2580, 2600), atr=5.0),
        )
        assert fills[0].reason is ExitReason.STOP_LOSS


class TestTakeProfitLadder:
    """Ch X-B: TP1 50%, TP2 30%, TP3 20%."""

    def test_allocations_match_the_manual(self):
        cfg = TitanConfig().trade_management
        assert (cfg.tp1_allocation, cfg.tp2_allocation, cfg.tp3_allocation) == (
            0.50, 0.30, 0.20
        )
        assert cfg.tp1_allocation + cfg.tp2_allocation + cfg.tp3_allocation == 1.0

    def test_tp1_takes_half_and_leaves_the_rest_running(self, setup):
        _, _, manager, pos = setup
        initial = pos.initial_size
        fills = manager.update(
            pos, TradeContext(candle=bar(NOW, 2600, 2623, 2599, 2622), atr=5.0)
        )
        assert fills and fills[0].reason is ExitReason.TP1
        assert fills[0].size == pytest.approx(initial * 0.5)
        assert pos.is_open and pos.size == pytest.approx(initial * 0.5)

    def test_the_ladder_runs_all_the_way_to_tp3(self, setup):
        _, _, manager, pos = setup
        manager.update(pos, TradeContext(candle=bar(NOW, 2600, 2623, 2599, 2622), atr=5.0))
        manager.update(
            pos,
            TradeContext(
                candle=bar(NOW + timedelta(minutes=15), 2622, 2633, 2621, 2632), atr=5.0
            ),
        )
        assert pos.is_open
        manager.update(
            pos,
            TradeContext(
                candle=bar(NOW + timedelta(minutes=30), 2632, 2643, 2631, 2642), atr=5.0
            ),
        )
        assert not pos.is_open
        assert [f.reason for f in pos.fills] == [
            ExitReason.TP1, ExitReason.TP2, ExitReason.TP3
        ]

    def test_realized_r_is_size_weighted(self, setup):
        _, _, manager, pos = setup
        manager.update(pos, TradeContext(candle=bar(NOW, 2600, 2623, 2599, 2622), atr=5.0))
        manager.update(
            pos,
            TradeContext(
                candle=bar(NOW + timedelta(minutes=15), 2622, 2645, 2621, 2644), atr=5.0
            ),
        )
        assert pos.realized_r > 0
        assert pos.realized_pnl > 0


class TestEarlyExits:
    """Ch X-C."""

    def test_news_closes_a_trade_that_is_not_yet_at_breakeven(self, setup):
        _, _, manager, pos = setup
        fills = manager.update(
            pos,
            TradeContext(
                candle=bar(NOW, 2600, 2602, 2598, 2601), atr=5.0, news_imminent=True
            ),
        )
        assert fills and fills[0].reason is ExitReason.EARLY_NEWS

    def test_news_does_not_close_a_trade_already_at_breakeven(self, setup):
        _, _, manager, pos = setup
        manager.update(
            pos,
            TradeContext(candle=bar(NOW, 2600, pos.price_at_r(1.2), 2599, 2610), atr=5.0),
        )
        assert pos.breakeven_done
        fills = manager.update(
            pos,
            TradeContext(
                candle=bar(NOW + timedelta(minutes=15), 2610, 2612, 2609, 2611),
                atr=5.0, news_imminent=True,
            ),
        )
        assert not any(f.reason is ExitReason.EARLY_NEWS for f in fills)

    def test_technical_invalidation_closes_the_trade(self, setup):
        _, _, manager, pos = setup
        fills = manager.update(
            pos,
            TradeContext(
                candle=bar(NOW, 2600, 2602, 2598, 2601), atr=5.0,
                invalidated=True, invalidation_detail="level reclaimed",
            ),
        )
        assert fills and fills[0].reason is ExitReason.EARLY_INVALIDATION

    def test_a_counter_pattern_closes_the_trade_before_tp1(self, setup):
        _, _, manager, pos = setup
        fills = manager.update(
            pos,
            TradeContext(
                candle=bar(NOW, 2600, 2602, 2598, 2601), atr=5.0, counter_pattern=True
            ),
        )
        assert fills and fills[0].reason is ExitReason.EARLY_COUNTER_PATTERN

    def test_a_stale_trade_is_closed_at_three_times_expected_duration(self, setup):
        cfg, _, manager, pos = setup
        limit = int(cfg.trade_management.expected_duration_bars
                    * cfg.trade_management.max_duration_multiple)
        ts = NOW
        for _ in range(limit + 1):
            ts += timedelta(minutes=15)
            fills = manager.update(
                pos,
                TradeContext(candle=bar(ts, 2600, 2602, 2598, 2601), atr=5.0),
            )
            if fills:
                break
        assert not pos.is_open
        assert pos.close_reason is ExitReason.EARLY_DURATION

    def test_duration_counts_entry_screen_bars_only(self, setup):
        _, _, manager, pos = setup
        manager.update(
            pos,
            TradeContext(
                candle=bar(NOW, 2600, 2602, 2598, 2601), atr=5.0, entry_tf_close=False
            ),
        )
        assert pos.bars_open == 0
        manager.update(
            pos,
            TradeContext(
                candle=bar(NOW + timedelta(minutes=5), 2601, 2602, 2599, 2601),
                atr=5.0, entry_tf_close=True,
            ),
        )
        assert pos.bars_open == 1


class TestShortSide:
    def test_a_short_runs_the_ladder_downwards(self):
        cfg = TitanConfig()
        broker = PaperBroker(cfg.execution, tp_config=cfg.trade_management)
        manager = TradeManager(cfg.trade_management, broker)
        signal = make_signal(
            direction=Direction.SHORT, entry=2600.0, stop=2610.0,
            tp1=2578.0, tp2=2568.0, tp3=2558.0,
        )
        pos = broker.open_position(
            signal, get_instrument("XAUUSD"), 10.0, NOW, signal.entry_price
        )
        assert pos.sign == -1
        fills = manager.update(
            pos, TradeContext(candle=bar(NOW, 2600, 2601, 2577, 2578), atr=5.0)
        )
        assert fills and fills[0].reason is ExitReason.TP1
        assert fills[0].pnl > 0

    def test_a_short_stop_is_above_entry(self):
        signal = make_signal(direction=Direction.SHORT, entry=2600, stop=2610, tp1=2580)
        assert signal.stop_loss > signal.entry_price > signal.take_profit_1
        assert signal.risk_reward == pytest.approx(2.0)
        assert signal.r_multiple_price(1.0) == pytest.approx(2590.0)
