"""Keyword suggestion responses. GSC is the first source; later sources share this shape."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.search import SearchConsoleConnectionOut


class KeywordSuggestionOut(BaseModel):
    query: str
    source: Literal["gsc"]
    impressions: int
    clicks: int
    ctr: float
    position: float
    start_date: date
    end_date: date
    search_console_row_id: int | None
    matched_filters: list[str]
    reason: str


class KeywordSuggestionFiltersOut(BaseModel):
    dimension: Literal["query"] = "query"
    match: Literal["any"] = "any"
    rank: Literal["impressions_desc"] = "impressions_desc"
    position_gte: float
    ctr_lt: float


class KeywordSuggestionsOut(BaseModel):
    source: Literal["gsc"] = "gsc"
    connection: SearchConsoleConnectionOut
    analysis_run_id: int | None
    start_date: date | None
    end_date: date | None
    query_row_count: int
    suggestion_count: int
    filters: KeywordSuggestionFiltersOut
    note: str
    suggestions: list[KeywordSuggestionOut] = Field(default_factory=list)
