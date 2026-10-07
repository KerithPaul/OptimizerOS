"""WordPress WebsiteConnector over REST + Application Passwords (step 10.1)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.connectors.base import CapabilityNotSupported, WebsiteConnector
from app.connectors.capabilities import (
    WORDPRESS_PLATFORM,
    CapabilityReport,
    dump_report,
    wordpress_capabilities,
)
from app.connectors.model import Image, Page, PlatformMetadata, StructuredData
from app.connectors.wordpress.adapters.base import (
    CORE_FIELDS,
    RANKMATH_RAW_KEYS,
    SEO_ALIASES,
    YOAST_RAW_KEYS,
    AdapterFieldError,
    SeoPlugin,
)
from app.connectors.wordpress.adapters.core import CoreAdapter, _rendered_or_raw, resource_rest_path
from app.connectors.wordpress.adapters.rankmath import RankMathAdapter
from app.connectors.wordpress.adapters.yoast import YoastAdapter
from app.connectors.wordpress.auth import (
    WordPressAuthError,
    decrypt_application_password,
    require_application_password,
)
from app.connectors.wordpress.detect import WordPressDetection, detect_wordpress
from app.connectors.wordpress.rest import WordPressApiError, WordPressRest
from app.connectors.wordpress.snapshot import (
    WordPressSnapshotError,
    read_snapshot_payload,
    record_snapshot,
    write_snapshot_payload,
)
from app.core.config import Settings, get_settings
from app.intelligence.website.extract import extract_page
from app.models.change import Snapshot, SnapshotReason
from app.models.job import Job
from app.models.website import PlatformConnection

_EDIT = {"context": "edit"}


class WordPressConnector(WebsiteConnector):
    """REST + Application Password. Plugin fields go only through matching adapters."""

    def __init__(
        self,
        rest: WordPressRest,
        *,
        settings: Settings | None = None,
        project_id: int | None = None,
    ) -> None:
        self.rest = rest
        self.settings = settings or get_settings()
        self.project_id = project_id
        self.detection: WordPressDetection | None = None
        self._resources: list[dict[str, Any]] = []
        self._by_url: dict[str, dict[str, Any]] = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        self._core = CoreAdapter()
        self._yoast = YoastAdapter()
        self._rankmath = RankMathAdapter()
        self._last_snapshot_token: str | None = None

    @classmethod
    def from_credentials(
        cls,
        base_url: str,
        username: str,
        application_password: str,
        *,
        client=None,
        lookup=None,
        settings: Settings | None = None,
        project_id: int | None = None,
    ) -> "WordPressConnector":
        rest = WordPressRest(
            base_url,
            username,
            application_password,
            client=client,
            lookup=lookup,
        )
        return cls(rest, settings=settings, project_id=project_id)

    @classmethod
    def from_connection(
        cls,
        connection: PlatformConnection,
        website_url: str,
        *,
        client=None,
        lookup=None,
        settings: Settings | None = None,
    ) -> "WordPressConnector":
        username, password = decrypt_application_password(connection)
        return cls.from_credentials(
            website_url,
            username,
            password,
            client=client,
            lookup=lookup,
            settings=settings,
            project_id=connection.project_id,
        )

    @classmethod
    def from_project(
        cls,
        db: Session,
        project_id: int,
        website_url: str,
        *,
        client=None,
        lookup=None,
        settings: Settings | None = None,
    ) -> "WordPressConnector":
        _connection, username, password = require_application_password(db, project_id)
        return cls.from_credentials(
            website_url,
            username,
            password,
            client=client,
            lookup=lookup,
            settings=settings,
            project_id=project_id,
        )

    @property
    def seo_plugin(self) -> SeoPlugin:
        if self.detection is None:
            return SeoPlugin.NONE
        return self.detection.seo_plugin

    def authenticate(self) -> None:
        self.detection = detect_wordpress(self.rest)
        try:
            self.rest.get_json("/wp-json/wp/v2/users/me")
        except WordPressAuthError:
            raise
        except WordPressApiError as exc:
            if exc.status_code in {401, 403}:
                raise WordPressAuthError("WordPress authentication failed") from exc
            raise

    def get_capabilities(self) -> CapabilityReport:
        if self.detection is None:
            self.authenticate()
        assert self.detection is not None
        return wordpress_capabilities(
            seo_plugin=self.detection.seo_plugin.value,
            revisions_available=self.detection.revisions_available,
        )

    def fetch_site_metadata(self) -> PlatformMetadata:
        if self.detection is None:
            self.authenticate()
        assert self.detection is not None
        return PlatformMetadata(
            platform=WORDPRESS_PLATFORM,
            site_name=self.detection.site_name,
            home_url=self.rest.origin + "/",
            language=self.detection.language,
        )

    def discover(self) -> list[Page]:
        self._load_resources()
        return [self._page_from_resource(item) for item in self._resources]

    def fetch_pages(self) -> list[Page]:
        return self.discover()

    def fetch_content(self, url: str) -> Page:
        return self._page_from_resource(self._resource(url))

    def fetch_metadata(self, url: str) -> Page:
        return self.fetch_content(url)

    def fetch_schema(self, url: str) -> list[StructuredData]:
        return list(self.fetch_content(url).structured_data)

    def fetch_media(self, url: str) -> list[Image]:
        return list(self.fetch_content(url).images)

    def update_content(self, url: str, content: str) -> None:
        self.update_metadata(url, {"content": content})

    def update_schema(self, url: str, schema: list[StructuredData]) -> None:
        del url, schema
        raise CapabilityNotSupported(
            "update_schema",
            platform=WORDPRESS_PLATFORM,
            reason="WordPress REST does not expose a writable structured-data field",
        )

    def update_metadata(self, url: str, metadata: dict) -> None:
        if not metadata:
            raise AdapterFieldError("update_metadata requires at least one field")
        resource = self._resource(url)
        schema = self._schema_for(resource)
        writes = [self._plan_field(resource, field, value, schema) for field, value in metadata.items()]
        for write in writes:
            self.rest.send_json("POST", write.rest_path, write.payload, params=_EDIT)
        self._refresh_resource(resource)

    def create_snapshot(self) -> str:
        self._load_resources()
        payload = {
            "origin": self.rest.origin,
            "seo_plugin": self.seo_plugin.value,
            "resources": list(self._resources),
        }
        token, _path = write_snapshot_payload(
            self.project_id or 0, payload, settings=self.settings
        )
        self._last_snapshot_token = token
        return token

    def persist_snapshot(
        self, db: Session, *, job: Job | None = None
    ) -> Snapshot:
        self._load_resources()
        if self.project_id is None:
            raise WordPressSnapshotError("WordPress snapshot requires a project_id")
        payload = {
            "origin": self.rest.origin,
            "seo_plugin": self.seo_plugin.value,
            "resources": list(self._resources),
        }
        return record_snapshot(
            db, project_id=self.project_id, job=job, payload=payload, settings=self.settings
        )

    def rollback(self, snapshot_id: str) -> None:
        if self.project_id is None:
            raise WordPressSnapshotError("WordPress rollback requires a project_id")
        root = (
            self.settings.resolved_workspace_root
            / str(self.project_id)
            / "wordpress_snapshots"
            / snapshot_id
        )
        snapshot = Snapshot(
            project_id=self.project_id,
            snapshot_path=str(root),
            reason=SnapshotReason.BEFORE_CMS_CHANGE,
        )
        self.restore_snapshot_row(snapshot)

    def restore_snapshot_row(self, snapshot: Snapshot) -> None:
        payload = read_snapshot_payload(snapshot)
        resources = payload.get("resources")
        if not isinstance(resources, list):
            raise WordPressSnapshotError("WordPress snapshot has no resources")
        for item in resources:
            if not isinstance(item, dict):
                continue
            try:
                path = resource_rest_path(item)
            except AdapterFieldError as exc:
                raise WordPressSnapshotError(str(exc)) from exc
            body = _restore_payload(item)
            if not body:
                continue
            self.rest.send_json("POST", path, body, params=_EDIT)

    def resource_identity(self, url: str) -> dict[str, Any]:
        resource = self._resource(url)
        return {
            "id": resource.get("id"),
            "rest_base": resource.get("_rest_base"),
            "link": resource.get("link"),
        }

    def list_revisions(self, url: str) -> list[dict[str, Any]]:
        resource = self._resource(url)
        path = f"{resource_rest_path(resource)}/revisions"
        try:
            rows = self.rest.paginate(path, params={"per_page": 20})
        except WordPressApiError as exc:
            raise WordPressApiError(
                f"WordPress revisions are unavailable: {exc}", status_code=exc.status_code
            ) from exc
        return rows if isinstance(rows, list) else []

    def restore_revision(self, url: str, revision_id: int) -> None:
        resource = self._resource(url)
        path = f"{resource_rest_path(resource)}/revisions/{revision_id}"
        revision = self.rest.get_json(path)
        if not isinstance(revision, dict):
            raise WordPressApiError("WordPress revision payload is not an object")
        body = _restore_payload(revision)
        if not body:
            raise WordPressApiError("WordPress revision has no restorable fields")
        self.rest.send_json("POST", resource_rest_path(resource), body, params=_EDIT)
        self._refresh_resource(resource)

    def capability_dump(self) -> dict:
        report = self.get_capabilities()
        extra = {
            "seo_plugin": self.seo_plugin.value,
            "namespaces": list(self.detection.namespaces) if self.detection else [],
            "revisions_available": bool(
                self.detection.revisions_available if self.detection else False
            ),
        }
        return {**dump_report(report), **extra}

    def _load_resources(self) -> None:
        if self._resources:
            return
        if self.detection is None:
            self.authenticate()
        resources: list[dict[str, Any]] = []
        for rest_base in self._rest_bases():
            try:
                rows = self.rest.paginate(
                    f"/wp-json/wp/v2/{rest_base}",
                    params={"context": "edit", "status": "publish,draft,private"},
                )
            except WordPressApiError:
                rows = self.rest.paginate(
                    f"/wp-json/wp/v2/{rest_base}",
                    params={"context": "edit"},
                )
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row["_rest_base"] = rest_base
                resources.append(row)
        try:
            media = self.rest.paginate("/wp-json/wp/v2/media", params={"context": "edit"})
        except WordPressApiError:
            media = []
        for row in media:
            if not isinstance(row, dict):
                continue
            row["_rest_base"] = "media"
            resources.append(row)
        self._resources = resources
        self._by_url = {}
        for row in resources:
            link = row.get("link")
            if isinstance(link, str):
                self._by_url[link.rstrip("/") + "/"] = row
                self._by_url[link] = row
            ident = row.get("id")
            rest_base = row.get("_rest_base")
            if ident is not None and rest_base:
                self._by_url[f"{self.rest.origin}/{rest_base}/{ident}"] = row

    def _rest_bases(self) -> list[str]:
        bases = ["pages", "posts"]
        if self.detection is None:
            return bases
        for item in self.detection.types:
            rest_base = item.get("rest_base")
            name = item.get("name")
            if rest_base in {"pages", "posts", "media", "attachments"}:
                continue
            if not item.get("rest_base"):
                continue
            if name in {"attachment", "wp_block", "wp_navigation", "wp_template"}:
                continue
            if isinstance(rest_base, str) and rest_base not in bases:
                bases.append(rest_base)
        return bases

    def _resource(self, url: str) -> dict[str, Any]:
        self._load_resources()
        row = self._by_url.get(url) or self._by_url.get(url.rstrip("/") + "/")
        if row is None:
            raise WordPressApiError(f"WordPress resource not found for {url}")
        return row

    def _refresh_resource(self, resource: dict[str, Any]) -> None:
        path = resource_rest_path(resource)
        fresh = self.rest.get_json(path, params=_EDIT)
        if not isinstance(fresh, dict):
            return
        fresh["_rest_base"] = resource.get("_rest_base")
        ident = resource.get("id")
        for index, row in enumerate(self._resources):
            if row.get("id") == ident and row.get("_rest_base") == resource.get("_rest_base"):
                self._resources[index] = fresh
                break
        self._by_url = {}
        for row in self._resources:
            link = row.get("link")
            if isinstance(link, str):
                self._by_url[link.rstrip("/") + "/"] = row
                self._by_url[link] = row

    def _schema_for(self, resource: dict[str, Any]) -> dict[str, Any]:
        rest_base = str(resource.get("_rest_base") or "")
        if rest_base not in self._schemas:
            try:
                self._schemas[rest_base] = self.rest.options_schema(f"/wp-json/wp/v2/{rest_base}")
            except WordPressApiError:
                self._schemas[rest_base] = {}
        return self._schemas[rest_base]

    def _plan_field(self, resource: dict[str, Any], field: str, value: Any, schema: dict[str, Any]):
        if field in CORE_FIELDS:
            return self._core.plan_write(self.rest, resource, field, value, schema=schema)
        if field in YOAST_RAW_KEYS:
            if self.seo_plugin not in {SeoPlugin.YOAST, SeoPlugin.BOTH}:
                raise AdapterFieldError(
                    "Yoast-only field cannot be written on a Rank Math (or core-only) site"
                )
            return self._yoast.plan_write(self.rest, resource, field, value, schema=schema)
        if field in RANKMATH_RAW_KEYS:
            if self.seo_plugin not in {SeoPlugin.RANKMATH, SeoPlugin.BOTH}:
                raise AdapterFieldError(
                    "Rank Math-only field cannot be written on a Yoast (or core-only) site"
                )
            return self._rankmath.plan_write(self.rest, resource, field, value, schema=schema)
        if field in SEO_ALIASES:
            if self.seo_plugin is SeoPlugin.YOAST:
                return self._yoast.plan_write(self.rest, resource, field, value, schema=schema)
            if self.seo_plugin is SeoPlugin.RANKMATH:
                return self._rankmath.plan_write(self.rest, resource, field, value, schema=schema)
            if self.seo_plugin is SeoPlugin.BOTH:
                raise AdapterFieldError(
                    "both Yoast and Rank Math are installed; use the plugin-specific meta key"
                )
            raise AdapterFieldError(
                f"{field} is not exposed; no SEO plugin adapter is active"
            )
        raise AdapterFieldError(f"unsupported WordPress field {field!r}")

    def _page_from_resource(self, resource: dict[str, Any]) -> Page:
        html = _rendered_or_raw(resource.get("content")) or ""
        link = resource.get("link") if isinstance(resource.get("link"), str) else self.rest.origin + "/"
        extracted = extract_page(html or "<html></html>", url=link, status_code=200)
        page = extracted.page
        title = _rendered_or_raw(resource.get("title")) or page.title
        plugin_meta: dict[str, Any] = {}
        if self.seo_plugin in {SeoPlugin.YOAST, SeoPlugin.BOTH}:
            plugin_meta = self._yoast.read(resource)
        elif self.seo_plugin is SeoPlugin.RANKMATH:
            plugin_meta = self._rankmath.read(resource)
            head_html = resource.get("rank_math_head")
            if isinstance(head_html, str) and head_html.strip():
                wrapped = f"<html><head>{head_html}</head><body></body></html>"
                head_page = extract_page(wrapped, url=link, status_code=200).page
                title = head_page.title or title
                if head_page.meta_description:
                    page = page.model_copy(
                        update={"meta_description": head_page.meta_description}
                    )
                if head_page.canonical:
                    page = page.model_copy(update={"canonical": head_page.canonical})
                if head_page.structured_data:
                    page = page.model_copy(update={"structured_data": head_page.structured_data})
        seo_title = plugin_meta.get("seo_title")
        description = plugin_meta.get("meta_description")
        canonical = plugin_meta.get("canonical")
        schema_block = plugin_meta.get("schema")
        structured = list(page.structured_data)
        if isinstance(schema_block, dict):
            structured.append(
                StructuredData(format="ld+json", type="graph", parsed=schema_block)
            )
        yoast_head = plugin_meta.get("yoast_head_json")
        if isinstance(yoast_head, dict) and not description:
            description = yoast_head.get("description")
        if isinstance(yoast_head, dict) and not canonical:
            canonical = yoast_head.get("canonical")
        if isinstance(yoast_head, dict) and seo_title:
            title = str(seo_title)
        images = list(page.images)
        if resource.get("_rest_base") == "media":
            src = None
            guid = resource.get("source_url") or resource.get("guid")
            if isinstance(guid, dict):
                src = guid.get("rendered")
            elif isinstance(guid, str):
                src = guid
            if src:
                images = [
                    Image(
                        src=src,
                        alt=resource.get("alt_text") if isinstance(resource.get("alt_text"), str) else None,
                    )
                ]
        return page.model_copy(
            update={
                "url": link,
                "title": str(seo_title) if seo_title else title,
                "meta_description": str(description) if description else page.meta_description,
                "canonical": str(canonical) if canonical else page.canonical,
                "content": _rendered_or_raw(resource.get("content")) or page.content,
                "images": images,
                "structured_data": structured,
                "status_code": 200,
                "discovery_source": "wordpress_rest",
            }
        )


def _restore_payload(resource: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for key in ("title", "content", "excerpt", "status"):
        value = resource.get(key)
        if isinstance(value, dict) and "raw" in value:
            body[key] = value["raw"]
        elif isinstance(value, str):
            body[key] = value
    meta = resource.get("meta")
    if isinstance(meta, dict) and meta:
        body["meta"] = meta
    if "alt_text" in resource and isinstance(resource.get("alt_text"), str):
        body["alt_text"] = resource["alt_text"]
    return body
