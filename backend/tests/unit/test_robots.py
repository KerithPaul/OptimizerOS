"""robots.txt grouping, allow/disallow, and unavailable state (step 3.B.2)."""

import httpx

from app.core.config import get_settings
from app.intelligence.website.robots import (
    fetch_robots,
    is_allowed_by_robots,
    parse_robots_document,
    parse_robots_rules,
)


def _lookup(host: str) -> list[str]:
    if host == "example.com":
        return ["93.184.216.34"]
    raise OSError("unrecognised test host")


def test_wildcard_group_is_used_instead_of_another_crawlers_rules() -> None:
    rules = parse_robots_rules(
        """
        User-agent: GPTBot
        Disallow: /

        User-agent: *
        Allow: /
        Disallow: /admin
        """,
        "ArchitectOSBot/1.0",
    )
    assert [(rule.directive, rule.path) for rule in rules] == [
        ("allow", "/"),
        ("disallow", "/admin"),
    ]
    assert is_allowed_by_robots("/", rules) is True
    assert is_allowed_by_robots("/publications", rules) is True
    assert is_allowed_by_robots("/admin", rules) is False
    assert is_allowed_by_robots("/admin/users", rules) is False


def test_more_specific_matching_group_wins() -> None:
    rules = parse_robots_rules(
        """
        User-agent: *
        Disallow: /private

        User-agent: ArchitectOSBot
        Disallow: /preview
        """,
        "ArchitectOSBot/1.0",
    )
    assert is_allowed_by_robots("/private", rules) is True
    assert is_allowed_by_robots("/preview", rules) is False


def test_gptbot_disallow_is_an_observation_not_our_rule() -> None:
    document = parse_robots_document(
        """
        User-agent: GPTBot
        Disallow: /

        User-agent: *
        Allow: /
        """,
        "ArchitectOSBot/1.0",
    )
    assert document.ai_bot_disallows == ["gptbot"]
    assert is_allowed_by_robots("/", document.our_rules) is True


def test_unavailable_robots_is_a_recorded_state_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    state = fetch_robots(
        "http://example.com",
        settings=get_settings(),
        client=client,
        lookup=_lookup,
    )
    assert state.state == "unavailable"
    assert state.http_status == 0
    assert state.our_rules == []


def test_missing_robots_is_recorded_as_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    state = fetch_robots(
        "http://example.com",
        settings=get_settings(),
        client=client,
        lookup=_lookup,
    )
    assert state.state == "missing"
    assert state.http_status == 404
