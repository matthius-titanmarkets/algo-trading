"""Candles, timeframes, indicators and market structure."""

from datetime import datetime, timedelta, timezone

import pytest

from titan_tfbs.core.candles import (
    Candle,
    CandleSeries,
    MultiTimeframeStore,
    Resampler,
    TimeFrame,
)
from titan_tfbs.core.indicators import atr, ema, rsi, sma, volume_ratio
from titan_tfbs.core.structure import (
    Line,
    PivotType,
    Trend,
    classify_trend,
    detect_rejection,
    find_levels,
    find_pivots,
    is_followthrough,
)

UTC = timezone.utc


def bars(n, start=datetime(2026, 1, 5, tzinfo=UTC), minutes=5, price=100.0, step=0.0):
    out = []
    for i in range(n):
        p = price + step * i
        out.append(Candle(start + timedelta(minutes=minutes * i), p, p + 0.5, p - 0.5, p, 1000))
    return out


class TestTimeFrame:
    @pytest.mark.parametrize(
        "text,minutes",
        [("5M", 5), ("15M", 15), ("1H", 60), ("4H", 240), ("1D", 1440), ("1W", 10080)],
    )
    def test_parses_the_firm_timeframes(self, text, minutes):
        assert TimeFrame.parse(text).minutes == minutes

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            TimeFrame.parse("banana")

    def test_floor_aligns_to_bucket(self):
        tf = TimeFrame.parse("4H")
        assert tf.floor(datetime(2026, 1, 5, 13, 37, tzinfo=UTC)) == datetime(
            2026, 1, 5, 12, 0, tzinfo=UTC
        )

    def test_weekly_buckets_start_monday(self):
        tf = TimeFrame.parse("1W")
        # 2026-01-08 is a Thursday; its week starts Monday the 5th.
        assert tf.floor(datetime(2026, 1, 8, tzinfo=UTC)) == datetime(2026, 1, 5, tzinfo=UTC)


class TestResampling:
    def test_aggregates_ohlcv(self):
        r = Resampler(TimeFrame.parse("15M"))
        source = [
            Candle(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), 10, 12, 9, 11, 100),
            Candle(datetime(2026, 1, 5, 0, 5, tzinfo=UTC), 11, 15, 10, 14, 200),
            Candle(datetime(2026, 1, 5, 0, 10, tzinfo=UTC), 14, 14, 8, 9, 300),
            Candle(datetime(2026, 1, 5, 0, 15, tzinfo=UTC), 9, 9, 9, 9, 50),
        ]
        closed = [c for c in (r.push(x) for x in source) if c]
        assert len(closed) == 1
        bar = closed[0]
        assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (10, 15, 8, 9, 600)

    def test_store_derives_every_screen_from_one_feed(self):
        store = MultiTimeframeStore("X", TimeFrame.parse("5M"), ["15M", "1H", "4H"])
        for c in bars(48):     # four hours of 5M
            store.push(c)
        store.finalize()
        assert len(store.require("5M")) == 48
        assert len(store.require("15M")) == 16
        assert len(store.require("1H")) == 4

    def test_rejects_timeframe_that_is_not_a_multiple(self):
        with pytest.raises(ValueError):
            MultiTimeframeStore("X", TimeFrame.parse("15M"), ["1H", "7M"])


class TestSeries:
    def test_rejects_out_of_order_candles(self):
        s = CandleSeries("X", TimeFrame.parse("5M"))
        s.extend(bars(3))
        earlier = Candle(datetime(2026, 1, 4, tzinfo=UTC), 1, 1, 1, 1)
        with pytest.raises(ValueError):
            s.append(earlier)

    def test_replaces_a_repeated_timestamp(self):
        s = CandleSeries("X", TimeFrame.parse("5M"))
        s.extend(bars(2))
        last = s.last
        s.append(Candle(last.ts, 1, 2, 0.5, 1.5))
        assert len(s) == 2 and s.last.close == 1.5


