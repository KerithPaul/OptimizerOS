"""Attach a website, start a crawl, and read persisted pages (3.A.3 / 3.D).

v1 allows one website per project. Credentials are not accepted here
(Phases 9/10). The canned URL-only report is written at attach time so
the website page stays honest. Project mode uses the merged git ∪
website report, so crawling a URL does not lock a git-backed project.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import require_project_access
from app.api.v1.auth import get_current_user
from app.connectors.capabilities import (
    GITHUB_PLATFORM,
    URL_ONLY_AUTH_TYPE,
    WORDPRESS_PLATFORM,
    CapabilityReport,
    ModeNotAllowed,
    UnknownPlatform,
    allowed_modes,
    assert_mode_allowed,
    dump_report,
    project_capability_report,
    report_for_platform,
    report_from_json,
)
from app.db.session import get_db
from app.jobs.queue import enqueue
from app.models.job import Job
from app.models.project import Project
from app.models.repository import Repository
from app.models.user import User
from app.models.website import CrawlRun, PlatformConnection, Website, WebsitePage
from app.schemas.job import JobOut
from app.schemas.website import (
    CrawlRunOut,
    PageDetailOut,
    PageSummaryOut,
    ProjectCapabilitiesOut,
    WebsiteCreate,
    WebsiteOut,
)

router = APIRouter(tags=["websites"])


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


def _has_repository(db: Session, project_id: int) -> bool:
    return (
        db.scalar(select(Repository.id).where(Repository.project_id == project_id))
        is not None
    )


def load_website_capability_report(
    db: Session, project_id: int
) -> CapabilityReport | None:
    """Persisted website-connector report. Does not include git source access."""

    connection = db.scalar(
        select(PlatformConnection).where(
            PlatformConnection.project_id == project_id,
            PlatformConnection.website_id.is_not(None),
        )
    )
    if connection is None:
        return None
    return report_from_json(connection.capabilities_json)


def load_github_capability_report(
    db: Session, project_id: int
) -> CapabilityReport | None:
    connection = db.scalar(
        select(PlatformConnection).where(
            PlatformConnection.project_id == project_id,
            PlatformConnection.platform == GITHUB_PLATFORM,
        )
    )
    if connection is None or not connection.credentials_encrypted:
        return None
    from app.connectors.github.auth import github_capability_report

    return github_capability_report(connection)


def load_capability_report(db: Session, project_id: int) -> CapabilityReport | None:
    """Project-level report used by the mode validator and the mode dropdown."""

    return project_capability_report(
        load_website_capability_report(db, project_id),
        has_repository=_has_repository(db, project_id),
        github_report=load_github_capability_report(db, project_id),
    )


@router.post(
    "/projects/{project_id}/website",
    response_model=WebsiteOut,
    status_code=status.HTTP_201_CREATED,
)
def attach_website(
    project_id: int,
    payload: WebsiteCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> WebsiteOut:
    project = _get_project(db, project_id)
    existing = db.scalar(select(Website).where(Website.project_id == project_id))
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "project already has a website",
        )
    if payload.platform == WORDPRESS_PLATFORM:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "WordPress requires Application Password authentication via "
            "/projects/{id}/wordpress/connection",
        )
    try:
        report = report_for_platform(payload.platform)
    except UnknownPlatform as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    try:
        assert_mode_allowed(
            project.mode,
            project_capability_report(
                report, has_repository=_has_repository(db, project_id)
            ),
        )
    except ModeNotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    website = Website(
        project_id=project_id,
        url=payload.url.strip(),
        platform=payload.platform,
    )
    db.add(website)
    db.flush()
    connection = PlatformConnection(
        website_id=website.id,
        project_id=project_id,
        platform=payload.platform,
        auth_type=URL_ONLY_AUTH_TYPE if payload.platform == "url_only" else payload.platform,
        capabilities_json=dump_report(report),
    )
    db.add(connection)
    db.commit()
    db.refresh(website)
    db.refresh(connection)
    return _website_out(db, website, connection)


@router.get("/projects/{project_id}/website", response_model=WebsiteOut | None)
def get_website(
    project_id: int,
    optional: bool = Query(False, description="Return null instead of 404 when not attached yet"),
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> WebsiteOut | None:
    website, connection = _require_website(db, project_id, optional=optional)
    if website is None:
        return None
    return _website_out(db, website, connection)


@router.post(
    "/projects/{project_id}/website/crawl",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
def start_website_crawl(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> Job:
    _require_website(db, project_id)
    return enqueue(db, project_id, "website_crawl")


@router.get(
    "/projects/{project_id}/website/pages",
    response_model=list[PageSummaryOut],
)
def list_website_pages(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> list[PageSummaryOut]:
    website, _connection = _require_website(db, project_id)
    latest = _latest_crawl(db, website.id)
    if latest is None:
        return []
    rows = db.scalars(
        select(WebsitePage)
        .where(WebsitePage.crawl_run_id == latest.id)
        .order_by(WebsitePage.url)
    )
    return [_page_summary(row) for row in rows]


@router.get(
    "/projects/{project_id}/website/pages/{page_id}",
    response_model=PageDetailOut,
)
def get_website_page(
    project_id: int,
    page_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> PageDetailOut:
    website, _connection = _require_website(db, project_id)
    page = db.get(WebsitePage, page_id)
    if page is None or page.website_id != website.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page not found")
    lighthouse = None
    if page.crawl_run_id is not None:
        run = db.get(CrawlRun, page.crawl_run_id)
        if run is not None and isinstance(run.stats_json, dict):
            for signal in run.stats_json.get("lighthouse") or []:
                if isinstance(signal, dict) and signal.get("url") == page.url:
                    lighthouse = signal
                    break
    summary = _page_summary(page)
    return PageDetailOut(
        **summary.model_dump(),
        model_json=page.model_json,
        lighthouse=lighthouse,
    )


@router.get(
    "/projects/{project_id}/capabilities",
    response_model=ProjectCapabilitiesOut,
)
def get_project_capabilities(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> ProjectCapabilitiesOut:
    _get_project(db, project_id)
    report = load_capability_report(db, project_id)
    return ProjectCapabilitiesOut(report=report, allowed_modes=allowed_modes(report))


def _page_summary(page: WebsitePage) -> PageSummaryOut:
    model = page.model_json if isinstance(page.model_json, dict) else {}
    fetch_ms = model.get("fetch_ms")
    word_count = model.get("word_count")
    return PageSummaryOut(
        id=page.id,
        website_id=page.website_id,
        crawl_run_id=page.crawl_run_id,
        url=page.url,
        status_code=page.status_code,
        title=page.title,
        canonical=page.canonical,
        fetch_ms=fetch_ms if isinstance(fetch_ms, int) else None,
        word_count=word_count if isinstance(word_count, int) else None,
    )


def _require_website(
    db: Session, project_id: int, *, optional: bool = False
) -> tuple[Website | None, PlatformConnection | None]:
    _get_project(db, project_id)
    website = db.scalar(select(Website).where(Website.project_id == project_id))
    if website is None:
        if optional:
            return None, None
        raise HTTPException(status.HTTP_404_NOT_FOUND, "website not found")
    connection = db.scalar(
        select(PlatformConnection).where(PlatformConnection.website_id == website.id)
    )
    if connection is None:
        if optional:
            return None, None
        raise HTTPException(status.HTTP_404_NOT_FOUND, "platform connection not found")
    return website, connection


def _latest_crawl(db: Session, website_id: int) -> CrawlRun | None:
    return db.scalar(
        select(CrawlRun)
        .where(CrawlRun.website_id == website_id)
        .order_by(CrawlRun.id.desc())
        .limit(1)
    )


def _crawl_out(run: CrawlRun) -> CrawlRunOut:
    stats = run.stats_json if isinstance(run.stats_json, dict) else None
    page_count = None
    if stats is not None:
        raw = stats.get("page_count")
        page_count = int(raw) if isinstance(raw, int) else None
    return CrawlRunOut(
        id=run.id,
        website_id=run.website_id,
        project_id=run.project_id,
        status=run.status,
        cap_reason=run.cap_reason,
        error=run.error,
        page_count=page_count,
        stats_json=stats,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _website_out(
    db: Session, website: Website, connection: PlatformConnection
) -> WebsiteOut:
    report = report_from_json(connection.capabilities_json)
    if report is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "platform connection is missing a capability report",
        )
    latest = _latest_crawl(db, website.id)
    return WebsiteOut(
        id=website.id,
        project_id=website.project_id,
        url=website.url,
        platform=website.platform,
        created_at=website.created_at,
        capabilities=report,
        auth_type=connection.auth_type,
        latest_crawl=_crawl_out(latest) if latest is not None else None,
    )
