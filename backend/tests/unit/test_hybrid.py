"""Hybrid retrieval pipeline (step 5.A.1 verify)."""

import pytest

from app.intelligence.repository.graph import GraphError
from app.retrieval.hybrid import (
    CANDIDATE_CAP,
    EVIDENCE_ITEM_CAP,
    RetrievalError,
    RetrievalHit,
    RetrievalScope,
    retrieve,
    understand_query,
)
from app.retrieval.rerank import RERANK_MODE_APPLIED, RERANK_MODE_FALLBACK, Reranker
from app.services.vectors import VectorError


def _hit(index: int, *, channel: str = "semantic", source_type: str = "code") -> RetrievalHit:
    return RetrievalHit(
        id=f"{channel}-{index}",
        text=f"chunk {index} product metadata generateMetadata",
        score=1.0 / (index + 1),
        channel=channel,
        source_type=source_type,
        payload={
            "file_path": f"lib/file-{index}.ts",
            "symbol": f"symbol{index}",
            "start_line": index,
            "source_type": source_type,
        },
    )


def _disabled_reranker() -> Reranker:
    return Reranker(enabled=False)


def test_hundreds_of_chunks_send_eight_compressed_items() -> None:
    semantic = [_hit(index) for index in range(200)]

    result = retrieve(
        "Where is product metadata generated?",
        scope=RetrievalScope(project_id=1, repository_id=1),
        semantic_fn=lambda _q, _s: semantic,
        graph_fn=lambda _q, _s: [],
        lexical_fn=lambda _q, _s: [],
        reranker=_disabled_reranker(),
    )

    assert result.candidates_considered == CANDIDATE_CAP
    assert len(result.items) == EVIDENCE_ITEM_CAP
    assert all(len(item.summary) <= 600 for item in result.items)
    untrusted = [
        message
        for message in result.llm_messages
        if "BEGIN UNTRUSTED PROJECT CONTENT" in message["content"]
    ]
    assert len(untrusted) == EVIDENCE_ITEM_CAP
    assert result.rerank == RERANK_MODE_FALLBACK


def test_disabled_reranker_records_fallback_and_still_returns() -> None:
    result = retrieve(
        "canonical urls",
        scope=RetrievalScope(project_id=1),
        semantic_fn=lambda _q, _s: [_hit(0), _hit(1)],
        graph_fn=lambda _q, _s: [],
        lexical_fn=lambda _q, _s: [],
        reranker=_disabled_reranker(),
    )

    assert result.items
    assert result.rerank == "fallback"
    assert result.rerank_reason == "disabled"
    assert '"rerank": "fallback"' in result.llm_messages[1]["content"]


def test_rerank_applied_reorders_candidates() -> None:
    def predict(_query: str, texts: list[str]) -> list[float]:
        return [0.1 if "chunk 0" in text else 0.9 for text in texts]

    result = retrieve(
        "product metadata",
        scope=RetrievalScope(project_id=1),
        semantic_fn=lambda _q, _s: [_hit(0), _hit(1)],
        graph_fn=lambda _q, _s: [],
        lexical_fn=lambda _q, _s: [],
        reranker=Reranker(predict_fn=predict),
    )

    assert result.rerank == RERANK_MODE_APPLIED
    assert result.items[0].id == "semantic-1"


def test_fusion_dedupes_the_same_chunk_from_two_channels() -> None:
    semantic = [_hit(0, channel="semantic")]
    lexical = [_hit(0, channel="lexical")]
    lexical[0].id = "lexical-0"

    result = retrieve(
        "product metadata",
        scope=RetrievalScope(project_id=1),
        semantic_fn=lambda _q, _s: semantic,
        graph_fn=lambda _q, _s: [],
        lexical_fn=lambda _q, _s: lexical,
        reranker=_disabled_reranker(),
    )

    assert result.candidates_considered == 1
    assert len(result.items) == 1


def test_metadata_filter_is_passed_to_semantic_channel() -> None:
    seen: dict[str, RetrievalScope] = {}

    def semantic(_query, scope: RetrievalScope) -> list[RetrievalHit]:
        seen["scope"] = scope
        return [_hit(0)]

    retrieve(
        "title",
        scope=RetrievalScope(
            project_id=9,
            repository_id=3,
            source_types=("code",),
            language="typescript",
            file_path="lib/product-service.ts",
        ),
        semantic_fn=semantic,
        graph_fn=lambda _q, _s: [],
        lexical_fn=lambda _q, _s: [],
        reranker=_disabled_reranker(),
    )

    assert seen["scope"].project_id == 9
    assert seen["scope"].source_types == ("code",)
    assert seen["scope"].file_path == "lib/product-service.ts"


def test_all_channels_down_is_an_error_not_empty_success() -> None:
    def boom_semantic(_q, _s):
        raise VectorError("Qdrant unavailable")

    def boom_graph(_q, _s):
        raise GraphError("Neo4j unavailable")

    def boom_lexical(_q, _s):
        raise VectorError("Qdrant unavailable")

    with pytest.raises(RetrievalError, match="required stores failed"):
        retrieve(
            "product metadata",
            scope=RetrievalScope(project_id=1),
            semantic_fn=boom_semantic,
            graph_fn=boom_graph,
            lexical_fn=boom_lexical,
            reranker=_disabled_reranker(),
        )


def test_empty_index_returns_no_items() -> None:
    result = retrieve(
        "product metadata",
        scope=RetrievalScope(project_id=1),
        semantic_fn=lambda _q, _s: [],
        graph_fn=lambda _q, _s: [],
        lexical_fn=lambda _q, _s: [],
        reranker=_disabled_reranker(),
    )
    assert result.items == []
    assert result.candidates_considered == 0
    untrusted = [
        message
        for message in result.llm_messages
        if "BEGIN UNTRUSTED PROJECT CONTENT" in message["content"]
    ]
    assert untrusted == []


def test_understand_query_extracts_rule_ids_and_identifiers() -> None:
    understood = understand_query("Which SEO-TITLE-001 rule covers generateMetadata?")
    assert "SEO-TITLE-001" in understood.rule_ids
    assert "generateMetadata" in understood.identifiers
    assert "SEO-TITLE-001" in understood.rewritten
