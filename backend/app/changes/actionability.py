"""Resolve a finding's actionability against the project as it is *now*.

`findings.actionability` is stamped by the audit job from whatever inputs
existed at that moment (`app.jobs.handlers.audit`). An audit that ran
before the repository was attached/cloned therefore leaves every finding
as `recommend_only`, and attaching the repository afterwards (or switching
the project mode) never updates those rows. The change endpoints call this
instead of trusting the stored value alone.
"""

from __future__ import annotations

from app.connectors.capabilities import WORDPRESS_PLATFORM
from app.models.repository import Repository
from app.models.website import Website

CODE_ACTIONABLE = frozenset({"code_change", "code_or_platform_change"})


def effective_actionability(
    stored: str | None,
    *,
    repository: Repository | None,
    website: Website | None,
) -> str:
    """Upgrade a stale `recommend_only` when a code/CMS target now exists."""

    stored = stored or ""
    if stored in CODE_ACTIONABLE:
        return stored
    if stored == "recommend_only":
        if repository is not None:
            return "code_change"
        if website is not None and website.platform == WORDPRESS_PLATFORM:
            return "code_or_platform_change"
    return stored
