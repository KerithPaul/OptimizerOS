"""Locate a Finding's evidence in the workspace before treating a patch as a fix.

Missing files, empty files, and identity diffs are first-class STOPs
(`ScopeViolation`), not successes. Presence locators (href/src observed
on the live page) must appear in a real target file; their absence is
`implementation_not_located`, never "already fixed."
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app.changes.scope import FileDiff, ScopeViolation, is_path_safe
from app.models.finding import Finding

STATUS_MISSING = "missing"
STATUS_EMPTY = "empty"
STATUS_PRESENT = "present"
STATUS_READ_ONLY = "read_only"

_SITEMAP_SKIP_DIRS = frozenset(
    {"node_modules", ".git", "dist", "build", ".next", "__snapshots__"}
)

# A single-line comment in any language this project touches (JS/TS/JSX,
# Python, HTML). Matched against one diff line with its +/- marker already
# stripped. Deliberately conservative (single-line only) — a false negative
# here just falls through to the Reviewer Agent's judgment, matching the
# "heuristic pre-write gate, not proof of a violation" posture used
# elsewhere in this package.
_COMMENT_ONLY_LINE_RE = re.compile(r"^\s*(//.*|#.*|/\*.*\*/|<!--.*-->)\s*$")


@dataclass(frozen=True)
class WorkspaceFile:
    path: str
    status: str
    content: str


def evidence_locators(finding: Finding) -> list[str]:
    """Deterministic presence markers taken from the Finding's measured evidence.

    Absence findings (missing title/canonical) typically have none of these,
    so the locate gate does not apply to them. Presence findings (broken
    href, image src) do: the live-page value must exist in a workspace file
    we are about to edit.
    """

    found: list[str] = []
    seen: set[str] = set()

    def add(raw: object) -> None:
        if not isinstance(raw, str):
            return
        token = raw.strip()
        if not token or token in seen:
            return
        seen.add(token)
        found.append(token)

    for row in finding.evidence or []:
        if not isinstance(row, dict):
            continue
        value = row.get("value")
        if isinstance(value, dict):
            add(value.get("href"))
            add(value.get("src"))

    resource = finding.affected_resource or ""
    if " -> " in resource:
        add(resource.split(" -> ", 1)[1])
    if " img:" in resource:
        add(resource.split(" img:", 1)[1])

    return found


def list_workspace_sitemap_files(workspace: Path, *, limit: int = 8) -> list[str]:
    """Existing sitemap/robots files the Change Planner should reuse.

    A sitemap finding whose retrieval query is a live URL otherwise
    returns unrelated chunks, and the planner invents
    `server/routes/sitemap.ts` even when `client/public/sitemap.xml`
    is already in the repo.
    """

    if not workspace.is_dir():
        return []
    found: list[str] = []
    for pattern in ("**/sitemap*.xml", "**/robots.txt"):
        for path in sorted(workspace.glob(pattern)):
            if not path.is_file():
                continue
            if any(part in _SITEMAP_SKIP_DIRS for part in path.parts):
                continue
            rel = path.relative_to(workspace).as_posix()
            if rel not in found:
                found.append(rel)
            if len(found) >= limit:
                return found
    return found


def locator_needles(locator: str) -> list[str]:
    """Search strings for one locator: the full value, then its URL path."""

    needles = [locator]
    parsed = urlsplit(locator)
    path = parsed.path or ""
    if path and path != "/" and path not in needles:
        needles.append(path)
    return needles


def locator_in_content(content: str, locator: str) -> bool:
    if not content:
        return False
    return any(needle and needle in content for needle in locator_needles(locator))


def read_workspace_files(workspace: Path, target_files: list[str]) -> list[WorkspaceFile]:
    """Full on-disk content of each target file.

    Never truncated: the content is what edits are applied to and what the
    diff is computed against, so a clipped copy would be written back as a
    clipped file. Prompt size is the Code Agent's concern (`render_file_view`).
    """

    files: list[WorkspaceFile] = []
    for rel in target_files:
        if not is_path_safe(rel):
            files.append(WorkspaceFile(path=rel, status=STATUS_MISSING, content=""))
            continue
        full = workspace / rel
        if not full.is_file():
            files.append(WorkspaceFile(path=rel, status=STATUS_MISSING, content=""))
            continue
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            files.append(WorkspaceFile(path=rel, status=STATUS_MISSING, content=""))
            continue
        if not content.strip():
            files.append(WorkspaceFile(path=rel, status=STATUS_EMPTY, content=content))
        else:
            files.append(WorkspaceFile(path=rel, status=STATUS_PRESENT, content=content))
    return files


def check_locate(
    files: list[WorkspaceFile], locators: list[str]
) -> ScopeViolation | None:
    """Return a STOP when presence evidence cannot be found in the workspace.

    Findings with no locators (typical absence rules) skip this gate so a
    Code Agent can still create or extend a file.
    """

    if not locators:
        return None
    if not files:
        return ScopeViolation("no_files", "the Change Plan named no target files")

    present = [item for item in files if item.status == STATUS_PRESENT]
    empty = [item for item in files if item.status == STATUS_EMPTY]
    missing = [item for item in files if item.status == STATUS_MISSING]

    if not present:
        if empty:
            names = ", ".join(item.path for item in empty)
            return ScopeViolation(
                "target_empty",
                f"target file(s) exist but are empty, so the observed evidence cannot "
                f"be in them: {names}. An empty file does not mean the finding is fixed.",
            )
        names = ", ".join(item.path for item in missing) or "(none)"
        return ScopeViolation(
            "target_missing",
            f"target file(s) do not exist in the workspace: {names}. "
            "A missing file does not mean the finding is fixed.",
        )

    for item in present:
        if any(locator_in_content(item.content, locator) for locator in locators):
            return None

    present_names = ", ".join(item.path for item in present)
    shown = ", ".join(locators[:5])
    return ScopeViolation(
        "implementation_not_located",
        f"none of the existing target files ({present_names}) contain the observed "
        f"evidence locator(s): {shown}. The Code Agent must not claim the issue "
        "is already resolved.",
    )


# Rules whose measured evidence is the live text itself (`value` is the page's
# title / meta description). A fix rewrites that text, so it has to exist in the
# files we are about to edit.
_OBSERVED_TEXT_FIELDS = {
    "SEO-METADESC-LENGTH-001": "meta description",
    "SEO-TITLE-LENGTH-001": "title",
}
_TAIL_PROBE_CHARS = 40
_QUOTE_DASH_TABLE = str.maketrans(
    {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"}
)


def observed_text(finding: Finding) -> tuple[str, str] | None:
    """`(field, live text)` for a title/meta-description length finding, else None."""

    field_name = _OBSERVED_TEXT_FIELDS.get(finding.rule)
    if field_name is None:
        return None
    for row in finding.evidence or []:
        if not isinstance(row, dict) or row.get("confidence") != "direct":
            continue
        value = row.get("value")
        if isinstance(value, str) and value.strip():
            return field_name, value.strip()
    return None


def _normalize_text(text: str) -> str:
    return " ".join(html.unescape(text).translate(_QUOTE_DASH_TABLE).split()).casefold()


def text_in_content(content: str, text: str) -> bool:
    """True when `text` appears in `content`, ignoring whitespace, case, entities, quote/dash style.

    The tail is accepted on its own: it is the distinctive end of a long
    description, and tolerates a source file that wraps or escapes the middle.
    """

    wanted = _normalize_text(text)
    if not wanted:
        return False
    haystack = _normalize_text(content)
    return wanted in haystack or wanted[-_TAIL_PROBE_CHARS:] in haystack


def check_content_in_repository(
    finding: Finding, files: list[WorkspaceFile], cms: str | None
) -> ScopeViolation | None:
    """STOP when a CMS-served page's live text is in none of the target files.

    On a headless-CMS site the repository holds fallback defaults; the text the
    crawler measured came from the CMS. An edit to a file that never contained
    it can only be a guess, so say where the fix has to be made instead.
    """

    observed = observed_text(finding)
    present = [item for item in files if item.status == STATUS_PRESENT]
    if observed is None or cms is None or not present:
        return None
    field_name, text = observed
    if any(text_in_content(item.content, text) for item in present):
        return None
    names = ", ".join(item.path for item in present)
    page = finding.affected_url or finding.affected_resource or "the page"
    return ScopeViolation(
        "content_not_in_repository",
        f"the live {field_name} of {page} is not in the target file(s) ({names}). "
        f"This site loads its content from {cms} at runtime and the repository only holds "
        f"fallback defaults, so change the {field_name} in {cms}; a code patch cannot reach it.",
    )


def _is_substantive_diff_line(line: str) -> bool:
    """True for an added/removed diff line that is more than a comment or blank."""

    text = line[1:]
    if not text.strip():
        return False
    return _COMMENT_ONLY_LINE_RE.match(text) is None


def _has_substantive_change(unified_diff: str) -> bool:
    for line in unified_diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")) and _is_substantive_diff_line(line):
            return True
    return False


def check_noop(diffs: list[FileDiff]) -> ScopeViolation | None:
    """Identity diffs are not a fix, even when the LLM describes them as one.

    Neither is a diff whose only added/removed lines are comments or blank
    lines: a `+// satisfies the no-op check` line passes the raw
    `lines_added + lines_removed` count above zero without implementing
    anything the Finding asked for. `check_noop` must catch that
    deterministically — waiting for the Reviewer Agent to notice means one
    full sandbox-validation-plus-review cycle burned on a patch that was
    never going to be approved.
    """

    if not diffs:
        return ScopeViolation("no_files", "the patch changed no files")
    total = sum(item.lines_added + item.lines_removed for item in diffs)
    if total == 0:
        return ScopeViolation(
            "no_op",
            "the patch does not change any file content; a finding is not "
            "resolved by an identity diff",
        )
    if not any(_has_substantive_change(item.unified_diff) for item in diffs):
        return ScopeViolation(
            "comment_only_diff",
            "the patch only adds or removes comments/blank lines; a finding is not "
            "resolved by a decorative edit that does not implement the "
            "recommended_action",
        )
    return None
