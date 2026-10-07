"""RAG evaluation harness (Phase 5.D, `IMPLEMENTATION_PLAN_V2.md` step 5.D.2).

Metrics `[SPEC AGENTS.md §22]`: Recall@K, Precision@K, MRR, nDCG,
evidence accuracy, source relevance.

The golden queries live in `testdata/rag-golden/queries.json`. Expected
evidence is the locators from golden A/B/E `EXPECTED.md` and the Phase 2
AST — not whatever the retriever currently ranks. A retrieval regression
must be able to fail this module.

The full gating threshold is Phase 13. This file exists so a recall drop
is visible now. Verify (step 5.D.2): breaking the Qdrant metadata filter
drops Recall@K and the harness reports it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.intelligence.repository.ast import extract_repository
from app.intelligence.repository.chunker import CodeChunk, _hash_text, chunk_repository
from app.intelligence.repository.filter import filter_repository
from app.intelligence.repository.graph import delete_repository_graph, write_graph
from app.intelligence.website.extract import extract_page
from app.intelligence.website.persist import content_hash
from app.retrieval.hybrid import (
    EVIDENCE_ITEM_CAP,
    SOURCE_CODE,
    CompressedEvidence,
    RetrievalScope,
    retrieve,
)
from app.retrieval.rerank import Reranker
from app.services.embeddings import hash_embed
from app.services.vectors import (
    PageContent,
    VectorError,
    delete_page_content,
    delete_repository_chunks,
    index_code_chunks,
    index_page_content,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN_PATH = _REPO_ROOT / "testdata" / "rag-golden" / "queries.json"
_PROJECT_ROOTS = {
    "a-nextjs-ts-mysql": _REPO_ROOT / "testdata" / "golden-projects" / "a-nextjs-ts-mysql",
    "b-react-fastapi": _REPO_ROOT / "testdata" / "golden-projects" / "b-react-fastapi",
    "e-static-html": _REPO_ROOT / "testdata" / "golden-projects" / "e-static-html",
}
_E_BASE = "https://golden-e.example"
_E_PAGES = ("index.html", "about.html", "products.html", "contact.html", "orphan.html")

# Isolated from clone-job / other integration ids.
_PID_A = 900_701
_RID_A = 900_701
_PID_B = 900_702
_RID_B = 900_702
_PID_E = 900_703
_WID_E = 900_703
_PID_DECOY = 900_704
_RID_DECOY = 900_704

_DISABLED_RERANKER = Reranker(enabled=False)


@dataclass(frozen=True)
class ExpectedEvidence:
    source_type: str
    file_path: str | None = None
    symbol: str | None = None
    rule_id: str | None = None
    url: str | None = None
    must_contain: str | None = None


@dataclass(frozen=True)
class GoldenQuery:
    id: str
    spec_query: str
    query: str
    project: str
    source_types: tuple[str, ...]
    expected_evidence: tuple[ExpectedEvidence, ...]
    issue: str | None = None


@dataclass(frozen=True)
class QueryMetrics:
    query_id: str
    k: int
    retrieved: int
    relevant_retrieved: int
    relevant_total: int
    recall_at_k: float
    precision_at_k: float
    mrr: float
    ndcg_at_k: float
    evidence_accuracy: float
    source_relevance: float
    matched_expected: tuple[str, ...]
    locators: tuple[str, ...]


def load_golden_dataset(path: Path = _GOLDEN_PATH) -> tuple[int, list[GoldenQuery]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    k = int(raw["k"])
    queries: list[GoldenQuery] = []
    for row in raw["queries"]:
        expected = tuple(
            ExpectedEvidence(
                source_type=item["source_type"],
                file_path=item.get("file_path"),
                symbol=item.get("symbol"),
                rule_id=item.get("rule_id"),
                url=item.get("url"),
                must_contain=item.get("must_contain"),
            )
            for item in row["expected_evidence"]
        )
        queries.append(
            GoldenQuery(
                id=row["id"],
                spec_query=row["spec_query"],
                query=row["query"],
                project=row["project"],
                source_types=tuple(row["source_types"]),
                expected_evidence=expected,
                issue=row.get("issue"),
            )
        )
    return k, queries


def _item_symbol(item: CompressedEvidence) -> str | None:
    symbol = item.metadata.get("symbol") or item.metadata.get("name")
    if symbol:
        return str(symbol)
    if "#" in item.locator:
        return item.locator.rsplit("#", 1)[1]
    return None


def item_matches(item: CompressedEvidence, expected: ExpectedEvidence) -> bool:
    if item.source_type != expected.source_type:
        return False
    if expected.file_path:
        path = str(item.metadata.get("file_path") or "")
        if path != expected.file_path and expected.file_path not in item.locator:
            return False
    if expected.symbol:
        got = _item_symbol(item)
        if got != expected.symbol and expected.symbol not in item.locator:
            return False
    if expected.rule_id:
        got_rule = item.metadata.get("rule_id")
        if got_rule != expected.rule_id and item.locator != expected.rule_id:
            return False
    if expected.url:
        got_url = item.metadata.get("url")
        if got_url != expected.url and item.locator != expected.url:
            return False
    return True


def _evidence_text(item: CompressedEvidence) -> str:
    return " ".join(
        part
        for part in (
            item.summary,
            item.locator,
            str(item.metadata.get("rule_id") or ""),
            str(item.metadata.get("file_path") or ""),
            str(_item_symbol(item) or ""),
        )
        if part
    )


def _item_satisfies_must_contain(item: CompressedEvidence, expected: ExpectedEvidence) -> bool:
    if not expected.must_contain:
        return True
    return expected.must_contain.lower() in _evidence_text(item).lower()


def _dcg(gains: list[float]) -> float:
    return sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))


def metrics_for_ranking(
    items: list[CompressedEvidence],
    expected: tuple[ExpectedEvidence, ...],
    *,
    k: int,
    source_types: tuple[str, ...],
    query_id: str,
) -> QueryMetrics:
    window = items[:k]
    matched: list[ExpectedEvidence] = []
    seen_expected: set[int] = set()
    first_rank: int | None = None
    relevant_flags: list[float] = []
    accurate = 0
    matched_items = 0
    for rank, item in enumerate(window, start=1):
        hits = [
            evidence
            for index, evidence in enumerate(expected)
            if item_matches(item, evidence)
        ]
        relevant_flags.append(1.0 if hits else 0.0)
        if not hits:
            continue
        matched_items += 1
        if first_rank is None:
            first_rank = rank
        if any(_item_satisfies_must_contain(item, evidence) for evidence in hits):
            accurate += 1
        for evidence in hits:
            key = id(evidence)
            if key not in seen_expected:
                seen_expected.add(key)
                matched.append(evidence)

    n_expected = len(expected)
    n_matched = len(matched)
    recall = (n_matched / n_expected) if n_expected else 0.0
    precision = (matched_items / k) if k else 0.0
    mrr = (1.0 / first_rank) if first_rank else 0.0
    dcg = _dcg(relevant_flags)
    idcg = _dcg([1.0] * min(n_expected, k))
    ndcg = (dcg / idcg) if idcg else 0.0
    evidence_accuracy = (accurate / matched_items) if matched_items else 0.0
    allowed = set(source_types)
    source_relevance = (
        sum(1 for item in window if item.source_type in allowed) / len(window)
        if window
        else 0.0
    )
    return QueryMetrics(
        query_id=query_id,
        k=k,
        retrieved=len(window),
        relevant_retrieved=n_matched,
        relevant_total=n_expected,
        recall_at_k=recall,
        precision_at_k=precision,
        mrr=mrr,
        ndcg_at_k=ndcg,
        evidence_accuracy=evidence_accuracy,
        source_relevance=source_relevance,
        matched_expected=tuple(
            evidence.symbol or evidence.rule_id or evidence.url or evidence.file_path or "?"
            for evidence in matched
        ),
        locators=tuple(item.locator for item in window),
    )


def _index_repository(project_id: int, repository_id: int, root: Path) -> None:
    filtered = filter_repository(root)
    extracted = extract_repository(root, filtered.included_paths)
    chunks = chunk_repository(root, extracted)
    if not chunks:
        raise AssertionError(f"no chunks extracted from {root}")
    index_code_chunks(project_id, repository_id, chunks, commit_hash="rag-eval")
    write_graph(project_id, repository_id, extracted, commit_hash="rag-eval")


def _index_golden_e_pages(project_id: int, website_id: int) -> None:
    root = _PROJECT_ROOTS["e-static-html"]
    pages: list[PageContent] = []
    for index, name in enumerate(_E_PAGES, start=1):
        html = (root / name).read_text(encoding="utf-8")
        page = extract_page(html, url=f"{_E_BASE}/{name}").page
        text = " ".join(part for part in (page.title, page.content) if part) or page.url
        pages.append(
            PageContent(
                page_id=index,
                url=page.url,
                title=page.title,
                text=text,
                language=page.language,
                content_hash=content_hash(text),
            )
        )
    index_page_content(project_id, website_id, pages)


def _scope_for(case: GoldenQuery) -> RetrievalScope:
    source_types = case.source_types
    if case.project == "a-nextjs-ts-mysql":
        return RetrievalScope(
            project_id=_PID_A,
            repository_id=_RID_A,
            source_types=source_types,
        )
    if case.project == "b-react-fastapi":
        return RetrievalScope(
            project_id=_PID_B,
            repository_id=_RID_B,
            source_types=source_types,
        )
    if case.project == "e-static-html":
        return RetrievalScope(
            project_id=_PID_E,
            website_id=_WID_E,
            source_types=source_types,
        )
    raise AssertionError(f"unknown golden project {case.project}")


def evaluate_query(case: GoldenQuery, *, k: int) -> QueryMetrics:
    result = retrieve(
        case.query,
        scope=_scope_for(case),
        reranker=_DISABLED_RERANKER,
    )
    assert len(result.items) <= EVIDENCE_ITEM_CAP
    return metrics_for_ranking(
        result.items,
        case.expected_evidence,
        k=k,
        source_types=case.source_types,
        query_id=case.id,
    )


def _cleanup_eval_stores() -> None:
    delete_repository_chunks(_PID_A, _RID_A)
    delete_repository_graph(_PID_A, _RID_A)
    delete_repository_chunks(_PID_B, _RID_B)
    delete_repository_graph(_PID_B, _RID_B)
    delete_page_content(_PID_E, _WID_E)
    delete_repository_chunks(_PID_DECOY, _RID_DECOY)
    delete_repository_graph(_PID_DECOY, _RID_DECOY)


# ---------------------------------------------------------------------------
# Dataset shape (no stores)
# ---------------------------------------------------------------------------


def test_golden_dataset_covers_the_five_spec_queries_and_projects_a_b_e() -> None:
    k, queries = load_golden_dataset()
    assert k == EVIDENCE_ITEM_CAP
    spec = {row.spec_query for row in queries}
    assert spec == {
        "Where is product metadata generated?",
        "Which component renders Product schema?",
        "What controls canonical URLs?",
        "Which routes use ProductMetadata?",
        "Which SEO rule applies to this issue?",
    }
    projects = {row.project for row in queries}
    assert projects == {"a-nextjs-ts-mysql", "b-react-fastapi", "e-static-html"}
    assert all(row.expected_evidence for row in queries)
    for row in queries:
        if "SEO rule" in row.spec_query:
            assert row.issue, f"{row.id} must bind 'this issue' to an EXPECTED.md defect"
            assert all(item.rule_id for item in row.expected_evidence)


def test_expected_code_paths_exist_on_disk() -> None:
    _, queries = load_golden_dataset()
    for row in queries:
        root = _PROJECT_ROOTS[row.project]
        assert root.is_dir(), root
        for item in row.expected_evidence:
            if item.file_path:
                assert (root / item.file_path).is_file(), f"{row.id}: {item.file_path}"


# ---------------------------------------------------------------------------
# Metric definitions (synthetic rankings — no stores)
# ---------------------------------------------------------------------------


def _fake(
    locator: str,
    *,
    source_type: str = "code",
    file_path: str | None = None,
    symbol: str | None = None,
    rule_id: str | None = None,
    summary: str = "",
) -> CompressedEvidence:
    metadata: dict[str, Any] = {}
    if file_path:
        metadata["file_path"] = file_path
    if symbol:
        metadata["symbol"] = symbol
    if rule_id:
        metadata["rule_id"] = rule_id
    return CompressedEvidence(
        id=locator,
        source_type=source_type,
        channel="test",
        score=1.0,
        locator=locator,
        summary=summary or locator,
        metadata=metadata,
    )


def test_recall_precision_mrr_ndcg_evidence_accuracy_source_relevance() -> None:
    expected = (
        ExpectedEvidence(
            source_type="code",
            file_path="app/products/[slug]/page.tsx",
            symbol="generateMetadata",
            must_contain="generateMetadata",
        ),
        ExpectedEvidence(
            source_type="code",
            file_path="app/products/[slug]/page.tsx",
            symbol="ProductPage",
            must_contain="ProductPage",
        ),
    )
    items = [
        _fake(
            "noise.ts:1#other",
            file_path="noise.ts",
            symbol="other",
            summary="unrelated",
        ),
        _fake(
            "app/products/[slug]/page.tsx:3#generateMetadata",
            file_path="app/products/[slug]/page.tsx",
            symbol="generateMetadata",
            summary="export async function generateMetadata",
        ),
        _fake(
            "app/products/[slug]/page.tsx:12#ProductPage",
            file_path="app/products/[slug]/page.tsx",
            symbol="ProductPage",
            summary="export default async function ProductPage",
        ),
    ]
    scored = metrics_for_ranking(
        items,
        expected,
        k=8,
        source_types=("code",),
        query_id="synthetic",
    )
    assert scored.recall_at_k == 1.0
    assert scored.precision_at_k == pytest.approx(2 / 8)
    assert scored.mrr == pytest.approx(0.5)
    assert scored.ndcg_at_k > 0
    assert scored.evidence_accuracy == 1.0
    assert scored.source_relevance == 1.0


def test_metrics_report_a_recall_drop_when_relevant_items_leave_the_window() -> None:
    expected = (
        ExpectedEvidence(
            source_type="code",
            file_path="app/products/[slug]/page.tsx",
            symbol="generateMetadata",
        ),
    )
    relevant = _fake(
        "app/products/[slug]/page.tsx:3#generateMetadata",
        file_path="app/products/[slug]/page.tsx",
        symbol="generateMetadata",
        summary="generateMetadata",
    )
    noise = [
        _fake(f"decoy/{index}.ts:1#x", file_path=f"decoy/{index}.ts", symbol="x")
        for index in range(8)
    ]
    with_hit = metrics_for_ranking(
        [relevant, *noise],
        expected,
        k=8,
        source_types=("code",),
        query_id="with",
    )
    without_hit = metrics_for_ranking(
        noise,
        expected,
        k=8,
        source_types=("code",),
        query_id="without",
    )
    assert with_hit.recall_at_k == 1.0
    assert without_hit.recall_at_k == 0.0
    assert without_hit.recall_at_k < with_hit.recall_at_k


# ---------------------------------------------------------------------------
# Live retrieval against golden A/B/E (Qdrant + Neo4j)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _hash_hybrid_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """conftest patches embeddings.embed_texts, but hybrid binds the name at import."""
    monkeypatch.setattr("app.retrieval.hybrid.embed_texts", hash_embed)


@pytest.fixture(scope="module")
def indexed_golden_stores():
    _cleanup_eval_stores()
    try:
        _index_repository(_PID_A, _RID_A, _PROJECT_ROOTS["a-nextjs-ts-mysql"])
        _index_repository(_PID_B, _RID_B, _PROJECT_ROOTS["b-react-fastapi"])
        _index_golden_e_pages(_PID_E, _WID_E)
        yield
    except VectorError as exc:
        pytest.fail(f"Qdrant unavailable during RAG eval index: {exc}")
    finally:
        _cleanup_eval_stores()


def test_harness_runs_every_golden_query_and_emits_numeric_metrics(
    indexed_golden_stores,
) -> None:
    k, queries = load_golden_dataset()
    rows = [evaluate_query(case, k=k) for case in queries]
    assert len(rows) == len(queries)
    for row in rows:
        for value in (
            row.recall_at_k,
            row.precision_at_k,
            row.mrr,
            row.ndcg_at_k,
            row.evidence_accuracy,
            row.source_relevance,
        ):
            assert 0.0 <= value <= 1.0
        assert row.retrieved <= EVIDENCE_ITEM_CAP


def test_product_metadata_query_retrieves_generateMetadata(indexed_golden_stores) -> None:
    k, queries = load_golden_dataset()
    case = next(row for row in queries if row.id == "A-product-metadata-location")
    scored = evaluate_query(case, k=k)
    assert scored.recall_at_k == 1.0, scored.locators
    assert "generateMetadata" in scored.matched_expected


def test_breaking_the_metadata_filter_drops_recall(
    indexed_golden_stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 5.D.2 verify: a broken project_id filter drops Recall@K.

    A unique probe token is indexed once on golden A's generateMetadata
    locator and many times on another project, with the decoy text equal
    to the query so hash-embedding cosine is exact. With the metadata
    filter, only the golden locator is visible. Without it, the exact-
    match decoys fill the 8-item budget and recall of the golden locator
    drops. Graph is withheld so this measures the Qdrant filter only.
    """
    from app.core.config import Settings
    from app.retrieval import hybrid as hybrid_mod

    token = "architectosragfilterprobe"
    k, queries = load_golden_dataset()
    case = next(row for row in queries if row.id == "A-product-metadata-location")

    extra_text = f"{token} generateMetadata extra"
    extra = CodeChunk(
        chunk_type="function",
        file_path="app/products/[slug]/page.tsx",
        symbol="generateMetadata",
        language="typescript",
        start_line=9000,
        end_line=9001,
        text=extra_text,
        content_hash=_hash_text(extra_text),
    )
    filtered = filter_repository(_PROJECT_ROOTS["a-nextjs-ts-mysql"])
    extracted = extract_repository(
        _PROJECT_ROOTS["a-nextjs-ts-mysql"], filtered.included_paths
    )
    chunks = chunk_repository(_PROJECT_ROOTS["a-nextjs-ts-mysql"], extracted)
    chunks.append(extra)
    index_code_chunks(_PID_A, _RID_A, chunks, commit_hash="rag-eval-filter")

    decoys: list[CodeChunk] = []
    for index in range(50):
        decoys.append(
            CodeChunk(
                chunk_type="function",
                file_path=f"decoy/page-{index}.tsx",
                symbol="generateMetadata",
                language="typescript",
                start_line=1,
                end_line=1,
                text=token,
                content_hash=_hash_text(token),
            )
        )
    index_code_chunks(_PID_DECOY, _RID_DECOY, decoys, commit_hash="rag-eval-decoy")

    def _score() -> QueryMetrics:
        result = retrieve(
            token,
            scope=_scope_for(case),
            reranker=_DISABLED_RERANKER,
            graph_fn=lambda *_args: [],
        )
        return metrics_for_ranking(
            result.items,
            case.expected_evidence,
            k=k,
            source_types=case.source_types,
            query_id=case.id,
        )

    baseline = _score()
    assert baseline.recall_at_k > 0, (
        "filtered Qdrant retrieve must find the golden locator before the "
        f"filter can be shown to drop recall; locators={baseline.locators}"
    )

    def _unfiltered_code(
        scope: RetrievalScope, settings: Settings
    ) -> list[tuple[str, str, list]]:
        if not hybrid_mod._source_allowed(scope, SOURCE_CODE):
            return []
        return [(settings.qdrant_collection_code, SOURCE_CODE, [])]

    monkeypatch.setattr(hybrid_mod, "_collection_filters", _unfiltered_code)
    broken = _score()
    assert broken.recall_at_k < baseline.recall_at_k, (
        "breaking the project_id metadata filter must drop recall: "
        f"baseline={baseline.recall_at_k} broken={broken.recall_at_k} "
        f"baseline_locators={baseline.locators} broken_locators={broken.locators}"
    )
