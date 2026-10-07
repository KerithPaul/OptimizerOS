"""Search Console live API (step 11.1 verify)."""

from datetime import date, datetime, timezone

from app.knowledge.authority import AuthorityLevel
from app.models.finding import Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.models.search import SearchConsoleDimension, SearchConsoleRow
from app.services.search_console import (
    DISPLAY_UNAVAILABLE,
    LOOKBACK_DAYS,
    SERIES_LOOKBACK_DAYS,
    STATUS_ATTACHED,
    STATUS_UNAVAILABLE,
    SearchConsoleSnapshot,
    SearchConsoleStatus,
    attach_to_findings,
    canonical_page_key,
    date_range,
    query_search_analytics,
    series_date_range,
    snapshot_from_rows,
)


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}" if payload is not None else b""
        self.text = ""

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse({"rows": self.rows})


def _finding(**overrides) -> Finding:
    defaults = dict(
        project_id=1,
        analysis_run_id=1,
        finding_id="SEO-TITLE-001:deadbeef",
        observation="generic title",
        problem="generic title",
        evidence=[{"source": "page", "excerpt": "Product | Company", "confidence": "direct"}],
        source="Google Search Central",
        source_url="https://developers.google.com/search",
        source_authority=AuthorityLevel.OFFICIAL_VENDOR_DOCS,
        rule="SEO-TITLE-001",
        rule_version=1,
        category=RuleCategory.TECHNICAL_SEO,
        severity=RuleSeverity.MEDIUM,
        confidence=RuleConfidence.HIGH,
        affected_resource="https://example.com/products/crm",
        affected_url="https://example.com/products/crm",
        affected_code_entity=None,
        expected_mechanism="mechanism",
        recommended_action="action",
        recommendation="action",
        actionability="recommend_only",
        risk="No change has been applied.",
        will_validate="re-check",
        change_worked="not_yet_applied",
        rollback="not applicable",
        status=FindingStatus.OPEN,
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _page_row(page: str, *, impressions: int, clicks: int, ctr: float, position: float) -> SearchConsoleRow:
    return SearchConsoleRow(
        project_id=1,
        dimension=SearchConsoleDimension.PAGE,
        page=page,
        query=None,
        impressions=impressions,
        clicks=clicks,
        ctr=ctr,
        position=position,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 28),
        fetched_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
    )


def test_series_lookback_is_longer_than_page_query_window() -> None:
    start, end = date_range(now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    series_start, series_end = series_date_range(now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert (end - start).days == LOOKBACK_DAYS - 1
    assert (series_end - series_start).days == SERIES_LOOKBACK_DAYS - 1
    assert series_end == end
    assert series_start < start


def test_canonical_page_key_strips_trailing_slash() -> None:
    assert canonical_page_key("https://Example.com/products/crm/") == "https://example.com/products/crm"


def test_query_search_analytics_posts_dimensions_and_parses_rows() -> None:
    client = _FakeClient(
        [{"keys": ["https://example.com/products/crm"], "impressions": 180000, "clicks": 1440, "ctr": 0.008, "position": 12.3}]
    )
    rows = query_search_analytics(
        client,  # type: ignore[arg-type]
        "token",
        "https://example.com/",
        date(2026, 8, 1),
        date(2026, 8, 28),
        ["page"],
    )
    assert rows[0]["impressions"] == 180000
    url, kwargs = client.calls[0]
    assert "searchAnalytics/query" in url
    assert kwargs["json"]["dimensions"] == ["page"]
    assert kwargs["headers"]["Authorization"] == "Bearer token"


def test_attach_adds_gsc_evidence_and_does_not_invent_when_unmatched() -> None:
    matched = _finding()
    unmatched = _finding(
        finding_id="SEO-TITLE-001:other",
        affected_url="https://example.com/about",
        affected_resource="https://example.com/about",
    )
    snapshot = snapshot_from_rows(
        [
            _page_row(
                "https://example.com/products/crm",
                impressions=180000,
                clicks=1440,
                ctr=0.008,
                position=12.3,
            )
        ],
        property_url="https://example.com/",
    )
    attach_to_findings([matched, unmatched], snapshot)
    assert matched.impressions == 180000
    assert matched.reach_input == "impressions"
    gsc_rows = [row for row in matched.evidence if row["source"] == "Google Search Console"]
    assert gsc_rows
    assert "180000 impressions" in gsc_rows[0]["excerpt"]
    assert "0.8% CTR" in gsc_rows[0]["excerpt"]
    assert unmatched.impressions is None
    assert unmatched.reach_input == "affected_page_count"
    assert all(row["source"] != "Google Search Console" for row in unmatched.evidence)


def test_unavailable_snapshot_does_not_invent_metrics() -> None:
    finding = _finding()
    snapshot = SearchConsoleSnapshot(
        status=SearchConsoleStatus(
            status=STATUS_UNAVAILABLE,
            display=DISPLAY_UNAVAILABLE,
            detail="no Search Console credentials",
        )
    )
    attach_to_findings([finding], snapshot)
    assert finding.impressions is None
    assert finding.reach_input == "affected_page_count"
    assert all(row["source"] != "Google Search Console" for row in finding.evidence)
    assert snapshot.status.display == "Search Console: unavailable"
    assert snapshot.status.status != STATUS_ATTACHED
