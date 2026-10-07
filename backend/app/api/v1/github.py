"""GitHub OAuth connection, repo picker, commits, PRs, CI, and publish."""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import require_project_access
from app.api.v1.auth import get_current_user
from app.connectors.github.auth import (
    GitHubAuthError,
    clear_pat,
    connection_login,
    github_capability_report,
    github_connection,
    parse_github_repo,
    require_github_token,
    selected_repo,
    store_oauth_tokens,
    store_pat,
    store_selected_repo,
)
from app.connectors.github.git_ops import GitOpsError
from app.connectors.github.oauth import (
    OAUTH_COOKIE_NAME,
    OAUTH_MAX_AGE_SECONDS,
    GitHubOAuthError,
    build_authorization_url,
    exchange_authorization_code,
    load_oauth_state,
    oauth_configured,
    oauth_redirect_uri,
    pack_oauth_cookie,
    unpack_oauth_cookie,
)
from app.connectors.github.publish import PublishError, publish_change_set, refresh_ci
from app.connectors.github.pull_request import PullRequestError
from app.connectors.github.rest import GitHubApiError, GitHubRest, summarize_repository
from app.core.config import Settings, get_settings
from app.core.security import SESSION_COOKIE_NAME, read_session_token
from app.db.session import get_db
from app.models.change import ChangeSet
from app.models.github import GithubCommit, GithubPullRequest
from app.models.project import Project, ProjectMode
from app.models.repository import CloneStatus, Repository
from app.models.user import User
from app.schemas.github import (
    GitHubConnectionIn,
    GitHubConnectionOut,
    GitHubProjectOut,
    GitHubPublishOut,
    GitHubRepoListOut,
    GitHubRepoOut,
    GithubCommitOut,
    GithubPullRequestOut,
)

router = APIRouter(prefix="/projects/{project_id}/github", tags=["github"])
oauth_router = APIRouter(prefix="/github", tags=["github"])


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


def _frontend_github_url(project_id: int, settings: Settings, **query: str) -> str:
    origin = (settings.frontend_origin or "http://localhost:2004").rstrip("/")
    qs = urlencode({key: value for key, value in query.items() if value})
    path = f"{origin}/projects/{project_id}/github"
    return f"{path}?{qs}" if qs else path


def _connection_out(
    connection, *, request: Request | None = None
) -> GitHubConnectionOut:
    settings = get_settings()
    redirect_uri = oauth_redirect_uri(
        settings, request_base_url=str(request.base_url) if request is not None else None
    )
    configured = oauth_configured(settings)
    if connection is None:
        return GitHubConnectionOut(
            connected=False,
            has_token=False,
            oauth_configured=configured,
            oauth_redirect_uri=redirect_uri,
            capabilities=None,
        )
    return GitHubConnectionOut(
        connected=True,
        platform=connection.platform,
        auth_type=connection.auth_type,
        has_token=connection.credentials_encrypted is not None,
        oauth_configured=configured,
        oauth_redirect_uri=redirect_uri,
        login=connection_login(connection),
        selected_repo=selected_repo(connection),
        capabilities=github_capability_report(connection),
    )


def _attach_selected_repository(
    db: Session, project_id: int, repo: dict
) -> Repository:
    full_name = str(repo.get("full_name") or "").strip()
    owner, name = parse_github_repo(full_name)
    clone_url = str(repo.get("clone_url") or f"https://github.com/{owner}/{name}.git")
    default_branch = str(repo.get("default_branch") or "main") or "main"
    existing = db.scalar(select(Repository).where(Repository.project_id == project_id))
    if existing is None:
        repository = Repository(
            project_id=project_id,
            url=clone_url,
            default_branch=default_branch,
        )
        db.add(repository)
        db.commit()
        db.refresh(repository)
        return repository
    try:
        existing_owner, existing_name = parse_github_repo(existing.url)
    except GitHubAuthError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "project already has a repository that is not a GitHub remote",
        ) from exc
    if (existing_owner, existing_name) != (owner, name):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"project already has a repository ({existing_owner}/{existing_name})",
        )
    existing.url = clone_url
    existing.default_branch = default_branch
    db.commit()
    db.refresh(existing)
    return existing


