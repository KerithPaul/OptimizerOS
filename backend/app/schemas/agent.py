"""Typed request/response models for the agent-run API (step 6.7)."""

from datetime import datetime

from pydantic import BaseModel

from app.models.agent import AgentRunStatus, AgentType


class AgentRunCreate(BaseModel):
    request_text: str


class AgentRunJobOut(BaseModel):
    job_id: int


class AgentRunOut(BaseModel):
    id: int
    project_id: int
    job_id: int | None
    agent_type: AgentType
    status: AgentRunStatus
    request_text: str | None
    objective_json: dict | None
    result_json: dict | list | None
    iterations_used: int
    tool_calls_used: int
    tokens_used: int
    files_modified: int
    stopped_reason: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}
