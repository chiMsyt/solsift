"""Pay parsing and currency conversion.

Two failure modes drive this whole module, and both are silent.

**A stale exchange rate reprices every decision you make.** In the run this tool
grew out of, the rate had moved 11% since the notes were written, which quietly
repriced two shortlisted roles downward and flipped a conclusion. Nothing errored.
So: rates are fetched live, cached for a day, and a cache hit is *reported as
stale* rather than used quietly.

**Guessing whether a number is hourly or monthly can delete a good job.** A
posting that says "5,000" might be a monthly salary or an hourly rate depending
on the currency and the market, and there is no table of magic numbers that gets
this right everywhere - the first version of this file had one, and a PHP 5,000
monthly salary sat exactly on its boundary and was read as PHP 5,000 *per hour*.

The fix is not a better table. It is admitting when we do not know:

1. **Wording wins.** "per hour", "monthly", "p.a." - if the posting says it, use
   it, and mark the result certain.
2. **Board metadata wins next.** Several feeds hand us a period field.
3. **Magnitude is a last resort, and it is never treated as certain.** The
   threshold is derived from the user's own numbers at live FX, not hardcoded per
   currency, so it works for any currency the rate service knows.

When the cadence came from magnitude alone, `Pay.certain` is False and
`Pay.ceiling` records the most generous plausible reading. The floor rule removes
a listing only when it is under the floor **under every plausible reading**, so an
ambiguous number gets kept and flagged rather than silently dropped.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Free, no API key, no account. Chosen deliberately over a keyed service so the
# tool works on a fresh clone with nothing configured.
FX_URL = "https://open.er-api.com/v6/latest/{base}"
CACHE_TTL = 60 * 60 * 24                      # one day

#: Conventional full-time month. Override per profile if your market differs.
DEFAULT_HOURS_PER_MONTH = 160

#: Above this many base-currency units per hour, a figure is not an hourly rate.
#: A default, not a law - profiles override it, and `Profile.hourly_ceiling`
#: derives a better one from the user's own target and floor.
DEFAULT_HOURLY_CEILING = 150.0

#: Below this many base-currency units per month, a figure is not a monthly wage.
DEFAULT_MONTHLY_FLOOR = 120.0

_SYMBOL = {"$": "USD", "₱": "PHP", "€": "EUR", "£": "GBP",
           "₹": "INR", "¥": "JPY", "₦": "NGN", "R$": "BRL"}


class Rates:
    """Live rates with a visible-staleness cache."""

    def __init__(self, base: str = "USD", cache_dir: Path | None = None):
        self.base = base.upper()
        self.cache = (cache_dir or Path.home() / ".cache" / "solsift")
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
                pass                       # a corrupt cache is not worth a crash
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

    def knows(self, currency: str) -> bool:
        c = currency.upper()
        return c == self.base or c in self._rates

    def to_base(self, amount: float, currency: str) -> float:
        currency = currency.upper()
        if currency == self.base:
            return amount
        rate = self._rates.get(currency)
        if not rate:
            raise KeyError(f"No rate for {currency} against {self.base}")
        return amount / rate


@dataclass
class Pay:
    """A parsed pay figure, in base currency per hour.

    `certain` is the important field. False means the hourly/monthly reading came
    from magnitude alone, so `low`/`high` are a best guess and `ceiling` holds the
    most generous plausible reading. Callers must not remove a listing on an
    uncertain figure unless it fails even at `ceiling`.
    """

    low: float | None = None
    high: float | None = None
    ceiling: float | None = None
    raw: str = ""
    note: str = ""
    certain: bool = True

    @property
    def stated(self) -> bool:
        return self.low is not None

    @classmethod
    def unknown(cls, raw: str = "", note: str = "") -> "Pay":
        return cls(None, None, None, raw, note, True)


# --- Parsing ----------------------------------------------------------------

_NUM = r"[\d,]+(?:\.\d+)?"
_CUR = (r"(?:USD|PHP|EUR|GBP|AUD|CAD|SGD|INR|IDR|MYR|NZD|ZAR|NGN|BRL|MXN|"
        r"JPY|CNY|THB|VND|PLN|Php|\$|₱|€|£|₹|¥)")
_RANGE = re.compile(
    rf"({_CUR})\s*({_NUM})\s*(?:-|to|–|—|until)\s*(?:{_CUR})?\s*({_NUM})", re.I)
_SINGLE = re.compile(rf"({_CUR})\s*({_NUM})", re.I)

_PER_HOUR = re.compile(r"per hour|/\s*h(?:ou)?r|hourly|an hour|/hr|each hour",
                       re.I)
_PER_DAY = re.compile(r"per day|/\s*day|daily|a day", re.I)
_PER_WEEK = re.compile(r"per week|/\s*w(?:ee)?k|weekly|a week", re.I)
_PER_MONTH = re.compile(r"per month|/\s*month|monthly|a month|/mo\b", re.I)
_PER_YEAR = re.compile(r"per (?:year|annum)|/\s*(?:year|yr)|annually|"
                       r"p\.a\.|a year", re.I)

#: How many working hours each cadence covers. Used to normalise to per-hour.
_CADENCE_HOURS = {"hourly": 1, "daily": 8, "weekly": 40,
                  "monthly": None, "yearly": None}       # None = from profile


def _currency_of(token: str) -> str:
    return _SYMBOL.get(token.strip(), token.strip().upper())


def _stated_cadence(text: str) -> str | None:
    """Cadence the posting actually says. None if it says nothing."""
    for pattern, name in ((_PER_HOUR, "hourly"), (_PER_DAY, "daily"),
                          (_PER_WEEK, "weekly"), (_PER_MONTH, "monthly"),
                          (_PER_YEAR, "yearly")):
        if pattern.search(text):
            return name
    return None


def _hours_for(cadence: str, hours_per_month: int) -> float:
    if cadence == "monthly":
        return hours_per_month
    if cadence == "yearly":
        return hours_per_month * 12
    return _CADENCE_HOURS[cadence]


def normalise(amount: float, cadence: str, hours_per_month: int) -> float:
    return amount / _hours_for(cadence, hours_per_month)


def parse_pay(text: str, rates: Rates, *, cadence: str | None = None,
              hourly_ceiling: float = DEFAULT_HOURLY_CEILING,
              monthly_floor: float = DEFAULT_MONTHLY_FLOOR,
              hours_per_month: int = DEFAULT_HOURS_PER_MONTH) -> Pay:
    """Parse pay out of `text` into base currency per hour.

    `cadence` overrides detection - pass it when a board's feed states the period,
    which is more reliable than anything we can infer from prose.
    """
    if not text:
        return Pay.unknown()

    m = _RANGE.search(text)
    if m:
        cur = _currency_of(m.group(1))
        lo, hi = (float(g.replace(",", "")) for g in (m.group(2), m.group(3)))
        raw = m.group(0)
    else:
        m = _SINGLE.search(text)
        if not m:
            return Pay.unknown()
        cur = _currency_of(m.group(1))
        lo = hi = float(m.group(2).replace(",", ""))
        raw = m.group(0)

    if hi < lo:                                       # "$9 - $7" happens
        lo, hi = hi, lo
    if lo <= 0:
        return Pay.unknown(raw)
    if not rates.knows(cur):
        return Pay.unknown(raw, f"unknown currency {cur}")

    lo_base, hi_base = rates.to_base(lo, cur), rates.to_base(hi, cur)

    # 1. Caller-supplied (board metadata) beats everything.
    if cadence in ("hourly", "daily", "weekly", "monthly", "yearly"):
        certain, source = True, f"{cur} {cadence} (from board)"
    else:
        # 2. What the posting says, read near the figure so a stray "per year"
        #    elsewhere in a long advert cannot capture it.
        window = text[max(0, m.start() - 60): m.end() + 60]
        stated = _stated_cadence(window) or _stated_cadence(text)
        if stated:
            cadence, certain, source = stated, True, f"{cur} {stated}"
        else:
            # 3. Magnitude. A guess, and recorded as one.
            #
            # Try each cadence smallest-first and take the first that produces
            # a believable hourly rate AND a believable pay packet. Three tiers,
            # not two: a bare "30000" is an annual salary in most currencies,
            # and reading it as monthly overstates the rate by twelve times.
            cadence = None
            for candidate in ("hourly", "monthly", "yearly"):
                hours = _hours_for(candidate, hours_per_month)
                per_hour = lo_base / hours
                # A packet that small is not a real wage for that period.
                packet_ok = candidate == "hourly" or lo_base >= monthly_floor * (
                    12 if candidate == "yearly" else 1)
                if per_hour <= hourly_ceiling and packet_ok:
                    cadence = candidate
                    break
            if cadence is None:
                # Believable as nothing - the figure is odd whichever way you
                # read it. Pick the least-wrong and lean hard on `certain`.
                cadence = "monthly" if lo_base > hourly_ceiling else "hourly"
            certain = False
            source = f"{cur}, period not stated - assumed {cadence}"

    hours = _hours_for(cadence, hours_per_month)
    low, high = lo_base / hours, hi_base / hours

    # The most generous plausible reading. For an uncertain figure that is the
    # hourly interpretation, which is always the largest. The floor rule uses
    # this so an ambiguous number is never removed on a guess.
    ceiling = high if certain else max(high, hi_base)

    note = source if cur != rates.base or cadence != "hourly" else ""
    return Pay(round(low, 2), round(high, 2), round(ceiling, 2), raw, note,
               certain)
