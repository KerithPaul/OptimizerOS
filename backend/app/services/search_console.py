"""Google Search Console live API (step 11.1).

Collects queries, pages, impressions, clicks, CTR, position, and date
ranges. Real GSC data outranks LLM opinion `[SPEC]`. Substituting an LLM
guess when GSC is present is forbidden. When credentials are absent the
stage is **unavailable** — not a silent omission and not an invented
number.

CSV/JSON import is not implemented (Q8: live API is the Phase 11
requirement).
"""

from __future__ import annotations

import os
import base64
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt, encrypt
from app.models.finding import Finding
from app.models.search import SearchConsoleDimension, SearchConsoleRow
from app.models.website import PlatformConnection, Website
from app.retrieval.evidence import EvidenceConfidence, EvidenceRow

logger = logging.getLogger("architectos.search_console")

request_base_url = os.getenv("NEXT_PUBLIC_API_BASE_URL")
GSC_PLATFORM = "search_console"
GSC_AUTH_TYPE = "oauth2"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GSC_API_BASE = "https://www.googleapis.com/webmasters/v3"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
GSC_OAUTH_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_COOKIE_NAME = "architectos_gsc_oauth"
OAUTH_MAX_AGE_SECONDS = 600
_OAUTH_SALT = "architectos-gsc-oauth"
# [PROPOSED] GSC Search Analytics window. Spec lists date ranges, not a
# required length; 28 days is the Search Console UI default.
LOOKBACK_DAYS = 28
# Daily series for week/month charts. 90 days covers last-30 vs prior-30.
SERIES_LOOKBACK_DAYS = 90
STATUS_UNAVAILABLE = "unavailable"
STATUS_ATTACHED = "attached"
STATUS_FAILED = "failed"
DISPLAY_UNAVAILABLE = "Search Console: unavailable"
DISPLAY_ATTACHED = "Search Console: attached"
DISPLAY_FAILED = "Search Console: unavailable"
REACH_INPUT_IMPRESSIONS = "impressions"
REACH_INPUT_PAGE_COUNT = "affected_page_count"
GSC_EVIDENCE_SOURCE = "Google Search Console"
GSC_SOURCE_URL = "https://developers.google.com/webmaster-tools/v1/searchanalytics/query"
TOP_QUERIES_PER_PAGE = 5
ROW_LIMIT = 25000


class SearchConsoleError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class SearchConsoleStatus:
    status: str
    display: str
    detail: str
    property_url: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    page_rows: int = 0
    query_rows: int = 0


@dataclass
class SearchConsoleSnapshot:
    status: SearchConsoleStatus
    pages: dict[str, dict[str, Any]] = field(default_factory=dict)
    queries_by_page: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    queries: list[dict[str, Any]] = field(default_factory=list)
    rows: list[SearchConsoleRow] = field(default_factory=list)


def search_console_connection(db: Session, project_id: int) -> PlatformConnection | None:
    return db.scalar(
        select(PlatformConnection).where(
            PlatformConnection.project_id == project_id,
            PlatformConnection.platform == GSC_PLATFORM,
        )
    )


def oauth_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(
        (settings.gsc_oauth_client_id or "").strip()
        and (settings.gsc_oauth_client_secret or "").strip()
    )


def oauth_redirect_uri(
    settings: Settings | None = None, *, request_base_url: str | None = None
) -> str:
    """Redirect URI sent to Google.

    Prefer an explicit `GSC_OAUTH_REDIRECT_URI` (proxied public URL).
    Otherwise derive it from the API request that started OAuth so the
    port/host always match (e.g. localhost:6001 vs the old :8000 default).
    """

    settings = settings or get_settings()
    configured = (settings.gsc_oauth_redirect_uri or "").strip()
    if configured:
        return configured.rstrip("/")
    if request_base_url:
        origin = str(request_base_url).rstrip("/")
        return f"{origin}/api/v1/search-console/oauth/callback"
    return "http://localhost:8000/api/v1/search-console/oauth/callback"


