"""Breakout protocol, confluence scoring, MTF alignment and the checklist.

TFBS Ch V, Ch VII, Ch IX, Ch XI and Appendix A.
"""

from datetime import datetime, timedelta, timezone

import pytest

from titan_tfbs.config import EntryMethod, TitanConfig
from titan_tfbs.core.candles import Candle, MultiTimeframeStore, TimeFrame
from titan_tfbs.core.structure import Trend
from titan_tfbs.data.synthetic import double_top, head_and_shoulders
from titan_tfbs.patterns.detector import PatternDetector
from titan_tfbs.strategy import mtf
from titan_tfbs.strategy.breakout import (
    ARMED,
    BROKEN,
    READY,
    RETESTED,
    BreakoutTracker,
    evaluate_breakout,
    method_ready_state,
)
from titan_tfbs.strategy.confluence import score_setup
from titan_tfbs.strategy.signals import (
    BreakoutEvent,
    ConfluenceScore,
    Direction,
    Grade,
    RetestEvent,
)
from titan_tfbs.strategy.tfbs import TFBSStrategy

UTC = timezone.utc
START = datetime(2026, 1, 5, tzinfo=UTC)


def _store(candles, symbol="XAUUSD"):
    store = MultiTimeframeStore(symbol, TimeFrame.parse("5M"), ["15M", "1H", "4H", "1D"])
    store.warmup(candles)
    store.finalize()
    return store


def _pattern(candles, timeframe="1H"):
    store = _store(candles)
    result = PatternDetector(TitanConfig()).scan(store.require(timeframe))
    assert result.patterns, "fixture produced no pattern"
    return result.patterns[0], store


class TestBreakoutConfirmation:
    """Ch V-A — the three criteria."""

    def _bars(self, level, closes, volumes=None, ts=START):
        out = []
        for i, c in enumerate(closes):
            vol = volumes[i] if volumes else 1000.0
            out.append(
                Candle(ts + timedelta(minutes=15 * i), c + 0.2, c + 0.6, c - 0.6, c, vol)
            )
        return out

    def test_a_wick_through_the_level_is_not_a_break(self):
        """Ch V-B: 'Wick-only penetration [...] No close = no trade.'"""
        pattern, store = _pattern(head_and_shoulders(START, 2650.0, 12, 55.0))
        level = pattern.trigger_price_at_ts(pattern.end_ts)
        entry = list(store.require("15M"))
        # Rewrite the last bar so it pierces the level but closes above it.
        last = entry[-1]
        entry[-1] = Candle(last.ts, level + 2, level + 3, level - 5, level + 1, 1000)
        pattern.set_time_anchor(pattern.end_ts, 60)
        event, reason = evaluate_breakout(
            pattern, entry, 1.0, TitanConfig().breakout, "15M"
        )
        assert event is None and reason == "wick_only_penetration"

    def test_a_close_beyond_the_level_confirms(self):
        pattern, store = _pattern(head_and_shoulders(START, 2650.0, 12, 55.0))
        level = pattern.trigger_price_at_ts(pattern.end_ts)
        entry = list(store.require("15M"))
        last = entry[-1]
        entry[-1] = Candle(last.ts, level, level + 0.2, level - 6, level - 5, 1000)
        event, reason = evaluate_breakout(
            pattern, entry, 1.0, TitanConfig().breakout, "15M"
        )
        assert event is not None and reason is None
        assert event.close < event.level

    def test_volume_surge_is_flagged_but_not_required(self):
        """Ch V-A criterion 2 is STRONG PREF, not REQUIRED."""
        pattern, store = _pattern(head_and_shoulders(START, 2650.0, 12, 55.0))
        level = pattern.trigger_price_at_ts(pattern.end_ts)
        entry = list(store.require("15M"))
        last = entry[-1]
        entry[-1] = Candle(last.ts, level, level + 0.2, level - 6, level - 5, 100.0)
        cfg = TitanConfig().breakout
        event, _ = evaluate_breakout(pattern, entry, 1.0, cfg, "15M")
        assert event is not None and not event.volume_surge

    def test_low_volume_break_can_be_rejected_by_config(self):
        pattern, store = _pattern(head_and_shoulders(START, 2650.0, 12, 55.0))
        level = pattern.trigger_price_at_ts(pattern.end_ts)
        entry = list(store.require("15M"))
        last = entry[-1]
        entry[-1] = Candle(last.ts, level, level + 0.2, level - 6, level - 5, 1.0)
        cfg = TitanConfig().breakout
        cfg.reject_low_volume_break = True
        event, reason = evaluate_breakout(pattern, entry, 1.0, cfg, "15M")
        assert event is None and reason == "low_volume_breakout"

    def test_no_break_means_no_event(self):
        """Ch XII-A1: anticipation trading is prohibited."""
        pattern, store = _pattern(head_and_shoulders(START, 2650.0, 12, 55.0))
        level = pattern.trigger_price_at_ts(pattern.end_ts)
        entry = list(store.require("15M"))
        last = entry[-1]
        entry[-1] = Candle(last.ts, level + 5, level + 6, level + 4, level + 5, 1000)
        event, reason = evaluate_breakout(
            pattern, entry, 1.0, TitanConfig().breakout, "15M"
        )
        assert event is None and reason is None


