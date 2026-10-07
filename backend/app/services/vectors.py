"""Qdrant `code_chunks` upsert and incremental indexing (step 2.D.3)."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchText,
    MatchValue,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from app.core.config import Settings, get_settings
from app.intelligence.repository.chunker import CodeChunk
from app.services.embeddings import embed_texts

logger = logging.getLogger("architectos.services.vectors")

_client: QdrantClient | None = None

RETRIEVAL_TEXT_CHARS = 4000
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class VectorError(Exception):
    """Qdrant is unavailable or an upsert failed. Must not be reported as success."""


@dataclass
class VectorHit:
    id: str
    score: float
    payload: dict
    text: str


@dataclass
class IndexStats:
    embedded: int
    reused: int
    deleted: int
    upserted: int


def get_qdrant(settings: Settings | None = None) -> QdrantClient:
    global _client
    if _client is None:
        settings = settings or get_settings()
        try:
            _client = QdrantClient(
                host=settings.qdrant_host,
                port=settings.qdrant_port,
                timeout=10,
                check_compatibility=False,
            )
            _client.get_collections()
        except Exception as exc:
            _client = None
            raise VectorError(f"Qdrant unavailable: {exc}") from exc
    return _client


def ensure_collection(
    settings: Settings | None = None,
    *,
    collection_name: str | None = None,
) -> None:
    settings = settings or get_settings()
    client = get_qdrant(settings)
    name = collection_name or settings.qdrant_collection_code
    try:
        if not client.collection_exists(name):
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=settings.embedding_dim,
                    distance=Distance.COSINE,
                    on_disk=True,
                ),
            )
    except VectorError:
        raise
    except Exception as exc:
        raise VectorError(f"Qdrant unavailable: {exc}") from exc


def point_id(
    project_id: int,
    repository_id: int,
    chunk: CodeChunk,
) -> str:
    key = (
        f"{project_id}:{repository_id}:{chunk.file_path}:"
        f"{chunk.chunk_type}:{chunk.symbol or ''}:{chunk.start_line}"
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def index_code_chunks(
    project_id: int,
    repository_id: int,
    chunks: list[CodeChunk],
    *,
    commit_hash: str | None,
    settings: Settings | None = None,
) -> IndexStats:
    """Upsert chunks, re-embedding only those whose content hash changed."""
    settings = settings or get_settings()
    ensure_collection(settings)
    client = get_qdrant(settings)
    collection = settings.qdrant_collection_code

    existing = _scroll_existing(client, collection, project_id, repository_id)
    current_ids = {point_id(project_id, repository_id, chunk) for chunk in chunks}

    to_embed: list[CodeChunk] = []
    to_patch_text: list[tuple[str, str]] = []
    reused = 0
    for chunk in chunks:
        pid = point_id(project_id, repository_id, chunk)
        payload = existing.get(pid)
        text = _retrieval_text(chunk.text)
        if payload is not None and payload.get("content_hash") == chunk.content_hash:
            reused += 1
            if payload.get("text") != text:
                to_patch_text.append((pid, text))
            continue
        to_embed.append(chunk)

    stale_ids = [pid for pid in existing if pid not in current_ids]

    vectors: list[list[float]] = []
    if to_embed:
        try:
            vectors = embed_texts([chunk.text for chunk in to_embed])
        except Exception as exc:
            raise VectorError(f"embedding failed: {exc}") from exc
        if len(vectors) != len(to_embed):
            raise VectorError("embedding count does not match chunk count")

    now = datetime.now(timezone.utc).isoformat()
    points: list[PointStruct] = []
    for chunk, vector in zip(to_embed, vectors, strict=True):
        pid = point_id(project_id, repository_id, chunk)
        prev = existing.get(pid) or {}
        points.append(
            PointStruct(
                id=pid,
                vector=vector,
                payload=_payload(
                    project_id=project_id,
                    repository_id=repository_id,
                    chunk=chunk,
                    commit_hash=commit_hash,
                    created_at=prev.get("created_at") or now,
                    updated_at=now,
                ),
            )
        )

    try:
        if points:
            client.upsert(collection_name=collection, points=points)
        _set_payload_text(client, collection, to_patch_text)
        if stale_ids:
            client.delete(
                collection_name=collection,
                points_selector=PointIdsList(points=stale_ids),
            )
    except VectorError:
        raise
    except Exception as exc:
        raise VectorError(f"Qdrant unavailable: {exc}") from exc

    stats = IndexStats(
        embedded=len(to_embed),
        reused=reused,
        deleted=len(stale_ids),
        upserted=len(points),
    )
    logger.info(
        "code_chunks indexed (project_id=%s, repository_id=%s, embedded=%s, reused=%s, deleted=%s)",
        project_id,
        repository_id,
        stats.embedded,
        stats.reused,
        stats.deleted,
    )
    return stats


def delete_repository_chunks(
    project_id: int,
    repository_id: int,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    client = get_qdrant(settings)
    collection = settings.qdrant_collection_code
    if not client.collection_exists(collection):
        return
    client.delete(
        collection_name=collection,
        points_selector=Filter(
            must=[
                FieldCondition(key="project_id", match=MatchValue(value=project_id)),
                FieldCondition(key="repository_id", match=MatchValue(value=repository_id)),
            ]
        ),
    )


def scroll_code_chunks(
    project_id: int,
    repository_id: int,
    *,
    file_path: str | None = None,
    settings: Settings | None = None,
) -> list[dict]:
    settings = settings or get_settings()
    client = get_qdrant(settings)
    collection = settings.qdrant_collection_code
    must = [
        FieldCondition(key="project_id", match=MatchValue(value=project_id)),
        FieldCondition(key="repository_id", match=MatchValue(value=repository_id)),
    ]
    if file_path:
        must.append(FieldCondition(key="file_path", match=MatchValue(value=file_path)))
    try:
        return _scroll_payloads(client, collection, Filter(must=must))
    except Exception as exc:
        raise VectorError(f"Qdrant unavailable: {exc}") from exc


def _scroll_existing(
    client: QdrantClient,
    collection: str,
    project_id: int,
    repository_id: int,
) -> dict[str, dict]:
    filt = Filter(
        must=[
            FieldCondition(key="project_id", match=MatchValue(value=project_id)),
            FieldCondition(key="repository_id", match=MatchValue(value=repository_id)),
        ]
    )
    existing: dict[str, dict] = {}
    for payload in _scroll_payloads(client, collection, filt, with_id=True):
        existing[str(payload.pop("_id"))] = payload
    return existing


def _scroll_payloads(
    client: QdrantClient,
    collection: str,
    filt: Filter,
    *,
    with_id: bool = False,
) -> list[dict]:
    if not client.collection_exists(collection):
        return []
    out: list[dict] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            scroll_filter=filt,
            limit=128,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        for point in points:
            payload = dict(point.payload or {})
            if with_id:
                payload["_id"] = point.id
            out.append(payload)
        if offset is None:
            break
    return out


def _payload(
    *,
    project_id: int,
    repository_id: int,
    chunk: CodeChunk,
    commit_hash: str | None,
    created_at: str,
    updated_at: str,
) -> dict:
    return {
        "project_id": project_id,
        "repository_id": repository_id,
        "source_type": "code",
        "file_path": chunk.file_path,
        "symbol": chunk.symbol,
        "language": chunk.language,
        "chunk_type": chunk.chunk_type,
        "url": None,
        "page_id": None,
        "rule_id": None,
        "authority": None,
        "source_url": None,
        "created_at": created_at,
        "updated_at": updated_at,
        "commit_hash": commit_hash,
        "content_hash": chunk.content_hash,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "text": _retrieval_text(chunk.text),
    }


@dataclass
class PageContent:
    """One page's embeddable text for the `page_content` collection."""

    page_id: int
    url: str
    title: str | None
    text: str
    language: str | None
    content_hash: str


