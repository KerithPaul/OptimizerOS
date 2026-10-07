"""Typed request/response models for the GitHub API (step 9.5)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.connectors.capabilities import CapabilityReport
from app.models.github import CiStatus, CommitStatus, PullRequestState
from app.models.project import ProjectMode


class GitHubConnectionIn(BaseModel):
    pat: str | None = Field(default=None, min_length=1, max_length=512)
    full_name: str | None = Field(default=None, min_length=3, max_length=255)


class GitHubConnectionOut(BaseModel):
    connected: bool
    platform: str = "github"
    auth_type: str = "pat"
    has_token: bool
    oauth_configured: bool = False
    oauth_redirect_uri: str = ""
    login: str | None = None
    selected_repo: str | None = None
    capabilities: CapabilityReport | None = None


class GitHubRepoOut(BaseModel):
    full_name: str
    private: bool
    html_url: str | None = None
    clone_url: str | None = None
    default_branch: str = "main"
    push: bool = False


class GitHubRepoListOut(BaseModel):
    login: str | None = None
    page: int
    per_page: int
    has_more: bool
    items: list[GitHubRepoOut]


class GithubCommitOut(BaseModel):
    id: int
    change_set_id: int
    sha: str | None
    branch: str
    message: str
    files_json: list
    status: CommitStatus
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class GithubPullRequestOut(BaseModel):
    id: int
    change_set_id: int
    commit_id: int | None
    number: int | None
    html_url: str | None
    title: str
    body: str
    head_branch: str
    base_branch: str
    status: PullRequestState
    ci_status: CiStatus
    ci_detail_json: dict | None
    error: str | None
    created_at: datetime
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class GitHubProjectOut(BaseModel):
    mode: ProjectMode
    connection: GitHubConnectionOut
    commits: list[GithubCommitOut]
    pull_requests: list[GithubPullRequestOut]


class GitHubPublishOut(BaseModel):
    change_set_id: int
    commit: GithubCommitOut
    pull_request: GithubPullRequestOut | None
    error: str | None = None
    pull_request_skipped_reason: str | None = None
