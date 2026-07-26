"""Pattern modules A and B — TFBS Ch III and Ch IV."""

from datetime import datetime, timezone

import pytest

from titan_tfbs.config import TitanConfig
from titan_tfbs.core.candles import MultiTimeframeStore, TimeFrame
from titan_tfbs.data.synthetic import double_top, head_and_shoulders, ranging_market
from titan_tfbs.patterns.base import PatternType
from titan_tfbs.patterns.detector import PatternDetector

UTC = timezone.utc
START = datetime(2026, 1, 5, tzinfo=UTC)


def scan(candles, cfg=None, timeframe="1H"):
    cfg = cfg or TitanConfig()
    store = MultiTimeframeStore("XAUUSD", TimeFrame.parse("5M"), ["15M", "1H", "4H"])
    store.warmup(candles)
    store.finalize()
    return PatternDetector(cfg).scan(store.require(timeframe))


class TestHeadAndShoulders:
    def test_detects_a_textbook_formation(self):
        result = scan(head_and_shoulders(START, 2650.0, 12, 55.0))
        hs = [p for p in result.patterns if p.type is PatternType.HEAD_SHOULDERS]
        assert hs, "no H&S found in a textbook H&S series"

    def test_head_stands_above_both_shoulders(self):
        result = scan(head_and_shoulders(START, 2650.0, 12, 55.0))
        p = next(p for p in result.patterns if p.type is PatternType.HEAD_SHOULDERS)
        peaks = sorted((pv.price for pv in p.pivots if pv.is_high), reverse=True)
        assert peaks[0] > peaks[1] and peaks[0] > peaks[2]

    def test_detects_the_inverse(self):
        result = scan(head_and_shoulders(START, 2650.0, 12, 55.0, inverse=True))
        inv = [p for p in result.patterns if p.type is PatternType.INVERSE_HEAD_SHOULDERS]
        assert inv, "no Inverse H&S found in a textbook inverse series"
        assert not inv[0].is_bearish

    def test_prior_trend_filter_is_mandatory(self):
        """Ch III-E: a clear prior trend is MANDATORY — no trend, no reversal."""
        result = scan(ranging_market(START, 100.0, cycles=14, unit=12, amplitude=4.0))
        assert not [
            p for p in result.patterns if p.type.family == "head_shoulders"
        ], "an H&S was reported inside a pure range"

    def test_measured_move_uses_head_to_neckline_height(self):
        """Ch III-D: Target = Break Price +/- |Head - Neckline|."""
        result = scan(head_and_shoulders(START, 2650.0, 12, 55.0))
        p = next(p for p in result.patterns if p.type is PatternType.HEAD_SHOULDERS)
        break_price = p.trigger_price_at(p.end_index)
        assert p.measured_target(break_price) == pytest.approx(
            break_price - p.measured_height
        )
        # Roughly the designed 55-point height, allowing for pivot noise.
        assert 35.0 < p.measured_height < 75.0

    def test_structural_invalidation_sits_at_the_right_shoulder(self):
        """Ch VII / Ch X-A: stop goes beyond the right shoulder."""
        result = scan(head_and_shoulders(START, 2650.0, 12, 55.0))
        p = next(p for p in result.patterns if p.type is PatternType.HEAD_SHOULDERS)
        assert p.structural_invalidation > p.trigger_price_at(p.end_index)
        assert p.structural_invalidation < p.apex_price

    def test_neckline_slope_filter_is_reported(self):
        result = scan(head_and_shoulders(START, 2650.0, 12, 55.0))
        p = next(p for p in result.patterns if p.type is PatternType.HEAD_SHOULDERS)
        assert any(f.name == "neckline_slope" for f in p.filters)

    def test_volume_signature_declines_left_to_right(self):
        """Ch III-C: volume should decline LS > Head > RS."""
        result = scan(head_and_shoulders(START, 2650.0, 12, 55.0))
        p = next(p for p in result.patterns if p.type is PatternType.HEAD_SHOULDERS)
        assert p.volume_profile_ok


class TestDoubleTop:
    def test_detects_a_double_top(self):
        result = scan(double_top(START, 1.2000, 12, 0.0150))
        assert [p for p in result.patterns if p.type is PatternType.DOUBLE_TOP]

    def test_detects_a_double_bottom(self):
        result = scan(double_top(START, 1.2000, 12, 0.0150, inverse=True))
        db = [p for p in result.patterns if p.type is PatternType.DOUBLE_BOTTOM]
        assert db and not db[0].is_bearish

    def test_detects_a_triple_top(self):
        result = scan(double_top(START, 1.2000, 12, 0.0150, triple=True))
        assert [p for p in result.patterns if p.type is PatternType.TRIPLE_TOP]

    def test_triple_is_preferred_over_the_double_inside_it(self):
        """Ch IV-D: three tests carry more conviction than two."""
        result = scan(double_top(START, 1.2000, 12, 0.0150, triple=True))
        tops = [p for p in result.patterns if p.type.family == "double_top"]
        assert any(p.type.is_triple for p in tops)
        # The double contained in the triple must not also be reported.
        assert not any(
            p.type is PatternType.DOUBLE_TOP and p.end_index >= min(
                t.start_index for t in tops if t.type.is_triple
            )
            for p in tops
        )

    def test_two_tests_must_be_close_in_price(self):
        """Ch IV-C level proximity."""
        result = scan(double_top(START, 1.2000, 12, 0.0150))
        p = next(p for p in result.patterns if p.type is PatternType.DOUBLE_TOP)
        peaks = [pv.price for pv in p.pivots if pv.is_high]
        spread = (max(peaks) - min(peaks)) / max(peaks)
        assert spread <= 0.03

    def test_nothing_is_found_in_a_range(self):
        """Ch IV-C: 'DT inside a range = noise.'"""
        result = scan(ranging_market(START, 100.0, cycles=14, unit=12, amplitude=4.0))
        assert not [p for p in result.patterns if p.type.family == "double_top"]

    def test_measured_move_uses_peak_to_confirmation_height(self):
        """Ch IV-D: Target = Confirmation Break +/- |Peak - Confirmation Line|."""
        result = scan(double_top(START, 1.2000, 12, 0.0150))
        p = next(p for p in result.patterns if p.type is PatternType.DOUBLE_TOP)
        assert 0.008 < p.measured_height < 0.022


class TestMacroContext:
    def test_a_formation_capping_the_range_is_not_choppy(self):
        result = scan(head_and_shoulders(START, 2650.0, 12, 55.0))
        p = next(p for p in result.patterns if p.type is PatternType.HEAD_SHOULDERS)
        assert not p.choppy_context


class TestTimeAnchor:
    def test_trigger_line_is_evaluable_at_any_timestamp(self):
        """Screen 2 finds the neckline; Screen 3 has to price it (Ch IX)."""
        result = scan(head_and_shoulders(START, 2650.0, 12, 55.0))
        p = result.patterns[0]
        at_end = p.trigger_price_at(p.end_index)
        assert p.trigger_price_at_ts(p.end_ts) == pytest.approx(at_end, abs=1e-6)

    def test_signature_is_stable_across_index_shifts(self):
        result = scan(head_and_shoulders(START, 2650.0, 12, 55.0))
        p = result.patterns[0]
        first = p.signature()
        p.start_index += 100
        p.end_index += 100
        assert p.signature() == first
