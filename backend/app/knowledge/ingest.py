"""Ingest job: `knowledge/` -> MySQL + Qdrant `optimization_knowledge` + Neo4j (step 4.B.2).

Idempotent and versioned `[SPEC]`: re-ingesting an unchanged rule leaves its
current `OptimizationRule` version untouched; changing any semantic field of
a rule (category, source, authority, content, conditions, severity,
recommendation, confidence) inserts a new, higher-numbered version and never
rewrites the old one, matching step 4.A.2's "historical audits keep the rule
version they used".

All three stores are written inside one attempt: MySQL rows are added and
flushed (visible in-transaction, not yet committed), then Qdrant, then
Neo4j; only once all three succeed does the MySQL transaction commit. A
Qdrant or Neo4j failure rolls the MySQL side back rather than leaving the
three stores disagreeing about what was ingested — the same discipline
`app.intelligence.website.persist.persist_crawl` uses for a crawl.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from neo4j.exceptions import Neo4jError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.intelligence.repository.graph import GraphError, get_driver
from app.knowledge.rulefile import KNOWLEDGE_ROOT, LoadedRule, RuleFile, load_all_rule_files
from app.models.knowledge import OptimizationRule, OptimizationSource
from app.services.vectors import KnowledgeChunk, index_optimization_knowledge

logger = logging.getLogger("architectos.knowledge.ingest")

_RULE_LABEL = "OptimizationRule"

# Fields whose change means a rule's meaning changed and a new version is
# owed. `retrieved_at` is deliberately excluded: re-running ingest on an
# unchanged file updates nothing just because time passed.
_VERSIONED_FIELDS = (
    "category",
    "source",
    "source_url",
    "authority",
    "published_at",
    "content",
    "conditions",
    "severity",
    "recommendation",
    "confidence",
)


@dataclass
class IngestStats:
    rule_files: int
    sources_created: int
    rules_created: int
    rules_versioned: int
    rules_unchanged: int
    qdrant_embedded: int
    qdrant_reused: int
    qdrant_deleted: int
    neo4j_nodes: int


def ingest_knowledge_base(
    db: Session,
    *,
    root=KNOWLEDGE_ROOT,
    settings: Settings | None = None,
) -> IngestStats:
    """Load, validate, and ingest every rule under `root`.

    Raises `app.knowledge.rulefile.RuleFileError` for malformed or
    unattributed rule files (ingest never proceeds partially), `VectorError`
    if Qdrant is unavailable, and `GraphError` if Neo4j is unavailable — in
    both of the latter two cases the MySQL transaction is rolled back first.
    """

    settings = settings or get_settings()
    loaded = load_all_rule_files(root)

    sources_created = 0
    rules_created = 0
    rules_versioned = 0
    rules_unchanged = 0
    current_rows: list[OptimizationRule] = []

    try:
        for item in loaded:
            source, created = _resolve_source(db, item.rule)
            if created:
                sources_created += 1

            row, outcome = _upsert_rule(db, item, source)
            current_rows.append(row)
            if outcome == "created":
                rules_created += 1
            elif outcome == "versioned":
                rules_versioned += 1
            else:
                rules_unchanged += 1

        db.flush()

        vector_stats = index_optimization_knowledge(
            [_knowledge_chunk(row) for row in current_rows],
            settings=settings,
        )
        neo4j_nodes = _write_rule_graph(current_rows, settings=settings)
    except Exception:
        db.rollback()
        raise

    db.commit()

    stats = IngestStats(
        rule_files=len(loaded),
        sources_created=sources_created,
        rules_created=rules_created,
        rules_versioned=rules_versioned,
        rules_unchanged=rules_unchanged,
        qdrant_embedded=vector_stats.embedded,
        qdrant_reused=vector_stats.reused,
        qdrant_deleted=vector_stats.deleted,
        neo4j_nodes=neo4j_nodes,
    )
    logger.info(
        "knowledge base ingested (files=%s, created=%s, versioned=%s, unchanged=%s, "
        "qdrant_embedded=%s, neo4j_nodes=%s)",
        stats.rule_files,
        stats.rules_created,
        stats.rules_versioned,
        stats.rules_unchanged,
        stats.qdrant_embedded,
        stats.neo4j_nodes,
    )
    return stats


def _resolve_source(db: Session, rule: RuleFile) -> tuple[OptimizationSource, bool]:
    """Get-or-create the source registry row. Never overwrites an existing
    source's authority — re-ranking a source is a separate, deliberate
    action, not a side effect of re-ingesting rules that cite it."""

    existing = db.scalar(
        select(OptimizationSource).where(
            OptimizationSource.name == rule.source.name,
            OptimizationSource.source_url == rule.source.source_url,
        )
    )
    if existing is not None:
        if existing.authority != rule.source.authority:
            logger.warning(
                "source %r already registered at authority=%s; rule file says %s — "
                "keeping the registered authority (re-ranking is a separate action)",
                rule.source.name,
                existing.authority.value,
                rule.source.authority.value,
            )
        return existing, False

    created = OptimizationSource(
        name=rule.source.name,
        source_url=rule.source.source_url,
        authority=rule.source.authority,
    )
    db.add(created)
    db.flush()
    return created, True


def _upsert_rule(
    db: Session, item: LoadedRule, source: OptimizationSource
) -> tuple[OptimizationRule, str]:
    rule = item.rule
    latest = db.scalar(
        select(OptimizationRule)
        .where(OptimizationRule.rule_id == rule.rule_id)
        .order_by(OptimizationRule.version.desc())
        .limit(1)
    )

    retrieved_at = datetime.combine(rule.retrieved_at, datetime.min.time())
    published_at = (
        datetime.combine(rule.published_at, datetime.min.time())
        if rule.published_at is not None
        else None
    )

    if latest is not None and not _changed(latest, rule, source, published_at):
        return latest, "unchanged"

    version = 1 if latest is None else latest.version + 1
    row = OptimizationRule(
        rule_id=rule.rule_id,
        category=rule.category,
        source_id=source.id,
        source=rule.source.name,
        source_url=rule.source.source_url,
        authority=rule.source.authority,
        published_at=published_at,
        retrieved_at=retrieved_at,
        version=version,
        content=rule.content,
        conditions=rule.conditions.model_dump(mode="json", exclude_none=True),
        severity=rule.severity,
        recommendation=rule.recommendation,
        confidence=rule.confidence,
    )
    db.add(row)
    db.flush()
    return row, ("created" if latest is None else "versioned")


def _changed(
    latest: OptimizationRule,
    rule: RuleFile,
    source: OptimizationSource,
    published_at: datetime | None,
) -> bool:
    candidate = {
        "category": rule.category,
        "source": rule.source.name,
        "source_url": rule.source.source_url,
        "authority": rule.source.authority,
        "published_at": published_at,
        "content": rule.content,
        "conditions": rule.conditions.model_dump(mode="json", exclude_none=True),
        "severity": rule.severity,
        "recommendation": rule.recommendation,
        "confidence": rule.confidence,
    }
    for field_name in _VERSIONED_FIELDS:
        if getattr(latest, field_name) != candidate[field_name]:
            return True
    return False


def _knowledge_chunk(row: OptimizationRule) -> KnowledgeChunk:
    text = f"{row.rule_id} — {row.content}\n\nRecommendation: {row.recommendation}"
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return KnowledgeChunk(
        rule_id=row.rule_id,
        version=row.version,
        category=row.category.value,
        severity=row.severity.value,
        confidence=row.confidence.value,
        authority=row.authority.value,
        source_url=row.source_url,
        text=text,
        content_hash=content_hash,
    )


def _write_rule_graph(rows: list[OptimizationRule], *, settings: Settings | None = None) -> int:
    driver = get_driver(settings)
    try:
        with driver.session() as session:
            session.execute_write(_ensure_constraint)
            for row in rows:
                session.execute_write(_merge_rule_node, row)
            count = session.execute_read(_count_rule_nodes)
    except GraphError:
        raise
    except Neo4jError as exc:
        raise GraphError(f"Neo4j write failed: {exc}") from exc
    except Exception as exc:
        raise GraphError(f"Neo4j unavailable: {exc}") from exc
    return count


def _ensure_constraint(tx) -> None:
    tx.run(
        f"CREATE CONSTRAINT optimizationrule_stable_id IF NOT EXISTS "
        f"FOR (n:{_RULE_LABEL}) REQUIRE n.stable_id IS UNIQUE"
    )


def _merge_rule_node(tx, row: OptimizationRule) -> None:
    stable_id = rule_stable_id(row.rule_id, row.version)
    tx.run(
        f"""
        MERGE (r:{_RULE_LABEL} {{stable_id: $stable_id}})
        SET r.rule_id = $rule_id,
            r.version = $version,
            r.category = $category,
            r.severity = $severity,
            r.confidence = $confidence,
            r.authority = $authority,
            r.source = $source,
            r.source_url = $source_url,
            r.is_current = true,
            r.kind = '{_RULE_LABEL}'
        WITH r
        MATCH (old:{_RULE_LABEL} {{rule_id: $rule_id}})
        WHERE old.stable_id <> $stable_id
        SET old.is_current = false
        """,
        stable_id=stable_id,
        rule_id=row.rule_id,
        version=row.version,
        category=row.category.value,
        severity=row.severity.value,
        confidence=row.confidence.value,
        authority=row.authority.value,
        source=row.source,
        source_url=row.source_url,
    )


def _count_rule_nodes(tx) -> int:
    record = tx.run(f"MATCH (n:{_RULE_LABEL}) RETURN count(n) AS c").single()
    return int(record["c"]) if record is not None else 0


def rule_stable_id(rule_id: str, version: int) -> str:
    return f"rule:{rule_id}:{version}"


if __name__ == "__main__":
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        result = ingest_knowledge_base(session)
        print(
            f"ingested {result.rule_files} rule file(s): "
            f"{result.rules_created} created, {result.rules_versioned} versioned, "
            f"{result.rules_unchanged} unchanged, {result.sources_created} new source(s), "
            f"{result.neo4j_nodes} Neo4j node(s)"
        )
    finally:
        session.close()