class TestEntryMethods:
    """Ch VII — which state each method fires in."""

    def test_state_mapping(self):
        assert method_ready_state(EntryMethod.A_AGGRESSIVE) == BROKEN
        assert method_ready_state(EntryMethod.B_STANDARD) == RETESTED
        assert method_ready_state(EntryMethod.C_CONSERVATIVE) == READY

    def test_firm_default_requires_a_retest(self):
        cfg = TitanConfig()
        assert method_ready_state(cfg.required_entry_method()) == RETESTED


class TestTracker:
    def test_overlapping_same_direction_formations_collapse_to_one(self):
        """Ch VI-B treats overlap as one A+ setup, not two trades."""
        candles = head_and_shoulders(START, 2650.0, 12, 55.0)
        store = _store(candles)
        detector = PatternDetector(TitanConfig())
        tracker = BreakoutTracker(TitanConfig())
        for tf in ("4H", "1H"):
            for p in detector.scan(store.require(tf)).patterns:
                tracker.register(p, START)
        bearish = [w for w in tracker.active if w.pattern.is_bearish]
        assert len(bearish) <= 1

    def test_armed_formations_expire(self):
        """Ch VI-B: the watchlist cannot hold a formation forever."""
        pattern, _ = _pattern(head_and_shoulders(START, 2650.0, 12, 55.0))
        cfg = TitanConfig()
        tracker = BreakoutTracker(cfg)
        watch = tracker.register(pattern, START)
        assert watch.state == ARMED
        for _ in range(cfg.breakout.pattern_expiry_bars + 1):
            tracker.on_pattern_bar()
        assert not watch.alive and watch.dead_reason == "expired_without_break"


