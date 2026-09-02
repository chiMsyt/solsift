# jobsift

**Read a job board so you don't have to.**

Job boards are mostly noise. On a real sample of a part-time assistant board,
**a third of the listings were sales roles wearing an admin title**, and a
quarter paid under half the asking rate. Reading those is the expensive part of
looking for work, and it's the part that doesn't need a person.

jobsift reads **eight job boards at once**, removes every listing that's
disqualifying for reasons requiring no judgment, and hands you one merged
shortlist with the reasoning left blank.

Most of those boards publish a public API or RSS feed, so **no browser and no
install** — a feed-only profile runs in seconds.

**It does not rank, score, or recommend.** That's deliberate — see
[What it won't do](#what-it-wont-do).

```console
$ jobsift run
timothy  floor 4.0 USD/hr
8 sources: remoteok, remotive, arbeitnow, jobicy, himalayas, weworkremotely, linkedin, jobstreet

  remoteok         14 new  virtual assistant
  remotive          9 new  assistant
  linkedin         22 new  virtual assistant | Philippines
  weworkremotely    6 new  remote-customer-support-jobs.rss
  jobstreet        30 new  ph.jobstreet.com/virtual-assistant-jobs/part-time
  ...

  USD/hr       Role                                    Company        Board
  8.97-10.97   WFH Accounting Supervisor (QuickBooks)  BruntWork      jobstreet
  7.00-9.00    GoHighLevel CRM Virtual Assistant       VA Masters     jobstreet
  6.25-8.75    Executive Assistant (Remote)            Hiro           remoteok
  ...

  74 survived, 88 removed  ->  shortlists/shortlist-2026-09-02.md
```

---

## Why it exists

It was built for one person's job hunt and it found things a careful manual pass
had missed — including a listing paying **$8.97–10.97/hr on a board where the
best find so far had been $7–9**. Not because the code is clever, but because it
read all 67 listings and a tired human read the first fifteen.

Then it grew a second reason: **one board is never the whole market.** The good
listings on a small, unfashionable board have a far shorter applicant queue than
the same role on the big one, and checking six boards by hand twice a week is
exactly the chore nobody sustains.

The filtering rules aren't generic. Each one is a mistake somebody already made:

- Unstated pay is **kept**, never treated as low — the best-paying listings
  routinely say "rate depends on the application."
- `PHP 5,000/month` was once parsed as `PHP 5,000/hour`, which ranked an unpaid
  intern role as the best-paid job on the board. There's a regression test.
- "Occasional on-site visits" in a fully remote role must not trip the on-site
  filter. `CPA preferred` must not trip the credential filter.

---

## Install

```bash
git clone https://github.com/chiMsyt/jobsift
cd jobsift
uv sync                      # or: pip install -e .
```

**Most boards need nothing else.** Seven of the eight read a public API or RSS
feed over plain HTTP.

Only `jobstreet` drives a real browser, because it sits behind a bot check that
no plain HTTP client gets past. If you use it, jobsift will pick up **Brave,
Chrome or Edge** automatically; if you have none:

```bash
playwright install chromium
```

Skip that entirely by leaving browser-based boards out of your profile.

Check everything's wired up:

```bash
jobsift doctor
```

## Start

```bash
jobsift init --name you      # writes a commented profile
# edit profiles/you.toml — at minimum: queries, floor_per_hour, currency
jobsift run
```

Your profile is **gitignored**. Nothing personal — your rate floor, your
searches, your webhook — ever lands in the repo.

---

## Your profile

Everything personal lives in one TOML file. No rate, country, job title or floor
is hardcoded anywhere in the code; that's what makes this a tool two people can
use rather than a script one person can.

```toml
name = "you"

# One [[source]] block per board. Results are merged and de-duplicated.
[[source]]
board = "remoteok"
queries = ["virtual assistant", "executive assistant"]

[[source]]
board = "linkedin"
# "terms | location", via the logged-out guest endpoint
queries = ["virtual assistant | Philippines"]

[[source]]
board = "weworkremotely"
queries = ["https://weworkremotely.com/categories/remote-customer-support-jobs.rss"]

[pay]
currency = "USD"          # everything is reported in this, at live rates
floor_per_hour = 4.0      # the rate you won't go under
target_per_hour = 6.0     # what you ask for — reporting only, never filters

[filters]
employment_types = ["part time"]
remote_only = true
credentials_held = []
credentials_to_screen = ["RN", "PRC"]
exclude_keywords = []
disable_rules = []

[notify]
# webhook = "http://localhost:5678/webhook/jobsift"   # n8n, Zapier, anything
```

---

## Commands

<!-- BEGIN:commands -->
| command | what it does |
|---|---|
| `jobsift init` | write a commented starter profile |
| `jobsift run` | scrape, screen, report |
| `jobsift rescreen` | re-apply rules to stored listings, no network |
| `jobsift rules` | every disqualifying rule and why it exists |
| `jobsift boards` | installed board adapters |
| `jobsift doctor` | check the install |
| `jobsift show` | print the current shortlist as markdown |
<!-- END:commands -->

`jobsift rescreen` is the one worth knowing. It re-applies every rule to
listings already stored — no network, no hit on the board. **Tuning a rule has
to be free**, or nobody checks whether a change was right and the rules quietly
rot.

---

## The rules

Every rule says *why it killed something*, in words you can argue with. A
boolean tells you nothing you can act on.

<!-- BEGIN:rules -->
| rule | removes | why it exists |
|---|---|---|
| `commission_only` | commission-only, no base pay | Entry-level work with no network produces no income on commission. A base is the difference between a job and a lottery ticket. |
| `disguised_sales` | sales role wearing an admin title | The single largest source of noise on VA and admin boards. Roughly a third of listings on a real sample. The title says assistant; the duties are quota-carrying outbound sales, which is a different job, a different skill and a different resume. |
| `pay_to_work` | asks for money before hiring | A legitimate employer never charges to be hired. Training fees, equipment deposits and placement fees are the most common shape of recruitment fraud aimed at inexperienced remote applicants. |
| `always_on` | demands 24/7 availability | Nobody is available 24/7. A posting that asks reveals how it will treat boundaries once you are hired. |
| `id_before_contract` | wants ID documents before any contract | Identity documents before a signed contract is an identity-theft pattern, not an onboarding step. |
| `below_floor` | pays below your floor | Your floor is the rate you will not go under. Anchoring below it is hard to undo: the first number you accept becomes the number every later client hears about. Listings with no stated pay are kept - unknown is not the same as low. |
| `wrong_employment` | wrong employment type | Set this to what you can actually take. A student who cannot work full-time should never read a full-time posting twice. |
| `not_remote` | on-site, and you asked for remote only | Job boards file remote and on-site roles under the same searches. If commuting is not possible, this is the highest-volume rule you have. |
| `missing_credential` | requires a credential you do not hold | Only fires when the posting states a credential as required and your profile does not list it. Deliberately strict: 'CPA preferred' on a bookkeeping role is a wish, not a bar, and must not kill it. |
| `excluded_keyword` | matches one of your excluded keywords | Your own escape hatch, for whatever this list does not cover. Substring match, case-insensitive. |
| `off_target` | the job title is not the kind of work you are after | Relevance, not disqualification. Feed boards match on the whole posting, so a search for 'assistant' returns a Golang role whose description mentions the word once. Requiring a hit in the title is crude but it separates the job from a passing mention. Leave title_keywords empty to switch this off. |
<!-- END:rules -->

Turn any of them off per profile:

```toml
disable_rules = ["disguised_sales"]
```

Rules are **deliberately conservative**. A false kill costs you a real job; a
false keep costs thirty seconds of reading. When those trade off, jobsift keeps.

---

## Boards

<!-- BEGIN:boards -->
| board | queries look like |
|---|---|
| `arbeitnow` | `Free-text terms. A lesser-known board, Europe-weighted, with a genuinely open API and a lot of remote listings.` |
| `himalayas` | `Free-text terms. Remote-only board with an open API.` |
| `jobicy` | `Free-text terms, e.g. "admin". Returns remote jobs worldwide.` |
| `jobstreet` | `https://ph.jobstreet.com/virtual-assistant-jobs/part-time?sortmode=ListedDate` |
| `linkedin` | `virtual assistant \| Philippines` |
| `remoteok` | `The feed returns everything current; jobsift filters locally.` |
| `remotive` | `Free-text terms, e.g. "assistant". Searched server-side.` |
| `weworkremotely` | `https://weworkremotely.com/categories/remote-customer-support-jobs.rss` |
<!-- END:boards -->

**A board that fails doesn't take the run with it.** If LinkedIn rate-limits
you, the other seven still report, and the shortlist says which source was
missing — incomplete rather than quietly wrong.

Adding a board means writing one class with one `search` method and registering
it. For an API or RSS board, subclass `FeedBoard` and it's about twenty lines of
"where does this board keep the title" — see
[`jobsift/boards/feeds.py`](jobsift/boards/feeds.py). Nothing else in the
codebase knows any board's name.

### On LinkedIn specifically

jobsift reads LinkedIn's **logged-out guest endpoint** — no account, no cookies,
nothing that can be suspended. That's deliberate: LinkedIn is aggressive about
automated access from authenticated sessions, and the penalty lands on the
account, which is the worst thing to lose mid-job-hunt. The trade-off is that
guest results carry less detail and usually no salary. A missing salary costs
you a click; a suspended account costs you the search.

---

## What it won't do

**It won't score, rank or recommend.** The report leaves an empty assessment
under every survivor, and that's the point: the reasoning you write there is
what you reuse in the application. A number produced without reasoning is worse
than no number, because it looks like it means something.

**It won't guess an exchange rate.** Rates are fetched live and cached for a
day. If the network is down you get the cached rate *and a visible warning* —
never a silent guess. A stale rate silently reprices every decision you make;
in the run this grew out of, the rate had moved 11% and quietly repriced two
shortlisted roles downward.

**It won't treat unknown pay as low pay.** Ever.

---

## Please use it responsibly

**Prefer the feed.** Where a board publishes an API or RSS, that's the access
route the board itself offers, and jobsift uses it — that's seven of the eight
adapters. Only `jobstreet` is scraped, because it publishes nothing.

Automated access may still conflict with a board's terms of service. jobsift is
published as a tool for **reading your own job search** — the same pages you'd
open by hand, at a human pace. Check the terms of any board you point it at.
You're responsible for how you use it.

Some boards require credit as a condition of use. jobsift renders those
attributions into every report automatically; **don't strip them.**

Be decent about it: run it on a schedule, not in a loop. The seen-list means
each listing is fetched exactly once, ever.

---

## Status

**v0.1 — early, working, and in real daily use.** Eight board adapters. The API
will move. Issues and new board adapters very welcome — the feed ones are easy,
and lesser-known boards are the most valuable to add.

## Licence

MIT — see [LICENSE](LICENSE).
