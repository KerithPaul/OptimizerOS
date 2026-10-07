"""Capability report + mode validator — checkpoint 3.A.3.

The project-level report is the only source the UI and the mode validator
consult (plan step 3.A.3, AGENTS.md §41). A capability the connector does
not have must never be claimed.

URL-only cannot modify, snapshot, or roll back the live site (plan §1.6,
step 3.D.2). That report is about the *website connector*, not the whole
project. A git repository independently unlocks APPLY_LOCALLY / COMMIT /
CREATE_PR because patches target the isolated workspace (AGENTS.md §7
Repository Mode). A URL-only project with no repository may not be raised
above SUGGEST_ONLY.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict

from app.models.project import ProjectMode


class ModificationLevel(str, enum.Enum):
    """Levels used in the AGENTS.md §41 capability matrix."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CapabilityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str
    source_access: bool
    content_access: bool
    metadata_access: bool
    theme_source: bool
    seo_modification: ModificationLevel
    aeo_modification: ModificationLevel
    geo_modification: ModificationLevel
    theme_modification: ModificationLevel
    automatic_rollback: ModificationLevel
    snapshot: bool
    ast_access: bool = False
    git_history: bool = False
    code_modification: ModificationLevel = ModificationLevel.NONE
    pull_request: ModificationLevel = ModificationLevel.NONE

    def allows_modification(self) -> bool:
        return any(
            level is not ModificationLevel.NONE
            for level in (
                self.seo_modification,
                self.aeo_modification,
                self.geo_modification,
                self.theme_modification,
            )
        )


class ModeNotAllowed(Exception):
    """The requested project mode exceeds the persisted capability report."""

    def __init__(self, mode: ProjectMode, report: CapabilityReport) -> None:
        self.mode = mode
        self.report = report
        self.max_mode = ProjectMode.SUGGEST_ONLY
        super().__init__(
            f"{report.platform} cannot modify; {mode.value} is not allowed "
            f"(maximum {self.max_mode.value})"
        )


MODES_REQUIRING_MODIFICATION = frozenset(
    {
        ProjectMode.APPLY_LOCALLY,
        ProjectMode.COMMIT,
        ProjectMode.CREATE_PR,
    }
)

_ALL_MODES: tuple[ProjectMode, ...] = tuple(ProjectMode)
_READ_ONLY_MODES: tuple[ProjectMode, ...] = (
    ProjectMode.AUDIT_ONLY,
    ProjectMode.SUGGEST_ONLY,
)

URL_ONLY_PLATFORM = "url_only"
URL_ONLY_AUTH_TYPE = "none"
GIT_PLATFORM = "git"
GITHUB_PLATFORM = "github"
WORDPRESS_PLATFORM = "wordpress"
WORDPRESS_AUTH_TYPE = "application_password"

KNOWN_PLATFORMS: frozenset[str] = frozenset({URL_ONLY_PLATFORM, WORDPRESS_PLATFORM})

_LEVEL_RANK: dict[ModificationLevel, int] = {
    ModificationLevel.NONE: 0,
    ModificationLevel.LOW: 1,
    ModificationLevel.MEDIUM: 2,
    ModificationLevel.HIGH: 3,
}


def url_only_capabilities() -> CapabilityReport:
    """Honest URL-only report: fetch/extract later, modification never."""

    return CapabilityReport(
        platform=URL_ONLY_PLATFORM,
        source_access=False,
        content_access=True,
        metadata_access=True,
        theme_source=False,
        seo_modification=ModificationLevel.NONE,
        aeo_modification=ModificationLevel.NONE,
        geo_modification=ModificationLevel.NONE,
        theme_modification=ModificationLevel.NONE,
        automatic_rollback=ModificationLevel.NONE,
        snapshot=False,
    )


def git_repository_capabilities() -> CapabilityReport:
    """Git source access: patches go to the isolated workspace, not production."""

    return CapabilityReport(
        platform=GIT_PLATFORM,
        source_access=True,
        content_access=True,
        metadata_access=True,
        theme_source=True,
        seo_modification=ModificationLevel.HIGH,
        aeo_modification=ModificationLevel.HIGH,
        geo_modification=ModificationLevel.HIGH,
        theme_modification=ModificationLevel.HIGH,
        automatic_rollback=ModificationLevel.HIGH,
        snapshot=True,
        ast_access=True,
        git_history=True,
        code_modification=ModificationLevel.HIGH,
        pull_request=ModificationLevel.NONE,
    )


def wordpress_capabilities(
    *,
    seo_plugin: str | None = None,
    revisions_available: bool = True,
) -> CapabilityReport:
    """Honest WordPress REST report (AGENTS.md §41).

    Source and theme are unavailable over REST. Content and metadata
    SEO/AEO/GEO writes are high where core or the detected plugin exposes
    them. Theme modification is low. Automatic rollback is high when
    revisions are available, otherwise medium (ArchitectOS snapshot only).
    CREATE_PR is not a WordPress capability.
    """

    del seo_plugin  # detection is recorded on the connection, not this matrix
    rollback = (
        ModificationLevel.HIGH if revisions_available else ModificationLevel.MEDIUM
    )
    return CapabilityReport(
        platform=WORDPRESS_PLATFORM,
        source_access=False,
        content_access=True,
        metadata_access=True,
        theme_source=False,
        seo_modification=ModificationLevel.HIGH,
        aeo_modification=ModificationLevel.HIGH,
        geo_modification=ModificationLevel.HIGH,
        theme_modification=ModificationLevel.LOW,
        automatic_rollback=rollback,
        snapshot=True,
        ast_access=False,
        git_history=False,
        code_modification=ModificationLevel.NONE,
        pull_request=ModificationLevel.NONE,
    )


