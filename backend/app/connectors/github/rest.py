"""GitHub REST client (step 9.2). Deterministic backend operations.

The access token (OAuth or PAT) is sent only as an Authorization header.
Response/error bodies are redacted before they leave this module.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.connectors.github.auth import redact_secret

GITHUB_API_VERSION = "2022-11-28"
DEFAULT_API_BASE = "https://api.github.com"


class GitHubApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class GitHubRest:
    def __init__(self, token: str, *, base_url: str | None = None) -> None:
        if base_url is None:
            from app.core.config import get_settings

            base_url = get_settings().github_api_base_url or DEFAULT_API_BASE
        self._token = token
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": "ArchitectOS",
            },
            timeout=20.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubRest":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return "GitHubRest(redacted)"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubApiError(redact_secret(str(exc), self._token)) from exc
        if response.status_code >= 400:
            detail = redact_secret(response.text, self._token)
            raise GitHubApiError(
                f"GitHub API {response.status_code}: {detail}",
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get_authenticated_user(self) -> dict:
        return self._request("GET", "/user")

    def get_repository(self, owner: str, repo: str) -> dict:
        return self._request("GET", f"/repos/{owner}/{repo}")

    def list_user_repositories(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        affiliation: str = "owner,collaborator,organization_member",
    ) -> list[dict]:
        rows = self._request(
            "GET",
            "/user/repos",
            params={
                "page": page,
                "per_page": per_page,
                "affiliation": affiliation,
                "sort": "full_name",
                "direction": "asc",
            },
        )
        if not isinstance(rows, list):
            return []
        return rows

    def list_branches(self, owner: str, repo: str, *, per_page: int = 30) -> list[dict]:
        return self._request(
            "GET", f"/repos/{owner}/{repo}/branches", params={"per_page": per_page}
        )

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict:
        return self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={"title": title, "body": body, "head": head, "base": base},
        )

    def get_pull_request(self, owner: str, repo: str, number: int) -> dict:
        return self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}")

    def combined_status(self, owner: str, repo: str, ref: str) -> dict:
        return self._request("GET", f"/repos/{owner}/{repo}/commits/{ref}/status")

    def check_runs(self, owner: str, repo: str, ref: str) -> dict:
        return self._request("GET", f"/repos/{owner}/{repo}/commits/{ref}/check-runs")

    def list_issues(
        self, owner: str, repo: str, *, state: str = "open", per_page: int = 10
    ) -> list[dict]:
        return self._request(
            "GET",
            f"/repos/{owner}/{repo}/issues",
            params={"state": state, "per_page": per_page},
        )


def summarize_repository(row: dict) -> dict:
    perms = row.get("permissions") if isinstance(row.get("permissions"), dict) else {}
    full_name = str(row.get("full_name") or "").strip()
    return {
        "full_name": full_name,
        "private": bool(row.get("private")),
        "html_url": row.get("html_url"),
        "clone_url": row.get("clone_url"),
        "default_branch": str(row.get("default_branch") or "main"),
        "push": bool(perms.get("push") or perms.get("admin")),
    }