class TestConfluenceScoring:
    """Ch XI — the mandatory 10-point score."""

    def _score(self, **overrides):
        s = ConfluenceScore(
            pattern_quality=2, breakout_strength=2, retest_confirm=1,
            htf_alignment=2, sr_confluence=1, rr_ratio=1, clean_path=1,
        )
        for k, v in overrides.items():
            setattr(s, k, v)
        return s

    def test_a_perfect_setup_scores_ten(self):
        assert self._score().total == 10

    def test_the_factor_maxima_sum_to_ten(self):
        cfg = TitanConfig().confluence
        assert (
            cfg.max_pattern_quality + cfg.max_breakout_strength
            + cfg.max_retest_confirm + cfg.max_htf_alignment
            + cfg.max_sr_confluence + cfg.max_rr_ratio + cfg.max_clean_path
        ) == 10

    def test_grade_bands(self):
        """Ch XI: 9-10 ELITE, 7-8 APPROVED, 5-6 WATCHLIST, 0-4 DISCARD."""
        assert self._score().total == 10
        assert self._score().grade() is Grade.ELITE
        # 2+2+1+1+0+1+1 = 8
        approved = self._score(htf_alignment=1, sr_confluence=0)
        assert approved.total == 8 and approved.grade() is Grade.APPROVED
        # 1+2+1+1+0+1+0 = 6
        watchlist = self._score(
            pattern_quality=1, htf_alignment=1, sr_confluence=0, clean_path=0
        )
        assert watchlist.total == 6 and watchlist.grade() is Grade.WATCHLIST
        # 1+1+0+0+0+1+0 = 3
        discard = self._score(
            pattern_quality=1, breakout_strength=1, retest_confirm=0,
            htf_alignment=0, sr_confluence=0, clean_path=0,
        )
        assert discard.total == 3 and discard.grade() is Grade.DISCARD

    def test_sub_two_to_one_is_an_auto_skip_whatever_else_scores(self):
        """Ch XI: 'R:R Ratio — 0 = Below 2:1 (auto-skip)'."""
        s = self._score(rr_ratio=0)
        assert s.grade() is Grade.DISCARD
        assert not s.grade().tradeable

    def test_bonuses_cannot_push_past_ten(self):
        s = self._score(triple_bonus=1, dual_pattern_bonus=1)
        assert s.raw_total == 12 and s.total == 10

    def test_only_seven_and_above_are_tradeable(self):
        assert Grade.ELITE.tradeable and Grade.APPROVED.tradeable
        assert not Grade.WATCHLIST.tradeable and not Grade.DISCARD.tradeable

    def test_scoring_a_real_setup(self):
        pattern, store = _pattern(head_and_shoulders(START, 2650.0, 12, 55.0))
        cfg = TitanConfig()
        level = pattern.trigger_price_at_ts(pattern.end_ts)
        event = BreakoutEvent(
            pattern=pattern, ts=pattern.end_ts,
            candle=list(store.require("15M"))[-1], timeframe="15M",
            level=level, close=level - 3, atr=2.0,
            volume_ratio=2.1, volume_surge=True,
        )
        retest = RetestEvent(
            ts=pattern.end_ts, candle=list(store.require("15M"))[-1],
            wick_price=level + 1, rejection_kind="pin_bar", rejection_strength=0.8,
        )
        entry = level - 1
        stop = level + 3
        tp1 = pattern.measured_target(level)
        score = score_setup(
            pattern, event, retest, Direction.SHORT, htf_points=2,
            entry_price=entry, stop_loss=stop, take_profit_1=tp1,
            levels=[], atr_value=2.0, cfg=cfg.confluence, risk_cfg=cfg.risk,
        )
        assert score.breakout_strength == 2      # close + volume surge
        assert score.retest_confirm == 1
        assert score.htf_alignment == 2
        assert score.rr_ratio == 1
        assert score.total >= cfg.confluence.min_score_to_trade


class TestMTFAlignment:
    """Ch IX — the three screens."""

    def test_aligned_bias_scores_two(self):
        alignment = mtf.MTFAlignment(bias=Trend.DOWN, bias_strength=1.0)
        cfg = TitanConfig().mtf
        assert alignment.alignment_points(Direction.SHORT, cfg) == 2

    def test_neutral_bias_scores_one(self):
        alignment = mtf.MTFAlignment(bias=Trend.RANGE, bias_strength=0.0)
        cfg = TitanConfig().mtf
        assert alignment.alignment_points(Direction.SHORT, cfg) == 1

    def test_counter_trend_scores_zero(self):
        """Ch IX: 'Trade WITH the HTF trend.'"""
        alignment = mtf.MTFAlignment(bias=Trend.UP, bias_strength=1.0)
        cfg = TitanConfig().mtf
        assert alignment.alignment_points(Direction.SHORT, cfg) == 0
        assert alignment.is_counter_trend(Direction.SHORT)
        assert not alignment.is_counter_trend(Direction.LONG)

    def test_conflicting_screens_reduce_size(self):
        """Ch IX: 'Conflicting screens = reduce size or wait.'"""
        cfg = TitanConfig().mtf
        conflicted = mtf.MTFAlignment(bias=Trend.DOWN, bias_strength=0.5, conflict=True)
        assert conflicted.size_factor(cfg) == cfg.conflicting_screen_size_factor
        assert conflicted.alignment_points(Direction.SHORT, cfg) == 1

    def test_insufficient_history_yields_a_neutral_bias(self):
        store = MultiTimeframeStore("XAUUSD", TimeFrame.parse("5M"), ["1D"])
        alignment = mtf.analyze(store, TitanConfig(), ["1D"])
        assert alignment.bias is Trend.RANGE


