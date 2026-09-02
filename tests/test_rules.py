"""Rules.

Half these tests assert a rule FIRES; the other half assert it does not. The
second half matters more. A rule that over-matches deletes real jobs silently,
and every exception in `rules.py` exists because the naive pattern killed
something a person would have applied to.
"""

import pytest

from solsift.listing import Listing
from solsift.profile import Profile, Source
from solsift.rules import ALL_RULES, RULES_BY_KEY, screen


def listing(**kw):
    base = dict(board="test", id="1", url="http://x", title="Virtual Assistant",
                description="General admin support.", employment_type="Part time",
                location="Remote", pay_low=6.0, pay_high=8.0)
    return Listing(**{**base, **kw})


def profile(**kw):
    base = dict(name="t", sources=[Source("remoteok", ["x"])],
                floor_per_hour=4.0, employment_types=["part time"],
                remote_only=True)
    return Profile(**{**base, **kw})


def killed_by(l, p):
    v = screen(l, p)
    return v.killed_by.key if v.killed_by else None


# --- clean listing ----------------------------------------------------------

def test_ordinary_listing_survives():
    assert killed_by(listing(), profile()) is None


# --- pay --------------------------------------------------------------------

def test_below_floor_dies():
    assert killed_by(listing(pay_low=3.0, pay_high=3.0, pay_ceiling=3.0),
                     profile()) == "below_floor"


def test_at_floor_survives():
    assert killed_by(listing(pay_low=4.0, pay_high=4.0, pay_ceiling=4.0),
                     profile()) is None


def test_range_straddling_the_floor_survives():
    """A $3-8/hr posting is not a sub-$4 job; it might pay $8. The floor tests
    the top of the range, because a false removal costs a real job."""
    assert killed_by(listing(pay_low=3.0, pay_high=8.0, pay_ceiling=8.0),
                     profile()) is None


def test_a_guessed_cadence_cannot_remove_a_listing():
    """The whole point of pay_ceiling.

    PHP 5,000 with no stated period is ~USD 80. Read as monthly that is
    USD 0.50/hr - under any floor. Read as hourly it is USD 80/hr. We do not
    know which, so it must survive and be flagged, not vanish.
    """
    guessed = listing(pay_low=0.50, pay_high=0.60, pay_ceiling=80.0,
                      pay_certain=False)
    assert killed_by(guessed, profile()) is None

    # The same figure, with the period actually stated, is removable.
    stated = listing(pay_low=0.50, pay_high=0.60, pay_ceiling=0.60,
                     pay_certain=True)
    assert killed_by(stated, profile()) == "below_floor"


def test_uncertain_pay_is_marked_in_the_display():
    from solsift.listing import Listing
    l = Listing(board="t", id="1", url="u", title="x",
                pay_low=5.0, pay_high=6.0, pay_certain=False)
    assert l.pay_display.endswith("?")


def test_unstated_pay_survives():
    """Unknown pay is not low pay. Killing on it loses the best listings,
    which routinely say 'rate depends on the application'."""
    assert killed_by(listing(pay_low=None, pay_high=None), profile()) is None


def test_no_floor_set_keeps_everything():
    assert killed_by(listing(pay_low=0.5), profile(floor_per_hour=None)) is None


# --- employment type --------------------------------------------------------

def test_full_time_dies_when_part_time_wanted():
    assert killed_by(listing(employment_type="Full time"),
                     profile()) == "wrong_employment"


def test_empty_employment_filter_accepts_any():
    assert killed_by(listing(employment_type="Full time"),
                     profile(employment_types=[])) is None


# --- remote -----------------------------------------------------------------

def test_office_based_dies():
    assert killed_by(listing(description="This is an office-based role."),
                     profile()) == "not_remote"


def test_onsite_visits_in_remote_role_survives():
    """'on-site visits' is a duty, not a workplace."""
    assert killed_by(
        listing(description="Fully remote, with occasional on-site visits."),
        profile()) is None


