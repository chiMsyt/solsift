"""jobsift command line.

UX principles, since this is the whole surface most people see:

- **Every failure says what to do next.** A traceback is a bug report, not an
  error message.
- **Nothing is silent.** A stale exchange rate, a skipped listing and a rule
  that killed half the board all say so.
- **The first run works with no configuration.** `jobsift init` writes a profile
  with comments explaining every field, then tells you the next command.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import boards
from .profile import Profile, ProfileError, write_template
from .report import markdown, write as write_report
from .rules import ALL_RULES
from .run import find_browser, rescreen, run

# Windows' legacy console is cp1252, and any non-ASCII character in output
# raises UnicodeEncodeError mid-render - the run dies AFTER the work is done,
# which is the worst possible time. Job titles routinely contain en-dashes and
# accented characters, so this is not avoidable by being careful in our own
# strings. Force UTF-8 and degrade unmappable characters instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):        # not a real tty, already set
        pass

console = Console()
err = Console(stderr=True)


def _profile_path(args) -> Path:
    if args.profile:
        return Path(args.profile)
    local = Path("profiles")
    if local.is_dir():
        found = sorted(p for p in local.glob("*.toml") if p.stem != "example")
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise ProfileError(
                "More than one profile in ./profiles - say which:\n  "
                + "\n  ".join(f"jobsift {sys.argv[1]} --profile {p}" for p in found))
    return Path.home() / ".config" / "jobsift" / "profile.toml"


def _fail(msg: str, code: int = 1):
    err.print(Panel(Text(str(msg), style="white"), title="[bold red]jobsift",
                    border_style="red", title_align="left"))
    sys.exit(code)


# ------------------------------------------------------------------ commands

def cmd_init(args):
    path = Path(args.profile) if args.profile else Path("profiles") / f"{args.name}.toml"
    written = write_template(path, args.name)
    console.print(Panel(
        f"Wrote [bold cyan]{written}[/]\n\n"
        f"Open it and set at least:\n"
        f"  • [bold]queries[/]         which searches to run\n"
        f"  • [bold]floor_per_hour[/]  the rate you will not go under\n"
        f"  • [bold]currency[/]        what to report pay in\n\n"
        f"Then:  [bold green]jobsift run[/]",
        title="[bold green]Profile created", border_style="green",
        title_align="left"))


def cmd_rules(args):
    table = Table(title="Disqualifying rules", title_justify="left",
                  header_style="bold", show_lines=True, expand=True)
    table.add_column("key", style="cyan", no_wrap=True)
    table.add_column("kills", style="bold")
    table.add_column("why")
    for r in ALL_RULES:
        table.add_row(r.key, r.kills, r.why)
    console.print(table)
    console.print("\nDisable any of these per profile:  "
                  "[cyan]disable_rules = [\"disguised_sales\"][/]\n")


def cmd_boards(args):
    boards.load_all()
    for b in boards.available():
        tag = "[yellow]needs a browser[/]" if b.needs_browser else "[green]feed[/]"
        console.print(Panel(b.help, title=f"[bold cyan]{b.name}[/]  {tag}",
                            border_style="cyan", title_align="left"))
    console.print("\n[dim]Feed boards use a public API or RSS - fast, and "
                  "nothing to install.[/]\n")


def cmd_doctor(args):
    table = Table(show_header=False, box=None, padding=(0, 2))
    ok, bad = "[green]OK[/]", "[red]MISSING[/]"

    browser = find_browser()
    table.add_row(ok if browser else bad, "browser",
                  browser or "install Brave/Chrome/Edge, or "
                             "`playwright install chromium`")
    try:
        import playwright  # noqa: F401
        table.add_row(ok, "playwright", "installed")
    except ImportError:
        table.add_row(bad, "playwright", "pip install playwright")

    try:
        from .money import Rates
        r = Rates("USD")
        table.add_row("[yellow]STALE[/]" if r.stale else ok, "exchange rates",
                      f"cached {r.age_hours:.0f}h ago"
                      + (" - network unreachable" if r.stale else ""))
    except Exception as e:
        table.add_row(bad, "exchange rates", str(e)[:70])

    try:
        p = Profile.load(_profile_path(args))
        table.add_row(ok, "profile",
                      f"{p.path} ({len(p.sources)} sources, "
                      f"{sum(len(s.queries) for s in p.sources)} queries)")
    except ProfileError as e:
        table.add_row(bad, "profile", str(e).splitlines()[0])

    console.print(Panel(table, title="[bold]jobsift doctor", border_style="blue",
                        title_align="left"))


def _notify(profile, kept) -> None:
    if not profile.notify_webhook:
        return
    payload = json.dumps({
        "profile": profile.name, "count": len(kept),
        "listings": [v.listing.to_dict() for v in kept]}).encode()
    req = urllib.request.Request(profile.notify_webhook, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            console.print(f"  webhook  [green]HTTP {r.status}[/]")
    except (urllib.error.URLError, TimeoutError) as e:
        console.print(f"  webhook  [yellow]failed ({type(e).__name__})[/] - "
                      f"the run itself succeeded, only the notification did not")


def _emit(result, profile, out_dir: Path, quiet: bool):
    if not result.kept and result.new_count == 0:
        console.print("\n[yellow]Nothing new since the last run.[/] "
                      "Report left untouched.\n"
                      "Force a full re-scan with [cyan]jobsift run --rescan[/]")
        return None
    path = write_report(result, profile, out_dir)

    table = Table(header_style="bold", expand=True)
    table.add_column(f"{profile.currency}/hr", style="green", no_wrap=True)
    table.add_column("Role")
    table.add_column("Company", style="dim")
    table.add_column("Board", style="cyan", no_wrap=True)
    for v in sorted(result.kept, key=lambda v: -(v.listing.pay_low or 0))[:40]:
        table.add_row(v.listing.pay_display, v.listing.title[:52],
                      v.listing.company[:24], v.listing.board)
    if result.kept and not quiet:
        console.print(table)

    if result.rates.stale:
        console.print("[yellow]Exchange rates came from cache - the network was "
                      "unreachable. Check borderline pay by hand.[/]")
    for board, why in result.failures.items():
        console.print(f"[yellow]{board} failed:[/] {why[:110]}")
    console.print(f"\n[bold green]{len(result.kept)}[/] survived, "
                  f"[dim]{len(result.killed)} removed[/]  ->  [cyan]{path}[/]")
    return path


def cmd_run(args):
    profile = Profile.load(_profile_path(args))

    def progress(kind, **kw):
        if kind == "query":
            console.print(f"  [cyan]{kw['board']:<15}[/] {kw['found']:>3} new  "
                          f"[dim]{kw['query'][:52]}[/]", highlight=False)
        elif kind == "board_failed":
            console.print(f"  [yellow]{kw['board']:<15}[/] failed  "
                          f"[dim]{str(kw['error'])[:52]}[/]", highlight=False)

    srcs = ", ".join(s.board for s in profile.sources)
    console.print(f"[bold]{profile.name}[/]  floor "
                  f"{profile.floor_per_hour or '-'} {profile.currency}/hr\n"
                  f"[dim]{len(profile.sources)} sources: {srcs}[/]\n")
    result = run(profile, headed=args.headed, limit=args.limit,
                 rescan=args.rescan, progress=progress)
    if _emit(result, profile, Path(args.out), args.quiet):
        _notify(profile, result.kept)


def cmd_rescreen(args):
    profile = Profile.load(_profile_path(args))
    result = rescreen(profile)
    console.print(f"[bold]{profile.name}[/] · re-screening "
                  f"{result.seen_total} stored listings [dim](no network)[/]\n")
    result.new_count = len(result.kept)          # so _emit does not short-circuit
    if _emit(result, profile, Path(args.out), args.quiet) and args.notify:
        _notify(profile, result.kept)


def cmd_show(args):
    profile = Profile.load(_profile_path(args))
    console.print(markdown(rescreen(profile), profile))


# --------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jobsift",
        description="Read a job board so you do not have to. "
                    "Removes what is disqualifying; leaves the judgment to you.")
    p.add_argument("--profile", help="path to a profile TOML")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="write a commented starter profile")
    i.add_argument("--name", default="me")
    i.set_defaults(func=cmd_init)

    for name, fn, helptext in [("run", cmd_run, "scrape, screen, report"),
                               ("rescreen", cmd_rescreen,
                                "re-apply rules to stored listings, no network")]:
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--out", default="shortlists", help="where reports go")
        s.add_argument("--quiet", action="store_true", help="no result table")
        if name == "run":
            s.add_argument("--headed", action="store_true",
                           help="show the browser (use if a bot check blocks you)")
            s.add_argument("--limit", type=int, default=0,
                           help="fetch at most N new listings")
            s.add_argument("--rescan", action="store_true",
                           help="ignore what has been seen; fetch everything")
        else:
            s.add_argument("--notify", action="store_true")
        s.set_defaults(func=fn)

    sub.add_parser("rules", help="every disqualifying rule and why it exists"
                   ).set_defaults(func=cmd_rules)
    sub.add_parser("boards", help="installed board adapters"
                   ).set_defaults(func=cmd_boards)
    sub.add_parser("doctor", help="check the install"
                   ).set_defaults(func=cmd_doctor)
    sub.add_parser("show", help="print the current shortlist as markdown"
                   ).set_defaults(func=cmd_show)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except ProfileError as e:
        _fail(e)
    except KeyboardInterrupt:
        err.print("\n[yellow]Stopped.[/] Listings already fetched were saved.")
        return 130
    except RuntimeError as e:
        _fail(e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