def _oauth_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.app_secret_key, salt=_OAUTH_SALT)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_authorization_url(
    project_id: int,
    *,
    settings: Settings | None = None,
    redirect_uri: str | None = None,
) -> tuple[str, str, str, str]:
    """Return (google_auth_url, signed_state, code_verifier, redirect_uri)."""

    settings = settings or get_settings()
    if not oauth_configured(settings):
        raise SearchConsoleError(
            "GSC OAuth is not configured (set GSC_OAUTH_CLIENT_ID and GSC_OAUTH_CLIENT_SECRET)"
        )
    resolved_redirect = (redirect_uri or oauth_redirect_uri(settings)).strip()
    verifier = _b64url(os.urandom(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    nonce = _b64url(os.urandom(16))
    state = _oauth_serializer(settings).dumps({"project_id": project_id, "nonce": nonce})
    params = {
        "client_id": settings.gsc_oauth_client_id.strip(),
        "redirect_uri": resolved_redirect,
        "response_type": "code",
        "scope": GSC_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{GSC_OAUTH_AUTHORIZE}?{urlencode(params)}", state, verifier, resolved_redirect


def pack_oauth_cookie(
    project_id: int,
    verifier: str,
    state: str,
    settings: Settings,
    *,
    redirect_uri: str,
) -> str:
    payload = _oauth_serializer(settings).dumps(
        {
            "project_id": project_id,
            "verifier": verifier,
            "state": state,
            "redirect_uri": redirect_uri,
        }
    )
    return payload


def unpack_oauth_cookie(token: str, settings: Settings) -> dict[str, Any]:
    try:
        data = _oauth_serializer(settings).loads(token, max_age=OAUTH_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired) as exc:
        raise SearchConsoleError("OAuth session expired; start Connect with Google again") from exc
    if not isinstance(data, dict) or "verifier" not in data or "project_id" not in data:
        raise SearchConsoleError("OAuth session is invalid")
    return data


def load_oauth_state(state: str, settings: Settings) -> dict[str, Any]:
    try:
        data = _oauth_serializer(settings).loads(state, max_age=OAUTH_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired) as exc:
        raise SearchConsoleError("OAuth state expired or invalid") from exc
    if not isinstance(data, dict) or "project_id" not in data:
        raise SearchConsoleError("OAuth state is invalid")
    return data


def exchange_authorization_code(
    code: str,
    verifier: str,
    *,
    settings: Settings | None = None,
    redirect_uri: str | None = None,
    client: httpx.Client,
) -> dict[str, Any]:
    settings = settings or get_settings()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.gsc_oauth_client_id.strip(),
        "client_secret": settings.gsc_oauth_client_secret.strip(),
        "redirect_uri": (redirect_uri or oauth_redirect_uri(settings)).strip(),
        "code_verifier": verifier,
    }
    try:
        response = client.post(DEFAULT_TOKEN_URI, data=data)
    except httpx.HTTPError as exc:
        raise SearchConsoleError(_redact(str(exc))) from exc
    if response.status_code >= 400:
        raise SearchConsoleError(
            f"GSC OAuth token {response.status_code}: {_redact(response.text)}",
            status_code=response.status_code,
        )
    body = response.json()
    refresh = body.get("refresh_token")
    access = body.get("access_token")
    if not access:
        raise SearchConsoleError("GSC OAuth token response had no access_token")
    if not refresh:
        raise SearchConsoleError(
            "Google did not return a refresh token. Disconnect and connect again, "
            "and ensure the OAuth client is a Web application with access_type=offline."
        )
    return body


def list_gsc_sites(client: httpx.Client, token: str) -> list[str]:
    try:
        response = client.get(
            f"{GSC_API_BASE}/sites",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise SearchConsoleError(_redact(str(exc))) from exc
    if response.status_code >= 400:
        raise SearchConsoleError(
            f"GSC sites {response.status_code}: {_redact(response.text)}",
            status_code=response.status_code,
        )
    body = response.json() if response.content else {}
    entries = body.get("siteEntry") or []
    urls: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        site = entry.get("siteUrl")
        if isinstance(site, str) and site.strip():
            urls.append(site.strip())
    return urls


def store_oauth_tokens(
    db: Session,
    project_id: int,
    *,
    refresh_token: str,
    properties: list[str],
    settings: Settings | None = None,
) -> PlatformConnection:
    settings = settings or get_settings()
    blob = {
        "type": "authorized_user",
        "client_id": settings.gsc_oauth_client_id.strip(),
        "client_secret": settings.gsc_oauth_client_secret.strip(),
        "refresh_token": refresh_token,
        "token_uri": DEFAULT_TOKEN_URI,
    }
    ciphertext = encrypt(json.dumps(blob, separators=(",", ":")))
    connection = search_console_connection(db, project_id)
    caps: dict[str, Any] = {}
    if connection is not None and isinstance(connection.capabilities_json, dict):
        caps = dict(connection.capabilities_json)
    caps["properties"] = properties
    if not caps.get("property_url") and len(properties) == 1:
        caps["property_url"] = properties[0]
    if connection is None:
        connection = PlatformConnection(
            website_id=None,
            project_id=project_id,
            platform=GSC_PLATFORM,
            auth_type=GSC_AUTH_TYPE,
            capabilities_json=caps,
            credentials_encrypted=ciphertext,
        )
        db.add(connection)
    else:
        connection.auth_type = GSC_AUTH_TYPE
        connection.capabilities_json = caps
        connection.credentials_encrypted = ciphertext
        flag_modified(connection, "capabilities_json")
    db.commit()
    db.refresh(connection)
    return connection


def store_connection(
    db: Session,
    project_id: int,
    *,
    property_url: str,
) -> PlatformConnection:
    cleaned_property = property_url.strip()
    if not cleaned_property:
        raise SearchConsoleError("GSC property URL must not be empty")
    connection = search_console_connection(db, project_id)
    if connection is None or not connection.credentials_encrypted:
        raise SearchConsoleError(
            "Connect Google Search Console with OAuth before selecting a property"
        )
    caps = dict(connection.capabilities_json or {})
    allowed = caps.get("properties") or []
    if allowed and cleaned_property not in allowed:
        raise SearchConsoleError(
            "property URL is not one of the Search Console properties on this Google account"
        )
    caps["property_url"] = cleaned_property
    connection.auth_type = GSC_AUTH_TYPE
    connection.capabilities_json = caps
    flag_modified(connection, "capabilities_json")
    db.commit()
    db.refresh(connection)
    return connection


def clear_connection(db: Session, project_id: int) -> None:
    connection = search_console_connection(db, project_id)
    if connection is None:
        return
    db.delete(connection)
    db.commit()


def credentials_available(db: Session, project_id: int, settings: Settings | None = None) -> bool:
    connection = search_console_connection(db, project_id)
    return connection is not None and connection.credentials_encrypted is not None


def properties_for(db: Session, project_id: int) -> list[str]:
    connection = search_console_connection(db, project_id)
    if connection is None:
        return []
    raw = (connection.capabilities_json or {}).get("properties")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item.strip()]


def stored_property_url(db: Session, project_id: int) -> str | None:
    """GSC property the user saved (or the sole property auto-selected on OAuth)."""

    connection = search_console_connection(db, project_id)
    if connection is None:
        return None
    stored = (connection.capabilities_json or {}).get("property_url")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return None


def property_url_for(db: Session, project_id: int) -> str | None:
    stored = stored_property_url(db, project_id)
    if stored:
        return stored
    allowed = properties_for(db, project_id)
    website = db.scalar(select(Website).where(Website.project_id == project_id))
    if website is None or not website.url:
        return None
    origin = _origin_url(website.url)
    if not allowed:
        return origin
    if origin in allowed:
        return origin
    origin_key = origin.rstrip("/")
    for item in allowed:
        if item.rstrip("/") == origin_key:
            return item
    return None


def fetch_and_store(
    db: Session,
    project_id: int,
    *,
    analysis_run_id: int | None = None,
    settings: Settings | None = None,
    http_client: httpx.Client | None = None,
    now: datetime | None = None,
) -> SearchConsoleSnapshot:
    """Fetch live GSC Search Analytics and persist `search_console_data` rows.

    Missing credentials → unavailable snapshot, no invented metrics.
    Present credentials that fail → failed snapshot (caller records a gap).
    """

    settings = settings or get_settings()
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=20.0)
    try:
        creds, load_error, configured = _load_credentials(db, project_id, settings)
        property_url = property_url_for(db, project_id)
        if creds is None:
            status_value = STATUS_FAILED if configured else STATUS_UNAVAILABLE
            return SearchConsoleSnapshot(
                status=SearchConsoleStatus(
                    status=status_value,
                    display=DISPLAY_FAILED if configured else DISPLAY_UNAVAILABLE,
                    detail=load_error or "no Search Console credentials",
                    property_url=property_url,
                )
            )
        if not property_url:
            return SearchConsoleSnapshot(
                status=SearchConsoleStatus(
                    status=STATUS_UNAVAILABLE,
                    display=DISPLAY_UNAVAILABLE,
                    detail="no GSC property URL (connect a property or attach a website)",
                )
            )
        try:
            token = access_token_from_credentials(creds, client=client)
            start, end = date_range(now=now)
            series_start, series_end = series_date_range(now=now)
            page_rows = query_search_analytics(
                client, token, property_url, start, end, ["page"]
            )
            query_rows = query_search_analytics(
                client, token, property_url, start, end, ["query"]
            )
            page_query_rows = query_search_analytics(
                client, token, property_url, start, end, ["page", "query"]
            )
            date_rows = _optional_analytics(
                client, token, property_url, series_start, series_end, ["date"]
            )
            device_rows = _optional_analytics(
                client, token, property_url, start, end, ["device"]
            )
            country_rows = _optional_analytics(
                client, token, property_url, start, end, ["country"]
            )
        except SearchConsoleError as exc:
            logger.info("search console fetch failed: %s", _redact(str(exc)))
            return SearchConsoleSnapshot(
                status=SearchConsoleStatus(
                    status=STATUS_FAILED,
                    display=DISPLAY_FAILED,
                    detail=str(exc),
                    property_url=property_url,
                )
            )

        website = db.scalar(select(Website).where(Website.project_id == project_id))
        website_id = website.id if website is not None else None
        fetched_at = now or datetime.now(timezone.utc)
        persisted = _persist_rows(
            db,
            project_id=project_id,
            analysis_run_id=analysis_run_id,
            website_id=website_id,
            start=start,
            end=end,
            fetched_at=fetched_at,
            page_rows=page_rows,
            query_rows=query_rows,
            page_query_rows=page_query_rows,
            date_rows=date_rows,
            device_rows=device_rows,
            country_rows=country_rows,
        )
        snapshot = snapshot_from_rows(persisted, property_url=property_url)
        return snapshot
    finally:
        if owns_client:
            client.close()


def snapshot_from_rows(
    rows: list[SearchConsoleRow], *, property_url: str | None
) -> SearchConsoleSnapshot:
    pages: dict[str, dict[str, Any]] = {}
    queries_by_page: dict[str, list[dict[str, Any]]] = {}
    queries: list[dict[str, Any]] = []
    start = end = None
    for row in rows:
        start = row.start_date.isoformat()
        end = row.end_date.isoformat()
        if row.dimension is SearchConsoleDimension.PAGE and row.page:
            pages[canonical_page_key(row.page)] = _row_metrics(row)
        elif row.dimension is SearchConsoleDimension.QUERY:
            queries.append(_row_metrics(row))
        elif row.dimension is SearchConsoleDimension.PAGE_QUERY and row.page:
            queries_by_page.setdefault(canonical_page_key(row.page), []).append(
                _row_metrics(row)
            )
        # date / device / country rows are stored for reports; they are not
        # mixed into page or query snapshots.
    for key, items in queries_by_page.items():
        items.sort(key=lambda item: item["impressions"], reverse=True)
        queries_by_page[key] = items[:TOP_QUERIES_PER_PAGE]
    return SearchConsoleSnapshot(
        status=SearchConsoleStatus(
            status=STATUS_ATTACHED,
            display=DISPLAY_ATTACHED,
            detail=f"pages={len(pages)} queries={len(queries)}",
            property_url=property_url,
            start_date=start,
            end_date=end,
            page_rows=len(pages),
            query_rows=len(queries),
        ),
        pages=pages,
        queries_by_page=queries_by_page,
        queries=queries,
        rows=rows,
    )


def attach_to_findings(findings: list[Finding], snapshot: SearchConsoleSnapshot) -> None:
    """Append GSC evidence and record the reach input on each finding.

    Does not invent metrics. A finding with no matching GSC page keeps
    `reach_input=affected_page_count` and no GSC evidence row.
    """

    gsc_present = snapshot.status.status == STATUS_ATTACHED
    for finding in findings:
        page_metrics = _metrics_for_finding(finding, snapshot) if gsc_present else None
        if page_metrics is None:
            finding.impressions = None
            finding.reach_input = REACH_INPUT_PAGE_COUNT
            continue
        finding.impressions = int(page_metrics["impressions"])
        finding.reach_input = REACH_INPUT_IMPRESSIONS
        evidence = list(finding.evidence or [])
        evidence.append(
            EvidenceRow(
                source=GSC_EVIDENCE_SOURCE,
                excerpt=_gsc_excerpt(finding, page_metrics),
                selector=page_metrics.get("page"),
                value=page_metrics,
                confidence=EvidenceConfidence.DIRECT,
                source_authority="official_vendor_docs",
            ).model_dump(mode="json")
        )
        finding.evidence = evidence
        flag_modified(finding, "evidence")


def lookup_impressions(finding: Finding, snapshot: SearchConsoleSnapshot) -> int | None:
    metrics = _metrics_for_finding(finding, snapshot)
    if metrics is None:
        return None
    return int(metrics["impressions"])


def date_range(*, now: datetime | None = None) -> tuple[date, date]:
    today = (now or datetime.now(timezone.utc)).date()
    end = today - timedelta(days=1)
    start = end - timedelta(days=LOOKBACK_DAYS - 1)
    return start, end


def series_date_range(*, now: datetime | None = None) -> tuple[date, date]:
    today = (now or datetime.now(timezone.utc)).date()
    end = today - timedelta(days=1)
    start = end - timedelta(days=SERIES_LOOKBACK_DAYS - 1)
    return start, end


def access_token_from_credentials(
    creds: dict[str, Any], *, client: httpx.Client
) -> str:
    cred_type = str(creds.get("type") or "")
    token_uri = str(creds.get("token_uri") or DEFAULT_TOKEN_URI)
    if cred_type != "authorized_user":
        raise SearchConsoleError(
            f"unsupported GSC credentials type: {cred_type or 'missing'} (OAuth refresh token required)"
        )
    data = {
        "grant_type": "refresh_token",
        "client_id": creds.get("client_id") or "",
        "client_secret": creds.get("client_secret") or "",
        "refresh_token": creds.get("refresh_token") or "",
    }
    try:
        response = client.post(token_uri, data=data)
    except httpx.HTTPError as exc:
        raise SearchConsoleError(_redact(str(exc))) from exc
    if response.status_code >= 400:
        raise SearchConsoleError(
            f"GSC token {response.status_code}: {_redact(response.text)}",
            status_code=response.status_code,
        )
    body = response.json()
    token = body.get("access_token")
    if not token:
        raise SearchConsoleError("GSC token response had no access_token")
    return str(token)


def query_search_analytics(
    client: httpx.Client,
    token: str,
    property_url: str,
    start: date,
    end: date,
    dimensions: list[str],
) -> list[dict[str, Any]]:
    encoded = quote(property_url, safe="")
    url = f"{GSC_API_BASE}/sites/{encoded}/searchAnalytics/query"
    rows: list[dict[str, Any]] = []
    start_row = 0
    while True:
        payload = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": dimensions,
            "rowLimit": ROW_LIMIT,
            "startRow": start_row,
        }
        try:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise SearchConsoleError(_redact(str(exc))) from exc
        if response.status_code >= 400:
            raise SearchConsoleError(
                f"GSC searchAnalytics {response.status_code}: {_redact(response.text)}",
                status_code=response.status_code,
            )
        body = response.json() if response.content else {}
        batch = body.get("rows") or []
        rows.extend(batch)
        if len(batch) < ROW_LIMIT:
            break
        start_row += ROW_LIMIT
    return rows


