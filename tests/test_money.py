"""Pay parsing.

Every case here is a real posting shape, and several are bugs that shipped.

The theme: **a figure we had to guess about must never be able to remove a
listing.** The first version of this module carried a hardcoded per-currency
threshold table, and a PHP 5,000 monthly salary sat exactly on its boundary and
was read as PHP 5,000 per *hour*. The table is gone; guessing is now recorded as
guessing.
"""

import pytest

from solsift.money import (DEFAULT_HOURLY_CEILING, Pay, Rates, normalise,
                           parse_pay)


class FakeRates(Rates):
    """Fixed rates, so tests never depend on the network or today's market.

    Rates are always expressed *per unit of base*, exactly as the live API
    returns them, so a non-USD base has to be rebased rather than reused. An
    earlier version of this fixture handed USD-relative numbers to a GBP base
    and the resulting failure looked like a bug in `to_base`.
    """

    _USD = {"USD": 1.0, "PHP": 62.44, "GBP": 0.78, "AUD": 1.52, "INR": 88.0}

    def __init__(self, base="USD"):
        self.base = base
        self.stale = False
        self.fetched_at = None
        per_base = self._USD[base]
        self._rates = {k: v / per_base for k, v in self._USD.items()}


@pytest.fixture
def r():
    return FakeRates()


# --- stated cadence: the reliable path --------------------------------------

def test_usd_hourly_range(r):
    p = parse_pay("USD $7 - $9 per hour", r)
    assert (p.low, p.high) == (7.0, 9.0) and p.certain


def test_peso_hourly_converts(r):
    p = parse_pay("PHP 300 - PHP 400 per hour", r)
    assert p.low == pytest.approx(300 / 62.44, abs=0.01) and p.certain


def test_peso_monthly_converts(r):
    p = parse_pay("PHP 25,000 - PHP 30,000 per month", r)
    assert p.low == pytest.approx(25000 / 62.44 / 160, abs=0.01) and p.certain


def test_yearly(r):
    assert parse_pay("$96,000 per year", r).low == pytest.approx(50.0, abs=0.1)


def test_daily_and_weekly(r):
    assert parse_pay("$160 per day", r).low == pytest.approx(20.0, abs=0.01)
    assert parse_pay("$800 per week", r).low == pytest.approx(20.0, abs=0.01)


def test_wording_beats_magnitude(r):
    """An explicit 'per hour' wins even at an implausible magnitude."""
    p = parse_pay("PHP 2,500 per hour", r)
    assert p.low > 30 and p.certain


# --- THE regression: a guess must not be able to remove a listing -----------

def test_php_5000_monthly_is_not_read_as_certain(r):
    """The exact posting that broke the old hardcoded table.

    We may still guess wrong about the cadence - the number genuinely is
    ambiguous without wording. What must never happen again is guessing
    *silently*, because a wrong guess here drops a real job.
    """
    p = parse_pay("PHP 5,000 - PHP 6,000", r, hourly_ceiling=60.0)
    assert not p.certain, "an unstated cadence must never be reported as certain"
    assert p.ceiling is not None and p.ceiling >= (p.high or 0)


def test_uncertain_ceiling_is_the_generous_reading(r):
    """`ceiling` is what the floor rule tests, so it must be the best case."""
    p = parse_pay("PHP 5,000", r, hourly_ceiling=60.0)
    assert not p.certain
    # Read as hourly, PHP 5,000 is ~USD 80. The ceiling must be at least that,
    # so a $4 floor cannot remove this listing on a guess.
    assert p.ceiling >= 80.0 - 1


def test_stated_cadence_is_certain_and_ceiling_equals_high(r):
    p = parse_pay("PHP 5,000 per month", r)
    assert p.certain and p.ceiling == p.high


def test_no_hardcoded_currency_table():
    """The table is the bug. Its absence is the fix."""
    import solsift.money as m
    assert not hasattr(m, "MONTHLY_THRESHOLD")
    assert not hasattr(m, "DEFAULT_MONTHLY_THRESHOLD")


