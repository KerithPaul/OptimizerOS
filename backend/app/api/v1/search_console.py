"""Search Console OAuth connection and stored metrics (step 11.1)."""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.access import require_project_access
from app.api.v1.auth import get_current_user
from app.core.config import Settings, get_settings
from app.core.security import SESSION_COOKIE_NAME, read_session_token
from app.db.session import get_db
from app.models.project import Project
from app.models.search import SearchConsoleDimension, SearchConsoleRow
from app.models.user import User
from app.schemas.search import (
    SearchConsoleConnectionIn,
    SearchConsoleConnectionOut,
    SearchConsoleProjectOut,
    SearchConsoleRowOut,
)
from app.services.search_console import (
    DISPLAY_UNAVAILABLE,
    OAUTH_COOKIE_NAME,
    OAUTH_MAX_AGE_SECONDS,
    STATUS_UNAVAILABLE,
    SearchConsoleError,
    build_authorization_url,
    clear_connection,
    credentials_available,
    exchange_authorization_code,
    list_gsc_sites,
    load_oauth_state,
    oauth_configured,
    oauth_redirect_uri,
    pack_oauth_cookie,
    properties_for,
    search_console_connection,
    stored_property_url,
    store_connection,
    store_oauth_tokens,
    unpack_oauth_cookie,
)

router = APIRouter(prefix="/projects/{project_id}/search-console", tags=["search-console"], dependencies=[Depends(require_project_access)])
oauth_router = APIRouter(prefix="/search-console", tags=["search-console"])

# Persist writes page, then query, then page_query. A single id-desc limit
# therefore returns only page_query rows and the UI tables look empty.
LIST_LIMIT_PER_DIMENSION = 100
LIST_LIMIT_SINGLE_DIMENSION = 200


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


def _frontend_search_console_url(project_id: int, settings: Settings, **query: str) -> str:
    origin = (settings.frontend_origin or "http://localhost:2004").rstrip("/")
    qs = urlencode({key: value for key, value in query.items() if value})
    path = f"{origin}/projects/{project_id}/search-console"
    return f"{path}?{qs}" if qs else path


def _connection_out(
    db: Session, project_id: int, *, request: Request | None = None
) -> SearchConsoleConnectionOut:
    settings = get_settings()
    connection = search_console_connection(db, project_id)
    has_stored = connection is not None and connection.credentials_encrypted is not None
    property_url = stored_property_url(db, project_id)
    properties = properties_for(db, project_id)
    available = credentials_available(db, project_id)
    configured = oauth_configured(settings)
    redirect_uri = oauth_redirect_uri(
        settings, request_base_url=str(request.base_url) if request is not None else None
    )
    if available:
        status_value = "ready"
        display = "Search Console: credentials present"
    else:
        status_value = STATUS_UNAVAILABLE
        display = DISPLAY_UNAVAILABLE
    if connection is None:
        return SearchConsoleConnectionOut(
            connected=False,
            has_stored_credentials=False,
            oauth_configured=configured,
            oauth_redirect_uri=redirect_uri,
            property_url=property_url,
            properties=properties,
            status=status_value,
            display=display,
        )
    return SearchConsoleConnectionOut(
        connected=True,
        platform=connection.platform,
        auth_type=connection.auth_type,
        has_stored_credentials=has_stored,
        oauth_configured=configured,
        oauth_redirect_uri=redirect_uri,
        property_url=property_url,
        properties=properties,
        status=status_value,
        display=display,
    )


def _latest_gsc_run_id(db: Session, project_id: int) -> int | None:
    return db.scalar(
        select(func.max(SearchConsoleRow.analysis_run_id)).where(
            SearchConsoleRow.project_id == project_id
        )
    )


def _list_dimension_rows(
    db: Session,
    project_id: int,
    dimension: SearchConsoleDimension,
    *,
    analysis_run_id: int | None,
    limit: int,
) -> list[SearchConsoleRow]:
    stmt = select(SearchConsoleRow).where(
        SearchConsoleRow.project_id == project_id,
        SearchConsoleRow.dimension == dimension,
    )
    if analysis_run_id is not None:
        stmt = stmt.where(SearchConsoleRow.analysis_run_id == analysis_run_id)
    stmt = stmt.order_by(
        SearchConsoleRow.impressions.desc(),
        SearchConsoleRow.id.desc(),
    ).limit(limit)
    return list(db.scalars(stmt))


