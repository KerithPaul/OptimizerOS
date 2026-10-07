"""Typed request/response models for analysis runs and findings (step 5.C.3)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.knowledge.authority import AuthorityLevel
from app.models.finding import AnalysisRunStatus, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity


class AnalysisRunOut(BaseModel):
    id: int
    project_id: int
    status: AnalysisRunStatus
    inputs_json: dict | None
    gaps_json: list | None
    scores_json: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class PriorityFactorsOut(BaseModel):
    impact: float
    confidence: float
    reach: float
    actionability: float
    risk: float
    reach_input: str


class FindingOut(BaseModel):
    id: int
    project_id: int
    analysis_run_id: int
    finding_id: str
    observation: str
    problem: str
    evidence: list[dict[str, Any]]
    source: str
    source_url: str
    source_authority: AuthorityLevel
    rule: str
    rule_version: int
    category: RuleCategory
    severity: RuleSeverity
    confidence: RuleConfidence
    affected_resource: str
    affected_url: str | None
    affected_code_entity: str | None
    expected_mechanism: str
    recommended_action: str
    recommendation: str
    actionability: str
    risk: str
    will_validate: str
    change_worked: str
    rollback: str
    impressions: int | None = None
    reach_input: str | None = None
    status: FindingStatus
    created_at: datetime
    priority: float
    priority_factors: PriorityFactorsOut

    model_config = {"from_attributes": True}
