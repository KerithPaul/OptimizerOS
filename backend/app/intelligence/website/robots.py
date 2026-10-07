"""robots.txt fetch + parse — checkpoint 3.B.2.

Robots is advisory on the open web and mandatory in ArchitectOS
(AGENTS.md §14). Unavailable robots is a recorded state, not a crash.
A GPTBot-style disallow is an AEO/GEO observation, not a crawl error.

Group matching is harvested from ClearSite `parseRobotsRules` /
`isAllowedByRobots` (Appendix D).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from app.core.config import Settings, get_settings
from app.intelligence.website.fetch import (
    FetchError,
    LookupFn,
    fetch_public,
)

_AI_BOT_AGENTS = (
    "gptbot",
    "chatgpt-user",
    "google-extended",
    "ccbot",
    "anthropic-ai",
    "claudebot",
    "perplexitybot",
    "bytespider",
)


@dataclass(frozen=True)
class RobotsRule:
    directive: str
    path: str


@dataclass
class RobotsGroup:
    agents: list[str]
    rules: list[RobotsRule]


@dataclass
class RobotsDocument:
    groups: list[RobotsGroup]
    sitemap_urls: list[str]
    our_rules: list[RobotsRule]
    ai_bot_disallows: list[str]


@dataclass
class RobotsState:
    """Recorded robots outcome the crawler and later rules consult."""

    state: str
    http_status: int
    raw_text: str | None
    our_rules: list[RobotsRule] = field(default_factory=list)
    sitemap_urls: list[str] = field(default_factory=list)
    ai_bot_disallows: list[str] = field(default_factory=list)
    disallows_us: bool = False


def parse_robots_rules(raw_text: str, user_agent: str) -> list[RobotsRule]:
    """Rules that apply to `user_agent` (longest matching group, else *)."""

    document = parse_robots_document(raw_text, user_agent)
    return document.our_rules


def parse_robots_document(raw_text: str, user_agent: str) -> RobotsDocument:
    groups: list[RobotsGroup] = []
    current: RobotsGroup | None = None
    sitemap_urls: list[str] = []
    for untrimmed in raw_text.splitlines():
        line = untrimmed.split("#", 1)[0].strip()
        if not line:
            continue
        separator = line.find(":")
        if separator == -1:
            continue
        field = line[:separator].strip().lower()
        value = line[separator + 1 :].strip()
        if field == "sitemap":
            if value:
                sitemap_urls.append(value)
            continue
        if field == "user-agent":
            if current is None or current.rules:
                current = RobotsGroup(agents=[], rules=[])
                groups.append(current)
            if value:
                current.agents.append(value.lower())
            continue
        if field in ("allow", "disallow") and current is not None:
            current.rules.append(RobotsRule(directive=field, path=value))

    our_rules = _select_rules(groups, user_agent)
    ai_bot_disallows = _ai_bot_disallows(groups)
    return RobotsDocument(
        groups=groups,
        sitemap_urls=sitemap_urls,
        our_rules=our_rules,
        ai_bot_disallows=ai_bot_disallows,
    )


def _select_rules(groups: list[RobotsGroup], user_agent: str) -> list[RobotsRule]:
    normalized = re.split(r"[\s/]", user_agent.lower(), maxsplit=1)[0]

    def specificity(agent: str) -> int:
        if agent == "*":
            return 0
        if normalized.startswith(agent):
            return len(agent)
        return -1

    best = -1
    for group in groups:
        for agent in group.agents:
            best = max(best, specificity(agent))
    if best < 0:
        return []
    selected: list[RobotsRule] = []
    for group in groups:
        if any(specificity(agent) == best for agent in group.agents):
            selected.extend(rule for rule in group.rules if rule.path)
    return selected


def _ai_bot_disallows(groups: list[RobotsGroup]) -> list[str]:
    found: list[str] = []
    for group in groups:
        blocks_all = any(
            rule.directive == "disallow" and rule.path == "/" for rule in group.rules
        )
        if not blocks_all:
            continue
        for agent in group.agents:
            if agent in _AI_BOT_AGENTS and agent not in found:
                found.append(agent)
    return found


def is_allowed_by_robots(pathname: str, rules: list[RobotsRule]) -> bool:
    matches = [
        rule
        for rule in rules
        if _path_matches(rule.path, pathname)
    ]
    if not matches:
        return True
    matches.sort(
        key=lambda rule: (
            -len(rule.path),
            0 if rule.directive == "allow" else 1,
        )
    )
    return matches[0].directive == "allow"


def _path_matches(pattern: str, pathname: str) -> bool:
    escaped = re.escape(pattern).replace(r"\*", ".*")
    return re.match(f"^{escaped}", pathname) is not None


def fetch_robots(
    origin: str,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    lookup: LookupFn | None = None,
) -> RobotsState:
    """Fetch `{origin}/robots.txt`. Failure is recorded, never raised."""

    settings = settings or get_settings()
    robots_url = origin.rstrip("/") + "/robots.txt"
    try:
        result = fetch_public(
            robots_url, settings=settings, client=client, lookup=lookup
        )
    except FetchError:
        return RobotsState(state="unavailable", http_status=0, raw_text=None)

    if result.status_code == 404:
        return RobotsState(state="missing", http_status=404, raw_text=None)
    if result.status_code < 200 or result.status_code >= 300:
        return RobotsState(
            state="unavailable",
            http_status=result.status_code,
            raw_text=None,
        )

    text = result.text[:200_000]
    document = parse_robots_document(text, settings.crawl_user_agent)
    disallows_us = any(
        rule.directive == "disallow" and rule.path for rule in document.our_rules
    )
    return RobotsState(
        state="fetched",
        http_status=result.status_code,
        raw_text=text,
        our_rules=document.our_rules,
        sitemap_urls=document.sitemap_urls,
        ai_bot_disallows=document.ai_bot_disallows,
        disallows_us=disallows_us,
    )
