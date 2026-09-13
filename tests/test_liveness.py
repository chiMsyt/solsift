"""A shortlist that reports dead jobs as live is worse than no shortlist.

Every case here is a page shape that was actually observed, or the exact
mistake the module exists to stop: closing a listing because a request failed.
"""

from solsift.liveness import CLOSED, OPEN, UNKNOWN, classify

# What JobStreet really serves for an expired posting - HTTP 200, with the
# notice in an <h2>. This is the byte sequence that proved a runbook's "next
# action" had been dead for days while a 200 said otherwise.
JOBSTREET_EXPIRED = (
    '<html><body><h2 class="_5l756w0 _5l756wh">This job is no longer '
    'advertised</h2><span>Search similar jobs</span></body></html>')

JOBSTREET_LIVE = (
    '<html><body><h1 data-automation="job-detail-title">GoHighLevel CRM '
    'Virtual Assistant</h1><div data-automation="jobAdDetails">We are '
    'looking for...</div></body></html>')


def test_expired_page_served_with_200_is_closed():
    """The whole point: status 200 must not be mistaken for "still open"."""
    assert classify(JOBSTREET_EXPIRED, 200).state == CLOSED


def test_live_page_is_open():
    assert classify(JOBSTREET_LIVE, 200).state == OPEN


def test_404_and_410_close_without_needing_wording():
    for code in (404, 410):
        assert classify("", code).state == CLOSED


def test_other_http_errors_do_not_close_a_listing():
    """403 is a refusal to answer, not an answer. Keeping it is the point."""
    for code in (403, 429, 500, 503):
        assert classify("", code).state == UNKNOWN


def test_bot_check_is_not_an_answer():
    assert classify("<title>Just a moment...</title>", 200).state == UNKNOWN


def test_linkedin_wording():
    assert classify("<span>No longer accepting applications</span>").state == CLOSED


def test_ordinary_prose_does_not_close_a_live_listing():
    """The failure that would hurt most: a real job dropped on a word match.

    Live postings say "closed" and "expired" all the time - month-end close,
    expired invoices, closing the books. Only the board's own done-wording may
    close a listing.
    """
    for prose in [
        "You will handle month-end close and chase expired invoices.",
        "Experience closing the books is preferred.",
        "Track expired contracts and closed deals in the CRM.",
        "This is a closed-loop reporting process.",
    ]:
        assert classify(f"<div>{prose}</div>").state == OPEN, prose


def test_why_quotes_the_evidence():
    """A verdict you cannot audit is a verdict you cannot trust."""
    s = classify(JOBSTREET_EXPIRED)
    assert "no longer advertised" in s.why.lower()
    assert s.gone
