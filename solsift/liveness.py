"""Is the listing still open?

A shortlist decays, and it decays silently. Boards keep an expired posting at
the same URL and serve it with **HTTP 200** - JobStreet answers 200 with
"This job is no longer advertised" in the body - so checking the status code
reports a dead listing as live. That is not hypothetical: on a real shortlist,
two of the three listings at the top were gone ten days later and a 200 said
otherwise, including the one a runbook had named as the next thing to apply to.

So: fetch the page, look for the board's own wording, and only fall back to the
status code for 404 and 410, which are unambiguous.

**Only direct evidence closes a listing.** A timeout, a 403, a bot check or an
unrecognised page leaves it `UNKNOWN`, and unknown is kept. An unreachable page
is not a closed job, and dropping a listing on a failed request is the same
mistake as treating unstated pay as low pay.
"""

from __future__ import annotations

import re
import urllib.error
from dataclasses import dataclass

from .boards.feeds import http_get

OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"

#: The board saying, in its own words, that the posting is done. Each is
#: specific enough that it cannot appear as ordinary prose in a live posting -
#: "closed" and "expired" alone are not, which is why they are not here.
_GONE = re.compile(
    r"no longer advertised"                 # jobstreet / seek
    r"|no longer accepting applications"    # linkedin
    r"|this (?:job|position|posting|role|vacancy) (?:has )?(?:expired|closed)"
    r"|job (?:advert(?:isement)?|posting) has expired"
    r"|(?:position|role|vacancy) (?:has been|is) (?:filled|closed)"
    r"|applications (?:are|have) closed"
    r"|this (?:job|listing) is no longer available",
    re.I)

#: A bot check is not an answer either way. Says so rather than guessing.
_CHALLENGE = re.compile(r"Just a moment|cf-challenge|Checking your browser"
                        r"|captcha", re.I)


@dataclass(frozen=True)
class Status:
    state: str          # OPEN / CLOSED / UNKNOWN
    why: str            # what the evidence actually was

    @property
    def gone(self) -> bool:
        return self.state == CLOSED


def classify(body: str, http_status: int = 200) -> Status:
    """Decide from a fetched page. Split out from the fetch so it is testable."""
    if http_status in (404, 410):
        return Status(CLOSED, f"HTTP {http_status}")
    if http_status >= 400:
        return Status(UNKNOWN, f"HTTP {http_status} - kept, a refusal is not a closure")
    m = _GONE.search(body)
    if m:
        return Status(CLOSED, f'page says "{m.group(0)}"')
    if _CHALLENGE.search(body):
        return Status(UNKNOWN, "bot check - kept, no answer either way")
    return Status(OPEN, "posting still served")


#: Liveness is the one job that fetches hundreds of URLs from a single host in
#: one burst - a scrape spreads its requests over eight boards, this does not.
#: At the feed boards' 1s spacing, JobStreet started answering 403 partway
#: through a real run, and a check that cannot answer is worth nothing. Slower
#: than manners require, because the alternative is an unusable result.
PACE_SECONDS = 4.0
_last_host: dict[str, float] = {}


def _pace(url: str) -> None:
    import time
    import urllib.parse
    host = urllib.parse.urlsplit(url).netloc
    gap = time.monotonic() - _last_host.get(host, 0.0)
    if gap < PACE_SECONDS:
        time.sleep(PACE_SECONDS - gap)
    _last_host[host] = time.monotonic()


def check(url: str, *, timeout: int = 20) -> Status:
    """Fetch one listing URL and say whether it is still open.

    Uses the same polite fetcher the feed boards use, so Retry-After handling
    is not reimplemented here - only the spacing is widened.
    """
    _pace(url)
    try:
        raw = http_get(url, timeout=timeout, retries=1)
    except urllib.error.HTTPError as e:
        return classify("", e.code)
    except Exception as e:                      # DNS, TLS, timeout, reset
        return Status(UNKNOWN, f"{type(e).__name__} - kept, unreachable is not closed")
    return classify(raw.decode("utf-8", "replace"))