@router.get("", response_model=GitHubProjectOut)
def get_github(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> GitHubProjectOut:
    project = _get_project(db, project_id)
    connection = github_connection(db, project_id)
    commits = list(
        db.scalars(
            select(GithubCommit)
            .where(GithubCommit.project_id == project_id)
            .order_by(GithubCommit.id.desc())
        )
    )
    pull_requests = list(
        db.scalars(
            select(GithubPullRequest)
            .where(GithubPullRequest.project_id == project_id)
            .order_by(GithubPullRequest.id.desc())
        )
    )
    return GitHubProjectOut(
        mode=project.mode,
        connection=_connection_out(connection, request=request),
        commits=[GithubCommitOut.model_validate(row) for row in commits],
        pull_requests=[GithubPullRequestOut.model_validate(row) for row in pull_requests],
    )


@router.get("/oauth/start")
def start_github_oauth(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> RedirectResponse:
    _get_project(db, project_id)
    settings = get_settings()
    redirect_uri = oauth_redirect_uri(settings, request_base_url=str(request.base_url))
    try:
        url, state, verifier, redirect_uri = build_authorization_url(
            project_id, settings=settings, redirect_uri=redirect_uri
        )
    except GitHubOAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    redirect = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        key=OAUTH_COOKIE_NAME,
        value=pack_oauth_cookie(
            project_id, verifier, state, settings, redirect_uri=redirect_uri
        ),
        max_age=OAUTH_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.app_env != "development",
    )
    return redirect


@router.get("/repositories", response_model=GitHubRepoListOut)
def list_github_repositories(
    project_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> GitHubRepoListOut:
    _get_project(db, project_id)
    try:
        connection, token = require_github_token(db, project_id)
    except GitHubAuthError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    try:
        with GitHubRest(token) as rest:
            rows = rest.list_user_repositories(page=page, per_page=per_page)
    except GitHubApiError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"GitHub API failure: {exc}"
        ) from exc
    items = [
        GitHubRepoOut.model_validate(summarize_repository(row))
        for row in rows
        if isinstance(row, dict) and row.get("full_name")
    ]
    return GitHubRepoListOut(
        login=connection_login(connection),
        page=page,
        per_page=per_page,
        has_more=len(items) >= per_page,
        items=items,
    )


@router.put("/connection", response_model=GitHubConnectionOut)
def upsert_github_connection(
    project_id: int,
    payload: GitHubConnectionIn,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> GitHubConnectionOut:
    _get_project(db, project_id)
    pat = (payload.pat or "").strip()
    full_name = (payload.full_name or "").strip()
    if pat and full_name:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "provide pat or full_name, not both",
        )
    if pat:
        try:
            connection = store_pat(db, project_id, pat)
        except GitHubAuthError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return _connection_out(connection, request=request)
    if not full_name:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "provide full_name after OAuth, or a pat as a fallback",
        )
    try:
        _connection, token = require_github_token(db, project_id)
        owner, name = parse_github_repo(full_name)
        with GitHubRest(token) as rest:
            repo = rest.get_repository(owner, name)
        store_selected_repo(db, project_id, str(repo.get("full_name") or full_name))
        _attach_selected_repository(db, project_id, repo)
        connection = github_connection(db, project_id)
    except GitHubAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except GitHubApiError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"GitHub API failure: {exc}"
        ) from exc
    return _connection_out(connection, request=request)


