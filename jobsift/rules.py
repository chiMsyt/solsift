"""The judgment layer.

A job board is mostly noise. On a real Philippine part-time VA board, a third of
the listings were sales roles wearing an admin title, and a quarter paid under
half the asking floor. Reading those is the expensive part of a job hunt, and it
is the part that does not need a person.

Every rule here answers one question: **is this listing disqualified for reasons
that need no judgment?** If a human would have to weigh anything, it is not a
rule and it belongs in the manual pass.

Two design commitments that everything else follows from:

1. **A rule that kills says why, in words a person would use.** A boolean tells
   you nothing you can act on, and the reason is what you reuse when you decide
   the rule was wrong.
2. **Rules are data, not code branches.** The README's rule table, the
   `jobsift rules` command and the profile's `disable` list all read the same
   objects. That is why the docs cannot drift from behaviour.

Rules are deliberately conservative. A false kill costs a real job; a false keep
costs thirty seconds of reading. When those two trade off, keep.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .listing import Listing
from .profile import Profile


@dataclass(frozen=True)
class Rule:
    """One disqualification. `key` is the stable name used to disable it."""

    key: str
    kills: str            # shown on the listing when it fires
    why: str              # the rationale - rendered into the README
    test: Callable[[Listing, Profile], bool]
    tags: tuple[str, ...] = ()

    def fires(self, listing: Listing, profile: Profile) -> bool:
        return self.test(listing, profile)


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)


# --- Pattern rules ----------------------------------------------------------
# Each is (key, kills, why, pattern, exception-pattern). The exception exists
# because the obvious pattern over-matches: "on-site visits" in an otherwise
# remote role, "CPA preferred" on a bookkeeping role. Every exception here was
# added because the naive version killed something real.

_PATTERNS: list[tuple[str, str, str, str, str | None]] = [
    (
        "commission_only",
        "commission-only, no base pay",
        "Entry-level work with no network produces no income on commission. "
        "A base is the difference between a job and a lottery ticket.",
        r"commission[- ]only|purely commission|100%\s*commission|no basic pay|"
        r"uncapped commission only",
        None,
    ),
    (
        "disguised_sales",
        "sales role wearing an admin title",
        "The single largest source of noise on VA and admin boards. Roughly a "
        "third of listings on a real sample. The title says assistant; the "
        "duties are quota-carrying outbound sales, which is a different job, a "
        "different skill and a different resume.",
        r"\bappointment setter\b|\bISA\b|\bSDR\b|cold[- ]call|telemarket|"
        r"insurance agent|sales agent|sales representative|"
        r"lead qualification specialist|\bcloser\b",
        # An admin role that merely supports a sales team is a real admin role.
        r"support(?:ing)? the sales team|assist(?:ing)? the sales|"
        r"sales support administrat|sales admin(?:istrative)? assistant",
    ),
    (
        "pay_to_work",
        "asks for money before hiring",
        "A legitimate employer never charges to be hired. Training fees, "
        "equipment deposits and placement fees are the most common shape of "
        "recruitment fraud aimed at inexperienced remote applicants.",
        r"training fee|placement fee|application fee|equipment deposit|"
        r"pay (?:a|an) (?:fee|deposit)|registration fee|processing fee",
        None,
    ),
    (
        "always_on",
        "demands 24/7 availability",
        "Nobody is available 24/7. A posting that asks reveals how it will "
        "treat boundaries once you are hired.",
        r"24/7 availability|available 24/7|on call 24/7|round[- ]the[- ]clock "
        r"availability",
        None,
    ),
    (
        "id_before_contract",
        "wants ID documents before any contract",
        "Identity documents before a signed contract is an identity-theft "
        "pattern, not an onboarding step.",
        r"(?:valid |government )?ID(?:s)? (?:before|prior to) "
        r"(?:signing|contract|hiring|interview)|send.{0,20}ID.{0,20}to apply",
        None,
    ),
]


def _pattern_rule(key, kills, why, pattern, exception) -> Rule:
    pat, exc = _rx(pattern), _rx(exception) if exception else None

    def test(listing: Listing, profile: Profile) -> bool:
        blob = f"{listing.title}\n{listing.description}"
        if exc and exc.search(blob):
            return False
        return bool(pat.search(blob))

    return Rule(key=key, kills=kills, why=why, test=test, tags=("pattern",))


# --- Profile rules ----------------------------------------------------------
# These read the profile, so they mean something different for every user. That
# is the whole reason profiles exist.

def _below_floor(listing: Listing, profile: Profile) -> bool:
    if profile.floor_per_hour is None or listing.pay_low is None:
        return False          # unknown pay is not a disqualification
    return listing.pay_low < profile.floor_per_hour


def _wrong_employment(listing: Listing, profile: Profile) -> bool:
    if not profile.employment_types or not listing.employment_type:
        return False
    got = listing.employment_type.lower()
    return not any(t.lower() in got for t in profile.employment_types)


_ONSITE = _rx(r"office[- ]based|on[- ]?site|work from (?:the )?office|"
              r"report(?:ing)? to (?:the )?office|in[- ]office")
# "occasional on-site visits" and "on-site support" describe a duty, not a
# workplace. Killing on those removed genuinely remote roles.
_ONSITE_OK = _rx(r"on[- ]?site (?:visits?|support|meetings?)|"
                 r"fully remote|100% remote|work from home")


def _not_remote(listing: Listing, profile: Profile) -> bool:
    if not profile.remote_only:
        return False
    blob = f"{listing.title}\n{listing.location}\n{listing.description}"
    if _ONSITE_OK.search(blob):
        return False
    return bool(_ONSITE.search(blob))


# Common abbreviation/expansion pairs, so a profile saying "RN" also catches
# "registered nurse". Users can always list both explicitly.
_CRED_ALIASES = {
    "rn": ("registered nurse",),
    "lpn": ("licensed practical nurse",),
    "cpa": ("certified public accountant",),
    "prc": ("prc licence", "prc license"),
    "cfa": ("chartered financial analyst",),
    "pmp": ("project management professional",),
}

# Word-level analysis, done per sentence. An earlier version used a character
# window and killed "CPA preferred but not required" - it saw CPA...required
# and never saw the "not". That is the dangerous direction of failure: it
# silently deletes jobs the person is qualified for.
_REQUIRED = _rx(r"\b(?:required|requires|mandatory|must have|must be|must hold|"
                r"is a must|essential|non-negotiable|only)\b")
_PREFERRED = _rx(r"\b(?:preferred|preferable|a plus|nice to have|desirable|"
                 r"desired|advantageous|an advantage|not required|"
                 r"not necessary|bonus|ideally|would be|welcome)\b")
_SENTENCE = re.compile(r"[^.!?\n]+")


def _aliases_for(cred: str) -> list[str]:
    c = cred.lower()
    return [c, *_CRED_ALIASES.get(c, ())]


def _missing_credential(listing: Listing, profile: Profile) -> bool:
    """Kill only when a credential is stated as REQUIRED and is not held.

    A preference beats a requirement: if a sentence says both "preferred" and
    "required" it is almost always "preferred, not required", so we keep.
    """
    if not profile.credentials_required_kill:
        return False

    held: set[str] = set()
    for c in profile.credentials:
        held.update(_aliases_for(c))

    for cred in profile.credentials_required_kill:
        names = [a for a in _aliases_for(cred) if a not in held]
        if not names:
            continue
        word = _rx(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b")

        # A credential in the job TITLE is the role, not a preference.
        if word.search(listing.title):
            return True

        for sentence in _SENTENCE.findall(listing.description):
            if not word.search(sentence):
                continue
            if _PREFERRED.search(sentence):
                continue                       # a wish, not a bar
            if _REQUIRED.search(sentence):
                return True
    return False


def _excluded_keyword(listing: Listing, profile: Profile) -> bool:
    if not profile.exclude_keywords:
        return False
    blob = f"{listing.title}\n{listing.description}".lower()
    return any(k.lower() in blob for k in profile.exclude_keywords)


def _off_target(listing: Listing, profile: Profile) -> bool:
    """Relevance, which is separate from disqualification.

    Feed boards match on the whole posting, so a search for "assistant" returns
    a Golang role whose description happens to say "assistant" once. Requiring a
    hit in the TITLE is crude but it is what actually distinguishes the job from
    a passing mention.
    """
    if not profile.title_keywords:
        return False
    title = listing.title.lower()
    return not any(k.lower() in title for k in profile.title_keywords)


_PROFILE_RULES = [
    Rule("below_floor", "pays below your floor",
         "Your floor is the rate you will not go under. Anchoring below it is "
         "hard to undo: the first number you accept becomes the number every "
         "later client hears about. Listings with no stated pay are kept - "
         "unknown is not the same as low.",
         _below_floor, ("profile", "pay")),
    Rule("wrong_employment", "wrong employment type",
         "Set this to what you can actually take. A student who cannot work "
         "full-time should never read a full-time posting twice.",
         _wrong_employment, ("profile",)),
    Rule("not_remote", "on-site, and you asked for remote only",
         "Job boards file remote and on-site roles under the same searches. If "
         "commuting is not possible, this is the highest-volume rule you have.",
         _not_remote, ("profile", "location")),
    Rule("missing_credential", "requires a credential you do not hold",
         "Only fires when the posting states a credential as required and your "
         "profile does not list it. Deliberately strict: 'CPA preferred' on a "
         "bookkeeping role is a wish, not a bar, and must not kill it.",
         _missing_credential, ("profile",)),
    Rule("excluded_keyword", "matches one of your excluded keywords",
         "Your own escape hatch, for whatever this list does not cover. "
         "Substring match, case-insensitive.",
         _excluded_keyword, ("profile", "custom")),
    Rule("off_target", "the job title is not the kind of work you are after",
         "Relevance, not disqualification. Feed boards match on the whole "
         "posting, so a search for 'assistant' returns a Golang role whose "
         "description mentions the word once. Requiring a hit in the title is "
         "crude but it separates the job from a passing mention. Leave "
         "title_keywords empty to switch this off.",
         _off_target, ("profile", "relevance")),
]


ALL_RULES: list[Rule] = [_pattern_rule(*p) for p in _PATTERNS] + _PROFILE_RULES
RULES_BY_KEY = {r.key: r for r in ALL_RULES}


@dataclass
class Verdict:
    """Why a listing was kept or killed. The reason is the point."""

    listing: Listing
    killed_by: Rule | None = None
    fired: list[Rule] = field(default_factory=list)

    @property
    def kept(self) -> bool:
        return self.killed_by is None

    @property
    def reason(self) -> str:
        return self.killed_by.kills if self.killed_by else "kept"


def screen(listing: Listing, profile: Profile) -> Verdict:
    """Apply every enabled rule. First firing rule is the headline reason."""
    verdict = Verdict(listing=listing)
    for rule in ALL_RULES:
        if rule.key in profile.disabled_rules:
            continue
        if rule.fires(listing, profile):
            verdict.fired.append(rule)
            if verdict.killed_by is None:
                verdict.killed_by = rule
    return verdict


def screen_all(listings, profile) -> list[Verdict]:
    return [screen(l, profile) for l in listings]