def github_token_capabilities() -> CapabilityReport:
    """Token-backed GitHub: source, AST, git history, code, rollback, PR = high."""

    return CapabilityReport(
        platform=GITHUB_PLATFORM,
        source_access=True,
        content_access=True,
        metadata_access=True,
        theme_source=True,
        seo_modification=ModificationLevel.HIGH,
        aeo_modification=ModificationLevel.HIGH,
        geo_modification=ModificationLevel.HIGH,
        theme_modification=ModificationLevel.HIGH,
        automatic_rollback=ModificationLevel.HIGH,
        snapshot=True,
        ast_access=True,
        git_history=True,
        code_modification=ModificationLevel.HIGH,
        pull_request=ModificationLevel.HIGH,
    )


def _max_level(left: ModificationLevel, right: ModificationLevel) -> ModificationLevel:
    return left if _LEVEL_RANK[left] >= _LEVEL_RANK[right] else right


def project_capability_report(
    website_report: CapabilityReport | None,
    *,
    has_repository: bool,
    github_report: CapabilityReport | None = None,
) -> CapabilityReport | None:
    """Merge website-connector capabilities with git source access.

    The persisted PlatformConnection report stays website-honest. Project
    mode uses this merged view so attaching a URL for crawl does not lock
    a git-backed project into AUDIT_ONLY / SUGGEST_ONLY. A GitHub PAT
    raises pull-request capability to high without rewriting the website
    report.
    """

    reports: list[CapabilityReport] = []
    if has_repository:
        reports.append(git_repository_capabilities())
    if github_report is not None:
        reports.append(github_report)
    if website_report is not None:
        reports.append(website_report)
    if not reports:
        return None
    merged = reports[0]
    for extra in reports[1:]:
        merged = _merge_reports(merged, extra)
    return merged


def _merge_reports(left: CapabilityReport, right: CapabilityReport) -> CapabilityReport:
    return CapabilityReport(
        platform=f"{left.platform}+{right.platform}",
        source_access=left.source_access or right.source_access,
        content_access=left.content_access or right.content_access,
        metadata_access=left.metadata_access or right.metadata_access,
        theme_source=left.theme_source or right.theme_source,
        seo_modification=_max_level(left.seo_modification, right.seo_modification),
        aeo_modification=_max_level(left.aeo_modification, right.aeo_modification),
        geo_modification=_max_level(left.geo_modification, right.geo_modification),
        theme_modification=_max_level(
            left.theme_modification, right.theme_modification
        ),
        automatic_rollback=_max_level(
            left.automatic_rollback, right.automatic_rollback
        ),
        snapshot=left.snapshot or right.snapshot,
        ast_access=left.ast_access or right.ast_access,
        git_history=left.git_history or right.git_history,
        code_modification=_max_level(
            left.code_modification, right.code_modification
        ),
        pull_request=_max_level(left.pull_request, right.pull_request),
    )


def report_for_platform(platform: str) -> CapabilityReport:
    """Return the canned report for a known platform.

    GitHub is a project-level PAT connection, not a website platform.
    Unknown platforms are rejected rather than guessed. The canned
    WordPress report is the typical REST matrix; a live connection
    overwrites it after plugin and revision detection.
    """

    if platform == URL_ONLY_PLATFORM:
        return url_only_capabilities()
    if platform == WORDPRESS_PLATFORM:
        return wordpress_capabilities()
    raise UnknownPlatform(platform)


class UnknownPlatform(Exception):
    def __init__(self, platform: str) -> None:
        self.platform = platform
        super().__init__(
            f"unsupported platform: {platform} "
            f"(known: {', '.join(sorted(KNOWN_PLATFORMS))})"
        )


def allowed_modes(report: CapabilityReport | None) -> list[ProjectMode]:
    """Modes the UI may offer. `None` report means no connector and no git yet.

    CREATE_PR is hidden when pull-request capability is none (WordPress,
    git without a GitHub token). COMMIT remains for WordPress API publish
    and for local git commits.
    """

    if report is None:
        return list(_ALL_MODES)
    if not report.allows_modification():
        return list(_READ_ONLY_MODES)
    modes = [
        ProjectMode.AUDIT_ONLY,
        ProjectMode.SUGGEST_ONLY,
        ProjectMode.APPLY_LOCALLY,
        ProjectMode.COMMIT,
    ]
    if _LEVEL_RANK[report.pull_request] > 0:
        modes.append(ProjectMode.CREATE_PR)
    return modes


def assert_mode_allowed(
    mode: ProjectMode, report: CapabilityReport | None
) -> None:
    if report is None:
        return
    if mode in MODES_REQUIRING_MODIFICATION and not report.allows_modification():
        raise ModeNotAllowed(mode, report)


def report_from_json(payload: dict | None) -> CapabilityReport | None:
    if not payload:
        return None
    return CapabilityReport.model_validate(payload)


def dump_report(report: CapabilityReport) -> dict:
    return report.model_dump(mode="json")
