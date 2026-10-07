"""Turn a reviewed Change Set into one logical commit and optionally one PR."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.github.auth import (
    GitHubAuthError,
    parse_github_repo,
    require_pat,
)
from app.connectors.github.git_ops import GitOpsError, branch_name, commit_and_push
from app.connectors.github.mcp import get_ci_status
from app.connectors.github.pull_request import (
    PullRequestError,
    assert_create_pr_mode,
    build_pr_body,
    create_pull_request,
    pr_title,
)
from app.connectors.github.rest import GitHubApiError, GitHubRest
from app.intelligence.repository.clone import workspace_path
from app.models.change import ChangeSet, ValidationResult, ValidationRun
from app.models.finding import Finding, FindingStatus
from app.models.github import (
    CiStatus,
    CommitStatus,
    GithubCommit,
    GithubPullRequest,
    PullRequestState,
)
from app.models.project import Project, ProjectMode
from app.models.repository import Repository


class PublishError(Exception):
    """GitHub publish failed after the local Change Set was already applied."""


# COMMIT is allowed to push and must not open a pull request. The reason is
# returned on the publish result so a second click is not a silent no-op.
COMMIT_PR_SKIPPED = (
    "Project mode is COMMIT, so this change set was pushed and no pull request "
    "was opened. Set the project mode to CREATE_PR, then publish this change "
    "set again to open one."
)


@dataclass
class PublishResult:
    commit: GithubCommit
    pull_request: GithubPullRequest | None
    error: str | None = None
    pull_request_skipped_reason: str | None = None

    def as_json(self) -> dict:
        pr = self.pull_request
        return {
            "commit_id": self.commit.id,
            "sha": self.commit.sha,
            "branch": self.commit.branch,
            "commit_status": self.commit.status.value,
            "pull_request_id": pr.id if pr is not None else None,
            "pull_request_number": pr.number if pr is not None else None,
            "pull_request_url": pr.html_url if pr is not None else None,
            "pull_request_status": pr.status.value if pr is not None else None,
            "ci_status": pr.ci_status.value if pr is not None else None,
            "error": self.error or (pr.error if pr is not None else None) or self.commit.error,
            "pull_request_skipped_reason": self.pull_request_skipped_reason,
        }


def publish_change_set(
    db: Session,
    *,
    project: Project,
    repository: Repository,
    change_set: ChangeSet,
    job_id: int | None = None,
    rest_factory=GitHubRest,
) -> PublishResult:
    """COMMIT: branch + logical commit + push. CREATE_PR: also open one PR.

    Reviewer PASS is assumed by the caller. Missing PAT, git failure, and
    PR failure are explicit — never converted into a silent success.
    """

    if project.mode not in {ProjectMode.COMMIT, ProjectMode.CREATE_PR}:
        raise PublishError(
            f"project mode is {project.mode.value}; COMMIT or CREATE_PR is required"
        )

    existing_commit = db.scalar(
        select(GithubCommit).where(GithubCommit.change_set_id == change_set.id)
    )
    existing_pr = db.scalar(
        select(GithubPullRequest).where(GithubPullRequest.change_set_id == change_set.id)
    )
    if (
        existing_commit is not None
        and existing_commit.status is CommitStatus.PUSHED
        and (
            project.mode is ProjectMode.COMMIT
            or (
                existing_pr is not None
                and existing_pr.status is not PullRequestState.FAILED
            )
        )
    ):
        # CREATE_PR with no pull request (or only a failed one) falls through
        # and opens a pull request for the branch that COMMIT already pushed.
        return PublishResult(
            commit=existing_commit,
            pull_request=existing_pr,
            pull_request_skipped_reason=(
                COMMIT_PR_SKIPPED if project.mode is ProjectMode.COMMIT else None
            ),
        )

    _connection, token = require_pat(db, project.id)
    files = list(change_set.affected_resources_json or [])
    message = _commit_message(change_set)
    branch = branch_name(change_set.id, change_set.objective or "change")
    workspace = workspace_path(project.id, repository.id)

    commit_row = existing_commit or GithubCommit(
        project_id=project.id,
        repository_id=repository.id,
        change_set_id=change_set.id,
        job_id=job_id,
        branch=branch,
        message=message,
        files_json=files,
        status=CommitStatus.CREATED,
    )
    if existing_commit is None:
        db.add(commit_row)
        db.commit()
        db.refresh(commit_row)

    if existing_commit is not None and existing_commit.status is CommitStatus.PUSHED:
        git_result_sha = existing_commit.sha or ""
        git_result_branch = existing_commit.branch
        if not git_result_sha:
            raise PublishError("previous commit was marked pushed but has no sha")
    else:
        try:
            git_result = commit_and_push(
                workspace,
                files=files,
                message=message,
                branch=branch,
                remote_url=repository.url,
                token=token,
            )
        except (GitOpsError, GitHubAuthError) as exc:
            commit_row.status = CommitStatus.FAILED
            commit_row.error = str(exc)
            db.commit()
            raise PublishError(str(exc)) from exc

        git_result_sha = git_result.sha
        git_result_branch = git_result.branch
        commit_row.sha = git_result.sha
        commit_row.branch = git_result.branch
        commit_row.files_json = list(git_result.files)
        commit_row.status = CommitStatus.PUSHED
        commit_row.error = None
        change_set.git_commit_ref = git_result.sha
        db.commit()

    if project.mode is ProjectMode.COMMIT:
        _mark_findings_fixed(db, project.id, change_set)
        return PublishResult(
            commit=commit_row,
            pull_request=None,
            pull_request_skipped_reason=COMMIT_PR_SKIPPED,
        )

    try:
        assert_create_pr_mode(project.mode)
        owner, repo = parse_github_repo(repository.url)
    except (PullRequestError, GitHubAuthError) as exc:
        pr_row = _failed_pr(
            db,
            project=project,
            repository=repository,
            change_set=change_set,
            commit=commit_row,
            existing=existing_pr,
            title=pr_title(change_set),
            body="",
            branch=git_result_branch,
            error=str(exc),
        )
        raise PublishError(str(exc)) from exc

    findings = _load_findings(db, project.id, change_set)
    validation_run, validation_results = _load_validation(db, change_set)
    body = build_pr_body(
        change_set,
        findings=findings,
        validation_run=validation_run,
        validation_results=validation_results,
        commit_sha=git_result_sha,
        branch=git_result_branch,
    )
    title = pr_title(change_set)

    try:
        with rest_factory(token) as rest:
            payload = create_pull_request(
                rest,
                owner=owner,
                repo=repo,
                title=title,
                body=body,
                head=git_result_branch,
                base=repository.default_branch or "main",
            )
            ci = get_ci_status(rest, owner, repo, git_result_sha)
    except (GitHubApiError, PullRequestError) as exc:
        pr_row = _failed_pr(
            db,
            project=project,
            repository=repository,
            change_set=change_set,
            commit=commit_row,
            existing=existing_pr,
            title=title,
            body=body,
            branch=git_result_branch,
            error=str(exc),
        )
        raise PublishError(str(exc)) from exc

    pr_row = existing_pr or GithubPullRequest(
        project_id=project.id,
        repository_id=repository.id,
        change_set_id=change_set.id,
        commit_id=commit_row.id,
        title=title,
        body=body,
        head_branch=git_result_branch,
        base_branch=repository.default_branch or "main",
        status=PullRequestState.OPEN,
        ci_status=CiStatus.UNKNOWN,
    )
    if existing_pr is None:
        db.add(pr_row)
    pr_row.commit_id = commit_row.id
    pr_row.number = int(payload["number"])
    pr_row.html_url = payload.get("html_url")
    pr_row.title = title
    pr_row.body = body
    pr_row.head_branch = git_result_branch
    pr_row.base_branch = repository.default_branch or "main"
    pr_row.status = PullRequestState.OPEN
    pr_row.error = None
    pr_row.ci_status = CiStatus(ci["status"])
    pr_row.ci_detail_json = ci.get("detail")
    pr_row.updated_at = datetime.now(timezone.utc)
    change_set.pull_request_ref = pr_row.html_url or str(pr_row.number)
    db.commit()
    db.refresh(pr_row)
    _mark_findings_fixed(db, project.id, change_set)
    return PublishResult(commit=commit_row, pull_request=pr_row)


def refresh_ci(
    db: Session,
    *,
    project: Project,
    repository: Repository,
    pull_request: GithubPullRequest,
    rest_factory=GitHubRest,
) -> GithubPullRequest:
    _connection, token = require_pat(db, project.id)
    owner, repo = parse_github_repo(repository.url)
    ref = None
    if pull_request.commit_id is not None:
        commit = db.get(GithubCommit, pull_request.commit_id)
        if commit is not None:
            ref = commit.sha
    ref = ref or pull_request.head_branch
    try:
        with rest_factory(token) as rest:
            ci = get_ci_status(rest, owner, repo, ref)
            if pull_request.number is not None:
                inspected = rest.get_pull_request(owner, repo, pull_request.number)
                state = str(inspected.get("state") or "").lower()
                if inspected.get("merged"):
                    pull_request.status = PullRequestState.MERGED
                elif state == "closed":
                    pull_request.status = PullRequestState.CLOSED
                elif state == "open":
                    pull_request.status = PullRequestState.OPEN
    except (GitHubApiError, GitHubAuthError) as exc:
        pull_request.ci_status = CiStatus.UNAVAILABLE
        pull_request.ci_detail_json = {"errors": [str(exc)]}
        pull_request.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(pull_request)
        return pull_request

    pull_request.ci_status = CiStatus(ci["status"])
    pull_request.ci_detail_json = ci.get("detail")
    pull_request.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(pull_request)
    return pull_request


def _commit_message(change_set: ChangeSet) -> str:
    objective = (change_set.objective or "ArchitectOS change").strip()
    finding_ids = ", ".join(change_set.finding_ids_json or []) or "none"
    return (
        f"{objective}\n\n"
        f"Change Set #{change_set.id}\n"
        f"Findings: {finding_ids}\n"
        "Logical unit — one commit for this Change Set, not one commit per file.\n"
    )


def _load_findings(db: Session, project_id: int, change_set: ChangeSet) -> list[Finding]:
    ids = list(change_set.finding_ids_json or [])
    if not ids:
        return []
    rows = list(
        db.scalars(
            select(Finding)
            .where(Finding.project_id == project_id, Finding.finding_id.in_(ids))
            .order_by(Finding.id.desc())
        )
    )
    seen: set[str] = set()
    out: list[Finding] = []
    for row in rows:
        if row.finding_id in seen:
            continue
        seen.add(row.finding_id)
        out.append(row)
    return out


def _load_validation(
    db: Session, change_set: ChangeSet
) -> tuple[ValidationRun | None, list[ValidationResult]]:
    if change_set.validation_run_id is None:
        return None, []
    run = db.get(ValidationRun, change_set.validation_run_id)
    if run is None:
        return None, []
    results = list(
        db.scalars(
            select(ValidationResult).where(ValidationResult.validation_run_id == run.id)
        )
    )
    return run, results


def _mark_findings_fixed(db: Session, project_id: int, change_set: ChangeSet) -> None:
    for finding in _load_findings(db, project_id, change_set):
        if finding.status is FindingStatus.VALIDATED:
            finding.status = FindingStatus.FIXED
    db.commit()


def _failed_pr(
    db: Session,
    *,
    project: Project,
    repository: Repository,
    change_set: ChangeSet,
    commit: GithubCommit,
    existing: GithubPullRequest | None,
    title: str,
    body: str,
    branch: str,
    error: str,
) -> GithubPullRequest:
    row = existing or GithubPullRequest(
        project_id=project.id,
        repository_id=repository.id,
        change_set_id=change_set.id,
        commit_id=commit.id,
        title=title or pr_title(change_set),
        body=body or "(PR was not opened)",
        head_branch=branch,
        base_branch=repository.default_branch or "main",
        status=PullRequestState.FAILED,
        ci_status=CiStatus.UNAVAILABLE,
        error=error,
    )
    if existing is None:
        db.add(row)
    row.status = PullRequestState.FAILED
    row.error = error
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row
