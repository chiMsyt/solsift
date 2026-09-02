"""Boards that publish a public API or RSS feed.

These need no browser. Where a board offers a feed, that is the route it wants
you to use, and it is better on every axis: fast, stable across redesigns, and
unambiguous about what is allowed.

Every adapter here is thin on purpose. All the shared work - fetching, decoding,
matching the query, pay parsing, building a `Listing` - lives in `FeedBoard`, so
adding a board is roughly twenty lines of "where does this board keep the title".
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

# RSS feeds are untrusted third-party XML. Python's stdlib parser will happily
# resolve external entities and expand a billion-laughs bomb; defusedxml will
# not. This is not paranoia for a tool whose whole job is fetching remote files.
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError as _XmlParseError

from ..listing import Listing
from ..money import Rates, parse_pay
from .base import register

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]*\n\s*\n\s*")


#: Minimum gap between requests to the same host. Not a rate limiter so much as
#: basic manners: solsift runs a couple of times a week, not in a loop, and a
#: tool that hammers a free public API is how free public APIs stop being free.
MIN_INTERVAL = 1.0
_last_request: dict[str, float] = {}


def http_get(url: str, timeout: int = 25, retries: int = 2) -> bytes:
    """Fetch a URL politely: spaced out, and backing off when asked to.

    Honours `Retry-After` on 429 and 503. A board telling us to slow down is
    the clearest signal there is, and ignoring it is how an IP gets blocked for
    everyone using the tool.
    """
    host = urllib.parse.urlsplit(url).netloc
    for attempt in range(retries + 1):
        gap = time.monotonic() - _last_request.get(host, 0.0)
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "application/json, text/xml, */*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                _last_request[host] = time.monotonic()
                return r.read()
        except urllib.error.HTTPError as e:
            _last_request[host] = time.monotonic()
            if e.code in (429, 503) and attempt < retries:
                wait = e.headers.get("Retry-After")
                try:
                    delay = min(float(wait), 30.0) if wait else 2.0 ** attempt
                except (TypeError, ValueError):
                    delay = 2.0 ** attempt
                time.sleep(delay)
                continue
            raise


def strip_html(html: str) -> str:
    if not html:
        return ""
    text = _TAGS.sub(" ", html)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(a, b)
    return _WS.sub("\n\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


class FeedBoard:
    """Base for API/RSS boards. Subclasses supply `url()` and `parse()`."""

    needs_browser = False
    attribution = ""
    #: Most feeds return everything and expect the client to filter.
    filter_locally = True

    #: Feeds that page. `url()` receives the offset; None means one page only.
    page_size: int | None = None
    max_pages: int = 5

    def url(self, query: str, offset: int = 0) -> str:
        raise NotImplementedError

    def parse(self, raw: bytes) -> list[dict]:
        raise NotImplementedError

    def to_listing(self, item: dict, rates: Rates, **pay_kw) -> Listing | None:
        raise NotImplementedError

    def matches(self, listing: Listing, query: str) -> bool:
        """Substring match on title + description. Feeds rarely search well."""
        if not self.filter_locally or not query.strip():
            return True
        terms = [t for t in re.split(r"[\s,]+", query.lower()) if t]
        blob = f"{listing.title} {listing.description}".lower()
        return all(t in blob for t in terms)

    def search(self, query: str, rates: Rates, *, page=None,
               skip: frozenset[str] = frozenset(), **pay_kw) -> Iterator[Listing]:
        # Page until the feed runs dry. A single fixed-size call silently shows
        # a truncated board, which looks identical to a quiet week.
        pages = self.max_pages if self.page_size else 1
        for n in range(pages):
            offset = n * (self.page_size or 0)
            try:
                raw = http_get(self.url(query, offset))
            except (urllib.error.URLError, TimeoutError) as e:
                if n:
                    return                     # keep what earlier pages gave us
                raise RuntimeError(
                    f"{self.name}: could not reach the feed "
                    f"({type(e).__name__}). Other boards still ran.") from None
            try:
                items = self.parse(raw)
            except (ValueError, _XmlParseError, DefusedXmlException) as e:
                raise RuntimeError(
                    f"{self.name}: the feed did not parse ({e}). The board most "
                    f"likely changed its format - please open an issue."
                ) from None

            if not items:
                return
            for item in items:
                try:
                    listing = self.to_listing(item, rates, **pay_kw)
                except (KeyError, TypeError, ValueError):
                    continue                   # one bad record is not fatal
                if listing is None or f"{self.name}:{listing.id}" in skip:
                    continue
                if self.matches(listing, query):
                    yield listing
            if self.page_size is None or len(items) < self.page_size:
                return


#: How boards spell the pay period in their own JSON.
_PERIOD_WORDS = {
    "hour": "hourly", "hourly": "hourly", "hr": "hourly",
    "day": "daily", "daily": "daily",
    "week": "weekly", "weekly": "weekly",
    "month": "monthly", "monthly": "monthly",
    "year": "yearly", "yearly": "yearly", "annual": "yearly",
    "annually": "yearly",
}


def _pay(item, rates, *, lo_key, hi_key, cur_key=None, period_key=None,
         text="", **kw):
    """Use a board's structured salary fields where it provides them.

    A stated period from the board is the single most reliable signal there is,
    so it is passed straight through as `cadence` and the result is certain.
    When the board gives numbers but NO period, the cadence is unknown and must
    stay unknown - an earlier version guessed with a hardcoded
    `160*12 if amount > 10000 else 160 if > 1000 else 1`, which ignored the
    currency entirely and quietly mispriced every non-USD board.
    """
    lo, hi = item.get(lo_key), item.get(hi_key)
    if lo in (None, "", 0):
        return parse_pay(text, rates, **kw)

    try:
        lo_f, hi_f = float(lo), float(hi or lo)
    except (TypeError, ValueError):
        return parse_pay(text, rates, **kw)

    cur = (str(item.get(cur_key) or "USD")).upper() if cur_key else "USD"
    raw_period = str(item.get(period_key) or "").strip().lower() \
        if period_key else ""
    cadence = _PERIOD_WORDS.get(raw_period)

    # Hand the numbers back through the one parser, so board figures and prose
    # figures go down exactly the same path and get the same certainty rules.
    synthetic = f"{cur} {lo_f:.2f} - {cur} {hi_f:.2f}"
    return parse_pay(synthetic, rates, cadence=cadence, **kw)


# --------------------------------------------------------------------- boards

@register
class RemoteOK(FeedBoard):
    name = "remoteok"
    help = ("Free-text terms, e.g. \"virtual assistant\" or \"admin\".\n"
            "The feed returns everything current; solsift filters locally.")
    # Required by their API terms of service, not decoration. See report.py.
    attribution = "Jobs from [Remote OK](https://remoteok.com)"

    def url(self, query, offset=0): return "https://remoteok.com/api"

    def parse(self, raw):
        data = json.loads(raw)
        return [d for d in data if d.get("id")]     # [0] is the legal notice

    def to_listing(self, item, rates, **pay_kw):
        desc = strip_html(item.get("description", ""))
        pay = _pay(item, rates, lo_key="salary_min",
                                 hi_key="salary_max", text=desc[:2500], **pay_kw)
        return Listing(
            board=self.name, id=str(item["id"]),
            url=item.get("url") or item.get("apply_url", ""),
            title=item.get("position", ""), company=item.get("company", ""),
            location=item.get("location") or "Remote",
            employment_type="Remote", description=desc[:8000],
            posted=item.get("date", "")).apply_pay(pay)


@register
class Remotive(FeedBoard):
    name = "remotive"
    help = "Free-text terms, e.g. \"assistant\". Searched server-side."
    filter_locally = False
    attribution = "Jobs from [Remotive](https://remotive.com)"

    page_size = 100

    def url(self, query, offset=0):
        return ("https://remotive.com/api/remote-jobs?limit=100&search="
                + urllib.parse.quote(query))

    def parse(self, raw): return json.loads(raw).get("jobs", [])

    def to_listing(self, item, rates, **pay_kw):
        desc = strip_html(item.get("description", ""))
        pay = parse_pay(f"{item.get('salary', '', **pay_kw)}\n{desc[:2000]}", rates)
        return Listing(
            board=self.name, id=str(item["id"]), url=item.get("url", ""),
            title=item.get("title", ""), company=item.get("company_name", ""),
            location=item.get("candidate_required_location", "Remote"),
            employment_type=item.get("job_type", "").replace("_", " "),
            description=desc[:8000], pay_raw=item.get("salary", ""),
            posted=item.get("publication_date", "")).apply_pay(pay)


@register
class Jobicy(FeedBoard):
    name = "jobicy"
    help = "Free-text terms, e.g. \"admin\". Returns remote jobs worldwide."

    def url(self, query, offset=0):
        return "https://jobicy.com/api/v2/remote-jobs?count=50"

    def parse(self, raw): return json.loads(raw).get("jobs", [])

    def to_listing(self, item, rates, **pay_kw):
        desc = strip_html(item.get("jobDescription")
                          or item.get("jobExcerpt", ""))
        pay = _pay(item, rates, lo_key="salaryMin",
                                 hi_key="salaryMax", cur_key="salaryCurrency",
                                 period_key="salaryPeriod", text=desc[:2000], **pay_kw)
        types = item.get("jobType")
        return Listing(
            board=self.name, id=str(item["id"]), url=item.get("url", ""),
            title=item.get("jobTitle", ""), company=item.get("companyName", ""),
            location=item.get("jobGeo", "Remote"),
            employment_type=", ".join(types) if isinstance(types, list)
                            else str(types or ""),
            description=desc[:8000],
            posted=item.get("pubDate", "")).apply_pay(pay)


@register
class Arbeitnow(FeedBoard):
    name = "arbeitnow"
    help = ("Free-text terms. A lesser-known board, Europe-weighted, with a "
            "genuinely open API and a lot of remote listings.")

    page_size = 100

    def url(self, query, offset=0):
        page = offset // 100 + 1
        return ("https://www.arbeitnow.com/api/job-board-api"
                f"?page={page}")

    def parse(self, raw): return json.loads(raw).get("data", [])

    def to_listing(self, item, rates, **pay_kw):
        desc = strip_html(item.get("description", ""))
        pay = parse_pay(desc[:2500], rates, **pay_kw)
        types = item.get("job_types") or []
        return Listing(
            board=self.name, id=str(item["slug"]), url=item.get("url", ""),
            title=item.get("title", ""), company=item.get("company_name", ""),
            location=item.get("location", ""),
            employment_type=("Remote, " if item.get("remote") else "")
                            + ", ".join(types),
            description=desc[:8000],
            posted=str(item.get("created_at", ""))).apply_pay(pay)


@register
class Himalayas(FeedBoard):
    name = "himalayas"
    help = "Free-text terms. Remote-only board with an open API."

    page_size = 100

    def url(self, query, offset=0):
        return ("https://himalayas.app/jobs/api?limit=100"
                f"&offset={offset}")

    def parse(self, raw):
        d = json.loads(raw)
        return d.get("jobs") or d.get("data") or []

    def to_listing(self, item, rates, **pay_kw):
        desc = strip_html(item.get("description") or item.get("excerpt", ""))
        pay = _pay(item, rates, lo_key="minSalary",
                                 hi_key="maxSalary", cur_key="currency",
                                 period_key="salaryPeriod", text=desc[:2000], **pay_kw)
        loc = item.get("locationRestrictions") or ["Remote"]
        return Listing(
            board=self.name, id=str(item.get("guid") or item.get("title")),
            url=item.get("applicationLink", ""), title=item.get("title", ""),
            company=item.get("companyName", ""),
            location=", ".join(loc) if isinstance(loc, list) else str(loc),
            employment_type=str(item.get("employmentType", "")),
            description=desc[:8000],
            posted=str(item.get("pubDate", ""))).apply_pay(pay)


@register
class WeWorkRemotely(FeedBoard):
    name = "weworkremotely"
    help = ("An RSS category URL, e.g.\n"
            "  https://weworkremotely.com/categories/"
            "remote-customer-support-jobs.rss\n"
            "Browse the site, pick a category, use its .rss URL.")
    attribution = "Jobs from [We Work Remotely](https://weworkremotely.com)"

    def url(self, query, offset=0):
        return query if query.startswith("http") else (
            f"https://weworkremotely.com/categories/{query}.rss")

    def parse(self, raw):
        root = ET.fromstring(raw)
        out = []
        for item in root.iter("item"):
            out.append({c.tag.split('}')[-1]: (c.text or "")
                        for c in item})
        return out

    def matches(self, listing, query):
        return True                    # the category URL is already the filter

    def to_listing(self, item, rates, **pay_kw):
        desc = strip_html(item.get("description", ""))
        title = item.get("title", "")
        company, _, role = title.partition(":")
        pay = parse_pay(desc[:2500], rates, **pay_kw)
        return Listing(
            board=self.name, id=item.get("guid") or item.get("link", ""),
            url=item.get("link", ""), title=(role or title).strip(),
            company=company.strip() if role else "",
            location=item.get("region", "Remote"), employment_type="Remote",
            description=desc[:8000],
            posted=item.get("pubDate", "")).apply_pay(pay)
