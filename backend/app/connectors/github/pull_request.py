"""PR creation (step 9.4). One PR per Change Set.

The body links the Change Set, its findings, evidence, and validation.
Gating on Reviewer PASS and mode `CREATE_PR` is the caller's job.
"""

from __future__ import annotations

from app.connectors.github.rest import GitHubRest
from app.models.change import ChangeSet, ValidationResult, ValidationRun
from app.models.finding import Finding
from app.models.project import ProjectMode


class PullRequestError(Exception):
    """PR was not opened. Message is safe to persist and show."""


def assert_create_pr_mode(mode: ProjectMode) -> None:
    if mode is not ProjectMode.CREATE_PR:
        raise PullRequestError(
            f"project mode is {mode.value}; CREATE_PR is required to open a pull request"
        )


def pr_title(change_set: ChangeSet) -> str:
    objective = (change_set.objective or "ArchitectOS change").strip()
    if len(objective) > 70:
        objective = objective[:67].rstrip() + "..."
    return f"ArchitectOS Change Set #{change_set.id}: {objective}"


def build_pr_body(
    change_set: ChangeSet,
    *,
    findings: list[Finding],
    validation_run: ValidationRun | None,
    validation_results: list[ValidationResult],
    commit_sha: str,
    branch: str,
) -> str:
    finding_lines = []
    for finding in findings:
        finding_lines.append(
            f"- `{finding.finding_id}` ({finding.rule}): {finding.observation}"
        )
        for row in finding.evidence or []:
            if not isinstance(row, dict):
                continue
            excerpt = str(row.get("excerpt") or "").strip()
            source = str(row.get("source") or "").strip()
            if excerpt:
                finding_lines.append(f"  - evidence ({source or 'source'}): {excerpt}")
    if not finding_lines:
        for finding_id in change_set.finding_ids_json or []:
            finding_lines.append(f"- `{finding_id}`")

    evidence_block = change_set.evidence_json or []
    evidence_lines = []
    for row in evidence_block:
        if isinstance(row, dict):
            excerpt = str(row.get("excerpt") or "").strip()
            source = str(row.get("source") or "").strip()
            if excerpt:
                evidence_lines.append(f"- ({source or 'source'}) {excerpt}")
        elif row:
            evidence_lines.append(f"- {row}")

    if validation_run is None:
        validation_lines = ["- validation run: not recorded"]
    else:
        validation_lines = [
            f"- validation run #{validation_run.id}: **{validation_run.status.value}**"
        ]
        for result in validation_results:
            detail = f" — {result.detail}" if result.detail else ""
            validation_lines.append(
                f"- `{result.check_type.value}`: {result.status.value}{detail}"
            )

    files = "\n".join(
        f"- `{path}`" for path in (change_set.affected_resources_json or [])
    )

    return "\n".join(
        [
            f"## Change Set #{change_set.id}",
            "",
            (change_set.objective or "").strip() or "(no objective)",
            "",
            "This pull request is one logical unit. It is not one PR per file.",
            "",
            "### Findings",
            *(finding_lines or ["- (none)"]),
            "",
            "### Evidence",
            *(evidence_lines or ["- (see findings)"]),
            "",
            "### Validation",
            *validation_lines,
            "",
            "### Affected resources",
            files or "- (none)",
            "",
            "### Risk",
            (change_set.risk or "").strip() or "(not recorded)",
            "",
            "### Git",
            f"- branch: `{branch}`",
            f"- commit: `{commit_sha}`",
            "",
            "Opened by ArchitectOS. No ranking or traffic outcome is claimed.",
        ]
    )


def create_pull_request(
    rest: GitHubRest,
    *,
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
) -> dict:
    payload = rest.create_pull_request(
        owner, repo, title=title, body=body, head=head, base=base
    )
    if not isinstance(payload, dict) or payload.get("number") is None:
        raise PullRequestError("GitHub returned an empty pull request payload")
    return payload
