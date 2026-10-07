"""Authenticated WordPress REST client (Phase 10).

Application Passwords use HTTP Basic auth. Error bodies are redacted
before they leave this module. 401/403 are authentication failures;
every other transport or HTTP error is an API failure.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.connectors.wordpress.auth import WordPressAuthError, redact_secret
from app.intelligence.website.fetch import (
    FetchError,
    LookupFn,
    is_private_host,
    parse_public_url,
)

WP_JSON = "/wp-json/"
WP_V2 = "/wp-json/wp/v2/"


class WordPressApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class WordPressRest:
    def __init__(
        self,
        base_url: str,
        username: str,
        application_password: str,
        *,
        client: httpx.Client | None = None,
        lookup: LookupFn | None = None,
        timeout: float = 20.0,
    ) -> None:
        parsed = parse_public_url(base_url)
        host = parsed.hostname or ""
        if is_private_host(host, lookup=lookup):
            raise WordPressApiError(
                "WordPress site host is private, local, or failed DNS"
            )
        origin = f"{parsed.scheme}://{parsed.netloc}"
        self.origin = origin
        self._username = username
        self._password = application_password.replace(" ", "")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=origin,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "WordPressRest":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return "WordPressRest(redacted)"

    def url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.origin, path)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        url = self.url(path)
        try:
            response = self._client.request(
                method,
                url,
                params=params,
                json=json,
                auth=(self._username, self._password),
            )
        except httpx.HTTPError as exc:
            raise WordPressApiError(
                redact_secret(f"WordPress API failure: {exc}", self._password)
            ) from exc
        if response.status_code in {401, 403}:
            raise WordPressAuthError(
                f"WordPress authentication failed ({response.status_code})"
            )
        if response.status_code >= 400:
            detail = redact_secret(response.text, self._password)
            raise WordPressApiError(
                f"WordPress API failure ({response.status_code}): {detail}",
                status_code=response.status_code,
            )
        return response

    def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = self.request("GET", path, params=params)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise WordPressApiError("WordPress API returned non-JSON") from exc

    def send_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any],
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = self.request(method, path, params=params, json=payload)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise WordPressApiError("WordPress API returned non-JSON") from exc

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        per_page: int = 100,
        max_pages: int = 50,
    ) -> list[Any]:
        query = dict(params or {})
        query.setdefault("per_page", per_page)
        items: list[Any] = []
        page = 1
        while page <= max_pages:
            query["page"] = page
            response = self.request("GET", path, params=query)
            try:
                chunk = response.json() if response.content else []
            except ValueError as exc:
                raise WordPressApiError("WordPress API returned non-JSON") from exc
            if not isinstance(chunk, list):
                raise WordPressApiError("WordPress list endpoint did not return an array")
            items.extend(chunk)
            total_pages = int(response.headers.get("X-WP-TotalPages") or "1")
            if page >= total_pages or not chunk:
                break
            page += 1
        return items

    def options_schema(self, path: str) -> dict[str, Any]:
        response = self.request("OPTIONS", path)
        try:
            body = response.json() if response.content else {}
        except ValueError:
            return {}
        if not isinstance(body, dict):
            return {}
        schema = body.get("schema")
        return schema if isinstance(schema, dict) else {}


def rest_path_is_same_origin(base_url: str, candidate: str) -> bool:
    left = urlsplit(base_url)
    right = urlsplit(candidate)
    return (left.scheme, left.netloc) == (right.scheme, right.netloc)
