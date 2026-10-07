"""Read-only pre-fix inspection of the workspace for one Finding.

This is the guided-fix workflow's STEP 1/2: before any modification is
planned or applied, resolve the files the recommendation is actually
about in the *real* cloned workspace, read their *current* content, and
report honestly whether the recommendation applies to what is on disk.

Everything here is deterministic — no LLM call, no write, no snapshot.
Locating is anchored on the same signals the Change Planner and the
scope/locate gates use:

* `affected_code_entity` when it is a repository-relative path
  (the same pin `app.planners.change.constrain_target_files` applies);
* the Finding's evidence locators (`app.changes.locate.evidence_locators`)
  searched across the workspace source files.

The result feeds the "Current Implementation / Detected Issue / Planned
Fix" panel; the user decides whether to proceed to the existing
`POST .../changes/apply` pipeline, which takes its own immutable
workspace snapshot before any file is written (STEP 3).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from app.changes.locate import (
    STATUS_EMPTY,
    STATUS_MISSING,
    STATUS_PRESENT,
    evidence_locators,
    locator_in_content,
)
from app.changes.scope import is_path_safe
from app.core.config import Settings, get_settings
from app.intelligence.repository.clone import workspace_path
from app.models.finding import Finding
from app.models.repository import CloneStatus, Repository

logger = logging.getLogger("architectos.changes.inspect")

_MAX_CANDIDATE_FILES = 5
_MAX_SEARCH_HITS = 40
_MAX_SEARCH_FILE_BYTES = 1_000_000

# Same exclusion posture as snapshots (app.changes.snapshot): generated,
# dependency, and VCS directories are never searched or shown.
_SEARCH_EXCLUDE = {
    ".git", "node_modules", ".next", ".turbo", "dist", "build",
    ".venv", "__pycache__",
}


@dataclass
class InspectedFile:
    """One candidate file with its current, unmodified content."""

    path: str
    status: str  # present | missing | empty | unsafe
    content: str
    locator_hits: list[str] = field(default_factory=list)


@dataclass
class InspectionResult:
    finding_id: str
    repository_attached: bool
    workspace_ready: bool
    files: list[InspectedFile] = field(default_factory=list)
    locators: list[str] = field(default_factory=list)
    detected_issue: str = ""
    recommended_action: str = ""
    applies: bool = False
    applies_reason: str = ""
    # `code_change` / `code_or_platform_change` / `recommend_only` / … — the
    # existing apply pipeline refuses anything but a code-actionable finding,
    # so the inspection reports it up front instead of letting the user hit
    # the 400 mid-flow.
    actionability: str = ""


def _looks_like_repo_path(value: str | None) -> bool:
    if not value:
        return False
    token = value.replace("\\", "/").strip()
    if not token or token.startswith("/") or "://" in token or ".." in token.split("/"):
        return False
    return "/" in token or "." in token


def _iter_source_files(workspace: Path):
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(workspace).parts
        if any(part in _SEARCH_EXCLUDE for part in rel_parts):
            continue
        try:
            if path.stat().st_size > _MAX_SEARCH_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path


def _search_workspace(workspace: Path, needles: list[str]) -> list[str]:
    """Workspace grep for the evidence locators; repo-relative paths, bounded."""

    if not needles:
        return []
    hits: list[str] = []
    for path in _iter_source_files(workspace):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(needle in text for needle in needles):
            hits.append(path.relative_to(workspace).as_posix())
            if len(hits) >= _MAX_SEARCH_HITS:
                break
    return hits


def inspect_finding_code(
    db,  # Session — unused today, kept for symmetry with the rest of app.changes
    *,
    project_id: int,
    finding: Finding,
    repository: Repository | None,
    settings: Settings | None = None,
) -> InspectionResult:
    """Locate and read the finding's current implementation. Never writes."""

    settings = settings or get_settings()
    result = InspectionResult(
        finding_id=finding.finding_id,
        repository_attached=repository is not None,
        workspace_ready=False,
        locators=evidence_locators(finding),
        detected_issue=finding.observation,
        recommended_action=finding.recommended_action,
        actionability=getattr(finding, "actionability", "") or "",
    )

    if repository is None:
        result.applies_reason = (
            "no repository is attached to this project, so there is no source code to inspect"
        )
        return result
    if repository.clone_status != CloneStatus.CLONED:
        result.applies_reason = (
            f"repository is not cloned yet (clone_status={repository.clone_status.value})"
        )
        return result

    workspace = workspace_path(project_id, repository.id, settings)
    if not workspace.is_dir():
        result.applies_reason = f"workspace directory does not exist: {workspace}"
        return result
    result.workspace_ready = True

    # Candidate 1: the finding's own code entity when it is a repo path —
    # the same anchor `constrain_target_files` pins as the first target.
    candidates: list[str] = []
    entity = (finding.affected_code_entity or "").replace("\\", "/").strip()
    if _looks_like_repo_path(entity) and is_path_safe(entity):
        candidates.append(entity)

    # Candidate 2: real grep hits for the evidence locators / affected URL
    # path in the workspace source files.
    needles = list(result.locators)
    resource = finding.affected_resource or ""
    if " -> " in resource:
        path_part = resource.split(" -> ", 1)[1].strip()
        parsed_ok = path_part and not path_part.startswith("http")
        if parsed_ok and path_part not in needles:
            needles.append(path_part)
    hits = _search_workspace(workspace, needles)
    for hit in hits:
        if hit not in candidates:
            candidates.append(hit)

    # Candidate 3: absence findings pinned to a URL (missing H1, title,
    # canonical — no evidence locators to grep). Map the URL's path onto
    # workspace file paths deterministically: `/blogs` -> source files whose
    # path carries the segment. Exact route files (`blogs/page.tsx`,
    # `blogs/layout.tsx`) beat deeper matches (`blogs/[slug]/page.tsx`) and
    # bare substring hits. A match is a *candidate*, never a certainty —
    # it is read and shown as-is.
    if not candidates and not needles:
        segments = [
            segment.lower()
            for segment in urlsplit(finding.affected_url or "").path.split("/")
            if len(segment) > 2
        ][:2]
        code_exts = {".tsx", ".ts", ".jsx", ".js", ".mdx", ".md", ".html"}
        exact_route: list[str] = []
        deeper: list[str] = []
        for segment in segments:
            for path in _iter_source_files(workspace):
                rel = path.relative_to(workspace).as_posix()
                lowered = rel.lower()
                if segment not in lowered or path.suffix.lower() not in code_exts:
                    continue
                if re.search(rf"(?:^|/){re.escape(segment)}/(?:page|layout|index)\.", lowered):
                    if rel not in exact_route:
                        exact_route.append(rel)
                elif rel not in deeper:
                    deeper.append(rel)
        candidates.extend(exact_route[:_MAX_CANDIDATE_FILES])
        for rel in deeper:
            if len(candidates) >= _MAX_CANDIDATE_FILES:
                break
            if rel not in candidates:
                candidates.append(rel)

    candidates = candidates[:_MAX_CANDIDATE_FILES]

    for rel in candidates:
        if not is_path_safe(rel):
            result.files.append(InspectedFile(path=rel, status="unsafe", content=""))
            continue
        full = workspace / rel
        if not full.is_file():
            result.files.append(InspectedFile(path=rel, status=STATUS_MISSING, content=""))
            continue
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            result.files.append(InspectedFile(path=rel, status=STATUS_MISSING, content=""))
            continue
        if not content.strip():
            result.files.append(InspectedFile(path=rel, status=STATUS_EMPTY, content=content))
            continue
        hits_in_file = [
            locator for locator in result.locators if locator_in_content(content, locator)
        ]
        result.files.append(
            InspectedFile(
                path=rel, status=STATUS_PRESENT, content=content, locator_hits=hits_in_file
            )
        )

    present = [item for item in result.files if item.status == STATUS_PRESENT]
    located = [item for item in present if item.locator_hits]

    if not result.files:
        result.applies_reason = (
            "the affected implementation could not be located in the workspace: no repository-"
            "relative code entity is named by the finding and the evidence locators matched no "
            "source file. The recommendation cannot be applied safely without knowing the file."
        )
        return result

    if result.locators:
        if located:
            result.applies = True
            result.applies_reason = (
                "the observed evidence was located in "
                + ", ".join(item.path for item in located)
                + "; the recommendation applies to the current implementation"
            )
            return result
        if present:
            result.applies_reason = (
                "the target file(s) exist but none of them contain the observed evidence "
                "(" + ", ".join(result.locators[:3]) + "), so the issue may already be "
                "resolved or lives elsewhere. Applying the recommendation as-is would risk "
                "a no-op or a duplicate."
            )
            return result
        result.applies_reason = (
            "the target file(s) do not exist in the workspace; a missing file does not mean "
            "the finding is fixed, but there is nothing to inspect or minimally patch yet"
        )
        return result

    # Absence findings (missing title/canonical/…) carry no locators: the
    # located gate does not apply, a present target file is enough.
    if present:
        result.applies = True
        result.applies_reason = (
            "target file "
            + ", ".join(item.path for item in present)
            + " read from the workspace; this is an absence finding, so there is no "
            "evidence marker to locate and the recommendation can be planned against "
            "the current content"
        )
        return result
    result.applies_reason = (
        "no existing target file could be read; the fix would need to create a new file, "
        "which this inspection cannot confirm in advance"
    )
    return result
