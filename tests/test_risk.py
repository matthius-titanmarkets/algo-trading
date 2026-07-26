"""Risk management — TFBS Ch VIII, Ch XII and the Risk Management Guide."""

from datetime import datetime, timedelta, timezone

import pytest

from titan_tfbs.config import TitanConfig, TraderProfile
from titan_tfbs.instruments import get_instrument, shares_correlation_group
from titan_tfbs.risk.compliance import ComplianceMonitor, Flag, Rule
from titan_tfbs.risk.limits import AccountState, AccountStatus, DrawdownMonitor
from titan_tfbs.risk.sizing import calculate_position_size, pnl_for

UTC = timezone.utc
NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


class TestPositionSizing:
    def test_reproduces_the_manual_worked_example(self):
        """Ch VIII-B: $500K, 1% risk, entry 1.3050, SL 1.3100 -> 10 lots."""
        result = calculate_position_size(
            get_instrument("EURUSD"),
            account_balance=500_000,
            risk_pct=1.0,
            entry_price=1.3050,
            stop_loss=1.3100,
            include_commission=False,
        )
        assert result.risk_budget == pytest.approx(5_000.0)
        assert result.stop_pips == pytest.approx(50.0)
        assert result.size == pytest.approx(10.0)      # 10 lots = 1,000,000 units

    def test_reproduces_the_risk_guide_example(self):
        """RMG s.02: $25,000 account -> $250 at 1%, $125 at the 0.50% default."""
        for pct, expected in ((1.0, 250.0), (0.5, 125.0)):
            result = calculate_position_size(
                get_instrument("XAUUSD"), 25_000, pct, 2650.0, 2645.0,
                include_commission=False,
            )
            assert result.risk_budget == pytest.approx(expected)

    def test_gold_uses_a_100_ounce_contract(self):
        inst = get_instrument("XAUUSD")
        assert inst.value_per_point == pytest.approx(100.0)
        result = calculate_position_size(
            inst, 100_000, 1.0, 2650.0, 2640.0, include_commission=False
        )
        # $1,000 risk / (10.00 move * $100 per point) = 1.00 lot
        assert result.size == pytest.approx(1.0)

    def test_index_futures_size_in_whole_contracts(self):
        inst = get_instrument("ES")
        assert inst.value_per_point == pytest.approx(50.0)
        result = calculate_position_size(
            inst, 500_000, 1.0, 5000.0, 4990.0, include_commission=False
        )
        # $5,000 / (10 points * $50) = 10 contracts exactly
        assert result.size == 10.0
        assert result.size == int(result.size)

    def test_inverse_quote_currency_is_converted(self):
        """USDJPY is quoted in JPY; risk must be expressed in the USD account."""
        result = calculate_position_size(
            get_instrument("USDJPY"), 500_000, 1.0, 150.00, 149.50,
            include_commission=False,
        )
        # 100,000 units * 0.50 JPY = 50,000 JPY per lot, /150 = $333.33
        assert result.value_per_point == pytest.approx(100_000 / 150.0)
        assert result.risk_amount <= result.risk_budget + 1e-6

    def test_rounding_is_always_down(self):
        """Rounding up would breach the Ch VIII-A hard limit."""
        result = calculate_position_size(
            get_instrument("ES"), 100_000, 1.0, 5000.0, 4993.0
        )
        assert result.risk_amount <= result.risk_budget + 1e-9
        assert result.size == int(result.size)

    def test_refuses_when_the_minimum_position_would_over_risk(self):
        result = calculate_position_size(
            get_instrument("ES"), 2_000, 0.5, 5000.0, 4900.0
        )
        assert not result.tradeable and "minimum" in result.reason

    def test_refuses_a_zero_width_stop(self):
        result = calculate_position_size(
            get_instrument("EURUSD"), 500_000, 1.0, 1.1000, 1.1000
        )
        assert not result.tradeable and "invalidation" in result.reason

    def test_commission_is_charged_against_the_risk_budget(self):
        inst = get_instrument("EURUSD")
        with_comm = calculate_position_size(inst, 500_000, 1.0, 1.3050, 1.3100)
        without = calculate_position_size(
            inst, 500_000, 1.0, 1.3050, 1.3100, include_commission=False
        )
        assert with_comm.size < without.size

    def test_pnl_round_trip(self):
        inst = get_instrument("XAUUSD")
        # Short 1 lot of gold from 2650 to 2600 = 50 points * $100 = $5,000.
        gross = pnl_for(inst, 1.0, 2650.0, 2600.0, -1, include_commission=False)
        assert gross == pytest.approx(5_000.0)


