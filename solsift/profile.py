"""Your profile: everything about a job search that is personal to one person.

Nothing in this project hardcodes a rate, a country, a job title or a floor.
That is not tidiness - it is the difference between a script one person can use
and a tool two people can. A profile is a TOML file, and profiles other than the
bundled example are gitignored, so a shared repo never carries anyone's pay
expectations.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ProfileError(Exception):
    """A profile problem stated in terms the user can fix."""


@dataclass
class Source:
    """One board plus the queries to run against it."""
    board: str
    queries: list[str] = field(default_factory=list)


@dataclass
class Profile:
    name: str
    sources: list[Source] = field(default_factory=list)

    currency: str = "USD"
    floor_per_hour: float | None = None
    target_per_hour: float | None = None
    hours_per_month: int = 160
    #: Above this, per hour, a figure is not an hourly rate. None = derive it.
    hourly_ceiling_override: float | None = None

    employment_types: list[str] = field(default_factory=list)
    remote_only: bool = False

    credentials: list[str] = field(default_factory=list)
    credentials_required_kill: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    title_keywords: list[str] = field(default_factory=list)
    disabled_rules: list[str] = field(default_factory=list)

    board_options: dict = field(default_factory=dict)
    notify_webhook: str = ""
    path: Path | None = None

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        path = Path(path)
        if not path.exists():
            raise ProfileError(
                f"No profile at {path}\n"
                f"Create one with:  solsift init --name <you>")
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ProfileError(f"{path} is not valid TOML: {e}") from None

        pay = raw.get("pay", {})
        filters = raw.get("filters", {})

        # Preferred shape: repeated [[source]] blocks, one per board.
        sources = [Source(board=s.get("board", ""), queries=s.get("queries", []))
                   for s in raw.get("source", [])]
        # Back-compat: a single [search] block from before multi-board support.
        legacy = raw.get("search")
        if legacy and not sources:
            sources = [Source(board=legacy.get("board", "jobstreet"),
                              queries=legacy.get("queries", []))]

        p = cls(
            name=raw.get("name") or path.stem,
            sources=sources,
            currency=(pay.get("currency") or "USD").upper(),
            floor_per_hour=pay.get("floor_per_hour"),
            target_per_hour=pay.get("target_per_hour"),
            hours_per_month=pay.get("hours_per_month", 160),
            hourly_ceiling_override=pay.get("hourly_ceiling"),
            employment_types=filters.get("employment_types", []),
            remote_only=filters.get("remote_only", False),
            credentials=filters.get("credentials_held", []),
            credentials_required_kill=filters.get("credentials_to_screen", []),
            exclude_keywords=filters.get("exclude_keywords", []),
            title_keywords=filters.get("title_keywords", []),
            disabled_rules=filters.get("disable_rules", []),
            notify_webhook=raw.get("notify", {}).get("webhook", ""),
            path=path,
        )
        p.validate()
        return p

    def validate(self) -> None:
        from .rules import RULES_BY_KEY          # local: avoids a cycle

        if not self.sources or not any(s.queries for s in self.sources):
            raise ProfileError(
                f"{self.path}: no sources with queries, so there is nothing to "
                f"search.\nAdd at least one:\n\n"
                f'  [[source]]\n  board = "remoteok"\n'
                f'  queries = ["virtual assistant"]\n\n'
                f"Run `solsift boards` to see what each board expects.")

        from .boards import available, load_all
        load_all()
        known = {b.name for b in available()}
        for src in self.sources:
            if src.board not in known:
                raise ProfileError(
                    f"{self.path}: unknown board {src.board!r}.\n"
                    f"Available: {', '.join(sorted(known))}")
        if self.floor_per_hour is not None and self.floor_per_hour < 0:
            raise ProfileError(f"{self.path}: floor_per_hour cannot be negative.")
        if (self.floor_per_hour and self.target_per_hour
                and self.target_per_hour < self.floor_per_hour):
            raise ProfileError(
                f"{self.path}: target_per_hour ({self.target_per_hour}) is below "
                f"floor_per_hour ({self.floor_per_hour}). Your target should be "
                f"what you ask for; your floor is what you refuse to go under.")
        unknown = set(self.disabled_rules) - set(RULES_BY_KEY)
        if unknown:
            raise ProfileError(
                f"{self.path}: disable_rules names rules that do not exist: "
                f"{', '.join(sorted(unknown))}\n"
                f"Run `solsift rules` to see the real names.")

    @property
    def hourly_ceiling(self) -> float:
        """Above this many base-currency units per hour, a figure is a salary.

        Derived from the user's own target and floor rather than a hardcoded
        per-currency table. A table only knows the currencies someone thought to
        list, and it silently misprices every other market; this scales with
        whatever the user says their own market pays, in any currency the rate
        service knows.
        """
        if self.hourly_ceiling_override:
            return float(self.hourly_ceiling_override)
        anchors = [v for v in (self.target_per_hour, self.floor_per_hour) if v]
        if not anchors:
            return 150.0
        # 10x the best anchor: generous enough that a genuinely high hourly rate
        # is still read as hourly, tight enough to catch a monthly salary.
        return max(max(anchors) * 10.0, 50.0)

    # ------------------------------------------------------------------ paths
    @property
    def state_dir(self) -> Path:
        d = Path.home() / ".local" / "state" / "solsift" / self.name
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def seen_path(self) -> Path:
        return self.state_dir / "seen.json"

    @property
    def listings_path(self) -> Path:
        return self.state_dir / "listings.json"


TEMPLATE = '''# solsift profile - "{name}"
#
# Everything personal about your job search lives here and nowhere else.
# This file is gitignored. Never commit it, and never put a real webhook
# secret in a profile you share.

name = "{name}"

# --- Where to look ----------------------------------------------------------
# One [[source]] block per board. Add as many as you like; solsift merges the
# results and de-duplicates. Run `solsift boards` to see every installed board
# and what it expects as a query.
#
# Most boards here are public APIs and need no browser, so they are fast and
# they do not break when a site is restyled. Only jobstreet drives a browser.

[[source]]
board = "remoteok"
queries = ["virtual assistant", "executive assistant"]

[[source]]
board = "remotive"
queries = ["assistant", "bookkeeping"]

[[source]]
board = "linkedin"
# "terms | location". Reads the logged-out guest endpoint - no account needed,
# so there is nothing that can be suspended.
queries = ["virtual assistant | Philippines"]

[[source]]
board = "weworkremotely"
queries = [
  "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
]

# Lesser-known boards, worth having precisely because fewer people watch them.
[[source]]
board = "arbeitnow"
queries = ["assistant"]

[[source]]
board = "jobicy"
queries = ["admin"]

[[source]]
board = "himalayas"
queries = ["assistant"]

# Needs a browser. Paste full search URLs - build the search on the site first,
# because the board's own filters are cheaper than ours.
# [[source]]
# board = "jobstreet"
# queries = [
#   "https://ph.jobstreet.com/virtual-assistant-jobs/part-time?sortmode=ListedDate",
# ]

# --- What it is worth -------------------------------------------------------
[pay]
# Everything is reported in this currency, converted at live rates.
currency = "USD"

# The rate you will not go under. Listings below it are removed, with a reason.
# Leave unset to keep everything. Listings with NO stated pay are always kept -
# unknown is not the same as low.
floor_per_hour = 4.0

# What you actually ask for. Reporting only; it never filters anything.
target_per_hour = 6.0

# --- What to rule out -------------------------------------------------------
[filters]
# Substring match against the board's employment-type field. Empty = accept any.
# Feed-based boards often leave this blank, and a blank field never removes a
# listing.
employment_types = []

# Remove on-site roles. Exceptions are handled: "occasional on-site visits" in
# an otherwise remote role will not trigger this.
remote_only = true

# Credentials you actually hold. Anything listed here can never remove a job.
credentials_held = []

# Credentials to screen for. A listing is removed only when it states one as
# REQUIRED and you have not listed it above. "CPA preferred" survives.
credentials_to_screen = ["RN", "PRC"]

# Relevance filter. At least one of these must appear in the job TITLE.
# Without it, a search for "assistant" returns any posting that happens to say
# the word once - a Golang role, a sales role. Leave empty to switch it off.
title_keywords = ["assistant", "admin", "bookkeep", "data entry", "operations"]

# Your own catch-all. Case-insensitive substring match on title + description.
exclude_keywords = []

# Turn off any built-in rule by name. `solsift rules` lists them.
disable_rules = []

[notify]
# Optional. Survivors are POSTed here as JSON after each run.
# webhook = "http://localhost:5678/webhook/solsift"
'''


def write_template(path: Path, name: str) -> Path:
    if path.exists():
        raise ProfileError(f"{path} already exists. Delete it or pick another name.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.format(name=name), encoding="utf-8")
    return path
