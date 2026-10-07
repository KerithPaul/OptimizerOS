"""GSC-first keyword suggestions.

Derived from stored `search_console_data` query rows. Does not invent
keywords, volume, or difficulty. An external research provider is a later
source on the same suggestion shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.search import SearchConsoleDimension, SearchConsoleRow

SOURCE_GSC = "gsc"
POSITION_GTE = 8.0
CTR_LT = 0.02
FILTER_POSITION = "position_gte_8"
FILTER_CTR = "ctr_lt_0.02"
RANK = "impressions_desc"
OBSERVED_NOTE = (
    "These rows are queries Google Search Console already recorded for this property."
)


@dataclass(frozen=True)
class KeywordSuggestion:
    query: str
    source: str
    impressions: int
    clicks: int
    ctr: float
    position: float
    start_date: object
    end_date: object
    search_console_row_id: int | None
    matched_filters: tuple[str, ...]
    reason: str


def matched_filters_for(position: float, ctr: float) -> tuple[str, ...]:
    matched: list[str] = []
    if position >= POSITION_GTE:
        matched.append(FILTER_POSITION)
    if ctr < CTR_LT:
        matched.append(FILTER_CTR)
    return tuple(matched)


def is_query_opportunity(*, query: str | None, position: float, ctr: float) -> bool:
    if not (query or "").strip():
        return False
    return bool(matched_filters_for(position, ctr))


def format_reason(*, impressions: int, position: float, ctr: float) -> str:
    return (
        f"{impressions:,} impressions, position {position:.1f}, CTR {ctr * 100:.1f}%"
    )


def suggestions_from_query_rows(rows: list[SearchConsoleRow]) -> list[KeywordSuggestion]:
    """Keep GSC query-dimension opportunities, ranked by impressions descending."""

    selected: list[KeywordSuggestion] = []
    for row in rows:
        if row.dimension is not SearchConsoleDimension.QUERY:
            continue
        query = (row.query or "").strip()
        if not is_query_opportunity(query=query, position=row.position, ctr=row.ctr):
            continue
        filters = matched_filters_for(row.position, row.ctr)
        selected.append(
            KeywordSuggestion(
                query=query,
                source=SOURCE_GSC,
                impressions=row.impressions,
                clicks=row.clicks,
                ctr=row.ctr,
                position=row.position,
                start_date=row.start_date,
                end_date=row.end_date,
                search_console_row_id=row.id,
                matched_filters=filters,
                reason=format_reason(
                    impressions=row.impressions,
                    position=row.position,
                    ctr=row.ctr,
                ),
            )
        )
    selected.sort(
        key=lambda item: (
            -item.impressions,
            -(item.search_console_row_id or 0),
        )
    )
    return selected


def latest_gsc_run_id(db: Session, project_id: int) -> int | None:
    return db.scalar(
        select(func.max(SearchConsoleRow.analysis_run_id)).where(
            SearchConsoleRow.project_id == project_id
        )
    )


def load_query_rows(
    db: Session, project_id: int, *, analysis_run_id: int | None
) -> list[SearchConsoleRow]:
    stmt = select(SearchConsoleRow).where(
        SearchConsoleRow.project_id == project_id,
        SearchConsoleRow.dimension == SearchConsoleDimension.QUERY,
    )
    if analysis_run_id is not None:
        stmt = stmt.where(SearchConsoleRow.analysis_run_id == analysis_run_id)
    stmt = stmt.order_by(
        SearchConsoleRow.impressions.desc(),
        SearchConsoleRow.id.desc(),
    )
    return list(db.scalars(stmt))
