"""Cross-board deduplication.

The asymmetry that shapes every test here: merging two different jobs deletes a
real opportunity, while failing to merge two identical ones costs a duplicate
row. So when in doubt, keep both.
"""

import pytest

from solsift.dedupe import collapse, key_for, normalise_company, normalise_title
from solsift.listing import Listing


def listing(board="a", id="1", title="Virtual Assistant", company="Acme",
            desc="Admin work.", pay=None):
    return Listing(board=board, id=id, url=f"http://{board}/{id}", title=title,
                   company=company, description=desc, pay_low=pay,
                   pay_high=pay, pay_ceiling=pay)


# --- normalisation ----------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("Virtual Assistant", "URGENT: Virtual Assistant - Remote"),
    ("Executive Assistant", "Executive Assistant (Work From Home)"),
    ("Bookkeeper", "Bookkeeper - Full Time - Hiring Now!"),
])
def test_decorated_titles_normalise_together(a, b):
    assert normalise_title(a) == normalise_title(b)


def test_different_jobs_do_not_normalise_together():
    assert normalise_title("Virtual Assistant") != normalise_title("Bookkeeper")
    assert normalise_title("Senior Developer") != normalise_title("Data Entry")


@pytest.mark.parametrize("a,b", [
    ("Acme Inc", "Acme"),
    ("Acme Solutions Ltd.", "Acme"),
    ("ACME GLOBAL GROUP", "Acme"),
])
def test_company_suffixes_normalise_away(a, b):
    assert normalise_company(a) == normalise_company(b)


def test_different_companies_stay_different():
    assert normalise_company("Acme") != normalise_company("Globex")


# --- collapsing -------------------------------------------------------------

def test_same_job_on_two_boards_collapses():
    r = collapse([listing(board="linkedin"), listing(board="jobstreet")])
    assert len(r.kept) == 1 and r.collapsed == 1


def test_the_more_informative_copy_wins():
    """A listing with a pay figure beats one without."""
    bare = listing(board="linkedin", desc="Admin work.")
    priced = listing(board="remoteok", desc="Admin work.", pay=8.0)
    r = collapse([bare, priced])
    assert len(r.kept) == 1 and r.kept[0].pay_low == 8.0


def test_longer_description_wins_when_pay_is_equal():
    short = listing(board="a", desc="Admin.")
    long = listing(board="b", desc="Admin work with a great deal more detail.")
    assert collapse([short, long]).kept[0].description == long.description


def test_the_other_boards_are_recorded_not_hidden():
    r = collapse([listing(board="linkedin", pay=8.0), listing(board="jobstreet")])
    survivor = r.kept[0]
    assert "jobstreet" in r.also_on[f"{survivor.board}:{survivor.id}"]


def test_same_title_different_company_is_not_a_duplicate():
    """The failure that matters. Every board has a 'Virtual Assistant'."""
    r = collapse([listing(company="Acme"), listing(company="Globex")])
    assert len(r.kept) == 2 and r.collapsed == 0


def test_same_company_different_role_is_not_a_duplicate():
    r = collapse([listing(title="Virtual Assistant"),
                  listing(title="Senior Developer")])
    assert len(r.kept) == 2


def test_a_listing_with_no_company_is_never_merged():
    """Not enough to be confident, so keep both. Guessing deletes a real job."""
    r = collapse([listing(company=""), listing(company="", board="b")])
    assert len(r.kept) == 2 and r.collapsed == 0
    assert key_for(listing(company="")) is None


def test_empty_input():
    r = collapse([])
    assert r.kept == [] and r.collapsed == 0


def test_order_is_preserved():
    a = listing(id="1", title="Virtual Assistant")
    b = listing(id="2", title="Bookkeeper")
    c = listing(id="3", title="Data Entry Clerk")
    assert [l.id for l in collapse([a, b, c]).kept] == ["1", "2", "3"]
