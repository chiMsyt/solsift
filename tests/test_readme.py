"""The README cannot go stale.

Every section of README.md that describes behaviour is generated from the same
objects the program uses. This test regenerates them and fails if the file has
drifted, which turns "keep the docs current" from a good intention into a build
failure.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_matches_the_code():
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sync_readme.py"), "--check"],
        capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, (
        f"{r.stdout}{r.stderr}\n"
        f"README.md no longer matches the code. Run:\n"
        f"    python tools/sync_readme.py")


def test_every_rule_is_documented():
    from solsift.rules import ALL_RULES
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for rule in ALL_RULES:
        assert f"`{rule.key}`" in readme, f"rule {rule.key} missing from README"


def test_every_command_is_documented():
    from solsift.cli import build_parser
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    sub = next(a for a in build_parser()._actions
               if hasattr(a, "choices") and a.choices)
    for name in sub.choices:
        assert f"`solsift {name}`" in readme, f"command {name} missing from README"


def test_readme_promises_match_reality():
    """Claims in the prose that a future edit could quietly falsify."""
    from solsift.listing import Listing
    from solsift.profile import Profile, Source
    from solsift.rules import screen

    p = Profile(name="t", sources=[Source("remoteok", ["x"])],
                floor_per_hour=4.0)
    unknown_pay = Listing(board="t", id="1", url="u", title="VA",
                          description="Admin.", pay_low=None)
    assert screen(unknown_pay, p).kept, \
        "README promises unknown pay is never treated as low pay"