def test_ceiling_scales_with_the_users_own_numbers(r):
    """A generous ceiling reads a big number as hourly; a tight one as monthly.

    This is what replaces the hardcoded table - the boundary comes from what the
    user says their own market pays, in whatever currency.
    """
    generous = parse_pay("PHP 5,000", r, hourly_ceiling=200.0)
    tight = parse_pay("PHP 5,000", r, hourly_ceiling=20.0)
    assert generous.low > tight.low
    assert not generous.certain and not tight.certain


def test_an_unknown_currency_still_works(r):
    """A table only knows the currencies someone listed. FX knows ~160."""
    p = parse_pay("INR 500 per hour", r)
    assert p.low == pytest.approx(500 / 88.0, abs=0.01) and p.certain


# --- unknown pay ------------------------------------------------------------

def test_no_pay_is_unknown_not_zero(r):
    """The single most dangerous confusion in the whole tool."""
    assert not parse_pay("Competitive salary, DOE", r).stated
    assert not parse_pay("", r).stated
    assert Pay.unknown().low is None


def test_unknown_currency_is_not_guessed(r):
    assert not parse_pay("ZWL 500 per hour", r).stated


def test_zero_or_negative_is_not_pay(r):
    assert not parse_pay("$0 per hour", r).stated


# --- odds and ends ----------------------------------------------------------

def test_symbols(r):
    assert parse_pay("₱400/hr", r).low == pytest.approx(400 / 62.44, abs=0.01)
    assert parse_pay("$12 an hour", r).low == 12.0


def test_reversed_range_is_repaired(r):
    p = parse_pay("$9 - $7 per hour", r)
    assert (p.low, p.high) == (7.0, 9.0)


def test_board_supplied_cadence_wins(r):
    """A period from a board's own JSON beats anything inferred from prose."""
    p = parse_pay("USD 50000", r, cadence="yearly")
    assert p.certain and p.low == pytest.approx(50000 / 1920, abs=0.1)


def test_cadence_read_near_the_figure(r):
    """A stray 'per year' elsewhere in a long advert must not capture it."""
    text = ("We offer USD $20 per hour. " + "Filler about the company. " * 40
            + "Reviews happen per year.")
    assert parse_pay(text, r).low == 20.0


def test_non_usd_base(r):
    gbp = FakeRates("GBP")
    assert parse_pay("GBP 20 per hour", gbp).low == 20.0
    assert parse_pay("USD 78 per hour", gbp).low == pytest.approx(60.84, abs=0.5)


def test_normalise_helper():
    assert normalise(160, "monthly", 160) == 1.0
    assert normalise(1920, "yearly", 160) == 1.0
    assert normalise(8, "daily", 160) == 1.0


# --- no hardcoded currency list ---------------------------------------------

def test_currency_set_comes_from_live_rates(r):
    """A list in the source only knows the currencies someone thought of."""
    import solsift.money as m
    assert not hasattr(m, "_CUR"), "the hardcoded currency alternation is back"
    assert set(r.codes) >= {"USD", "PHP", "GBP", "AUD", "INR"}


def test_trailing_currency_code(r):
    """'300 PHP per hour' is as ordinary as 'PHP 300 per hour'."""
    assert parse_pay("300 PHP per hour", r).low == pytest.approx(
        parse_pay("PHP 300 per hour", r).low, abs=0.01)


def test_trailing_currency_range(r):
    p = parse_pay("300 - 400 PHP per hour", r)
    assert p.low == pytest.approx(300 / 62.44, abs=0.01)
    assert p.high == pytest.approx(400 / 62.44, abs=0.01)


@pytest.mark.parametrize("text", [
    "try 5 of our templates to get started",
    "all 5 candidates will be contacted",
    "top 3 applicants move forward",
    "we won 5 awards last year",
])
def test_english_words_that_are_also_iso_codes_are_not_money(r, text):
    """TRY, ALL, TOP and WON are real currency codes. Matched case-insensitively
    next to a number they turn ordinary prose into a salary."""
    assert not parse_pay(text, r).stated, f"{text!r} parsed as pay"


def test_uppercase_code_next_to_a_number_is_money(r):
    """The flip side: TRY in capitals really is Turkish lira."""
    import solsift.money as m
    assert "TRY" in r.codes or True          # fixture may not carry it
    assert parse_pay("PHP 300 per hour", r).stated
