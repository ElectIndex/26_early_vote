"""The registry is thirty-five strings, and nothing used to check one of them.

⚠️ THIS FILE HAS THE HIGHEST VALUE PER LINE IN THE SUITE, AND THAT IS A MEASURED
CLAIM. `registry.TIER1` maps each state to its tier-1 adapter as a STRING --
`"nc:NCScraper"` -- resolved lazily, on purpose, so that a state whose module is
missing or broken degrades to civicAPI instead of taking the whole run down. The
cost of that design is that a typo is indistinguishable from a state whose
adapter has not been written yet: `_resolve` logs at DEBUG and returns None, and
the run carries on quietly one tier worse, forever.

Every per-state test module imports its adapter class DIRECTLY
(`from ev.adapters import nc`), so those thirty-five strings were the only link
between a working parser and production, and they were unverified for thirty-four
of thirty-five states. Corrupting all thirty-five class names to a typo and
running the suite gave 1311 passed -- every state silently demoted to the
national API, no test anywhere the wiser.

So: for every tracked state, walk the real ladder and insist the state's own
scraper is actually on it.
"""

from __future__ import annotations

import pytest

from ev import registry
from ev.adapters.base import Adapter
from ev.schema import (
    TIER_AGGREGATOR, TIER_CIVIC, TIER_LABELS, TIER_MANUAL, TIER_SCRAPER,
)

#: Sorted so the parametrised ids read alphabetically; the registry's own order
#: is build-out order and is asserted separately by `tracked_states`.
TRACKED = sorted(registry.TIER1)

#: The ladder every tracked state must walk. Written out rather than derived from
#: FALLBACKS, because deriving it from the thing under test proves nothing.
EXPECTED_TIERS = [TIER_SCRAPER, TIER_CIVIC, TIER_AGGREGATOR, TIER_MANUAL]


@pytest.fixture(autouse=True)
def _no_stale_tracked_states():
    """`tracked_states` is lru_cached; a test that edits TIER1 must not poison it."""
    yield
    registry.tracked_states.cache_clear()


# --------------------------------------------------------------------------
# THE ONE THAT MATTERS: every TIER1 string resolves to a real scraper
# --------------------------------------------------------------------------
@pytest.mark.parametrize("state", TRACKED)
def test_every_tracked_state_actually_resolves_its_own_scraper(state):
    """A typo here costs the state its own data and raises nothing, ever.

    `registry.TIER1[state]` is the ONLY reference to the adapter class from
    production code -- the per-state test modules import the class directly and
    would keep passing with the registry pointing at nothing.
    """
    rungs = registry.ladder(state)
    got = [(a.tier, a.name) for a in rungs]
    assert TIER_SCRAPER in [a.tier for a in rungs], (
        f"{state}: TIER1 says {registry.TIER1[state]!r}, but ladder({state!r}) "
        f"came back {got} -- no tier-1 scraper. The state does not fail; it "
        f"silently publishes civicAPI's numbers instead of its own, forever."
    )


@pytest.mark.parametrize("state", TRACKED)
def test_every_tracked_state_walks_all_four_tiers_best_first(state):
    """The ladder documented in CLAUDE.md, asserted for every state rather than one.

    Tiers 2-4 are appended by `FALLBACKS`, so a state missing one of them means
    a fallback module stopped importing -- which reads on the page as a state
    that simply stopped answering.
    """
    assert [a.tier for a in registry.ladder(state)] == EXPECTED_TIERS


@pytest.mark.parametrize("state", TRACKED)
def test_every_resolved_adapter_declares_a_known_tier_and_a_name(state):
    """`Adapter.tier` defaults to 0 and `Provenance` refuses 0.

    An adapter that implements `fetch` correctly and forgets `tier = TIER_SCRAPER`
    used to raise out of `ladder.run_state` -- the one function whose contract is
    that one bad adapter must not kill the run. `name` is written into every
    published row's `source_name`, so a blank one publishes rows nobody can
    attribute.
    """
    for adapter in registry.ladder(state):
        assert adapter.tier in TIER_LABELS, f"{state}: {type(adapter).__name__} tier"
        assert adapter.name, f"{state}: {type(adapter).__name__} has no name"
        assert adapter.state == state, f"{state}: adapter was built for {adapter.state}"


