#!/usr/bin/env python3
"""Regenerate the parts of README.md that describe the code.

    python tools/sync_readme.py          # rewrite in place
    python tools/sync_readme.py --check  # exit 1 if stale (used by the test)

A README goes stale because it is written once and the code moves. So the
sections that describe behaviour - the rules, the commands, the boards - are not
written by hand at all. They are generated from the same objects the program
uses, between markers, and `tests/test_readme.py` fails the build the moment
they drift.

Prose stays hand-written. Only the parts that mirror code are generated.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solsift import boards                       # noqa: E402
from solsift.cli import build_parser             # noqa: E402
from solsift.rules import ALL_RULES              # noqa: E402

README = ROOT / "README.md"


def _cell(s: str) -> str:
    return s.replace("|", r"\|").replace("\n", " ")


def gen_rules() -> str:
    out = ["| rule | removes | why it exists |", "|---|---|---|"]
    for r in ALL_RULES:
        out.append(f"| `{r.key}` | {_cell(r.kills)} | {_cell(r.why)} |")
    return "\n".join(out)


def gen_commands() -> str:
    parser = build_parser()
    sub = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    out = ["| command | what it does |", "|---|---|"]
    for name, p in sub.choices.items():
        out.append(f"| `solsift {name}` | {_cell(p.description or p.prog)} |")
    # argparse keeps help on the parent action, not the child parser.
    helps = {c.dest: c.help for c in sub._choices_actions}
    out = ["| command | what it does |", "|---|---|"] + [
        f"| `solsift {name}` | {_cell(helps.get(name, ''))} |"
        for name in sub.choices]
    return "\n".join(out)


def gen_boards() -> str:
    boards.load_all()
    out = ["| board | queries look like |", "|---|---|"]
    for b in boards.available():
        first = b.help.strip().splitlines()[1].strip() if "\n" in b.help else b.help
        out.append(f"| `{b.name}` | `{_cell(first)}` |")
    return "\n".join(out)


GENERATORS = {"rules": gen_rules, "commands": gen_commands, "boards": gen_boards}


def render(text: str) -> str:
    for name, fn in GENERATORS.items():
        pattern = re.compile(
            rf"<!-- BEGIN:{name} -->.*?<!-- END:{name} -->", re.DOTALL)
        if not pattern.search(text):
            raise SystemExit(
                f"README.md has no <!-- BEGIN:{name} --> / <!-- END:{name} --> "
                f"markers. Add them where that section belongs.")
        block = f"<!-- BEGIN:{name} -->\n{fn()}\n<!-- END:{name} -->"
        text = pattern.sub(lambda _: block, text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    current = README.read_text(encoding="utf-8")
    updated = render(current)

    if args.check:
        if current != updated:
            print("README.md is out of date with the code.\n"
                  "Run:  python tools/sync_readme.py", file=sys.stderr)
            return 1
        print("README.md is current.")
        return 0

    if current == updated:
        print("README.md already current.")
    else:
        README.write_text(updated, encoding="utf-8")
        print(f"README.md updated ({len(GENERATORS)} generated sections).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
