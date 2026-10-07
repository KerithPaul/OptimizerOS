"""Common Website Model — checkpoint 3.A.1.

This is the only representation the optimization engine may operate on
(AGENTS.md §8). Platform-specific fields (WordPress, GitHub, plugin
metadata, ClearSite-shaped records) must not appear here. `extra` is
forbidden so a connector cannot smuggle them through.

Core page fields are the §8 tree. Optional fields are the platform-agnostic
observations listed in AGENTS.md §13 / plan step 3.C.1; they stay on this
model so later extraction populates it rather than inventing a second shape.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _CwmModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Heading(_CwmModel):
    level: int = Field(ge=1, le=6)
    text: str


class Link(_CwmModel):
    href: str
    text: str | None = None
    rel: str | None = None
    internal: bool | None = None


class Image(_CwmModel):
    src: str
    alt: str | None = None
    width: int | None = None
    height: int | None = None


class StructuredData(_CwmModel):
    """One structured-data block. Parse failures are recorded, not dropped."""

    format: str | None = None
    type: str | None = None
    raw: str | None = None
    parsed: dict[str, Any] | None = None
    parse_error: str | None = None


class Entity(_CwmModel):
    name: str
    type: str | None = None


class Question(_CwmModel):
    text: str
    answer: str | None = None


class RobotsDirectives(_CwmModel):
    meta: str | None = None
    x_robots_tag: str | None = None


class Hreflang(_CwmModel):
    lang: str
    href: str


class AgentTool(_CwmModel):
    """One WebMCP tool observed on a page. Not an invocation.

    `description_missing`, `input_schema`, and `semantic_annotation` are
    the three gap checks. `structured_response` is recorded on the
    discovery finding and is not itself a gap.
    """

    name: str
    registration: Literal["imperative", "declarative"]
    description: str | None = None
    description_missing: bool = True
    input_schema: bool = False
    semantic_annotation: bool = False
    structured_response: bool = False


class PlatformMetadata(_CwmModel):
    """Site-level facts that are still platform-agnostic.

    Connector-specific keys do not belong here. Capabilities live on
    `CapabilityReport`, not on this model.
    """

    platform: str
    site_name: str | None = None
    home_url: str | None = None
    language: str | None = None


class Page(_CwmModel):
    url: str
    title: str | None = None
    meta_description: str | None = None
    canonical: str | None = None
    robots: RobotsDirectives | None = None
    headings: list[Heading] = Field(default_factory=list)
    content: str | None = None
    links: list[Link] = Field(default_factory=list)
    images: list[Image] = Field(default_factory=list)
    structured_data: list[StructuredData] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    status_code: int | None = None
    redirect_chain: list[str] = Field(default_factory=list)
    language: str | None = None
    hreflang: list[Hreflang] = Field(default_factory=list)
    open_graph: dict[str, str] = Field(default_factory=dict)
    twitter: dict[str, str] = Field(default_factory=dict)
    viewport: str | None = None
    word_count: int | None = None
    sitemap_member: bool | None = None
    crawl_depth: int | None = None
    discovery_source: str | None = None
    raw_html_hash: str | None = None
    agent_tools: list[AgentTool] = Field(default_factory=list)
    rendered_dom_hash: str | None = None
    rendered_text: str | None = None
    fetch_ms: int | None = None


class Website(_CwmModel):
    home_url: str
    pages: list[Page] = Field(default_factory=list)
    platform_metadata: PlatformMetadata