def _optional_analytics(
    client: httpx.Client,
    token: str,
    property_url: str,
    start: date,
    end: date,
    dimensions: list[str],
) -> list[dict[str, Any]]:
    """Extra GSC dimensions. Failure leaves page/query snapshots intact."""

    try:
        return query_search_analytics(client, token, property_url, start, end, dimensions)
    except SearchConsoleError as exc:
        logger.info(
            "optional GSC dimension %s failed: %s",
            ",".join(dimensions),
            _redact(str(exc)),
        )
        return []


def canonical_page_key(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    netloc = (parts.netloc or "").lower()
    scheme = (parts.scheme or "https").lower()
    if netloc:
        return urlunsplit((scheme, netloc, path, "", ""))
    return path.lower()


def _metrics_for_finding(finding: Finding, snapshot: SearchConsoleSnapshot) -> dict[str, Any] | None:
    candidates = [
        finding.affected_url,
        finding.affected_resource,
    ]
    for raw in candidates:
        if not raw:
            continue
        key = canonical_page_key(str(raw))
        if key in snapshot.pages:
            metrics = dict(snapshot.pages[key])
            metrics["queries"] = snapshot.queries_by_page.get(key, [])
            return metrics
        path = urlsplit(str(raw)).path or str(raw)
        path_key = canonical_page_key(path)
        for page_key, page_metrics in snapshot.pages.items():
            if canonical_page_key(urlsplit(page_key).path) == path_key:
                metrics = dict(page_metrics)
                metrics["queries"] = snapshot.queries_by_page.get(page_key, [])
                return metrics
    return None


def _gsc_excerpt(finding: Finding, metrics: dict[str, Any]) -> str:
    page = metrics.get("page") or finding.affected_url or finding.affected_resource
    impressions = metrics["impressions"]
    ctr_pct = float(metrics["ctr"]) * 100
    queries = metrics.get("queries") or []
    query_bit = ""
    if queries:
        top = queries[0]
        query_bit = f"; top query {top.get('query')!r} ({top.get('impressions')} impressions)"
    return (
        f"Page {page}: {impressions} impressions, {ctr_pct:.1f}% CTR, "
        f"avg position {metrics['position']:.1f}{query_bit}"
    )


def _row_metrics(row: SearchConsoleRow) -> dict[str, Any]:
    return {
        "page": row.page,
        "query": row.query,
        "impressions": row.impressions,
        "clicks": row.clicks,
        "ctr": row.ctr,
        "position": row.position,
        "start_date": row.start_date.isoformat(),
        "end_date": row.end_date.isoformat(),
    }


def _persist_rows(
    db: Session,
    *,
    project_id: int,
    analysis_run_id: int | None,
    website_id: int | None,
    start: date,
    end: date,
    fetched_at: datetime,
    page_rows: list[dict[str, Any]],
    query_rows: list[dict[str, Any]],
    page_query_rows: list[dict[str, Any]],
    date_rows: list[dict[str, Any]] | None = None,
    device_rows: list[dict[str, Any]] | None = None,
    country_rows: list[dict[str, Any]] | None = None,
) -> list[SearchConsoleRow]:
    if analysis_run_id is not None:
        existing = list(
            db.scalars(
                select(SearchConsoleRow).where(
                    SearchConsoleRow.analysis_run_id == analysis_run_id
                )
            )
        )
        for row in existing:
            db.delete(row)
        db.flush()
    persisted: list[SearchConsoleRow] = []
    for raw in page_rows:
        keys = raw.get("keys") or [None]
        persisted.append(
            _new_row(
                project_id=project_id,
                analysis_run_id=analysis_run_id,
                website_id=website_id,
                dimension=SearchConsoleDimension.PAGE,
                page=keys[0] if keys else None,
                query=None,
                raw=raw,
                start=start,
                end=end,
                fetched_at=fetched_at,
            )
        )
    for raw in query_rows:
        keys = raw.get("keys") or [None]
        persisted.append(
            _new_row(
                project_id=project_id,
                analysis_run_id=analysis_run_id,
                website_id=website_id,
                dimension=SearchConsoleDimension.QUERY,
                page=None,
                query=keys[0] if keys else None,
                raw=raw,
                start=start,
                end=end,
                fetched_at=fetched_at,
            )
        )
    for raw in page_query_rows:
        keys = raw.get("keys") or [None, None]
        persisted.append(
            _new_row(
                project_id=project_id,
                analysis_run_id=analysis_run_id,
                website_id=website_id,
                dimension=SearchConsoleDimension.PAGE_QUERY,
                page=keys[0] if len(keys) > 0 else None,
                query=keys[1] if len(keys) > 1 else None,
                raw=raw,
                start=start,
                end=end,
                fetched_at=fetched_at,
            )
        )
    for raw in date_rows or []:
        keys = raw.get("keys") or [None]
        day = _parse_gsc_day(keys[0] if keys else None)
        if day is None:
            continue
        persisted.append(
            _new_row(
                project_id=project_id,
                analysis_run_id=analysis_run_id,
                website_id=website_id,
                dimension=SearchConsoleDimension.DATE,
                page=None,
                query=None,
                raw=raw,
                start=day,
                end=day,
                fetched_at=fetched_at,
            )
        )
    for raw in device_rows or []:
        keys = raw.get("keys") or [None]
        persisted.append(
            _new_row(
                project_id=project_id,
                analysis_run_id=analysis_run_id,
                website_id=website_id,
                dimension=SearchConsoleDimension.DEVICE,
                page=None,
                query=str(keys[0]) if keys and keys[0] is not None else None,
                raw=raw,
                start=start,
                end=end,
                fetched_at=fetched_at,
            )
        )
    for raw in country_rows or []:
        keys = raw.get("keys") or [None]
        persisted.append(
            _new_row(
                project_id=project_id,
                analysis_run_id=analysis_run_id,
                website_id=website_id,
                dimension=SearchConsoleDimension.COUNTRY,
                page=None,
                query=str(keys[0]) if keys and keys[0] is not None else None,
                raw=raw,
                start=start,
                end=end,
                fetched_at=fetched_at,
            )
        )
    for row in persisted:
        db.add(row)
    db.flush()
    return persisted


def _new_row(
    *,
    project_id: int,
    analysis_run_id: int | None,
    website_id: int | None,
    dimension: SearchConsoleDimension,
    page: str | None,
    query: str | None,
    raw: dict[str, Any],
    start: date,
    end: date,
    fetched_at: datetime,
) -> SearchConsoleRow:
    return SearchConsoleRow(
        project_id=project_id,
        analysis_run_id=analysis_run_id,
        website_id=website_id,
        dimension=dimension,
        page=page,
        query=query,
        impressions=int(raw.get("impressions") or 0),
        clicks=int(raw.get("clicks") or 0),
        ctr=float(raw.get("ctr") or 0.0),
        position=float(raw.get("position") or 0.0),
        start_date=start,
        end_date=end,
        fetched_at=fetched_at,
    )


def _parse_gsc_day(raw: object) -> date | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _load_credentials(
    db: Session, project_id: int, settings: Settings
) -> tuple[dict[str, Any] | None, str | None, bool]:
    connection = search_console_connection(db, project_id)
    if connection is None or not connection.credentials_encrypted:
        return None, "Google Search Console is not connected (OAuth)", False
    try:
        return _parse_oauth_blob(decrypt(connection.credentials_encrypted)), None, True
    except (ValueError, SearchConsoleError) as exc:
        return None, str(exc), True


def _parse_oauth_blob(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SearchConsoleError("stored GSC OAuth credentials are not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise SearchConsoleError("stored GSC OAuth credentials are invalid")
    if parsed.get("type") != "authorized_user" or not parsed.get("refresh_token"):
        raise SearchConsoleError("stored GSC credentials are not an OAuth refresh token")
    return parsed


def _origin_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def _redact(text: str) -> str:
    if not text:
        return text
    lowered = text
    for needle in ("private_key", "client_secret", "refresh_token", "access_token"):
        if needle in lowered.lower():
            return "redacted GSC credential error"
    return text[:500]
