"""Agent run creation and listing (step 6.7).

`POST .../agents/run` creates the `jobs` row and the seed `agent_runs` row
(Research Agent, carrying the natural-language request) inside one
transaction *before* the job id is pushed onto the Redis queue — so the
worker can never observe a job with no seed row to claim
(`app.jobs.handlers.agent_run`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import require_project_access
from app.api.v1.auth import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.jobs.queue import get_redis
from app.models.agent import AgentRun, AgentRunStatus, AgentType
from app.models.job import Job, JobStatus
from app.models.project import Project
from app.models.user import User
from app.schemas.agent import AgentRunCreate, AgentRunJobOut, AgentRunOut

router = APIRouter(prefix="/projects/{project_id}/agents", tags=["agents"], dependencies=[Depends(require_project_access)])

JOB_TYPE = "agent_run"


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


@router.post("/run", response_model=AgentRunJobOut, status_code=status.HTTP_201_CREATED)
def start_agent_run(
    project_id: int,
    payload: AgentRunCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AgentRunJobOut:
    _get_project(db, project_id)
    if not payload.request_text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "request_text must not be blank")
    settings = get_settings()

    job = Job(project_id=project_id, type=JOB_TYPE, status=JobStatus.QUEUED)
    db.add(job)
    db.commit()
    db.refresh(job)

    seed = AgentRun(
        project_id=project_id,
        job_id=job.id,
        agent_type=AgentType.RESEARCH,
        status=AgentRunStatus.RUNNING,
        request_text=payload.request_text,
    )
    db.add(seed)
    db.commit()

    get_redis(settings).lpush(settings.job_queue_key, str(job.id))
    return AgentRunJobOut(job_id=job.id)


@router.get("/runs", response_model=list[AgentRunOut])
def list_agent_runs(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[AgentRun]:
    _get_project(db, project_id)
    return list(
        db.scalars(
            select(AgentRun).where(AgentRun.project_id == project_id).order_by(AgentRun.id.desc())
        )
    )


@router.get("/runs/by-job/{job_id}", response_model=list[AgentRunOut])
def get_agent_runs_for_job(
    project_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[AgentRun]:
    _get_project(db, project_id)
    rows = list(
        db.scalars(
            select(AgentRun)
            .where(AgentRun.project_id == project_id, AgentRun.job_id == job_id)
            .order_by(AgentRun.id.asc())
        )
    )
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no agent runs for this job")
    return rows


@router.get("/runs/{run_id}", response_model=AgentRunOut)
def get_agent_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AgentRun:
    _get_project(db, project_id)
    row = db.get(AgentRun, run_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent run not found")
    return row
