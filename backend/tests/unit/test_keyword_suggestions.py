"""GSC keyword suggestions are derived from stored query rows only."""

from datetime import date, datetime, timezone

from app.models.search import SearchConsoleDimension, SearchConsoleRow
from app.services.keyword_suggestions import (
    FILTER_CTR,
    FILTER_POSITION,
    SOURCE_GSC,
    format_reason,
    is_query_opportunity,
    suggestions_from_query_rows,
)


def _query_row(
    query: str,
    *,
    impressions: int,
    ctr: float,
    position: float,
    row_id: int | None = None,
    dimension: SearchConsoleDimension = SearchConsoleDimension.QUERY,
) -> SearchConsoleRow:
    row = SearchConsoleRow(
        project_id=1,
        dimension=dimension,
        query=query,
        page=None,
        impressions=impressions,
        clicks=0,
        ctr=ctr,
        position=position,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 28),
        fetched_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
    )
    row.id = row_id
    return row


def test_opportunity_requires_position_or_low_ctr() -> None:
    assert is_query_opportunity(query="crm software", position=8.0, ctr=0.10) is True
    assert is_query_opportunity(query="crm software", position=3.0, ctr=0.019) is True
    assert is_query_opportunity(query="crm software", position=7.9, ctr=0.02) is False
    assert is_query_opportunity(query="   ", position=12.0, ctr=0.001) is False
    assert is_query_opportunity(query=None, position=12.0, ctr=0.001) is False


def test_reason_uses_stored_numbers_only() -> None:
    assert (
        format_reason(impressions=1200, position=12.4, ctr=0.008)
        == "1,200 impressions, position 12.4, CTR 0.8%"
    )


def test_suggestions_filter_rank_and_keep_gsc_source() -> None:
    rows = [
        _query_row("keep position", impressions=50, ctr=0.10, position=8.0, row_id=1),
        _query_row("keep ctr", impressions=200, ctr=0.01, position=3.0, row_id=2),
        _query_row("drop both", impressions=9999, ctr=0.05, position=4.0, row_id=3),
        _query_row(
            "page row",
            impressions=500,
            ctr=0.001,
            position=20.0,
            row_id=4,
            dimension=SearchConsoleDimension.PAGE,
        ),
        _query_row("keep both", impressions=80, ctr=0.005, position=11.2, row_id=5),
    ]
    suggestions = suggestions_from_query_rows(rows)
    assert [item.query for item in suggestions] == ["keep ctr", "keep both", "keep position"]
    assert all(item.source == SOURCE_GSC for item in suggestions)
    assert suggestions[0].matched_filters == (FILTER_CTR,)
    assert suggestions[1].matched_filters == (FILTER_POSITION, FILTER_CTR)
    assert suggestions[2].matched_filters == (FILTER_POSITION,)
    assert suggestions[0].reason == "200 impressions, position 3.0, CTR 1.0%"