class TestIndicators:
    def test_sma(self):
        assert sma([1, 2, 3, 4], 2) == 3.5
        assert sma([1], 5) is None

    def test_ema_tracks_a_trend(self):
        rising = list(range(1, 60))
        assert ema(rising, 10) > ema(rising, 50)

    def test_atr_is_positive_and_scales(self):
        quiet = bars(40)
        assert atr(quiet, 14) == pytest.approx(1.0, abs=0.2)

    def test_rsi_bounds(self):
        up = list(range(1, 60))
        assert rsi(up, 14) == pytest.approx(100.0, abs=0.001)
        down = list(range(60, 1, -1))
        assert rsi(down, 14) == pytest.approx(0.0, abs=0.001)

    def test_volume_ratio_is_none_without_volume(self):
        no_vol = [
            Candle(datetime(2026, 1, 5, tzinfo=UTC) + timedelta(minutes=5 * i), 1, 1, 1, 1, 0)
            for i in range(30)
        ]
        assert volume_ratio(no_vol, 20) is None

    def test_volume_ratio_detects_a_surge(self):
        series = bars(30)
        spike = series[-1]
        series[-1] = Candle(spike.ts, spike.open, spike.high, spike.low, spike.close, 5000)
        assert volume_ratio(series, 20) == pytest.approx(5.0, abs=0.01)


class TestStructure:
    def _zigzag(self, legs, bars_per_leg=6, start_price=100.0):
        out, price, ts = [], start_price, datetime(2026, 1, 5, tzinfo=UTC)
        for target in legs:
            step = (target - price) / bars_per_leg
            for _ in range(bars_per_leg):
                nxt = price + step
                out.append(
                    Candle(ts, price, max(price, nxt) + 0.1, min(price, nxt) - 0.1, nxt, 1000)
                )
                price = nxt
                ts += timedelta(minutes=5)
        return out

    def test_finds_alternating_pivots(self):
        candles = self._zigzag([110, 95, 120, 90])
        pivots = find_pivots(candles, lookback=3)
        kinds = [p.kind for p in pivots]
        assert len(pivots) >= 2
        assert all(kinds[i] != kinds[i + 1] for i in range(len(kinds) - 1))

    def test_no_pivot_can_form_in_the_trailing_window(self):
        candles = self._zigzag([110, 95, 120])
        pivots = find_pivots(candles, lookback=3)
        assert all(p.index <= len(candles) - 4 for p in pivots)

    def test_classifies_an_uptrend(self):
        candles = self._zigzag([110, 105, 125, 118, 140, 132, 155], bars_per_leg=12)
        state = classify_trend(candles)
        assert state.trend is Trend.UP

    def test_classifies_a_downtrend(self):
        candles = self._zigzag([90, 95, 75, 82, 60, 68, 45], bars_per_leg=12)
        assert classify_trend(candles).trend is Trend.DOWN

    def test_levels_cluster_repeated_touches(self):
        candles = self._zigzag([110, 95, 110, 95, 110], bars_per_leg=8)
        levels = find_levels(candles)
        resistance = [lv for lv in levels if lv.kind is PivotType.HIGH]
        assert resistance and max(lv.touches for lv in resistance) >= 2

    def test_neckline_angle_is_atr_normalised(self):
        # One ATR of movement per bar is 45 degrees by definition.
        line = Line(0, 100.0, 10, 110.0)
        assert line.angle_deg(atr_value=1.0) == pytest.approx(45.0, abs=0.01)
        assert Line(0, 100.0, 10, 100.0).angle_deg(1.0) == 0.0


class TestRejectionCandles:
    def _c(self, o, h, l, c):
        return Candle(datetime(2026, 1, 5, tzinfo=UTC), o, h, l, c, 1000)

    def test_bearish_pin_bar(self):
        # Long upper wick, small body, closes back down.
        signal = detect_rejection([self._c(100, 110, 99.5, 100.2)], bullish=False)
        assert signal is not None and signal.kind == "pin_bar"

    def test_bearish_engulfing(self):
        prior = self._c(100, 103, 99.8, 102.5)
        current = self._c(102.5, 103, 98, 99)
        signal = detect_rejection([prior, current], bullish=False)
        assert signal is not None and signal.kind in ("engulfing", "strong_wick")

    def test_no_rejection_on_a_plain_trend_candle(self):
        assert detect_rejection([self._c(100, 105, 99.9, 104.9)], bullish=False) is None

    def test_followthrough_requires_a_close_beyond_the_rejection_bar(self):
        rejection = self._c(100, 110, 99, 100.5)
        assert is_followthrough(rejection, self._c(100.5, 101, 97, 98), bullish=False)
        assert not is_followthrough(rejection, self._c(100.5, 101, 100, 100.8), bullish=False)
