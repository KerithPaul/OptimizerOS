"""Derived keyword suggestions from stored GSC query rows."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_user
from app.api.v1.search_console import _connection_out
from app.db.session import get_db
from app.models.project import Project
from app.models.user import User
from app.schemas.keywords import (
    KeywordSuggestionFiltersOut,
    KeywordSuggestionOut,
    KeywordSuggestionsOut,
)
from app.services.keyword_suggestions import (
    CTR_LT,
    OBSERVED_NOTE,
    POSITION_GTE,
    SOURCE_GSC,
    latest_gsc_run_id,
    load_query_rows,
    suggestions_from_query_rows,
)

router = APIRouter(
    prefix="/projects/{project_id}/keyword-suggestions",
    tags=["keyword-suggestions"],
)


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


@router.get("", response_model=KeywordSuggestionsOut)
def get_keyword_suggestions(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> KeywordSuggestionsOut:
    _get_project(db, project_id)
    analysis_run_id = latest_gsc_run_id(db, project_id)
    query_rows = load_query_rows(db, project_id, analysis_run_id=analysis_run_id)
    suggestions = suggestions_from_query_rows(query_rows)
    start_date = suggestions[0].start_date if suggestions else None
    end_date = suggestions[0].end_date if suggestions else None
    if start_date is None and query_rows:
        start_date = query_rows[0].start_date
        end_date = query_rows[0].end_date
    return KeywordSuggestionsOut(
        source=SOURCE_GSC,
        connection=_connection_out(db, project_id, request=request),
        analysis_run_id=analysis_run_id,
        start_date=start_date,
        end_date=end_date,
        query_row_count=len(query_rows),
        suggestion_count=len(suggestions),
        filters=KeywordSuggestionFiltersOut(position_gte=POSITION_GTE, ctr_lt=CTR_LT),
        note=OBSERVED_NOTE,
        suggestions=[
            KeywordSuggestionOut(
                query=item.query,
                source=SOURCE_GSC,
                impressions=item.impressions,
                clicks=item.clicks,
                ctr=item.ctr,
                position=item.position,
                start_date=item.start_date,
                end_date=item.end_date,
                search_console_row_id=item.search_console_row_id,
                matched_filters=list(item.matched_filters),
                reason=item.reason,
            )
            for item in suggestions
        ],
    )
