"""WordPress connection, pages, revisions, and snapshot rollback (Phase 10)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import require_project_access
from app.api.v1.auth import get_current_user
from app.connectors.capabilities import (
    WORDPRESS_PLATFORM,
    ModeNotAllowed,
    dump_report,
    project_capability_report,
    report_from_json,
)
from app.connectors.wordpress.adapters.base import AdapterFieldError
from app.connectors.wordpress.auth import (
    WordPressAuthError,
    clear_application_password,
    store_application_password,
    wordpress_connection,
)
from app.connectors.wordpress.connector import WordPressConnector
from app.connectors.wordpress.rest import WordPressApiError
from app.intelligence.website.fetch import FetchError
from app.db.session import get_db
from app.models.change import ChangeSet, Snapshot
from app.models.project import Project
from app.models.repository import Repository
from app.models.user import User
from app.models.website import PlatformConnection, Website
from app.schemas.wordpress import (
    WordPressConnectionIn,
    WordPressConnectionOut,
    WordPressPageOut,
    WordPressProjectOut,
    WordPressPublishOut,
    WordPressRevisionOut,
    WordPressSnapshotOut,
)

router = APIRouter(prefix="/projects/{project_id}/wordpress", tags=["wordpress"], dependencies=[Depends(require_project_access)])


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


def _connection_out(
    connection: PlatformConnection | None,
    website: Website | None,
    *,
    seo_plugin: str | None = None,
) -> WordPressConnectionOut:
    if connection is None:
        return WordPressConnectionOut(
            connected=False,
            has_credentials=False,
            website_id=website.id if website is not None else None,
            url=website.url if website is not None else None,
        )
    return WordPressConnectionOut(
        connected=True,
        platform=connection.platform,
        auth_type=connection.auth_type,
        has_credentials=connection.credentials_encrypted is not None,
        website_id=connection.website_id,
        url=website.url if website is not None else None,
        seo_plugin=seo_plugin,
        capabilities=report_from_json(connection.capabilities_json),
    )


def _website(db: Session, project_id: int) -> Website | None:
    return db.scalar(select(Website).where(Website.project_id == project_id))


def _has_repository(db: Session, project_id: int) -> bool:
    return (
        db.scalar(select(Repository.id).where(Repository.project_id == project_id))
        is not None
    )


@router.get("", response_model=WordPressProjectOut)
def get_wordpress(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> WordPressProjectOut:
    _get_project(db, project_id)
    website = _website(db, project_id)
    connection = wordpress_connection(db, project_id)
    pages: list[WordPressPageOut] = []
    seo_plugin = None
    if connection is not None and website is not None and connection.credentials_encrypted:
        try:
            connector = WordPressConnector.from_connection(connection, website.url)
            connector.authenticate()
            seo_plugin = connector.seo_plugin.value
            for page in connector.fetch_pages():
                identity: dict = {}
                try:
                    identity = connector.resource_identity(page.url)
                except WordPressApiError:
                    identity = {}
                pages.append(
                    WordPressPageOut(
                        url=page.url,
                        title=page.title,
                        meta_description=page.meta_description,
                        canonical=page.canonical,
                        rest_base=identity.get("rest_base") if identity else None,
                        wordpress_id=identity.get("id") if identity else None,
                    )
                )
        except (WordPressAuthError, WordPressApiError, FetchError, ValueError):
            pages = []
    return WordPressProjectOut(
        connection=_connection_out(connection, website, seo_plugin=seo_plugin),
        pages=pages,
    )


@router.put("/connection", response_model=WordPressConnectionOut)
def upsert_wordpress_connection(
    project_id: int,
    payload: WordPressConnectionIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> WordPressConnectionOut:
    project = _get_project(db, project_id)
    website = _website(db, project_id)
    if website is not None and website.platform != WORDPRESS_PLATFORM:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "project already has a non-WordPress website",
        )
    try:
        connector = WordPressConnector.from_credentials(
            payload.url.strip(),
            payload.username,
            payload.application_password,
            project_id=project_id,
        )
        connector.authenticate()
        report = connector.get_capabilities()
    except WordPressAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except WordPressApiError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    merged = project_capability_report(
        report, has_repository=_has_repository(db, project_id)
    )
    try:
        from app.connectors.capabilities import assert_mode_allowed

        assert_mode_allowed(project.mode, merged)
    except ModeNotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    if website is None:
        website = Website(
            project_id=project_id,
            url=payload.url.strip(),
            platform=WORDPRESS_PLATFORM,
        )
        db.add(website)
        db.flush()
    else:
        website.url = payload.url.strip()
        website.platform = WORDPRESS_PLATFORM

    connection = store_application_password(
        db,
        project_id=project_id,
        website_id=website.id,
        username=payload.username,
        application_password=payload.application_password,
        capabilities=dump_report(report),
    )
    return _connection_out(connection, website, seo_plugin=connector.seo_plugin.value)


@router.delete("/connection", response_model=WordPressConnectionOut)
def delete_wordpress_connection(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> WordPressConnectionOut:
    _get_project(db, project_id)
    clear_application_password(db, project_id)
    return _connection_out(None, _website(db, project_id))


@router.get("/pages", response_model=list[WordPressPageOut])
def list_wordpress_pages(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[WordPressPageOut]:
    body = get_wordpress(project_id, db, _)
    return body.pages


@router.get("/pages/revisions", response_model=list[WordPressRevisionOut])
def list_revisions(
    project_id: int,
    url: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[WordPressRevisionOut]:
    _get_project(db, project_id)
    website = _website(db, project_id)
    connection = wordpress_connection(db, project_id)
    if website is None or connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WordPress is not connected")
    try:
        connector = WordPressConnector.from_connection(connection, website.url)
        connector.authenticate()
        rows = connector.list_revisions(url)
    except WordPressAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except WordPressApiError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    out: list[WordPressRevisionOut] = []
    for row in rows:
        title = row.get("title")
        if isinstance(title, dict):
            title = title.get("rendered") or title.get("raw")
        out.append(
            WordPressRevisionOut(
                id=int(row.get("id") or 0),
                parent=row.get("parent"),
                date=row.get("date"),
                title=str(title) if title else None,
                author=row.get("author"),
            )
        )
    return out


@router.get("/snapshots", response_model=list[WordPressSnapshotOut])
def list_snapshots(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[WordPressSnapshotOut]:
    _get_project(db, project_id)
    rows = list(
        db.scalars(
            select(Snapshot)
            .where(Snapshot.project_id == project_id, Snapshot.repository_id.is_(None))
            .order_by(Snapshot.id.desc())
        )
    )
    return [WordPressSnapshotOut.model_validate(row) for row in rows]


@router.post(
    "/change-sets/{change_set_id}/publish",
    response_model=WordPressPublishOut,
)
def publish_change_set_to_wordpress(
    project_id: int,
    change_set_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> WordPressPublishOut:
    """COMMIT path retry: already-recorded Change Set fields are re-applied live."""

    _get_project(db, project_id)
    change_set = db.get(ChangeSet, change_set_id)
    if change_set is None or change_set.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change set not found")
    website = _website(db, project_id)
    connection = wordpress_connection(db, project_id)
    if website is None or connection is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "WordPress is not connected")
    from app.models.change import ChangeItem, ChangeTransaction

    try:
        connector = WordPressConnector.from_connection(connection, website.url)
        connector.authenticate()
        snapshot = connector.persist_snapshot(db, job=None)
        for transaction in db.scalars(
            select(ChangeTransaction).where(ChangeTransaction.change_set_id == change_set_id)
        ):
            connector.update_metadata(
                transaction.resource,
                {transaction.field: _content_after(db, transaction.id)},
            )
    except WordPressAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except (WordPressApiError, AdapterFieldError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return WordPressPublishOut(change_set_id=change_set_id, snapshot_id=snapshot.id)


def _content_after(db: Session, transaction_id: int) -> str:
    from app.models.change import ChangeItem

    item = db.scalar(
        select(ChangeItem)
        .where(ChangeItem.change_transaction_id == transaction_id)
        .order_by(ChangeItem.id)
    )
    if item is not None and item.content_after:
        return item.content_after
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST, "change transaction has no content_after"
    )
