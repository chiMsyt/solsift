"""Pay parsing and currency conversion.

The reason this is its own module: **a stale exchange rate silently reprices
every decision you make.** In the run this tool grew out of, the rate had moved
11% since the notes were written, which quietly repriced two shortlisted roles
downward and flipped the conclusion on whether a foreign-currency role beat the
local band. Nothing errored. The numbers were just wrong.

So: rates are fetched live, cached for a day, and the cache records when it was
taken. If the network is down you get the cached rate and a visible warning, not
a silent guess.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

# Free, no API key, no account. Chosen deliberately over a keyed service so the
# tool works on a clone with nothing configured.
FX_URL = "https://open.er-api.com/v6/latest/{base}"
CACHE_TTL = 60 * 60 * 24          # one day
HOURS_PER_MONTH = 160             # 40h x 4 weeks, the conventional divisor

# Below this, a figure is an hourly rate; at or above it, a monthly salary.
# This is per-currency because "5000" means opposite things in USD and PHP.
# The boundary caused a real bug: a PHP 5,000-6,000 *monthly* posting sat
# exactly on a `> 5000` test and was read as PHP 5,000 per HOUR, which ranked an
# unpaid intern role as the best-paid job on the board.
MONTHLY_THRESHOLD = {"USD": 200, "EUR": 200, "GBP": 200, "AUD": 250,
                     "CAD": 250, "PHP": 2000, "INR": 2000, "IDR": 100000}
DEFAULT_MONTHLY_THRESHOLD = 500

_SYMBOL = {"$": "USD", "₱": "PHP", "€": "EUR", "£": "GBP",
           "₹": "INR", "¥": "JPY"}


class Rates:
    """Live rates with a visible-staleness cache."""

    def __init__(self, base: str = "USD", cache_dir: Path | None = None):
        self.base = base.upper()
        self.cache = (cache_dir or Path.home() / ".cache" / "jobsift")
        self.cache.mkdir(parents=True, exist_ok=True)
        self.path = self.cache / f"rates-{self.base}.json"
        self.stale = False
        self.fetched_at: float | None = None
        self._rates = self._load()

    def _load(self) -> dict[str, float]:
        if self.path.exists():
            try:
                blob = json.loads(self.path.read_text(encoding="utf-8"))
                if time.time() - blob["fetched_at"] < CACHE_TTL:
                    self.fetched_at = blob["fetched_at"]
                    return blob["rates"]
            except (ValueError, KeyError):
                pass                       # corrupt cache is not worth a crash
        try:
            with urllib.request.urlopen(
                    FX_URL.format(base=self.base), timeout=15) as r:
                data = json.loads(r.read())
            if data.get("result") != "success":
                raise ValueError(data.get("error-type", "unknown error"))
            rates = {k: float(v) for k, v in data["rates"].items()}
            self.fetched_at = time.time()
            self.path.write_text(json.dumps(
                {"fetched_at": self.fetched_at, "rates": rates}), encoding="utf-8")
            return rates
        except (urllib.error.URLError, ValueError, KeyError, TimeoutError):
            if self.path.exists():
                blob = json.loads(self.path.read_text(encoding="utf-8"))
                self.stale = True
                self.fetched_at = blob["fetched_at"]
                return blob["rates"]
            raise RuntimeError(
                f"Cannot reach the exchange-rate service and no cached rate for "
                f"{self.base} exists. Refusing to guess: a wrong rate silently "
                f"reprices every listing.") from None

    @property
    def age_hours(self) -> float:
        return (time.time() - self.fetched_at) / 3600 if self.fetched_at else 0.0

    def to_base(self, amount: float, currency: str) -> float:
        """Convert `amount` of `currency` into the base currency."""
        currency = currency.upper()
        if currency == self.base:
            return amount
        rate = self._rates.get(currency)
        if not rate:
            raise KeyError(f"No rate for {currency} against {self.base}")
        return amount / rate


# --- Parsing ----------------------------------------------------------------

_NUM = r"[\d,]+(?:\.\d+)?"
_CUR = r"(?:USD|PHP|EUR|GBP|AUD|CAD|SGD|INR|IDR|MYR|Php|\$|₱|€|£|₹)"
_RANGE = re.compile(
    rf"({_CUR})\s*({_NUM})\s*(?:-|to|–|—)\s*(?:{_CUR})?\s*({_NUM})", re.I)
_SINGLE = re.compile(rf"({_CUR})\s*({_NUM})", re.I)
_PER_HOUR = re.compile(r"per hour|/\s*h(?:ou)?r|hourly|an hour|/hr", re.I)
_PER_MONTH = re.compile(r"per month|/\s*month|monthly|a month|/mo\b", re.I)
_PER_YEAR = re.compile(r"per (?:year|annum)|/\s*year|annually|p\.a\.", re.I)


def _currency_of(token: str) -> str:
    token = token.strip()
    return _SYMBOL.get(token, token.upper())


def _cadence(text: str, amount: float, currency: str) -> str:
    """hourly / monthly / yearly, from wording first and magnitude second."""
    if _PER_HOUR.search(text):
        return "hourly"
    if _PER_MONTH.search(text):
        return "monthly"
    if _PER_YEAR.search(text):
        return "yearly"
    threshold = MONTHLY_THRESHOLD.get(currency, DEFAULT_MONTHLY_THRESHOLD)
    return "monthly" if amount >= threshold else "hourly"


def parse_pay(text: str, rates: Rates) -> tuple[float | None, float | None, str, str]:
    """Return (low_per_hour, high_per_hour, raw_match, note) in the base currency.

    Returns (None, None, "", "") when no pay is stated. **Unknown pay is not
    low pay** - every caller must treat it as unknown, never as zero.
    """
    if not text:
        return None, None, "", ""

    m = _RANGE.search(text)
    if m:
        cur = _currency_of(m.group(1))
        lo, hi = (float(g.replace(",", "")) for g in (m.group(2), m.group(3)))
        raw = m.group(0)
    else:
        m = _SINGLE.search(text)
        if not m:
            return None, None, "", ""
        cur = _currency_of(m.group(1))
        lo = hi = float(m.group(2).replace(",", ""))
        raw = m.group(0)

    if hi < lo:                                    # "$9 - $7" happens
        lo, hi = hi, lo

    window = text[max(0, m.start() - 40): m.end() + 40]
    cadence = _cadence(window, lo, cur)

    if cadence == "monthly":
        lo, hi = lo / HOURS_PER_MONTH, hi / HOURS_PER_MONTH
    elif cadence == "yearly":
        lo, hi = lo / (HOURS_PER_MONTH * 12), hi / (HOURS_PER_MONTH * 12)

    try:
        lo, hi = rates.to_base(lo, cur), rates.to_base(hi, cur)
    except KeyError:
        return None, None, raw, f"unknown currency {cur}"

    note = f"{cur} {cadence}"
    if cur != rates.base:
        note += f" -> {rates.base}/hr"
    elif cadence != "hourly":
        note += " -> /hr"
    return round(lo, 2), round(hi, 2), raw, note