def page_point_id(project_id: int, website_id: int, url: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{project_id}:{website_id}:{url}"))


def index_page_content(
    project_id: int,
    website_id: int,
    pages: list[PageContent],
    *,
    settings: Settings | None = None,
) -> IndexStats:
    """Upsert page text, re-embedding only when content_hash changed."""

    settings = settings or get_settings()
    collection = settings.qdrant_collection_pages
    ensure_collection(settings, collection_name=collection)
    client = get_qdrant(settings)

    existing = _scroll_existing_pages(client, collection, project_id, website_id)
    current_ids = {page_point_id(project_id, website_id, page.url) for page in pages}

    to_embed: list[PageContent] = []
    to_patch_text: list[tuple[str, str]] = []
    reused = 0
    for page in pages:
        pid = page_point_id(project_id, website_id, page.url)
        payload = existing.get(pid)
        text = _retrieval_text(page.text)
        if payload is not None and payload.get("content_hash") == page.content_hash:
            reused += 1
            if payload.get("text") != text:
                to_patch_text.append((pid, text))
            continue
        to_embed.append(page)

    stale_ids = [pid for pid in existing if pid not in current_ids]

    vectors: list[list[float]] = []
    if to_embed:
        try:
            vectors = embed_texts([page.text for page in to_embed])
        except Exception as exc:
            raise VectorError(f"embedding failed: {exc}") from exc
        if len(vectors) != len(to_embed):
            raise VectorError("embedding count does not match page count")

    now = datetime.now(timezone.utc).isoformat()
    points: list[PointStruct] = []
    for page, vector in zip(to_embed, vectors, strict=True):
        pid = page_point_id(project_id, website_id, page.url)
        prev = existing.get(pid) or {}
        points.append(
            PointStruct(
                id=pid,
                vector=vector,
                payload={
                    "project_id": project_id,
                    "repository_id": None,
                    "website_id": website_id,
                    "source_type": "page",
                    "file_path": None,
                    "symbol": None,
                    "language": page.language,
                    "chunk_type": "page_content",
                    "url": page.url,
                    "page_id": page.page_id,
                    "title": page.title,
                    "rule_id": None,
                    "authority": None,
                    "source_url": None,
                    "created_at": prev.get("created_at") or now,
                    "updated_at": now,
                    "commit_hash": None,
                    "content_hash": page.content_hash,
                    "text": _retrieval_text(page.text),
                },
            )
        )

    try:
        if points:
            client.upsert(collection_name=collection, points=points)
        _set_payload_text(client, collection, to_patch_text)
        if stale_ids:
            client.delete(
                collection_name=collection,
                points_selector=PointIdsList(points=stale_ids),
            )
    except VectorError:
        raise
    except Exception as exc:
        raise VectorError(f"Qdrant unavailable: {exc}") from exc

    stats = IndexStats(
        embedded=len(to_embed),
        reused=reused,
        deleted=len(stale_ids),
        upserted=len(points),
    )
    logger.info(
        "page_content indexed (project_id=%s, website_id=%s, embedded=%s, reused=%s, deleted=%s)",
        project_id,
        website_id,
        stats.embedded,
        stats.reused,
        stats.deleted,
    )
    return stats


def delete_page_content(
    project_id: int,
    website_id: int,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    client = get_qdrant(settings)
    collection = settings.qdrant_collection_pages
    if not client.collection_exists(collection):
        return
    client.delete(
        collection_name=collection,
        points_selector=Filter(
            must=[
                FieldCondition(key="project_id", match=MatchValue(value=project_id)),
                FieldCondition(key="website_id", match=MatchValue(value=website_id)),
            ]
        ),
    )


def scroll_page_content(
    project_id: int,
    website_id: int,
    *,
    settings: Settings | None = None,
) -> list[dict]:
    settings = settings or get_settings()
    client = get_qdrant(settings)
    collection = settings.qdrant_collection_pages
    filt = Filter(
        must=[
            FieldCondition(key="project_id", match=MatchValue(value=project_id)),
            FieldCondition(key="website_id", match=MatchValue(value=website_id)),
        ]
    )
    try:
        return _scroll_payloads(client, collection, filt)
    except Exception as exc:
        raise VectorError(f"Qdrant unavailable: {exc}") from exc


@dataclass
class KnowledgeChunk:
    """One rule's embeddable text for the `optimization_knowledge` collection."""

    rule_id: str
    version: int
    category: str
    severity: str
    confidence: str
    authority: str
    source_url: str
    text: str
    content_hash: str


def knowledge_point_id(rule_id: str) -> str:
    """Keyed by `rule_id` alone (not version) — Qdrant holds the current
    version for retrieval; MySQL is the versioned system of record."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"knowledge:{rule_id}"))


def index_optimization_knowledge(
    chunks: list[KnowledgeChunk],
    *,
    settings: Settings | None = None,
) -> IndexStats:
    """Upsert rule text, re-embedding only when content_hash changed.

    A rule dropped from `knowledge/` (no longer present in `chunks`) has its
    point deleted, mirroring `index_code_chunks` / `index_page_content`.
    """

    settings = settings or get_settings()
    collection = settings.qdrant_collection_knowledge
    ensure_collection(settings, collection_name=collection)
    client = get_qdrant(settings)

    existing = _scroll_existing_knowledge(client, collection)
    current_ids = {knowledge_point_id(chunk.rule_id) for chunk in chunks}

    to_embed: list[KnowledgeChunk] = []
    to_patch_text: list[tuple[str, str]] = []
    reused = 0
    for chunk in chunks:
        pid = knowledge_point_id(chunk.rule_id)
        payload = existing.get(pid)
        text = _retrieval_text(chunk.text)
        if payload is not None and payload.get("content_hash") == chunk.content_hash:
            reused += 1
            if payload.get("text") != text:
                to_patch_text.append((pid, text))
            continue
        to_embed.append(chunk)

    stale_ids = [pid for pid in existing if pid not in current_ids]

    vectors: list[list[float]] = []
    if to_embed:
        try:
            vectors = embed_texts([chunk.text for chunk in to_embed])
        except Exception as exc:
            raise VectorError(f"embedding failed: {exc}") from exc
        if len(vectors) != len(to_embed):
            raise VectorError("embedding count does not match rule count")

    now = datetime.now(timezone.utc).isoformat()
    points: list[PointStruct] = []
    for chunk, vector in zip(to_embed, vectors, strict=True):
        pid = knowledge_point_id(chunk.rule_id)
        prev = existing.get(pid) or {}
        points.append(
            PointStruct(
                id=pid,
                vector=vector,
                payload={
                    "project_id": None,
                    "repository_id": None,
                    "website_id": None,
                    "source_type": "knowledge",
                    "file_path": None,
                    "symbol": None,
                    "language": None,
                    "chunk_type": "optimization_rule",
                    "url": None,
                    "page_id": None,
                    "rule_id": chunk.rule_id,
                    "rule_version": chunk.version,
                    "category": chunk.category,
                    "severity": chunk.severity,
                    "confidence": chunk.confidence,
                    "authority": chunk.authority,
                    "source_url": chunk.source_url,
                    "created_at": prev.get("created_at") or now,
                    "updated_at": now,
                    "commit_hash": None,
                    "content_hash": chunk.content_hash,
                    "text": _retrieval_text(chunk.text),
                },
            )
        )

    try:
        if points:
            client.upsert(collection_name=collection, points=points)
        _set_payload_text(client, collection, to_patch_text)
        if stale_ids:
            client.delete(
                collection_name=collection,
                points_selector=PointIdsList(points=stale_ids),
            )
    except VectorError:
        raise
    except Exception as exc:
        raise VectorError(f"Qdrant unavailable: {exc}") from exc

    stats = IndexStats(
        embedded=len(to_embed),
        reused=reused,
        deleted=len(stale_ids),
        upserted=len(points),
    )
    logger.info(
        "optimization_knowledge indexed (embedded=%s, reused=%s, deleted=%s)",
        stats.embedded,
        stats.reused,
        stats.deleted,
    )
    return stats


def delete_knowledge_points(rule_ids: list[str], settings: Settings | None = None) -> None:
    """Remove specific rule points by id. `optimization_knowledge` has no
    project/website scoping dimension to delete by, unlike the other two
    collections — callers (tests; a future "retire this rule" operation)
    must name exactly which rule_ids to remove rather than relying on
    `index_optimization_knowledge`'s stale-point cleanup, which compares
    against the *complete* current rule set and would otherwise treat every
    rule_id absent from a partial call as deleted.
    """
    settings = settings or get_settings()
    client = get_qdrant(settings)
    collection = settings.qdrant_collection_knowledge
    if not rule_ids or not client.collection_exists(collection):
        return
    client.delete(
        collection_name=collection,
        points_selector=PointIdsList(points=[knowledge_point_id(rid) for rid in rule_ids]),
    )


def scroll_optimization_knowledge(settings: Settings | None = None) -> list[dict]:
    settings = settings or get_settings()
    client = get_qdrant(settings)
    collection = settings.qdrant_collection_knowledge
    try:
        if not client.collection_exists(collection):
            return []
        return _scroll_payloads(client, collection, Filter(must=[]))
    except Exception as exc:
        raise VectorError(f"Qdrant unavailable: {exc}") from exc


def _scroll_existing_knowledge(client: QdrantClient, collection: str) -> dict[str, dict]:
    existing: dict[str, dict] = {}
    for payload in _scroll_payloads(client, collection, Filter(must=[]), with_id=True):
        existing[str(payload.pop("_id"))] = payload
    return existing


def _scroll_existing_pages(
    client: QdrantClient,
    collection: str,
    project_id: int,
    website_id: int,
) -> dict[str, dict]:
    filt = Filter(
        must=[
            FieldCondition(key="project_id", match=MatchValue(value=project_id)),
            FieldCondition(key="website_id", match=MatchValue(value=website_id)),
        ]
    )
    existing: dict[str, dict] = {}
    for payload in _scroll_payloads(client, collection, filt, with_id=True):
        existing[str(payload.pop("_id"))] = payload
    return existing


def search_semantic(
    collection_name: str,
    query_vector: list[float],
    *,
    limit: int,
    must: list[FieldCondition] | None = None,
    settings: Settings | None = None,
) -> list[VectorHit]:
    """Dense vector search against one collection. Metadata filter is `must`."""
    settings = settings or get_settings()
    client = get_qdrant(settings)
    try:
        if not client.collection_exists(collection_name):
            return []
        response = client.query_points(
            collection_name=collection_name,
            query=query_vector,
            query_filter=Filter(must=must) if must else None,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
    except VectorError:
        raise
    except Exception as exc:
        raise VectorError(f"Qdrant unavailable: {exc}") from exc
    return [_hit_from_point(point) for point in response.points]


def search_lexical(
    collection_name: str,
    query_text: str,
    *,
    limit: int,
    must: list[FieldCondition] | None = None,
    settings: Settings | None = None,
) -> list[VectorHit]:
    """Keyword match against stored `text` payloads, scored by token overlap."""
    query_tokens = _tokens(query_text)
    if not query_tokens:
        return []
    settings = settings or get_settings()
    client = get_qdrant(settings)
    must_conditions = list(must or [])
    must_conditions.append(
        FieldCondition(key="text", match=MatchText(text=query_text))
    )
    try:
        if not client.collection_exists(collection_name):
            return []
        points, _offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(must=must_conditions),
            limit=max(limit, 1),
            with_payload=True,
            with_vectors=False,
        )
    except VectorError:
        raise
    except Exception as exc:
        raise VectorError(f"Qdrant unavailable: {exc}") from exc

    hits: list[VectorHit] = []
    for point in points:
        payload = dict(point.payload or {})
        text = str(payload.get("text") or "")
        score = _keyword_score(query_tokens, text)
        if score <= 0:
            continue
        hits.append(
            VectorHit(id=str(point.id), score=score, payload=payload, text=text)
        )
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[:limit]


def metadata_must(
    *,
    project_id: int | None = None,
    repository_id: int | None = None,
    website_id: int | None = None,
    source_type: str | None = None,
    language: str | None = None,
    file_path: str | None = None,
    rule_id: str | None = None,
    url: str | None = None,
) -> list[FieldCondition]:
    must: list[FieldCondition] = []
    if project_id is not None:
        must.append(FieldCondition(key="project_id", match=MatchValue(value=project_id)))
    if repository_id is not None:
        must.append(
            FieldCondition(key="repository_id", match=MatchValue(value=repository_id))
        )
    if website_id is not None:
        must.append(FieldCondition(key="website_id", match=MatchValue(value=website_id)))
    if source_type is not None:
        must.append(FieldCondition(key="source_type", match=MatchValue(value=source_type)))
    if language is not None:
        must.append(FieldCondition(key="language", match=MatchValue(value=language)))
    if file_path is not None:
        must.append(FieldCondition(key="file_path", match=MatchValue(value=file_path)))
    if rule_id is not None:
        must.append(FieldCondition(key="rule_id", match=MatchValue(value=rule_id)))
    if url is not None:
        must.append(FieldCondition(key="url", match=MatchValue(value=url)))
    return must


def _retrieval_text(text: str) -> str:
    if not text:
        return ""
    if len(text) <= RETRIEVAL_TEXT_CHARS:
        return text
    return text[:RETRIEVAL_TEXT_CHARS]


def _set_payload_text(
    client: QdrantClient,
    collection: str,
    patches: list[tuple[str, str]],
) -> None:
    for pid, text in patches:
        client.set_payload(
            collection_name=collection,
            payload={"text": text},
            points=[pid],
        )


def _hit_from_point(point: object) -> VectorHit:
    payload = dict(getattr(point, "payload", None) or {})
    return VectorHit(
        id=str(getattr(point, "id")),
        score=float(getattr(point, "score", 0.0) or 0.0),
        payload=payload,
        text=str(payload.get("text") or ""),
    )


def _tokens(text: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(text.lower()) if len(token) >= 3}


def _keyword_score(query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    overlap = query_tokens & _tokens(text)
    return len(overlap) / len(query_tokens)
