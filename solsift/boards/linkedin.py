"""LinkedIn, via the public guest endpoint.

**No login, and deliberately so.** LinkedIn is aggressive about automated access
from authenticated sessions, and the penalty falls on the account - which for
someone in the middle of a job hunt is the worst possible thing to lose. So this
adapter only reads the same guest endpoint that backs LinkedIn's public,
logged-out job pages. No cookies, no credentials, nothing to ban.

The trade-off is honest: guest results carry less detail than the logged-in site
and no salary for most listings. That is the correct trade. A missing salary
costs you a click; a suspended account costs you the search.

It returns HTML fragments rather than JSON, so this parses conservatively and
skips anything it cannot read rather than guessing.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Iterator

from ..listing import Listing
from ..money import Rates, parse_pay
from .base import register
from .feeds import http_get, strip_html

_CARD = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"')
_TITLE = re.compile(r'<h3[^>]*base-search-card__title[^>]*>(.*?)</h3>', re.S)
_COMPANY = re.compile(
    r'<h4[^>]*base-search-card__subtitle[^>]*>.*?<a[^>]*>(.*?)</a>', re.S)
_LOCATION = re.compile(
    r'<span[^>]*job-search-card__location[^>]*>(.*?)</span>', re.S)
_LINK = re.compile(r'href="(https://[a-z.]*linkedin\.com/jobs/view/[^"?]+)')
_SALARY = re.compile(
    r'<span[^>]*job-search-card__salary-info[^>]*>(.*?)</span>', re.S)


@register
class LinkedIn:
    name = "linkedin"
    needs_browser = False
    attribution = ""
    help = ("Either a plain search term, or `terms | location`:\n"
            "  virtual assistant | Philippines\n"
            "  bookkeeper | United Kingdom\n"
            "Reads LinkedIn's logged-out guest endpoint - no account, no login, "
            "nothing that can be suspended. Salary is often absent there.")

    URL = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/"
           "search?keywords={kw}&location={loc}&start={start}")
    PAGES = 3          # 25 cards per page; three is plenty for a twice-weekly run

    def _split(self, query: str) -> tuple[str, str]:
        terms, _, location = query.partition("|")
        return terms.strip(), location.strip()

    def search(self, query: str, rates: Rates, *, page=None,
               skip: frozenset[str] = frozenset(), **pay_kw) -> Iterator[Listing]:
        kw, loc = self._split(query)
        seen_here: set[str] = set()

        for n in range(self.PAGES):
            url = self.URL.format(kw=urllib.parse.quote(kw),
                                  loc=urllib.parse.quote(loc), start=n * 25)
            try:
                html = http_get(url).decode("utf-8", "replace")
            except Exception:
                break            # rate-limited or exhausted; keep what we have
            if not _CARD.search(html):
                break

            # Split on the card boundary so a field cannot leak across listings.
            chunks = re.split(r'(?=data-entity-urn="urn:li:jobPosting:)', html)
            for chunk in chunks:
                m = _CARD.search(chunk)
                if not m:
                    continue
                jid = m.group(1)
                if jid in seen_here or f"{self.name}:{jid}" in skip:
                    continue
                seen_here.add(jid)

                def one(rx, default=""):
                    hit = rx.search(chunk)
                    return strip_html(hit.group(1)).strip() if hit else default

                title = one(_TITLE)
                if not title:
                    continue               # unreadable card, skip rather than guess

                link = _LINK.search(chunk)
                salary = one(_SALARY)
                pay = parse_pay(salary, rates, **pay_kw) if salary else None

                listing = Listing(
                    board=self.name, id=jid,
                    url=link.group(1) if link
                        else f"https://www.linkedin.com/jobs/view/{jid}",
                    title=title, company=one(_COMPANY),
                    location=one(_LOCATION, loc), employment_type="",
                    description=title,     # guest cards carry no description
                    pay_raw=salary)
                yield listing.apply_pay(pay) if pay else listing