class TestGradeSizing:
    def test_elite_gets_full_size_and_approved_gets_the_standard_band(self):
        """Ch XI: ELITE up to 2%, APPROVED 1-1.5%."""
        cfg = TitanConfig()
        assert cfg.risk_pct_for_grade("ELITE") == 2.0
        assert 1.0 <= cfg.risk_pct_for_grade("APPROVED") <= 1.5

    def test_titan_entry_is_capped_at_half_a_percent_regardless_of_grade(self):
        """Ch XII-B: 'Maximum risk per trade: 0.5% (not 2%).'"""
        cfg = TitanConfig()
        cfg.account.profile = TraderProfile.TITAN_ENTRY
        assert cfg.risk_pct_for_grade("ELITE") == 0.5
        assert cfg.risk_pct_for_grade("APPROVED") == 0.5

    def test_titan_entry_must_use_method_c_until_thirty_positive_trades(self):
        """Ch XII-B."""
        cfg = TitanConfig()
        cfg.account.profile = TraderProfile.TITAN_ENTRY
        assert cfg.required_entry_method().value == "C"
        cfg.account.consecutive_positive_trades = 30
        assert cfg.required_entry_method().value == "B"

    def test_prop_desk_default_is_method_b(self):
        """Ch VII: 'Method B — Standard (Retest Entry) — FIRM DEFAULT.'"""
        assert TitanConfig().required_entry_method().value == "B"


class TestDrawdownLadder:
    def _state(self, equity, start=100_000.0):
        s = AccountState.open_account(start, NOW)
        s.mark_equity(equity)
        s.balance = equity
        return s

    def _verdict(self, equity):
        return DrawdownMonitor(TitanConfig().risk).evaluate(self._state(equity))

    def test_active_below_two_percent(self):
        """RMG s.05: <= 2% daily -> Active, no restriction."""
        v = self._verdict(98_500.0)     # -1.5%
        assert v.status is AccountStatus.ACTIVE
        assert v.can_trade and v.size_factor == 1.0

    def test_restricted_between_two_and_three_percent(self):
        """RMG s.05: 2%-3% -> position size cap enforced."""
        v = self._verdict(97_500.0)     # -2.5%
        assert v.status is AccountStatus.RESTRICTED
        assert v.can_trade and v.size_factor == 0.5

    def test_probation_at_three_percent_also_trips_the_daily_loss_limit(self):
        """RMG s.05 probation and Ch VIII-A's 3% daily limit are the same line."""
        v = self._verdict(96_500.0)     # -3.5%
        assert v.status is AccountStatus.PROBATION
        assert not v.can_trade
        assert any("cease trading for the day" in b for b in v.breaches)

    def test_suspended_beyond_four_percent(self):
        """RMG s.05: > 4% in session -> account suspended."""
        v = self._verdict(95_500.0)     # -4.5%
        assert v.status is AccountStatus.SUSPENDED
        assert not v.can_trade

    def test_max_drawdown_ladder_uses_the_high_water_mark(self):
        s = AccountState.open_account(100_000.0, NOW)
        s.mark_equity(120_000.0)        # new peak
        s.roll_periods(NOW + timedelta(days=1))
        s.balance = 109_000.0
        s.mark_equity(109_000.0)        # -9.2% from peak
        v = DrawdownMonitor(TitanConfig().risk).evaluate(s)
        assert v.max_dd_pct == pytest.approx(9.17, abs=0.1)
        assert v.status is AccountStatus.RESTRICTED

    def test_weekly_loss_limit_halts_trading(self):
        """Ch XII-A5: '3% daily or 6% weekly = mandatory cessation.'"""
        s = AccountState.open_account(100_000.0, NOW)
        s.week_start_balance = 100_000.0
        s.day_start_balance = 95_000.0      # today's loss is small
        s.balance = 93_500.0
        s.mark_equity(93_500.0)
        v = DrawdownMonitor(TitanConfig().risk).evaluate(s)
        assert not v.can_trade
        assert any("weekly loss" in b for b in v.breaches)

    def test_monthly_trigger_requires_ceo_review(self):
        """Ch VIII-A: 10% monthly drawdown -> mandatory CEO review."""
        s = AccountState.open_account(100_000.0, NOW)
        s.month_start_balance = 100_000.0
        s.week_start_balance = 92_000.0
        s.day_start_balance = 90_000.0
        s.balance = 89_500.0
        s.mark_equity(89_500.0)
        monitor = DrawdownMonitor(TitanConfig().risk)
        assert not monitor.evaluate(s).can_trade
        # A 10% monthly drawdown also puts the account in the RMG s.05
        # probation band, so both reviews have to be signed off — clearing one
        # must not clear the other.
        s.ceo_review_acknowledged_for_month = s.current_month
        assert not monitor.evaluate(s).can_trade
        s.review_acknowledged_for_day = s.current_day
        assert monitor.evaluate(s).can_trade

    def test_probation_review_alone_does_not_clear_the_ceo_trigger(self):
        s = AccountState.open_account(100_000.0, NOW)
        s.month_start_balance = 100_000.0
        s.week_start_balance = 92_000.0
        s.day_start_balance = 90_000.0
        s.balance = 89_500.0
        s.mark_equity(89_500.0)
        s.review_acknowledged_for_day = s.current_day
        assert not DrawdownMonitor(TitanConfig().risk).evaluate(s).can_trade

    def test_a_new_session_resets_the_daily_measure(self):
        s = AccountState.open_account(100_000.0, NOW)
        s.balance = 96_000.0
        s.mark_equity(96_000.0)
        assert s.daily_drawdown_pct() == pytest.approx(4.0)
        s.roll_periods(NOW + timedelta(days=1))
        assert s.daily_drawdown_pct() == 0.0