@router.delete("/connection", response_model=GitHubConnectionOut)
def delete_github_connection(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> GitHubConnectionOut:
    _get_project(db, project_id)
    clear_pat(db, project_id)
    return _connection_out(None, request=request)


@oauth_router.get("/oauth/callback")
def github_oauth_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
) -> RedirectResponse:
    settings = get_settings()
    project_id: int | None = None
    try:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        user_id = read_session_token(token, settings) if token else None
        if user_id is None or db.get(User, user_id) is None:
            raise GitHubOAuthError("not authenticated")
        if error:
            raise GitHubOAuthError(f"GitHub OAuth denied: {error}")
        if not code or not state:
            raise GitHubOAuthError("OAuth callback missing code or state")
        state_data = load_oauth_state(state, settings)
        project_id = int(state_data["project_id"])
        packed = request.cookies.get(OAUTH_COOKIE_NAME)
        if not packed:
            raise GitHubOAuthError(
                "OAuth session cookie missing; start Connect with GitHub again"
            )
        cookie = unpack_oauth_cookie(packed, settings)
        if int(cookie["project_id"]) != project_id or cookie.get("state") != state:
            raise GitHubOAuthError("OAuth state mismatch")
        if db.get(Project, project_id) is None:
            raise GitHubOAuthError("project not found")
        with httpx.Client(timeout=20.0) as client:
            tokens = exchange_authorization_code(
                code,
                str(cookie["verifier"]),
                settings=settings,
                redirect_uri=str(cookie.get("redirect_uri") or "") or None,
                client=client,
            )
        login = None
        try:
            with GitHubRest(str(tokens["access_token"])) as rest:
                user = rest.get_authenticated_user()
            if isinstance(user, dict):
                login = user.get("login")
        except GitHubApiError:
            login = None
        store_oauth_tokens(
            db,
            project_id,
            tokens,
            login=str(login) if login else None,
        )
        redirect = RedirectResponse(
            _frontend_github_url(project_id, settings, github="connected"),
            status_code=status.HTTP_302_FOUND,
        )
    except (GitHubOAuthError, GitHubAuthError) as exc:
        target_id = project_id if project_id is not None else 0
        if target_id:
            dest = _frontend_github_url(
                target_id, settings, github_error=str(exc)
            )
        else:
            dest = f"{(settings.frontend_origin or 'http://localhost:2004').rstrip('/')}/projects"
        redirect = RedirectResponse(dest, status_code=status.HTTP_302_FOUND)
    redirect.delete_cookie(OAUTH_COOKIE_NAME)
    return redirect


@router.get("/commits", response_model=list[GithubCommitOut])
def list_commits(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[GithubCommitOut]:
    _get_project(db, project_id)
    rows = list(
        db.scalars(
            select(GithubCommit)
            .where(GithubCommit.project_id == project_id)
            .order_by(GithubCommit.id.desc())
        )
    )
    return [GithubCommitOut.model_validate(row) for row in rows]


@router.get("/pull-requests", response_model=list[GithubPullRequestOut])
def list_pull_requests(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[GithubPullRequestOut]:
    _get_project(db, project_id)
    rows = list(
        db.scalars(
            select(GithubPullRequest)
            .where(GithubPullRequest.project_id == project_id)
            .order_by(GithubPullRequest.id.desc())
        )
    )
    return [GithubPullRequestOut.model_validate(row) for row in rows]


@router.post(
    "/pull-requests/{pull_request_id}/refresh-ci",
    response_model=GithubPullRequestOut,
)
def refresh_pull_request_ci(
    project_id: int,
    pull_request_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> GithubPullRequestOut:
    project = _get_project(db, project_id)
    pull_request = db.get(GithubPullRequest, pull_request_id)
    if pull_request is None or pull_request.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pull request not found")
    repository = db.scalar(select(Repository).where(Repository.project_id == project_id))
    if repository is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no repository attached")
    try:
        refreshed = refresh_ci(
            db, project=project, repository=repository, pull_request=pull_request
        )
    except GitHubAuthError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return GithubPullRequestOut.model_validate(refreshed)


@router.post(
    "/change-sets/{change_set_id}/publish",
    response_model=GitHubPublishOut,
)
def publish_existing_change_set(
    project_id: int,
    change_set_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> GitHubPublishOut:
    """Retry or complete GitHub publish for an already-applied Change Set."""

    project = _get_project(db, project_id)
    if project.mode not in {ProjectMode.COMMIT, ProjectMode.CREATE_PR}:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"project mode is {project.mode.value}; COMMIT or CREATE_PR is required",
        )
    change_set = db.get(ChangeSet, change_set_id)
    if change_set is None or change_set.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change set not found")
    repository = db.scalar(select(Repository).where(Repository.project_id == project_id))
    if repository is None or repository.clone_status != CloneStatus.CLONED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "repository is not attached or not cloned"
        )
    try:
        result = publish_change_set(
            db, project=project, repository=repository, change_set=change_set
        )
    except (
        PublishError,
        GitHubAuthError,
        GitOpsError,
        GitHubApiError,
        PullRequestError,
    ) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"GitHub PR failure: {exc}"
        ) from exc
    return GitHubPublishOut(
        change_set_id=change_set.id,
        commit=GithubCommitOut.model_validate(result.commit),
        pull_request=(
            GithubPullRequestOut.model_validate(result.pull_request)
            if result.pull_request is not None
            else None
        ),
        error=result.error,
        pull_request_skipped_reason=result.pull_request_skipped_reason,
    )
