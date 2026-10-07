"""WebsiteConnector ABC and CapabilityNotSupported (step 3.A.2 verify)."""

import pytest

from app.connectors.base import CapabilityNotSupported, WebsiteConnector
from app.connectors.capabilities import url_only_capabilities
from app.connectors.model import Image, Page, PlatformMetadata, StructuredData


def test_subclass_missing_a_method_fails_at_class_definition() -> None:
    with pytest.raises(TypeError, match="missing WebsiteConnector methods"):
        class IncompleteConnector(WebsiteConnector):
            pass


def test_subclass_missing_only_rollback_names_that_method() -> None:
    with pytest.raises(TypeError, match="rollback"):

        class Almost(_StubMixin, WebsiteConnector):
            # _StubMixin does not define rollback; WebsiteConnector still requires it.
            pass


class _StubMixin:
    def discover(self) -> list[Page]:
        return []

    def authenticate(self) -> None:
        return None

    def fetch_site_metadata(self) -> PlatformMetadata:
        return PlatformMetadata(platform="url_only")

    def fetch_pages(self) -> list[Page]:
        return []

    def fetch_content(self, url: str) -> Page:
        return Page(url=url)

    def fetch_metadata(self, url: str) -> Page:
        return Page(url=url)

    def fetch_schema(self, url: str) -> list[StructuredData]:
        return []

    def fetch_media(self, url: str) -> list[Image]:
        return []

    def update_content(self, url: str, content: str) -> None:
        raise CapabilityNotSupported("update_content", platform="url_only")

    def update_metadata(self, url: str, metadata: dict) -> None:
        raise CapabilityNotSupported("update_metadata", platform="url_only")

    def update_schema(self, url: str, schema: list[StructuredData]) -> None:
        raise CapabilityNotSupported("update_schema", platform="url_only")

    def create_snapshot(self) -> str:
        raise CapabilityNotSupported("create_snapshot", platform="url_only")

    def get_capabilities(self):
        return url_only_capabilities()


class StubUrlOnlyConnector(_StubMixin, WebsiteConnector):
    def rollback(self, snapshot_id: str) -> None:
        raise CapabilityNotSupported("rollback", platform="url_only")


def test_complete_subclass_can_be_instantiated() -> None:
    connector = StubUrlOnlyConnector()
    assert connector.get_capabilities().platform == "url_only"


def test_unsupported_mutation_raises_capability_not_supported() -> None:
    connector = StubUrlOnlyConnector()
    with pytest.raises(CapabilityNotSupported, match="does not support update_metadata") as exc:
        connector.update_metadata("https://example.com/", {"title": "x"})
    assert exc.value.method == "update_metadata"
    assert exc.value.platform == "url_only"