class TestCompliance:
    def test_escalation_ladder(self):
        """Ch XII-C: one violation YELLOW, two ORANGE, three RED."""
        m = ComplianceMonitor(TitanConfig().compliance)
        assert m.flag(NOW) is Flag.NONE
        m.record(Rule.CHECKLIST_REQUIRED, NOW, "skipped checklist")
        assert m.flag(NOW) is Flag.YELLOW
        m.record(Rule.NO_NEWS_TRADING, NOW, "traded into NFP")
        assert m.flag(NOW) is Flag.ORANGE
        assert m.size_factor(NOW) == 0.5
        m.record(Rule.RESPECT_LOSS_LIMITS, NOW, "traded past the daily limit")
        assert m.flag(NOW) is Flag.RED
        assert not m.can_trade(NOW)

    def test_violations_age_out_of_the_thirty_day_window(self):
        m = ComplianceMonitor(TitanConfig().compliance)
        m.record(Rule.CHECKLIST_REQUIRED, NOW - timedelta(days=45), "old")
        assert m.flag(NOW) is Flag.NONE

    def test_a_critical_breach_goes_straight_to_red(self):
        m = ComplianceMonitor(TitanConfig().compliance)
        m.record(Rule.NO_AVERAGING_DOWN, NOW, "averaged down", critical=True)
        assert m.flag(NOW) is Flag.RED

    def test_averaging_down_is_detected_and_is_critical(self):
        """Ch XII-A2 / Ch XIV-A5: adding to losers is prohibited."""
        m = ComplianceMonitor(TitanConfig().compliance)
        violation = m.check_averaging_down(
            NOW, "XAUUSD", direction_sign=1,
            existing_open_price=2650.0, new_price=2630.0,
            existing_unrealized=-500.0,
        )
        assert violation is not None and violation.critical
        assert m.flag(NOW) is Flag.RED

    def test_adding_to_a_winner_is_not_averaging_down(self):
        """Ch VIII-C permits scaling into winners."""
        m = ComplianceMonitor(TitanConfig().compliance)
        assert (
            m.check_averaging_down(
                NOW, "XAUUSD", 1, existing_open_price=2650.0,
                new_price=2670.0, existing_unrealized=+500.0,
            )
            is None
        )

    def test_promotion_criteria(self):
        """Ch XII-B: 90 days, PF > 1.5, max drawdown < 6%."""
        cfg = TitanConfig().compliance
        ready, checks = ComplianceMonitor.promotion_ready(95, 1.8, 4.0, cfg)
        assert ready and all(checks.values())
        not_ready, checks = ComplianceMonitor.promotion_ready(95, 1.8, 7.0, cfg)
        assert not not_ready and not checks["max_drawdown"]


class TestCorrelation:
    def test_same_currency_instruments_are_correlated(self):
        """Ch VIII-A: 'Max Correlated Exposure — 2 trades same currency/sector.'"""
        assert shares_correlation_group(
            get_instrument("EURUSD"), get_instrument("GBPUSD")
        )
        assert shares_correlation_group(get_instrument("ES"), get_instrument("NQ"))
        assert shares_correlation_group(get_instrument("XAUUSD"), get_instrument("GC"))

    def test_unrelated_instruments_are_not(self):
        assert not shares_correlation_group(
            get_instrument("CL"), get_instrument("ES")
        )
