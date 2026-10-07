"""Neo4j write of meaningful repository entities (step 2.C.2).

Node keys are `stable_id` values that include `project_id` and
`repository_id`, so a re-index MERGEs rather than duplicating.
Never writes every AST node — only the symbols and relations from
`extract_repository`.
"""

from __future__ import annotations

import logging

from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError

from app.core.config import Settings, get_settings
from app.intelligence.repository.ast import ExtractResult, Relation, Symbol

logger = logging.getLogger("architectos.intelligence.repository.graph")

_CONSTRAINT_LABELS = (
    "Repository",
    "Directory",
    "File",
    "Function",
    "Class",
    "Component",
    "Route",
    "API",
    "Database",
    "SEOImplementation",
    "Schema",
)

_driver: Driver | None = None


class GraphError(Exception):
    """Neo4j is unavailable or a graph write failed. Must not be reported as success."""


def get_driver(settings: Settings | None = None) -> Driver:
    global _driver
    if _driver is None:
        settings = settings or get_settings()
        try:
            _driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
            _driver.verify_connectivity()
        except Exception as exc:
            _driver = None
            raise GraphError(f"Neo4j unavailable: {exc}") from exc
    return _driver


def stable_id(project_id: int, repository_id: int, symbol: Symbol) -> str:
    return (
        f"{project_id}:{repository_id}:{symbol.kind}:"
        f"{symbol.file_path or ''}:"
        f"{symbol.name}:{symbol.start_line or 0}"
    )


def write_graph(
    project_id: int,
    repository_id: int,
    extracted: ExtractResult,
    *,
    commit_hash: str | None = None,
    settings: Settings | None = None,
) -> int:
    """MERGE extracted symbols and relations. Returns the node count for this repo."""
    driver = get_driver(settings)
    repo_symbol = Symbol(
        kind="Repository",
        name=str(repository_id),
        file_path=None,
        start_line=0,
    )
    symbols = [repo_symbol, *extracted.symbols]
    try:
        with driver.session() as session:
            session.execute_write(_ensure_constraints)
            session.execute_write(
                _merge_all,
                project_id,
                repository_id,
                symbols,
                extracted.relations,
                commit_hash,
            )
            count = session.execute_read(_count_nodes, project_id, repository_id)
    except GraphError:
        raise
    except Neo4jError as exc:
        raise GraphError(f"Neo4j write failed: {exc}") from exc
    except Exception as exc:
        raise GraphError(f"Neo4j unavailable: {exc}") from exc
    logger.info(
        "graph write (project_id=%s, repository_id=%s, nodes=%s, relations=%s)",
        project_id,
        repository_id,
        count,
        len(extracted.relations),
    )
    return count


def node_count(project_id: int, repository_id: int, settings: Settings | None = None) -> int:
    driver = get_driver(settings)
    try:
        with driver.session() as session:
            return session.execute_read(_count_nodes, project_id, repository_id)
    except Exception as exc:
        raise GraphError(f"Neo4j unavailable: {exc}") from exc


def delete_repository_graph(
    project_id: int,
    repository_id: int,
    settings: Settings | None = None,
) -> None:
    """Test helper: remove one repository's nodes. Not a user-facing rollback."""
    driver = get_driver(settings)
    with driver.session() as session:
        session.run(
            """
            MATCH (n {project_id: $project_id, repository_id: $repository_id})
            DETACH DELETE n
            """,
            project_id=project_id,
            repository_id=repository_id,
        )


def _ensure_constraints(tx) -> None:
    for label in _CONSTRAINT_LABELS:
        tx.run(
            f"CREATE CONSTRAINT {label.lower()}_stable_id IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.stable_id IS UNIQUE"
        )


def _merge_all(
    tx,
    project_id: int,
    repository_id: int,
    symbols: list[Symbol],
    relations: list[Relation],
    commit_hash: str | None,
) -> None:
    for symbol in symbols:
        sid = stable_id(project_id, repository_id, symbol)
        tx.run(
            f"""
            MERGE (n:{symbol.kind} {{stable_id: $stable_id}})
            SET n.project_id = $project_id,
                n.repository_id = $repository_id,
                n.name = $name,
                n.kind = $kind,
                n.file_path = $file_path,
                n.start_line = $start_line,
                n.end_line = $end_line,
                n.language = $language,
                n.role = $role,
                n.commit_hash = $commit_hash
            """,
            stable_id=sid,
            project_id=project_id,
            repository_id=repository_id,
            name=symbol.name,
            kind=symbol.kind,
            file_path=symbol.file_path,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            language=symbol.language,
            role=symbol.role,
            commit_hash=commit_hash,
        )

    index = {(s.kind, s.name, s.file_path, s.start_line): s for s in symbols}
    for relation in relations:
        source = index.get(
            (relation.from_kind, relation.from_name, relation.from_file, relation.from_start_line)
        )
        target = index.get(
            (relation.to_kind, relation.to_name, relation.to_file, relation.to_start_line)
        )
        if source is None or target is None:
            continue
        from_id = stable_id(project_id, repository_id, source)
        to_id = stable_id(project_id, repository_id, target)
        tx.run(
            f"""
            MATCH (a {{stable_id: $from_id}})
            MATCH (b {{stable_id: $to_id}})
            MERGE (a)-[r:{relation.type}]->(b)
            """,
            from_id=from_id,
            to_id=to_id,
        )


def _count_nodes(tx, project_id: int, repository_id: int) -> int:
    record = tx.run(
        """
        MATCH (n {project_id: $project_id, repository_id: $repository_id})
        RETURN count(n) AS c
        """,
        project_id=project_id,
        repository_id=repository_id,
    ).single()
    return int(record["c"]) if record is not None else 0
