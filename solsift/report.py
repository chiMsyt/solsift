"""Turning verdicts into something a person acts on.

The report deliberately leaves an empty assessment under every survivor.
solsift removes what needs no judgment; it does not rank, score or recommend.
The reasoning you write in that space is what you reuse in the application, and
a number produced without it is worse than no number.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from pathlib import Path

from .profile import Profile
from .run import RunResult


def markdown(result: RunResult, profile: Profile) -> str:
    cur = profile.currency
    today = date.today().isoformat()
    kept, killed = result.kept, result.killed

    lines = [
        f"# Shortlist - {today}",
        "",
        f"`{profile.name}` · "
        f"{len(profile.sources)} sources · "
        f"**{result.seen_total} listings seen, {len(kept)} survived**",
        "",
        f"Rates in **{cur}**, fetched "
        + (f"{result.rates.age_hours:.0f}h ago."
           if not result.rates.stale
           else f"**from cache, {result.rates.age_hours:.0f}h old - the network "
                f"was unreachable.** Check any borderline pay by hand."),
        "",
        "> solsift applied the disqualifying rules only. **It has not ranked "
        "these and it has not judged them.** Fill in the assessment under each "
        "one, then decide. That reasoning is what you reuse when you apply.",
        "",
        "---",
        "",
        "## Survivors",
        "",
    ]

    if not kept:
        lines += ["Nothing survived this run.", ""]
    else:
        lines += [f"| # | Pay ({cur}/hr) | Role | Company | Board |",
                  "|---|---|---|---|---|"]
        for i, v in enumerate(sorted(
                kept, key=lambda v: -(v.listing.pay_low or 0)), 1):
            l = v.listing
            lines.append(f"| {i} | {l.pay_display} | [{l.title}]({l.url}) | "
                         f"{l.company} | `{l.board}` |")
        lines.append("")

    unsure = [v for v in kept if v.listing.pay_low is not None
              and not v.listing.pay_certain]
    if unsure:
        lines += [
            f"> **{len(unsure)} of these had no stated pay period**, so whether "
            f"the figure is hourly, monthly or annual was inferred from its "
            f"size. Those are marked `?` above. They are kept deliberately - a "
            f"guessed period must never remove a listing - but **check the "
            f"figure before you rely on it.**", ""]
        for v in unsure:
            lines.append(f"> - [{v.listing.title}]({v.listing.url}) — "
                         f"`{v.listing.pay_raw}` read as {v.listing.pay_note}")
        lines.append("")

    for i, v in enumerate(sorted(kept, key=lambda v: -(v.listing.pay_low or 0)), 1):
        l = v.listing
        lines += [
            "---", "",
            f"## {i} — {l.title}", "",
            f"`{l.board}` · **{l.company}** · {l.location} · {l.employment_type} · "
            f"**{l.pay_display} {cur}/hr**"
            + (f" · *{l.pay_note}*" if l.pay_note else ""),
            "",
            f"`{l.url}`",
            "",
            "<details><summary>Posting</summary>", "", "```",
            l.description[:3500], "```", "", "</details>", "",
            "### Assessment", "",
            "*What is mandatory here versus merely preferred? Which gap is real, "
            "and is it closable in under two days? What is this worth per month, "
            "not per hour? Would finishing it produce a line worth putting on the "
            "next application?*", "",
            "**Verdict:** ", "",
        ]

    if killed:
        counts = Counter(v.reason for v in killed)
        lines += ["---", "", f"## Removed ({len(killed)})", "",
                  "Listed so you can check the rules are doing what you think. "
                  "A rule killing far more than you expect is usually the rule "
                  "being wrong, not the board.", "",
                  "| Reason | Count |", "|---|---|"]
        for reason, n in counts.most_common():
            lines.append(f"| {reason} | {n} |")
        lines += ["", "<details><summary>Every removed listing</summary>", ""]
        for v in killed:
            lines.append(f"- **{v.reason}** — [{v.listing.title}]"
                         f"({v.listing.url}) · {v.listing.company}")
        lines += ["", "</details>", ""]

    if result.failures:
        lines += ["---", "", "## Sources that failed", "",
                  "These boards returned nothing this run. The rest still "
                  "reported, so this shortlist is incomplete rather than wrong.",
                  ""]
        for board, why in result.failures.items():
            lines.append(f"- **{board}** — {why}")
        lines.append("")

    lines += ["---", ""]
    if result.attributions:
        # Some boards require credit as a condition of using their API.
        lines += ["### Sources", "", *(f"- {a}" for a in result.attributions), ""]
    lines += [f"*Generated by solsift at {datetime.now():%Y-%m-%d %H:%M}. "
              f"Re-run the rules for free with `solsift rescreen`.*", ""]
    return "\n".join(lines)


def write(result: RunResult, profile: Profile, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"shortlist-{date.today().isoformat()}.md"
    path.write_text(markdown(result, profile), encoding="utf-8")
    return path
