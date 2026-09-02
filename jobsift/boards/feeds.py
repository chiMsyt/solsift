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
import urllib.error
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


def http_get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


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

    def url(self, query: str) -> str:
        raise NotImplementedError

    def parse(self, raw: bytes) -> list[dict]:
        raise NotImplementedError

    def to_listing(self, item: dict, rates: Rates) -> Listing | None:
        raise NotImplementedError

    def matches(self, listing: Listing, query: str) -> bool:
        """Substring match on title + description. Feeds rarely search well."""
        if not self.filter_locally or not query.strip():
            return True
        terms = [t for t in re.split(r"[\s,]+", query.lower()) if t]
        blob = f"{listing.title} {listing.description}".lower()
        return all(t in blob for t in terms)

    def search(self, query: str, rates: Rates, *, page=None,
               skip: frozenset[str] = frozenset()) -> Iterator[Listing]:
        try:
            raw = http_get(self.url(query))
        except (urllib.error.URLError, TimeoutError) as e:
            raise RuntimeError(
                f"{self.name}: could not reach the feed ({type(e).__name__}). "
                f"Other boards in your profile still ran.") from None
        try:
            items = self.parse(raw)
        except (ValueError, _XmlParseError, DefusedXmlException) as e:
            raise RuntimeError(
                f"{self.name}: the feed did not parse ({e}). The board most "
                f"likely changed its format - please open an issue.") from None

        for item in items:
            try:
                listing = self.to_listing(item, rates)
            except (KeyError, TypeError, ValueError):
                continue                       # one bad record is not fatal
            if listing is None or f"{self.name}:{listing.id}" in skip:
                continue
            if self.matches(listing, query):
                yield listing


def _pay(item, rates, *, lo_key, hi_key, cur_key=None, period_key=None,
         text=""):
    """Structured salary fields where a board provides them, prose otherwise."""
    lo, hi = item.get(lo_key), item.get(hi_key)
    if lo:
        cur = (item.get(cur_key) or "USD").upper() if cur_key else "USD"
        period = (item.get(period_key) or "").lower() if period_key else ""
        unit = {"hour": 1, "hourly": 1}.get(period)
        try:
            lo_f, hi_f = float(lo), float(hi or lo)
        except (TypeError, ValueError):
            return parse_pay(text, rates)
        if unit is None:                        # annual is the usual default
            div = 160 * 12 if lo_f > 10000 else (160 if lo_f > 1000 else 1)
            lo_f, hi_f = lo_f / div, hi_f / div
        try:
            return (round(rates.to_base(lo_f, cur), 2),
                    round(rates.to_base(hi_f, cur), 2),
                    f"{cur} {lo}-{hi}", f"{cur} from feed")
        except KeyError:
            return None, None, "", ""
    return parse_pay(text, rates)


# --------------------------------------------------------------------- boards

@register
class RemoteOK(FeedBoard):
    name = "remoteok"
    help = ("Free-text terms, e.g. \"virtual assistant\" or \"admin\".\n"
            "The feed returns everything current; jobsift filters locally.")
    # Required by their API terms of service, not decoration. See report.py.
    attribution = "Jobs from [Remote OK](https://remoteok.com)"

    def url(self, query): return "https://remoteok.com/api"

    def parse(self, raw):
        data = json.loads(raw)
        return [d for d in data if d.get("id")]     # [0] is the legal notice

    def to_listing(self, item, rates):
        desc = strip_html(item.get("description", ""))
        lo, hi, raw, note = _pay(item, rates, lo_key="salary_min",
                                 hi_key="salary_max", text=desc[:2500])
        return Listing(
            board=self.name, id=str(item["id"]),
            url=item.get("url") or item.get("apply_url", ""),
            title=item.get("position", ""), company=item.get("company", ""),
            location=item.get("location") or "Remote",
            employment_type="Remote", description=desc[:8000],
            pay_low=lo, pay_high=hi, pay_raw=raw, pay_note=note,
            posted=item.get("date", ""))


@register
class Remotive(FeedBoard):
    name = "remotive"
    help = "Free-text terms, e.g. \"assistant\". Searched server-side."
    filter_locally = False
    attribution = "Jobs from [Remotive](https://remotive.com)"

    def url(self, query):
        return ("https://remotive.com/api/remote-jobs?limit=100&search="
                + urllib.request.quote(query))

    def parse(self, raw): return json.loads(raw).get("jobs", [])

    def to_listing(self, item, rates):
        desc = strip_html(item.get("description", ""))
        lo, hi, raw, note = parse_pay(
            f"{item.get('salary', '')}\n{desc[:2000]}", rates)
        return Listing(
            board=self.name, id=str(item["id"]), url=item.get("url", ""),
            title=item.get("title", ""), company=item.get("company_name", ""),
            location=item.get("candidate_required_location", "Remote"),
            employment_type=item.get("job_type", "").replace("_", " "),
            description=desc[:8000], pay_low=lo, pay_high=hi,
            pay_raw=raw or item.get("salary", ""), pay_note=note,
            posted=item.get("publication_date", ""))


