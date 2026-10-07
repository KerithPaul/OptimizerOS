"""Agent-level GitHub operations (step 9.2, AGENTS.md §47).

MCP-shaped tools: repository inspect, branches, PR inspect, CI status,
issues. Transport is GitHub REST — deterministic backend is cleaner than
forcing every call through an MCP subprocess. Local git stays in
`git_ops.py`. Tool output never includes the PAT.
"""

from __future__ import annotations

from typing import Any

from app.connectors.github.rest import GitHubApiError, GitHubRest
from app.models.github import CiStatus


def inspect_repository(rest: GitHubRest, owner: str, repo: str) -> dict[str, Any]:
    data = rest.get_repository(owner, repo)
    return {
        "full_name": data.get("full_name"),
        "default_branch": data.get("default_branch"),
        "private": data.get("private"),
        "html_url": data.get("html_url"),
        "description": data.get("description"),
    }


def list_branches(rest: GitHubRest, owner: str, repo: str) -> list[dict[str, Any]]:
    rows = rest.list_branches(owner, repo)
    return [
        {"name": row.get("name"), "sha": (row.get("commit") or {}).get("sha")}
        for row in rows
        if isinstance(row, dict)
    ]


def inspect_pull_request(
    rest: GitHubRest, owner: str, repo: str, number: int
) -> dict[str, Any]:
    data = rest.get_pull_request(owner, repo, number)
    return {
        "number": data.get("number"),
        "title": data.get("title"),
        "state": data.get("state"),
        "html_url": data.get("html_url"),
        "head": (data.get("head") or {}).get("ref"),
        "base": (data.get("base") or {}).get("ref"),
        "merged": data.get("merged"),
        "draft": data.get("draft"),
    }


def get_ci_status(rest: GitHubRest, owner: str, repo: str, ref: str) -> dict[str, Any]:
    """Combined commit status + check runs, mapped to ArchitectOS CI status."""

    combined: dict[str, Any] = {}
    checks: dict[str, Any] = {}
    errors: list[str] = []
    try:
        combined = rest.combined_status(owner, repo, ref)
    except GitHubApiError as exc:
        errors.append(str(exc))
    try:
        checks = rest.check_runs(owner, repo, ref)
    except GitHubApiError as exc:
        errors.append(str(exc))

    if not combined and not checks:
        return {
            "status": CiStatus.UNAVAILABLE.value,
            "detail": {"errors": errors},
        }

    check_runs = checks.get("check_runs") if isinstance(checks, dict) else None
    if not isinstance(check_runs, list):
        check_runs = []

    statuses = []
    if isinstance(combined, dict) and combined.get("state"):
        statuses.append(str(combined["state"]).lower())
    for run in check_runs:
        if not isinstance(run, dict):
            continue
        conclusion = run.get("conclusion")
        status = run.get("status")
        if conclusion:
            statuses.append(str(conclusion).lower())
        elif status:
            statuses.append(str(status).lower())

    mapped = _map_ci(statuses)
    return {
        "status": mapped.value,
        "detail": {
            "combined_state": combined.get("state") if isinstance(combined, dict) else None,
            "check_runs": [
                {
                    "name": run.get("name"),
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                }
                for run in check_runs
                if isinstance(run, dict)
            ],
            "errors": errors,
        },
    }


def list_issues(
    rest: GitHubRest, owner: str, repo: str, *, per_page: int = 10
) -> list[dict[str, Any]]:
    rows = rest.list_issues(owner, repo, per_page=per_page)
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("pull_request"):
            continue
        out.append(
            {
                "number": row.get("number"),
                "title": row.get("title"),
                "state": row.get("state"),
                "html_url": row.get("html_url"),
            }
        )
    return out


def _map_ci(states: list[str]) -> CiStatus:
    if not states:
        return CiStatus.UNKNOWN
    failing = {
        "failure",
        "error",
        "cancelled",
        "timed_out",
        "action_required",
        "stale",
    }
    pending = {"pending", "queued", "in_progress", "waiting", "requested", "neutral"}
    if any(state in failing for state in states):
        return CiStatus.FAILURE if "error" not in states else CiStatus.ERROR
    if any(state == "error" for state in states):
        return CiStatus.ERROR
    if any(state in pending for state in states):
        return CiStatus.PENDING
    if all(state in {"success", "skipped", "neutral"} for state in states):
        return CiStatus.SUCCESS
    if all(state == "success" for state in states):
        return CiStatus.SUCCESS
    return CiStatus.UNKNOWN
