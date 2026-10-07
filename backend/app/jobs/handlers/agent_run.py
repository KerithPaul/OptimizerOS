"""Agent run job (step 6.1 / 6.4 / 6.5 / 6.7).

Natural-language request -> Layer 1 Intent Planner -> Layer 2 Research
Planner -> Research Agent (read-only evidence package) -> SEO/AEO/GEO
agents (ranked hypothesised interventions via the Layer 3 Optimization
Planner), each inside hard per-agent budgets `[SPEC AGENTS.md §51]`. Zero
file or CMS mutation — no write tool exists (step 6.3).

The Research Agent's `agent_runs` row is created by
`app.api.v1.agents.start_agent_run` *before* this job is queued, so it
always exists by the time the worker picks the job up; this handler claims
it rather than creating a new one, then creates one `agent_runs` row per
downstream SEO/AEO/GEO agent it runs. A single agent's internal failure
(status `FAILED` on that one row) does not fail the whole job, matching
the `partial`-run pattern already used by the audit job (step 5.B.3) — the
job only fails outright if planning itself cannot produce an objective.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.aeo import run_aeo_agent
from app.agents.base import default_budget
from app.agents.geo import run_geo_agent
from app.agents.research import run_research_agent
from app.agents.runtime import AgentMessageRecord, AgentRunOutcome
from app.agents.seo import run_seo_agent
from app.agents.tools import AgentTools
from app.core.config import get_settings
from app.jobs.registry import ProgressReporter, register
from app.llm.gateway import LLMGateway
from app.models.agent import AgentMessage, AgentMessageRole, AgentRun, AgentRunStatus, AgentType
from app.models.finding import AnalysisRun, Finding
from app.models.job import Job
from app.models.repository import Repository
from app.models.website import Website
from app.planners.intent import IntentObjective, plan_intent
from app.planners.research import ResearchPlan, plan_research

_AGENT_RUNNERS = {
    AgentType.SEO: run_seo_agent,
    AgentType.AEO: run_aeo_agent,
    AgentType.GEO: run_geo_agent,
}


def agent_run(job: Job, db: Session, report_progress: ProgressReporter) -> None:
    research_row = db.scalar(
        select(AgentRun).where(
            AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.RESEARCH
        )
    )
    if research_row is None:
        raise RuntimeError("no seeded research agent_runs row for this job")
    request_text = (research_row.request_text or "").strip()
    if not request_text:
        raise RuntimeError("agent run requires a non-blank request")

    settings = get_settings()
    gateway = LLMGateway(settings)
    budget = default_budget(settings)

    report_progress("Planning", 5, "Interpreting the request")
    objective, objective_chat = plan_intent(gateway, request_text)
    research_row.objective_json = objective.model_dump()
    research_row.started_at = datetime.now(timezone.utc)
    db.commit()

    report_progress("Planning", 15, "Determining required research")
    research_plan, research_plan_chat = plan_research(gateway, objective)

    website = db.scalar(select(Website).where(Website.project_id == job.project_id))
    repository = db.scalar(select(Repository).where(Repository.project_id == job.project_id))
    tools = AgentTools(
        db,
        project_id=job.project_id,
        repository_id=repository.id if repository is not None else None,
        website_id=website.id if website is not None else None,
    )

    report_progress("Researching", 30, "Running the Research Agent")
    research_outcome = run_research_agent(
        objective=objective,
        research_plan=research_plan,
        tools=tools,
        budget=budget,
        request_text=request_text,
    )
    _prepend_planner_messages(
        research_outcome, objective, objective_chat, research_plan, research_plan_chat
    )
    _finish_run(db, research_row, research_outcome)

    report_progress("Analyzing", 55, "Selecting findings to analyze")
    latest_run = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == job.project_id)
        .order_by(AnalysisRun.id.desc())
    )
    findings = (
        list(db.scalars(select(Finding).where(Finding.analysis_run_id == latest_run.id)))
        if latest_run is not None
        else []
    )

    targets = _target_agents(objective)
    research_package = research_outcome.output if isinstance(research_outcome.output, dict) else None
    summary = {"research": research_outcome.status.value}
    total = len(targets)
    for index, agent_type in enumerate(targets):
        percent = 60 + int(35 * (index + 1) / max(total, 1))
        report_progress("Optimizing", percent, f"Running the {agent_type.value.upper()} Agent")
        sub_row = _new_run(db, job, agent_type, objective, request_text)
        outcome = _AGENT_RUNNERS[agent_type](
            findings=findings,
            gateway=gateway,
            budget=budget,
            objective=objective,
            research_package=research_package,
            request_text=request_text,
        )
        _finish_run(db, sub_row, outcome)
        summary[agent_type.value] = outcome.status.value

    report_progress("Finishing", 100, f"agent_run={summary}")


def _target_agents(objective: IntentObjective) -> list[AgentType]:
    text = objective.objective.lower()
    matched = [
        agent_type
        for agent_type, needle in (
            (AgentType.SEO, "seo"),
            (AgentType.AEO, "aeo"),
            (AgentType.GEO, "geo"),
        )
        if needle in text
    ]
    return matched or [AgentType.SEO, AgentType.AEO, AgentType.GEO]


def _new_run(
    db: Session,
    job: Job,
    agent_type: AgentType,
    objective: IntentObjective,
    request_text: str,
) -> AgentRun:
    row = AgentRun(
        project_id=job.project_id,
        job_id=job.id,
        agent_type=agent_type,
        status=AgentRunStatus.RUNNING,
        objective_json=objective.model_dump(),
        request_text=request_text,
        started_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _prepend_planner_messages(
    outcome: AgentRunOutcome,
    objective: IntentObjective,
    objective_chat,
    research_plan: ResearchPlan,
    research_plan_chat,
) -> None:
    outcome.messages = [
        AgentMessageRecord(
            role="assistant",
            content=objective.model_dump_json(),
            provider=objective_chat.provider,
            model=objective_chat.model,
            tokens=objective_chat.tokens,
        ),
        AgentMessageRecord(
            role="assistant",
            content=research_plan.model_dump_json(),
            provider=research_plan_chat.provider,
            model=research_plan_chat.model,
            tokens=research_plan_chat.tokens,
        ),
        *outcome.messages,
    ]


def _finish_run(db: Session, row: AgentRun, outcome: AgentRunOutcome) -> None:
    for index, message in enumerate(outcome.messages, start=1):
        db.add(
            AgentMessage(
                agent_run_id=row.id,
                seq=index,
                role=AgentMessageRole(message.role),
                content=message.content,
                tool_name=message.tool_name,
                provider=message.provider,
                model=message.model,
                tokens=message.tokens,
            )
        )
    row.status = outcome.status
    row.result_json = outcome.output
    row.iterations_used = outcome.iterations_used
    row.tool_calls_used = outcome.tool_calls_used
    row.tokens_used = outcome.tokens_used
    row.files_modified = outcome.files_modified
    row.stopped_reason = outcome.stopped_reason
    row.error = outcome.error
    row.finished_at = datetime.now(timezone.utc)
    db.commit()


register("agent_run", agent_run)
