"""Every adapter must satisfy the contract, without touching the network.

These are cheap structural checks, and they exist because the failure they catch
is embarrassing: registering a class instead of an instance broke all eight
boards simultaneously with "search() missing 1 required positional argument",
and it only showed up during a live run.
"""

import inspect

import pytest

from jobsift import boards
from jobsift.boards.feeds import FeedBoard, strip_html
from jobsift.listing import Listing
from tests.test_money import FakeRates

boards.load_all()
ALL = boards.available()


def test_boards_are_registered():
    names = {b.name for b in ALL}
    assert {"remoteok", "remotive", "jobicy", "arbeitnow", "himalayas",
            "weworkremotely", "linkedin", "jobstreet"} <= names


@pytest.mark.parametrize("board", ALL, ids=lambda b: b.name)
def test_registry_holds_instances_not_classes(board):
    """The bug that broke every board at once."""
    assert not inspect.isclass(board), (
        f"{board} was registered as a class; register an instance so `search` "
        f"is bound")


@pytest.mark.parametrize("board", ALL, ids=lambda b: b.name)
def test_contract(board):
    assert isinstance(board.name, str) and board.name
    assert isinstance(board.help, str) and len(board.help) > 20
    assert isinstance(board.needs_browser, bool)
    assert isinstance(board.attribution, str)

    sig = inspect.signature(board.search)
    assert set(sig.parameters) >= {"query", "rates", "page", "skip"}, \
        f"{board.name}.search must accept query, rates, page, skip"


@pytest.mark.parametrize("board", ALL, ids=lambda b: b.name)
def test_help_names_no_other_board(board):
    """A copy-pasted adapter that still describes its parent is a real hazard."""
    others = {b.name for b in ALL} - {board.name}
    assert not [o for o in others if o in board.help.lower()]


@pytest.mark.parametrize("board", [b for b in ALL if isinstance(b, FeedBoard)],
                         ids=lambda b: b.name)
def test_feed_boards_build_a_url(board):
    url = board.url("assistant")
    assert url.startswith("https://"), f"{board.name} produced {url!r}"


def test_feed_boards_do_not_need_a_browser():
    for b in ALL:
        if isinstance(b, FeedBoard):
            assert not b.needs_browser


def test_only_jobstreet_needs_a_browser():
    """If this changes, the README's install section is wrong."""
    assert {b.name for b in ALL if b.needs_browser} == {"jobstreet"}


def test_attribution_present_where_terms_require_it():
    """Remote OK's API terms make credit a condition of access."""
    by = {b.name: b for b in ALL}
    assert "remoteok" in by["remoteok"].attribution.lower().replace(" ", "")


# --- parsing helpers --------------------------------------------------------

def test_strip_html():
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert strip_html("a &amp; b &lt;c&gt;") == "a & b <c>"
    assert strip_html("") == ""


def test_feed_board_parses_a_record_without_network():
    """RemoteOK's real record shape, offline."""
    from jobsift.boards.feeds import RemoteOK
    board = RemoteOK()
    item = {"id": 42, "position": "Executive Assistant", "company": "Acme",
            "location": "Worldwide", "description": "<p>Admin work.</p>",
            "url": "https://remoteok.com/l/42",
            "salary_min": 30000, "salary_max": 50000, "date": "2026-09-01"}
    listing = board.to_listing(item, FakeRates())
    assert isinstance(listing, Listing)
    assert listing.title == "Executive Assistant"
    assert listing.board == "remoteok"
    assert listing.description == "Admin work."
    # 30000 reads as annual, so an hourly figure has to be plausible.
    assert 5 < listing.pay_low < 40


def test_a_broken_record_is_skipped_not_fatal():
    from jobsift.boards.feeds import RemoteOK
    with pytest.raises((KeyError, TypeError, ValueError)):
        RemoteOK().to_listing({"no": "id"}, FakeRates())


def test_linkedin_query_split():
    from jobsift.boards.linkedin import LinkedIn
    assert LinkedIn()._split("virtual assistant | Philippines") == \
        ("virtual assistant", "Philippines")
    assert LinkedIn()._split("bookkeeper") == ("bookkeeper", "")


def test_linkedin_uses_no_credentials():
    """The guest endpoint is the whole point - nothing to suspend."""
    from jobsift.boards.linkedin import LinkedIn
    src = inspect.getsource(LinkedIn)
    for forbidden in ("li_at", "cookie", "Cookie", "password", "JSESSIONID"):
        assert forbidden not in src, \
            f"linkedin adapter must stay logged-out; found {forbidden!r}"