@router.get("", response_model=SearchConsoleProjectOut)
def get_search_console(
    project_id: int,
    request: Request,
    dimension: SearchConsoleDimension | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SearchConsoleProjectOut:
    _get_project(db, project_id)
    latest_run_id = _latest_gsc_run_id(db, project_id)
    if dimension is not None:
        rows = _list_dimension_rows(
            db,
            project_id,
            dimension,
            analysis_run_id=latest_run_id,
            limit=LIST_LIMIT_SINGLE_DIMENSION,
        )
    else:
        rows = [
            *_list_dimension_rows(
                db,
                project_id,
                SearchConsoleDimension.PAGE,
                analysis_run_id=latest_run_id,
                limit=LIST_LIMIT_PER_DIMENSION,
            ),
            *_list_dimension_rows(
                db,
                project_id,
                SearchConsoleDimension.QUERY,
                analysis_run_id=latest_run_id,
                limit=LIST_LIMIT_PER_DIMENSION,
            ),
        ]
    return SearchConsoleProjectOut(
        connection=_connection_out(db, project_id, request=request),
        rows=[SearchConsoleRowOut.model_validate(row) for row in rows],
    )


@router.get("/oauth/start")
def start_search_console_oauth(
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
    except SearchConsoleError as exc:
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


@router.put("/connection", response_model=SearchConsoleConnectionOut)
def upsert_search_console_connection(
    project_id: int,
    payload: SearchConsoleConnectionIn,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SearchConsoleConnectionOut:
    _get_project(db, project_id)
    try:
        store_connection(db, project_id, property_url=payload.property_url)
    except SearchConsoleError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _connection_out(db, project_id, request=request)


@router.delete("/connection", response_model=SearchConsoleConnectionOut)
def delete_search_console_connection(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SearchConsoleConnectionOut:
    _get_project(db, project_id)
    clear_connection(db, project_id)
    return _connection_out(db, project_id, request=request)


@oauth_router.get("/oauth/callback")
def search_console_oauth_callback(
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
            raise SearchConsoleError("not authenticated")
        if error:
            raise SearchConsoleError(f"Google OAuth denied: {error}")
        if not code or not state:
            raise SearchConsoleError("OAuth callback missing code or state")
        state_data = load_oauth_state(state, settings)
        project_id = int(state_data["project_id"])
        packed = request.cookies.get(OAUTH_COOKIE_NAME)
        if not packed:
            raise SearchConsoleError("OAuth session cookie missing; start Connect with Google again")
        cookie = unpack_oauth_cookie(packed, settings)
        if int(cookie["project_id"]) != project_id or cookie.get("state") != state:
            raise SearchConsoleError("OAuth state mismatch")
        if db.get(Project, project_id) is None:
            raise SearchConsoleError("project not found")
        with httpx.Client(timeout=20.0) as client:
            tokens = exchange_authorization_code(
                code,
                str(cookie["verifier"]),
                settings=settings,
                redirect_uri=str(cookie.get("redirect_uri") or "") or None,
                client=client,
            )
            sites = list_gsc_sites(client, str(tokens["access_token"]))
        store_oauth_tokens(
            db,
            project_id,
            refresh_token=str(tokens["refresh_token"]),
            properties=sites,
            settings=settings,
        )
        redirect = RedirectResponse(
            _frontend_search_console_url(project_id, settings, gsc="connected"),
            status_code=status.HTTP_302_FOUND,
        )
    except SearchConsoleError as exc:
        target_id = project_id if project_id is not None else 0
        if target_id:
            dest = _frontend_search_console_url(target_id, settings, gsc_error=str(exc))
        else:
            dest = f"{(settings.frontend_origin or 'http://localhost:2004').rstrip('/')}/projects"
        redirect = RedirectResponse(dest, status_code=status.HTTP_302_FOUND)
    redirect.delete_cookie(OAUTH_COOKIE_NAME)
    return redirect
