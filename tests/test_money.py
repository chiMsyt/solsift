"""Pay parsing. Every case here is a real posting shape, and several are bugs."""

import pytest

from jobsift.money import Rates, parse_pay


class FakeRates(Rates):
    """Fixed rates, so tests never depend on the network or today's market.

    Rates are always expressed *per unit of base*, exactly as the live API
    returns them, so a non-USD base has to be rebased rather than reused. An
    earlier version of this fixture handed USD-relative numbers to a GBP base
    and the resulting test failure looked like a bug in `to_base`.
    """

    _USD = {"USD": 1.0, "PHP": 62.44, "GBP": 0.78, "AUD": 1.52}

    def __init__(self, base="USD"):
        self.base = base
        self.stale = False
        self.fetched_at = None
        per_base = self._USD[base]
        self._rates = {k: v / per_base for k, v in self._USD.items()}


@pytest.fixture
def r():
    return FakeRates()


def test_usd_hourly_range(r):
    lo, hi, raw, _ = parse_pay("USD $7 - $9 per hour", r)
    assert (lo, hi) == (7.0, 9.0)
    assert "7" in raw


def test_peso_hourly_converts(r):
    lo, _, _, note = parse_pay("PHP 300 - PHP 400 per hour", r)
    assert lo == pytest.approx(300 / 62.44, abs=0.01)
    assert "PHP" in note


def test_peso_monthly_converts(r):
    lo, _, _, _ = parse_pay("PHP 25,000 - PHP 30,000 per month", r)
    assert lo == pytest.approx(25000 / 62.44 / 160, abs=0.01)


def test_monthly_boundary_regression(r):
    """PHP 5,000/month was read as PHP 5,000/HOUR.

    The threshold was `> 5000` and the value sat exactly on it, so an intern
    role came out as the best-paid listing on the board at about $80/hr.
    """
    lo, _, _, _ = parse_pay("PHP 5,000 - PHP 6,000 per month", r)
    assert lo < 1.0, "a PHP 5,000 monthly salary is not an hourly rate"


def test_magnitude_used_when_cadence_unstated(r):
    """No 'per month' anywhere - magnitude has to carry it."""
    assert parse_pay("PHP 28,000", r)[0] < 5.0
    assert parse_pay("PHP 350", r)[0] > 4.0


def test_wording_beats_magnitude(r):
    """An explicit 'per hour' wins even at an implausible magnitude."""
    assert parse_pay("PHP 2,500 per hour", r)[0] > 30


def test_symbols(r):
    assert parse_pay("₱400/hr", r)[0] == pytest.approx(400 / 62.44, abs=0.01)
    assert parse_pay("$12 an hour", r)[0] == 12.0


def test_reversed_range_is_repaired(r):
    lo, hi, _, _ = parse_pay("$9 - $7 per hour", r)
    assert (lo, hi) == (7.0, 9.0)


def test_no_pay_is_none_not_zero(r):
    """The single most dangerous confusion in the whole tool."""
    assert parse_pay("Competitive salary, DOE", r)[:2] == (None, None)
    assert parse_pay("", r)[:2] == (None, None)


def test_yearly(r):
    lo, _, _, _ = parse_pay("$96,000 per year", r)
    assert lo == pytest.approx(50.0, abs=0.1)


def test_unknown_currency_is_not_guessed(r):
    lo, hi, _, note = parse_pay("ZWL 500 per hour", r)
    assert (lo, hi) == (None, None)


def test_non_usd_base(r):
    gbp = FakeRates("GBP")
    assert parse_pay("GBP 20 per hour", gbp)[0] == 20.0
    assert parse_pay("USD 78 per hour", gbp)[0] == pytest.approx(60.84, abs=0.5)