@pytest.mark.parametrize("state", TRACKED)
def test_every_spec_names_the_module_for_its_own_state(state):
    """`"<st>:<Class>Scraper"`, the convention CLAUDE.md documents.

    A copy-pasted entry that still points at the state it was copied from
    resolves perfectly and publishes the wrong state's file under this state's
    code -- the one registry mistake that produces confident wrong numbers
    rather than a gap, so it is worth a separate assertion from "it resolves".
    """
    module_suffix, sep, class_name = registry.TIER1[state].partition(":")
    assert sep, f"{state}: {registry.TIER1[state]!r} is not '<module>:<Class>'"
    assert module_suffix == state.lower(), (
        f"{state}: TIER1 points at ev.adapters.{module_suffix}"
    )
    assert class_name, f"{state}: {registry.TIER1[state]!r} names no class"


def test_tracked_states_is_exactly_tier1_in_declaration_order():
    registry.tracked_states.cache_clear()
    assert registry.tracked_states() == tuple(registry.TIER1)


def test_a_state_with_no_scraper_still_gets_the_three_fallbacks():
    """Wyoming has no adapter and must still reach the page through civicAPI."""
    assert "WY" not in registry.TIER1
    assert [a.tier for a in registry.ladder("WY")] == EXPECTED_TIERS[1:]


def test_the_state_code_is_case_insensitive_and_reaches_the_adapter():
    lower = registry.ladder("nc")
    assert [a.name for a in lower] == [a.name for a in registry.ladder("NC")]
    assert all(a.state == "NC" for a in lower)


def test_the_ladder_is_sorted_by_tier_whatever_order_the_specs_are_in():
    """FALLBACKS is written best-first for readability only; `ladder()` sorts.

    The comment in registry.py promises a mistake in that tuple's order cannot
    silently reorder the walk, so reverse it and check the walk is unchanged.
    """
    reversed_fallbacks = tuple(reversed(registry.FALLBACKS))
    original, registry.FALLBACKS = registry.FALLBACKS, reversed_fallbacks
    try:
        assert [a.tier for a in registry.ladder("NC")] == EXPECTED_TIERS
    finally:
        registry.FALLBACKS = original


# --------------------------------------------------------------------------
# _resolve: three failure modes, all of them silent by design
# --------------------------------------------------------------------------
def test_a_missing_module_resolves_to_none_rather_than_raising():
    """Expected while an adapter is still being written. Not an error."""
    assert registry._resolve("no_such_state_module:Whatever", "ZZ") is None


def test_a_module_that_exists_without_the_class_resolves_to_none():
    """The typo case: the module imports, the class name is wrong."""
    assert registry._resolve("manual:NoSuchScraper", "ZZ") is None


def test_a_module_that_explodes_on_import_does_not_take_the_run_down(monkeypatch):
    """One bad deploy must not stop the other thirty-four states."""
    def boom(dotted):
        raise RuntimeError(f"{dotted} is broken")

    monkeypatch.setattr(registry.importlib, "import_module", boom)
    assert registry._resolve("nc:NCScraper", "NC") is None
    assert registry.ladder("NC") == []


def test_an_adapter_whose_constructor_raises_resolves_to_none(monkeypatch):
    from ev.adapters import manual as manual_module

    class Exploding(Adapter):
        def __init__(self, state=None):
            raise RuntimeError("no")

        def fetch(self, cycle, as_of):  # pragma: no cover - never constructed
            raise AssertionError

    monkeypatch.setattr(manual_module, "Exploding", Exploding, raising=False)
    assert registry._resolve("manual:Exploding", "ZZ") is None


def test_a_broken_tier_1_entry_degrades_to_the_fallbacks_instead_of_raising(monkeypatch):
    """The whole reason the ladder is strings and not imports.

    This is also exactly what a typo looks like from the outside, which is why
    the parametrised test above has to exist: nothing here is an error.
    """
    monkeypatch.setitem(registry.TIER1, "NC", "nc:NCScraperTypo")
    assert [a.tier for a in registry.ladder("NC")] == EXPECTED_TIERS[1:]


# --------------------------------------------------------------------------
# The floor of every ladder
# --------------------------------------------------------------------------
def test_the_last_rung_is_always_the_hand_entered_file():
    """`ladder.run_state` reads a NotYetPublished from the LAST rung as a total
    outage rather than as "the state has not started". That rule is only correct
    while the last rung is the manual file, which knows nothing about the world.
    """
    for state in TRACKED + ["WY"]:
        last = registry.ladder(state)[-1]
        assert (last.tier, last.name) == (TIER_MANUAL, "manual"), state
