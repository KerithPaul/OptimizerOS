"""Qdrant incremental indexing (step 2.D.3 verify)."""

from pathlib import Path

from app.intelligence.repository.ast import extract_repository
from app.intelligence.repository.chunker import chunk_repository
from app.intelligence.repository.filter import filter_repository
from app.intelligence.repository.chunker import _hash_text
from app.services.vectors import (
    delete_repository_chunks,
    index_code_chunks,
    scroll_code_chunks,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN_A = _REPO_ROOT / "testdata" / "golden-projects" / "a-nextjs-ts-mysql"

_TEST_PROJECT_ID = 900_011
_TEST_REPOSITORY_ID = 900_011


def setup_function() -> None:
    delete_repository_chunks(_TEST_PROJECT_ID, _TEST_REPOSITORY_ID)


def teardown_function() -> None:
    delete_repository_chunks(_TEST_PROJECT_ID, _TEST_REPOSITORY_ID)


def _chunks():
    filtered = filter_repository(_GOLDEN_A)
    extracted = extract_repository(_GOLDEN_A, filtered.included_paths)
    return chunk_repository(_GOLDEN_A, extracted)


def test_chunks_are_retrievable_with_commit_hash() -> None:
    chunks = _chunks()
    stats = index_code_chunks(
        _TEST_PROJECT_ID,
        _TEST_REPOSITORY_ID,
        chunks,
        commit_hash="commit-aaa",
    )
    assert stats.embedded == len(chunks)
    stored = scroll_code_chunks(_TEST_PROJECT_ID, _TEST_REPOSITORY_ID)
    assert stored
    assert all(row["commit_hash"] == "commit-aaa" for row in stored)
    assert all(row["source_type"] == "code" for row in stored)
    assert any(row["symbol"] == "getProduct" for row in stored)


def test_one_file_change_reembeds_only_that_file() -> None:
    chunks = _chunks()
    first = index_code_chunks(
        _TEST_PROJECT_ID,
        _TEST_REPOSITORY_ID,
        chunks,
        commit_hash="commit-aaa",
    )
    second = index_code_chunks(
        _TEST_PROJECT_ID,
        _TEST_REPOSITORY_ID,
        chunks,
        commit_hash="commit-aaa",
    )
    assert second.embedded == 0
    assert second.reused == first.embedded

    target = "lib/product-service.ts"
    changed = 0
    for chunk in chunks:
        if chunk.file_path == target:
            chunk.text = chunk.text + "\n"
            chunk.content_hash = _hash_text(chunk.text)
            changed += 1
    assert changed > 0

    third = index_code_chunks(
        _TEST_PROJECT_ID,
        _TEST_REPOSITORY_ID,
        chunks,
        commit_hash="commit-bbb",
    )
    assert third.embedded == changed
    assert third.reused == len(chunks) - changed
