"""wingman/scrape_common.py — the scraper's importable core, moved out of the runnable agent.

CLAUDE.md's rule: `agents/` is for something an operator RUNS, `wingman/` for something other
code IMPORTS. Three agents were importing build_row / next_id_generator / insert_rows / the
FLAG_* constants out of agents/scrape_opportunities.py, so each paid the cost of a 1,600-line
agent — its prompts, its argparse, its module state — to borrow a row builder.

These tests pin the two things a VERBATIM move must preserve: the functions are the same
objects wherever you import them from, and nothing that used to import from the agent broke.
"""
import inspect

import pytest

from agents import scrape_opportunities as so
from wingman import scrape_common as sc


_SHARED = ["build_row", "next_id_generator", "insert_rows", "clean_value",
           "gate_dup_candidates", "collapse_intra_run_twins", "_url_rank", "_netloc",
           "_without", "_is_missing_column", "_http_detail"]

_SHARED_CONSTANTS = ["VALID_TYPES", "VALID_PRICE", "VALID_SUBJECTS", "VALID_LOCATION",
                     "VALID_INTL", "VALID_SEASON", "ATTRIBUTION_KEYS", "_SCHEMA_ERROR_CODES",
                     "FLAG_BARE_DOMAIN", "FLAG_LOW_VALUE", "FLAG_OFFSITE", "FLAG_NO_TYPE",
                     "FLAG_URL_UNSOURCED", "FLAG_URL_REPLACED", "FLAG_DEAD_LINK"]


@pytest.mark.parametrize("name", _SHARED)
def test_the_agent_reexports_the_same_function_object(name):
    """A re-export, not a copy. Two copies would drift, and eval/grade_scraper_batch grades
    through the agent while the agents call through wingman."""
    assert getattr(so, name) is getattr(sc, name)


@pytest.mark.parametrize("name", _SHARED_CONSTANTS)
def test_the_agent_reexports_the_same_constants(name):
    assert getattr(so, name) == getattr(sc, name)


def test_scrape_common_does_not_import_the_agent():
    """The whole point of the move. A back-import would restore the cycle it removes."""
    src = inspect.getsource(sc)
    assert "scrape_opportunities" not in src.split('"""', 2)[-1], (
        "wingman/scrape_common.py must not import the agent it was extracted from")


@pytest.mark.parametrize("module", ["agents.harvest_names", "agents.mine_hub_pages",
                                    "agents.refind_dead_links"])
def test_agents_borrow_from_the_shared_layer_not_the_runnable_agent(module):
    import importlib
    src = inspect.getsource(importlib.import_module(module))
    assert "from wingman.scrape_common import" in src or "from wingman import scrape_common" in src
    assert "from agents.scrape_opportunities import" not in src, (
        f"{module} still reaches into the runnable agent for shared plumbing")


def test_the_merge_logic_stayed_in_the_agent():
    """Operator decision 9 (2026-09-05): the merge logic is not to be touched at all, and a
    pure move counts. classify_same_url / merge_row / apply_merge stay put."""
    src = inspect.getsource(sc)
    for name in ("classify_same_url", "merge_row", "apply_merge", "MERGE_FILL_FIELDS"):
        assert f"def {name}" not in src and f"{name} =" not in src
        assert hasattr(so, name), f"{name} must still live in the agent"


def test_the_m4_prompt_and_url_logic_stayed_in_the_agent():
    """reconcile_url carries the M4 sentinel and the scraper's loop is its only caller; the
    prompts are M8. Neither moves."""
    src = inspect.getsource(sc)
    assert "def reconcile_url" not in src
    assert "MARQUEE M4" not in src
    assert "DISCOVERY_SYSTEM" not in src and "EXTRACT_SYSTEM" not in src


# --- the moved functions still work ---------------------------------------------------

def test_build_row_still_builds_a_row():
    row = sc.build_row({"name": "Test Program", "org": "MIT", "type": "Program",
                        "price": "Free", "subject_tags": ["STEM"]},
                       "ec99999", "scraper-test", "https://x.org/p", [])
    assert row["id"] == "ec99999" and row["name"] == "Test Program"
    assert row["is_active"] is False       # never trust a scrape with visibility
    assert row["url"] == "https://x.org/p"


def test_build_row_parks_an_invalid_type_rather_than_dropping_the_row():
    row = sc.build_row({"name": "N", "type": "Nonsense"}, "ec1", "s", "https://x.org", [])
    assert row["type"] == "Program"        # caller attaches FLAG_NO_TYPE


def test_build_row_returns_none_without_a_name_or_url():
    assert sc.build_row({"name": ""}, "ec1", "s", "https://x.org", []) is None
    assert sc.build_row({"name": "N"}, "ec1", "s", "", []) is None


def test_clean_value_filters_to_the_valid_set():
    assert sc.clean_value("Free", sc.VALID_PRICE) == "Free"
    assert sc.clean_value("Cheap", sc.VALID_PRICE) is None
