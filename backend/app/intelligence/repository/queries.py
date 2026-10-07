"""Neighbour queries over the repository graph (step 2.C.3).

what calls X · what routes to X · what imports X · what does X depend on.
"""

from __future__ import annotations

from app.intelligence.repository.graph import GraphError, get_driver


def what_calls(
    project_id: int,
    name: str,
    *,
    repository_id: int | None = None,
) -> list[dict]:
    return _query(
        """
        MATCH (caller)-[:CALLS]->(callee {project_id: $project_id, name: $name})
        WHERE $repository_id IS NULL OR callee.repository_id = $repository_id
        RETURN DISTINCT caller.name AS name, caller.kind AS kind,
               caller.file_path AS file_path, caller.start_line AS start_line,
               caller.end_line AS end_line
        ORDER BY caller.file_path, caller.start_line
        """,
        project_id,
        name,
        repository_id,
    )


def what_routes_to(
    project_id: int,
    target: str,
    *,
    repository_id: int | None = None,
) -> list[dict]:
    return _query(
        """
        MATCH (n)-[:ROUTES_TO]->(route {project_id: $project_id})
        WHERE (route.name = $name OR n.name = $name)
          AND ($repository_id IS NULL OR route.repository_id = $repository_id)
        RETURN DISTINCT n.name AS name, n.kind AS kind,
               n.file_path AS file_path, n.start_line AS start_line,
               n.end_line AS end_line
        ORDER BY n.file_path, n.start_line
        """,
        project_id,
        target,
        repository_id,
    )


def what_imports(
    project_id: int,
    name: str,
    *,
    repository_id: int | None = None,
) -> list[dict]:
    return _query(
        """
        MATCH (n)-[:IMPORTS]->(imported {project_id: $project_id})
        WHERE (imported.name = $name OR imported.file_path ENDS WITH $name
               OR imported.file_path = $name)
          AND ($repository_id IS NULL OR imported.repository_id = $repository_id)
        RETURN DISTINCT n.name AS name, n.kind AS kind,
               n.file_path AS file_path, n.start_line AS start_line,
               n.end_line AS end_line
        ORDER BY n.file_path, n.start_line
        """,
        project_id,
        name,
        repository_id,
    )


def what_depends_on(
    project_id: int,
    name: str,
    *,
    repository_id: int | None = None,
) -> list[dict]:
    return _query(
        """
        MATCH (n {project_id: $project_id, name: $name})-[rel:DEPENDS_ON|IMPORTS|CALLS|USES|QUERIES|ROUTES_TO]->(m)
        WHERE $repository_id IS NULL OR n.repository_id = $repository_id
        RETURN DISTINCT m.name AS name, m.kind AS kind,
               m.file_path AS file_path, m.start_line AS start_line,
               m.end_line AS end_line, type(rel) AS relation
        ORDER BY m.file_path, m.start_line
        """,
        project_id,
        name,
        repository_id,
    )


def list_files(project_id: int, repository_id: int) -> list[dict]:
    return _run(
        """
        MATCH (f:File {project_id: $project_id, repository_id: $repository_id})
        RETURN f.name AS path, f.language AS language
        ORDER BY f.name
        """,
        project_id=project_id,
        repository_id=repository_id,
    )


def list_symbols(project_id: int, repository_id: int) -> list[dict]:
    return _run(
        """
        MATCH (n {project_id: $project_id, repository_id: $repository_id})
        WHERE n.kind IN ['Function', 'Class', 'Component', 'API', 'SEOImplementation']
        RETURN n.name AS name, n.kind AS kind, n.file_path AS file_path,
               n.start_line AS start_line, n.end_line AS end_line, n.role AS role
        ORDER BY n.file_path, n.start_line, n.name
        """,
        project_id=project_id,
        repository_id=repository_id,
    )


def list_routes(project_id: int, repository_id: int) -> list[dict]:
    return _run(
        """
        MATCH (r:Route {project_id: $project_id, repository_id: $repository_id})
        RETURN r.name AS path, r.file_path AS file_path
        ORDER BY r.name
        """,
        project_id=project_id,
        repository_id=repository_id,
    )


def _run(cypher: str, **params: object) -> list[dict]:
    try:
        driver = get_driver()
        with driver.session() as session:
            return [dict(record) for record in session.run(cypher, **params)]
    except GraphError:
        raise
    except Exception as exc:
        raise GraphError(f"Neo4j unavailable: {exc}") from exc


def _query(
    cypher: str,
    project_id: int,
    name: str,
    repository_id: int | None,
) -> list[dict]:
    try:
        driver = get_driver()
        with driver.session() as session:
            records = session.run(
                cypher,
                project_id=project_id,
                name=name,
                repository_id=repository_id,
            )
            return [dict(record) for record in records]
    except GraphError:
        raise
    except Exception as exc:
        raise GraphError(f"Neo4j unavailable: {exc}") from exc
