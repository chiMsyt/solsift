"""Collapsing the same job seen on more than one board.

With eight sources the same role turns up repeatedly - agencies post to LinkedIn
and a local board at once, and aggregators re-list each other. Left alone, the
shortlist reads as though there is three times as much work available as there
is, and the duplicates crowd out genuinely distinct listings.

Matching is on **company plus normalised title**, deliberately. Matching on
description is slower and worse (aggregators rewrite them), and matching on
title alone merges every "Virtual Assistant" on the internet into one row, which
is the failure that matters - so the company has to agree too.

When duplicates collapse, the survivor is the one with the most information:
a stated pay figure first, then the longer description. The others are recorded
on it, so nothing is hidden - a listing that appeared on three boards says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .listing import Listing

# Words that decorate a title without changing what the job is.
_NOISE = re.compile(
    r"\b(?:urgent(?:ly)?|hiring|now|immediate(?:ly)?|start|asap|remote|"
    r"work from home|wfh|home based|home-based|full[- ]time|part[- ]time|"
    r"contract|freelance|permanent|new|apply|needed|wanted|position|role|"
    r"opening|vacancy|job|opportunity|w/|with|for|the|a|an)\b", re.I)
_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACE = re.compile(r"\s+")

# Company suffixes and agency decorations that differ between boards.
_CO_NOISE = re.compile(
    r"\b(?:inc|llc|ltd|limited|corp|corporation|co|company|pty|plc|gmbh|bv|"
    r"srl|sa|ag|group|holdings|solutions|services|international|global|"
    r"technologies|tech|labs|studio|studios|agency|consulting|consultancy)\b",
    re.I)


def normalise_title(title: str) -> str:
    """Reduce a title to the job it describes."""
    t = _PUNCT.sub(" ", title.lower())
    t = _NOISE.sub(" ", t)
    return _SPACE.sub(" ", t).strip()


def normalise_company(company: str) -> str:
    c = _PUNCT.sub(" ", company.lower())
    c = _CO_NOISE.sub(" ", c)
    return _SPACE.sub(" ", c).strip()


def key_for(listing: Listing) -> tuple[str, str] | None:
    """The identity two postings must share to be the same job.

    Returns None when there is not enough to be confident. **An unmatched
    listing is always kept** - guessing that two jobs are the same is how a real
    opportunity disappears, and that is the one outcome worth avoiding.
    """
    title = normalise_title(listing.title)
    company = normalise_company(listing.company)
    if not title or not company:
        return None
    return company, title


def _information(listing: Listing) -> tuple[int, int]:
    """How much a listing tells you. Higher wins when duplicates collapse."""
    return (1 if listing.pay_low is not None else 0, len(listing.description))


@dataclass
class Duplicates:
    kept: list[Listing] = field(default_factory=list)
    #: listing key -> the other boards it also appeared on
    also_on: dict[str, list[str]] = field(default_factory=dict)
    collapsed: int = 0


def collapse(listings: list[Listing]) -> Duplicates:
    """Merge listings that are the same job on different boards."""
    result = Duplicates()
    best: dict[tuple[str, str], Listing] = {}
    order: list[Listing] = []

    for listing in listings:
        key = key_for(listing)
        if key is None:
            order.append(listing)              # not confident: keep as its own
            continue
        incumbent = best.get(key)
        if incumbent is None:
            best[key] = listing
            order.append(listing)
            continue

        result.collapsed += 1
        winner, loser = ((listing, incumbent)
                         if _information(listing) > _information(incumbent)
                         else (incumbent, listing))
        if winner is not incumbent:
            order[order.index(incumbent)] = winner
            best[key] = winner
        seen_on = result.also_on.setdefault(f"{winner.board}:{winner.id}", [])
        if loser.board not in seen_on and loser.board != winner.board:
            seen_on.append(loser.board)

    result.kept = order
    return result
