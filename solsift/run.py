"""The search -> screen -> store cycle, across every source in a profile.

One design point worth stating: **a browser is only started if some board
actually needs one.** Most boards publish a feed, and a profile made only of
feed boards runs in a couple of seconds with no browser at all. Paying browser
startup for a JSON fetch would be silly, and it would make the tool unusable
anywhere a browser cannot be installed.

A board that fails does not take the run with it. If LinkedIn rate-limits you,
the other six still report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import boards
from .listing import Listing
from .money import Rates
from .profile import Profile
from .rules import Verdict, screen

BROWSER_CANDIDATES = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/brave-browser", "/usr/bin/google-chrome", "/usr/bin/chromium",
]

# Playwright's default advertises HeadlessChrome, which bot checks fingerprint
# on sight. A real UA is the difference between working and a silent block.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def find_browser() -> str | None:
    for c in BROWSER_CANDIDATES:
        if Path(c).exists():
            return c
    return None


@dataclass
class RunResult:
    verdicts: list[Verdict]
    seen_total: int
    new_count: int
    rates: Rates
    #: board name -> what went wrong. Surfaced, never swallowed.
    failures: dict[str, str] = field(default_factory=dict)
    #: Credit required by some boards' terms of use.
    attributions: list[str] = field(default_factory=list)

    @property
    def kept(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.kept]

    @property
    def killed(self) -> list[Verdict]:
        return [v for v in self.verdicts if not v.kept]


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return default
    return default


def run(profile: Profile, *, headed: bool = False, limit: int = 0,
        rescan: bool = False, progress=None) -> RunResult:
    """Search every source, screen the results, store them."""
    boards.load_all()
    adapters = [(src, boards.get(src.board)) for src in profile.sources]
    needs_browser = any(b.needs_browser for _, b in adapters)

    rates = Rates(profile.currency)
    # Derived from the user's own numbers, never a hardcoded per-currency table.
    pay_kw = dict(hourly_ceiling=profile.hourly_ceiling,
                  hours_per_month=profile.hours_per_month)
    seen = set() if rescan else set(_load_json(profile.seen_path, []))
    store = _load_json(profile.listings_path, {})
    skip = frozenset() if rescan else frozenset(seen)

    fetched: list[Listing] = []
    failures: dict[str, str] = {}
    attributions: list[str] = []
    browser = ctx = page = pw = None

    if needs_browser:
        from playwright.sync_api import sync_playwright
        path = find_browser()
        if not path:
            raise RuntimeError(
                "A board in your profile needs a browser, and none was found.\n"
                "Install Brave, Chrome or Edge, or run:\n"
                "    playwright install chromium\n"
                "Alternatively drop the browser-based boards - most sources "
                "use a public feed and need nothing installed.")
        pw = sync_playwright().start()
        browser = pw.chromium.launch(executable_path=path, headless=not headed)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900},
                                  user_agent=USER_AGENT)
        page = ctx.new_page()

    try:
        for src, board in adapters:
            if board.attribution and board.attribution not in attributions:
                attributions.append(board.attribution)
            for query in src.queries:
                got = 0
                try:
                    for listing in board.search(query, rates, page=page,
                                                skip=skip, **pay_kw):
                        key = f"{listing.board}:{listing.id}"
                        if key in seen and not rescan:
                            continue
                        seen.add(key)
                        store[key] = listing.to_dict()
                        fetched.append(listing)
                        got += 1
                        if limit and len(fetched) >= limit:
                            break
                except (RuntimeError, Exception) as e:   # one board, not the run
                    failures[board.name] = f"{type(e).__name__}: {e}"
                    if progress:
                        progress("board_failed", board=board.name, error=str(e))
                    continue
                if progress:
                    progress("query", board=board.name, query=query, found=got)
                if limit and len(fetched) >= limit:
                    break
            if limit and len(fetched) >= limit:
                break
    finally:
        if browser:
            browser.close()
        if pw:
            pw.stop()

    profile.seen_path.write_text(json.dumps(sorted(seen)), encoding="utf-8")
    profile.listings_path.write_text(json.dumps(store, indent=1),
                                     encoding="utf-8")

    verdicts = [screen(l, profile) for l in fetched]
    return RunResult(verdicts, len(store), len(fetched), rates,
                     failures, attributions)


def rescreen(profile: Profile) -> RunResult:
    """Re-apply rules to everything already stored. No network, no board hit.

    Tuning a rule has to be free. If checking whether a change was right costs a
    full scrape, nobody checks, and the rules quietly rot.
    """
    boards.load_all()
    rates = Rates(profile.currency)
    store = _load_json(profile.listings_path, {})
    listings = [Listing.from_dict(d) for d in store.values()]
    attributions = []
    for src in profile.sources:
        try:
            a = boards.get(src.board).attribution
        except KeyError:
            continue
        if a and a not in attributions:
            attributions.append(a)
    return RunResult([screen(l, profile) for l in listings], len(listings), 0,
                     rates, {}, attributions)
