"""Neo4j graph write + neighbour queries on golden project A (steps 2.C.2 / 2.C.3)."""

from pathlib import Path

from app.intelligence.repository.ast import extract_repository
from app.intelligence.repository.filter import filter_repository
from app.intelligence.repository.graph import (
    delete_repository_graph,
    node_count,
    write_graph,
)
from app.intelligence.repository.queries import (
    what_calls,
    what_depends_on,
    what_imports,
    what_routes_to,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN_A = _REPO_ROOT / "testdata" / "golden-projects" / "a-nextjs-ts-mysql"

# Isolated from live clone-job graphs. Not a real MySQL project id.
_TEST_PROJECT_ID = 900_001
_TEST_REPOSITORY_ID = 900_001


def _extracted():
    filtered = filter_repository(_GOLDEN_A)
    return extract_repository(_GOLDEN_A, filtered.included_paths)


def setup_function() -> None:
    delete_repository_graph(_TEST_PROJECT_ID, _TEST_REPOSITORY_ID)


def teardown_function() -> None:
    delete_repository_graph(_TEST_PROJECT_ID, _TEST_REPOSITORY_ID)


def test_reindexing_twice_does_not_duplicate_nodes() -> None:
    extracted = _extracted()
    first = write_graph(_TEST_PROJECT_ID, _TEST_REPOSITORY_ID, extracted, commit_hash="abc")
    second = write_graph(_TEST_PROJECT_ID, _TEST_REPOSITORY_ID, extracted, commit_hash="abc")
    assert first == second
    assert first == node_count(_TEST_PROJECT_ID, _TEST_REPOSITORY_ID)
    assert first > 0


def test_neighbour_queries_on_golden_a() -> None:
    extracted = _extracted()
    write_graph(_TEST_PROJECT_ID, _TEST_REPOSITORY_ID, extracted, commit_hash="abc")

    callers = what_calls(
        _TEST_PROJECT_ID, "getProduct", repository_id=_TEST_REPOSITORY_ID
    )
    caller_names = {row["name"] for row in callers}
    assert "ProductPage" in caller_names
    assert "generateMetadata" in caller_names

    routed = what_routes_to(
        _TEST_PROJECT_ID, "/products/[slug]", repository_id=_TEST_REPOSITORY_ID
    )
    assert any(row["name"] == "ProductPage" for row in routed)

    importers = what_imports(
        _TEST_PROJECT_ID, "getProduct", repository_id=_TEST_REPOSITORY_ID
    )
    importer_names = {row["name"] for row in importers}
    assert "ProductPage" in importer_names

    deps = what_depends_on(
        _TEST_PROJECT_ID, "getProduct", repository_id=_TEST_REPOSITORY_ID
    )
    dep_names = {row["name"] for row in deps}
    assert "query" in dep_names
