"""ArchitectOS snapshots of WordPress REST resources (Phase 10).

Git is the wrong rollback mechanism for WordPress content. Snapshots are
JSON copies of authenticated REST resources written under the project
workspace, plus WordPress revision ids when the revisions endpoint exists.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.change import Snapshot, SnapshotReason
from app.models.job import Job

SNAPSHOT_FILENAME = "resources.json"


class WordPressSnapshotError(Exception):
    """The WordPress resources could not be snapshotted. Mutation must not proceed."""


def wordpress_snapshot_root(project_id: int, settings: Settings) -> Path:
    return settings.resolved_workspace_root / str(project_id) / "wordpress_snapshots"


def write_snapshot_payload(
    project_id: int,
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> tuple[str, Path]:
    settings = settings or get_settings()
    snapshot_id = uuid.uuid4().hex
    dest = wordpress_snapshot_root(project_id, settings) / snapshot_id
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / SNAPSHOT_FILENAME
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return snapshot_id, path


def read_snapshot_payload(snapshot: Snapshot) -> dict[str, Any]:
    path = Path(snapshot.snapshot_path)
    if path.is_dir():
        path = path / SNAPSHOT_FILENAME
    if not path.is_file():
        raise WordPressSnapshotError(f"WordPress snapshot missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WordPressSnapshotError("WordPress snapshot is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise WordPressSnapshotError("WordPress snapshot payload is not an object")
    return payload


def record_snapshot(
    db: Session,
    *,
    project_id: int,
    job: Job | None,
    payload: dict[str, Any],
    reason: SnapshotReason = SnapshotReason.BEFORE_CMS_CHANGE,
    settings: Settings | None = None,
) -> Snapshot:
    snapshot_id, path = write_snapshot_payload(project_id, payload, settings=settings)
    resources = payload.get("resources")
    files = [SNAPSHOT_FILENAME]
    if isinstance(resources, list):
        files = [f"{item.get('rest_base')}/{item.get('id')}" for item in resources if isinstance(item, dict)]
    row = Snapshot(
        project_id=project_id,
        repository_id=None,
        job_id=job.id if job is not None else None,
        reason=reason,
        commit_hash=None,
        snapshot_path=str(path.parent),
        files_json=files,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    del snapshot_id
    return row
