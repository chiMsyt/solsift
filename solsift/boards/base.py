"""What a board adapter has to provide.

Adding a board means writing one class with one method and registering it.
Nothing else in the project should ever learn a specific board's name - if it
does, that is the bug.

**Most boards do not need a browser.** A surprising number publish a public JSON
API or an RSS feed, and where one exists it is better in every way: faster, it
does not break when the site is restyled, and it is the access route the board
itself offers. Only boards that publish nothing get scraped, and those set
`needs_browser = True`.
"""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from ..listing import Listing
from ..money import Rates


@runtime_checkable
class Board(Protocol):
    """One job board."""

    name: str
    #: Shown by `solsift boards`. Say what a query looks like for this board.
    help: str
    #: True if `search` needs a live Playwright page.
    needs_browser: bool
    #: Some APIs require credit as a condition of use. Rendered into reports.
    attribution: str

    def search(self, query: str, rates: Rates, *, page=None,
               skip: frozenset[str] = frozenset()) -> Iterator[Listing]:
        """Yield listings for one query.

        `skip` holds `board:id` keys already seen. Adapters must skip these
        **before** doing per-listing work, which for a browser board is the
        difference between one page load and eighty.
        """


_REGISTRY: dict[str, Board] = {}


def register(board):
    """Register an adapter. Usable as a class decorator or on an instance.

    The registry holds *instances*, so `search` is bound. Registering the class
    by mistake gives "search() missing 1 required positional argument" at run
    time on every board at once, which is a confusing way to learn this.
    """
    instance = board() if isinstance(board, type) else board
    _REGISTRY[instance.name] = instance
    return board          # give the decorator back the class, not the instance


def get(name: str) -> Board:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "none installed"
        raise KeyError(
            f"No board adapter named {name!r}.\nAvailable: {known}\n"
            f"Run `solsift boards` for what each one expects as a query.")
    return _REGISTRY[name]


def available() -> list[Board]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def load_all() -> None:
    """Import every bundled adapter so it registers itself."""
    from . import feeds, jobstreet, linkedin  # noqa: F401
