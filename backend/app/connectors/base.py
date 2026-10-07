"""WebsiteConnector interface — checkpoint 3.A.2.

Fourteen methods from AGENTS.md §9. Unsupported methods must raise
`CapabilityNotSupported` — never return None, never no-op.

A subclass that omits a method fails when the class is defined (import
time for a connector module), not when a missing method is later called.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.connectors.capabilities import CapabilityReport
from app.connectors.model import Image, Page, PlatformMetadata, StructuredData


def _method_is_abstract(cls: type, name: str) -> bool:
    """True if `name` is still the abstract stub from this ABC.

    `__abstractmethods__` is not populated yet when `__init_subclass__`
    runs, so we walk the MRO ourselves.
    """

    for klass in cls.__mro__:
        if name in klass.__dict__:
            return bool(getattr(klass.__dict__[name], "__isabstractmethod__", False))
    return True


_REQUIRED_METHODS: tuple[str, ...] = (
    "discover",
    "authenticate",
    "fetch_site_metadata",
    "fetch_pages",
    "fetch_content",
    "fetch_metadata",
    "fetch_schema",
    "fetch_media",
    "update_content",
    "update_metadata",
    "update_schema",
    "create_snapshot",
    "rollback",
    "get_capabilities",
)


class CapabilityNotSupported(Exception):
    """This connector does not have the requested capability.

    Callers must not catch this and report success (AGENTS.md §41, §65).
    """

    def __init__(
        self,
        method: str,
        *,
        platform: str,
        reason: str | None = None,
    ) -> None:
        self.method = method
        self.platform = platform
        self.reason = reason or f"{platform} does not support {method}"
        super().__init__(self.reason)


class WebsiteConnector(ABC):
    """Common website connector. Platform logic stays in subclasses."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        missing = [name for name in _REQUIRED_METHODS if _method_is_abstract(cls, name)]
        if missing:
            raise TypeError(
                f"{cls.__name__} is missing WebsiteConnector methods: "
                + ", ".join(missing)
            )

    @abstractmethod
    def discover(self) -> list[Page]:
        ...

    @abstractmethod
    def authenticate(self) -> None:
        ...

    @abstractmethod
    def fetch_site_metadata(self) -> PlatformMetadata:
        ...

    @abstractmethod
    def fetch_pages(self) -> list[Page]:
        ...

    @abstractmethod
    def fetch_content(self, url: str) -> Page:
        ...

    @abstractmethod
    def fetch_metadata(self, url: str) -> Page:
        ...

    @abstractmethod
    def fetch_schema(self, url: str) -> list[StructuredData]:
        ...

    @abstractmethod
    def fetch_media(self, url: str) -> list[Image]:
        ...

    @abstractmethod
    def update_content(self, url: str, content: str) -> None:
        ...

    @abstractmethod
    def update_metadata(self, url: str, metadata: dict) -> None:
        ...

    @abstractmethod
    def update_schema(self, url: str, schema: list[StructuredData]) -> None:
        ...

    @abstractmethod
    def create_snapshot(self) -> str:
        ...

    @abstractmethod
    def rollback(self, snapshot_id: str) -> None:
        ...

    @abstractmethod
    def get_capabilities(self) -> CapabilityReport:
        ...
