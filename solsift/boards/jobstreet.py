"""JobStreet (SEEK) adapter. Covers ph/sg/my/id, which share a front end.

The only bundled board that needs a browser: JobStreet publishes no feed and
sits behind Cloudflare, which no plain HTTP client gets past.

Two things learned the expensive way:

- **Every result card renders two `/job/<id>` links.** Counting matches without
  de-duplicating reports 60 listings on a page holding 30, and every downstream
  number inherits the error.
- **The path segment is the filter, not a query parameter.** `/part-time` works;
  `?worktype=244` silently redirects to contract/temp - the wrong board, no error.
"""

from __future__ import annotations

import re
from typing import Iterator

from ..listing import Listing
from ..money import Rates, parse_pay
from .base import register

_JOB_ID = re.compile(r"/job/(\d+)")
_CHALLENGE = re.compile(r"Just a moment|cf-challenge|Checking your browser", re.I)


@register
class JobStreet:
    name = "jobstreet"
    needs_browser = True
    attribution = ""
    help = ("A full JobStreet search URL, including the country subdomain:\n"
            "  https://<cc>.jobstreet.com/<role>-jobs/<worktype>"
            "?sortmode=ListedDate\n"
            "sg, my, id, ph - whichever market you are searching. There is no\n"
            "default country; a guess would quietly search someone else's market.\n"
            "Build the search on the site first, then paste the URL. The path\n"
            "segment is the filter - /part-time is right, ?worktype= is not.")

    # data-automation attributes are JobStreet's own test hooks - far more
    # stable than CSS classes, which are hashed and change every deploy.
    SEL = {"title": "[data-automation=job-detail-title]",
           "company": "[data-automation=advertiser-name]",
           "location": "[data-automation=job-detail-location]",
           "work_type": "[data-automation=job-detail-work-type]",
           "salary": "[data-automation=job-detail-salary]",
           "body": "[data-automation=jobAdDetails]"}

    def search(self, query: str, rates: Rates, *, page=None,
               skip: frozenset[str] = frozenset(), **pay_kw) -> Iterator[Listing]:
        if page is None:
            raise RuntimeError("jobstreet needs a browser page")

        origin_m = re.match(r"(https?://[^/]+)", query)
        if not origin_m:
            # No default country. Guessing one would quietly search a market the
            # user never asked for and report it as though it were theirs.
            raise RuntimeError(
                f"jobstreet needs a full search URL including the country "
                f"subdomain, got {query!r}.\n"
                f"Example: https://sg.jobstreet.com/admin-jobs/part-time"
                f"?sortmode=ListedDate")
        origin = origin_m.group(1)

        page.goto(query, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        html = page.content()
        if _CHALLENGE.search(html):
            raise RuntimeError(
                "jobstreet: blocked by a bot check. Re-run with --headed and "
                "solve it once; the browser keeps the result.")

        # set() - each card carries two links to the same listing.
        for jid in sorted(set(_JOB_ID.findall(html))):
            if f"{self.name}:{jid}" in skip:
                continue                   # skip BEFORE paying for a page load
            listing = self._fetch(page, origin, jid, rates, **pay_kw)
            if listing:
                yield listing

    def _fetch(self, page, origin: str, jid: str, rates: Rates,
               **pay_kw) -> Listing | None:
        url = f"{origin}/job/{jid}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
            title = page.locator(self.SEL["title"]).first.inner_text(
                timeout=6000).strip()
        except Exception:
            return None

        def grab(key: str) -> str:
            try:
                return page.locator(self.SEL[key]).first.inner_text(
                    timeout=2500).strip()
            except Exception:
                return ""

        salary, body = grab("salary"), grab("body")
        # Salary field first, so an explicit figure beats a stray number in prose.
        pay = parse_pay(f"{salary}\n{body[:2500]}", rates, **pay_kw)

        return Listing(
            board=self.name, id=jid, url=url, title=title,
            company=grab("company"), location=grab("location"),
            employment_type=grab("work_type"), description=body[:8000],
            pay_raw=salary).apply_pay(pay)