@register
class Jobicy(FeedBoard):
    name = "jobicy"
    help = "Free-text terms, e.g. \"admin\". Returns remote jobs worldwide."

    def url(self, query): return "https://jobicy.com/api/v2/remote-jobs?count=50"

    def parse(self, raw): return json.loads(raw).get("jobs", [])

    def to_listing(self, item, rates):
        desc = strip_html(item.get("jobDescription")
                          or item.get("jobExcerpt", ""))
        lo, hi, raw, note = _pay(item, rates, lo_key="salaryMin",
                                 hi_key="salaryMax", cur_key="salaryCurrency",
                                 period_key="salaryPeriod", text=desc[:2000])
        types = item.get("jobType")
        return Listing(
            board=self.name, id=str(item["id"]), url=item.get("url", ""),
            title=item.get("jobTitle", ""), company=item.get("companyName", ""),
            location=item.get("jobGeo", "Remote"),
            employment_type=", ".join(types) if isinstance(types, list)
                            else str(types or ""),
            description=desc[:8000], pay_low=lo, pay_high=hi,
            pay_raw=raw, pay_note=note, posted=item.get("pubDate", ""))


@register
class Arbeitnow(FeedBoard):
    name = "arbeitnow"
    help = ("Free-text terms. A lesser-known board, Europe-weighted, with a "
            "genuinely open API and a lot of remote listings.")

    def url(self, query): return "https://www.arbeitnow.com/api/job-board-api"

    def parse(self, raw): return json.loads(raw).get("data", [])

    def to_listing(self, item, rates):
        desc = strip_html(item.get("description", ""))
        lo, hi, raw, note = parse_pay(desc[:2500], rates)
        types = item.get("job_types") or []
        return Listing(
            board=self.name, id=str(item["slug"]), url=item.get("url", ""),
            title=item.get("title", ""), company=item.get("company_name", ""),
            location=item.get("location", ""),
            employment_type=("Remote, " if item.get("remote") else "")
                            + ", ".join(types),
            description=desc[:8000], pay_low=lo, pay_high=hi,
            pay_raw=raw, pay_note=note, posted=str(item.get("created_at", "")))


@register
class Himalayas(FeedBoard):
    name = "himalayas"
    help = "Free-text terms. Remote-only board with an open API."

    def url(self, query): return "https://himalayas.app/jobs/api?limit=100"

    def parse(self, raw):
        d = json.loads(raw)
        return d.get("jobs") or d.get("data") or []

    def to_listing(self, item, rates):
        desc = strip_html(item.get("description") or item.get("excerpt", ""))
        lo, hi, raw, note = _pay(item, rates, lo_key="minSalary",
                                 hi_key="maxSalary", cur_key="currency",
                                 period_key="salaryPeriod", text=desc[:2000])
        loc = item.get("locationRestrictions") or ["Remote"]
        return Listing(
            board=self.name, id=str(item.get("guid") or item.get("title")),
            url=item.get("applicationLink", ""), title=item.get("title", ""),
            company=item.get("companyName", ""),
            location=", ".join(loc) if isinstance(loc, list) else str(loc),
            employment_type=str(item.get("employmentType", "")),
            description=desc[:8000], pay_low=lo, pay_high=hi,
            pay_raw=raw, pay_note=note, posted=str(item.get("pubDate", "")))


@register
class WeWorkRemotely(FeedBoard):
    name = "weworkremotely"
    help = ("An RSS category URL, e.g.\n"
            "  https://weworkremotely.com/categories/"
            "remote-customer-support-jobs.rss\n"
            "Browse the site, pick a category, use its .rss URL.")
    attribution = "Jobs from [We Work Remotely](https://weworkremotely.com)"

    def url(self, query):
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

    def to_listing(self, item, rates):
        desc = strip_html(item.get("description", ""))
        title = item.get("title", "")
        company, _, role = title.partition(":")
        lo, hi, raw, note = parse_pay(desc[:2500], rates)
        return Listing(
            board=self.name, id=item.get("guid") or item.get("link", ""),
            url=item.get("link", ""), title=(role or title).strip(),
            company=company.strip() if role else "",
            location=item.get("region", "Remote"), employment_type="Remote",
            description=desc[:8000], pay_low=lo, pay_high=hi,
            pay_raw=raw, pay_note=note, posted=item.get("pubDate", ""))
