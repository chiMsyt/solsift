"""The one shape every board adapter must produce.

Boards disagree about everything - field names, pay formats, what "part time"
means, whether a location is a city or a country. An adapter's whole job is to
turn one board's mess into this, so that the rules, the reports and the profile
never learn that JobStreet exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date


@dataclass
class Listing:
    """A single job posting, normalised.

    `pay_low` / `pay_high` are **per hour in the profile's currency**, already
    converted. Adapters do the parsing; `money.py` does the conversion. A
    listing with no stated pay leaves both None, which is not the same as zero
    and must never be treated as low.
    """

    board: str
    id: str
    url: str
    title: str
    company: str = ""
    location: str = ""
    employment_type: str = ""
    description: str = ""

    pay_low: float | None = None
    pay_high: float | None = None
    #: Most generous plausible reading, in base currency per hour. Equals
    #: `pay_high` when the cadence is known. When it is a guess this is the
    #: hourly interpretation, and the floor rule tests against it - so an
    #: ambiguous figure is never removed on the strength of that guess.
    pay_ceiling: float | None = None
    #: False when hourly-vs-monthly came from magnitude alone.
    pay_certain: bool = True
    pay_raw: str = ""              # what the board actually said, for auditing
    pay_note: str = ""             # e.g. "PHP, period not stated - assumed monthly"

    posted: str = ""               # board's own wording: "3 hours ago"
    first_seen: str = field(default_factory=lambda: date.today().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Listing":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def pay_display(self) -> str:
        if self.pay_low is None:
            return "not stated"
        body = (f"{self.pay_low:.2f}-{self.pay_high:.2f}"
                if self.pay_high and self.pay_high != self.pay_low
                else f"{self.pay_low:.2f}")
        return body if self.pay_certain else body + " ?"

    def apply_pay(self, pay) -> "Listing":
        """Copy a parsed `money.Pay` onto this listing."""
        self.pay_low, self.pay_high = pay.low, pay.high
        self.pay_ceiling, self.pay_certain = pay.ceiling, pay.certain
        self.pay_raw, self.pay_note = pay.raw or self.pay_raw, pay.note
        return self