class TestPipelineGates:
    """The gates in Ch VI-A that stop a setup becoming a trade."""

    def _run(self, cfg, candles, symbol="XAUUSD"):
        store = MultiTimeframeStore(
            symbol, TimeFrame.parse("5M"), ["15M", "1H", "4H", "1D"]
        )
        strategy = TFBSStrategy(cfg)
        evaluations = []
        for candle in candles:
            closed = store.push(candle)
            evaluations.extend(strategy.on_candles(symbol, store, closed, candle.ts))
        return evaluations

    def test_a_clean_formation_produces_an_approved_setup(self):
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        accepted = [
            e for e in self._run(cfg, head_and_shoulders(START, 2650.0, 12, 55.0))
            if e.accepted
        ]
        assert accepted, "a textbook H&S with a confirmed break produced no setup"
        signal = accepted[0].signal
        assert signal.risk_reward >= cfg.risk.min_reward_risk
        assert signal.score.total >= cfg.confluence.min_score_to_trade
        assert signal.grade.tradeable

    def test_raising_the_score_gate_rejects_the_same_setup(self):
        """Ch XI is the gate — move it and the pipeline obeys."""
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.confluence.min_score_to_trade = 11
        cfg.confluence.approved_score = 11
        cfg.confluence.elite_score = 12
        results = self._run(cfg, head_and_shoulders(START, 2650.0, 12, 55.0))
        assert results and not any(e.accepted for e in results)
        assert any("confluence" in e.reason for e in results if e.reason)

    def test_raising_the_rr_floor_rejects_the_same_setup(self):
        """Ch VIII-A: below the minimum R:R is a hard skip."""
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        cfg.risk.min_reward_risk = 500.0
        results = self._run(cfg, head_and_shoulders(START, 2650.0, 12, 55.0))
        assert not any(e.accepted for e in results)
        assert any("below the firm minimum" in (e.reason or "") for e in results)

    def test_stops_sit_on_the_correct_side_of_entry(self):
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        for candles, symbol in (
            (head_and_shoulders(START, 2650.0, 12, 55.0), "XAUUSD"),
            (head_and_shoulders(START, 2650.0, 12, 55.0, inverse=True), "XAUUSD"),
        ):
            for e in self._run(cfg, candles, symbol):
                if not e.signal:
                    continue
                s = e.signal
                if s.is_long:
                    assert s.stop_loss < s.entry_price < s.take_profit_1
                else:
                    assert s.stop_loss > s.entry_price > s.take_profit_1

    def test_targets_are_ordered_along_the_trade_direction(self):
        """Ch X-B: TP2 sits beyond TP1, TP3 beyond TP2."""
        cfg = TitanConfig()
        cfg.mtf.min_bias_bars = 20
        for e in self._run(cfg, double_top(START, 1.2000, 12, 0.0150), "EURUSD"):
            if not e.signal:
                continue
            s = e.signal
            sign = s.direction.sign
            assert (s.take_profit_2 - s.take_profit_1) * sign > 0
            assert (s.take_profit_3 - s.take_profit_2) * sign > 0