def test_remote_only_off_keeps_office_roles():
    assert killed_by(listing(description="Office-based."),
                     profile(remote_only=False)) is None


# --- disguised sales --------------------------------------------------------

@pytest.mark.parametrize("text", [
    "You will be an appointment setter for our clients.",
    "Cold calling prospects daily.",
    "Role: Sales Representative, uncapped earnings",
    "Work as an insurance agent",
])
def test_disguised_sales_dies(text):
    assert killed_by(listing(description=text), profile()) == "disguised_sales"


@pytest.mark.parametrize("text", [
    "Provide administrative support to the sales team.",
    "Sales support administrative assistant duties, data entry.",
])
def test_genuine_admin_near_sales_survives(text):
    """An admin role that supports sales is an admin role."""
    assert killed_by(listing(description=text), profile()) is None


# --- fraud ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "A one-time training fee of PHP 500 applies.",
    "Equipment deposit required before start.",
    "Please send a valid ID before signing anything.",
])
def test_fraud_shapes_die(text):
    assert killed_by(listing(description=text), profile()) is not None


def test_commission_only_dies():
    assert killed_by(listing(description="This is a commission-only role."),
                     profile()) == "commission_only"


def test_always_on_dies():
    assert killed_by(listing(description="Must have 24/7 availability."),
                     profile()) == "always_on"


# --- credentials ------------------------------------------------------------

def test_required_credential_not_held_dies():
    p = profile(credentials_required_kill=["RN"], credentials=[])
    assert killed_by(listing(description="Must be a registered nurse."),
                     p) == "missing_credential"


def test_preferred_credential_survives():
    """'CPA preferred' is a wish, not a bar. This distinction is the whole
    reason the credential rule is written strictly."""
    p = profile(credentials_required_kill=["CPA"], credentials=[])
    assert killed_by(listing(description="CPA preferred but not required."),
                     p) is None


def test_held_credential_never_kills():
    p = profile(credentials_required_kill=["RN"], credentials=["RN"])
    assert killed_by(listing(description="Registered nurse required."), p) is None


# --- user rules -------------------------------------------------------------

def test_exclude_keyword():
    p = profile(exclude_keywords=["crypto"])
    assert killed_by(listing(description="Crypto trading support."),
                     p) == "excluded_keyword"


def test_disabling_a_rule_works():
    p = profile(disabled_rules=["disguised_sales"])
    assert killed_by(listing(description="Cold calling all day."), p) is None


# --- structure --------------------------------------------------------------

def test_every_rule_explains_itself():
    """The README and `solsift rules` are generated from these fields."""
    for r in ALL_RULES:
        assert r.key and r.kills and r.why
        assert r.why.endswith("."), f"{r.key}: why should read as prose"
        assert len(r.why) > 40, f"{r.key}: why is too thin to be useful"


def test_rule_keys_unique():
    assert len(RULES_BY_KEY) == len(ALL_RULES)


def test_verdict_records_every_firing_rule():
    l = listing(pay_low=1.0, description="Commission-only cold calling.")
    v = screen(l, profile())
    assert len(v.fired) >= 2 and v.killed_by is v.fired[0]


# --- relevance --------------------------------------------------------------

def test_off_target_title_dies():
    """A Golang role that mentions 'assistant' once must not survive an
    assistant search. This is relevance, not disqualification."""
    p = profile(title_keywords=["assistant", "admin"])
    assert killed_by(listing(title="Senior Golang Developer",
                             description="You will assist the team."),
                     p) == "off_target"


def test_on_target_title_survives():
    p = profile(title_keywords=["assistant", "admin"])
    assert killed_by(listing(title="Executive Assistant"), p) is None
    assert killed_by(listing(title="Admin & Operations Coordinator"), p) is None


def test_no_title_keywords_disables_relevance_filter():
    p = profile(title_keywords=[])
    assert killed_by(listing(title="Senior Golang Developer"), p) is None
