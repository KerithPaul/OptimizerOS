"""Redis-backed job queue (step 1.C.2, `[P5]`).

`enqueue` writes the `jobs` row first — that row is the durable fact — then
pushes `job_id` onto a Redis list as the wakeup signal. The worker blocks on
`BRPOP` against that same list.

Also carries the resource-manager thin hook (§6.2): `HEAVY_JOB_TYPES` marks
which job types count against `MAX_CONCURRENT_HEAVY_JOBS`, enforced with a
Redis counter so a later multi-worker setup has the same seam.
"""

import json
import logging

import redis
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.job import Job, JobStatus

logger = logging.getLogger("architectos.jobs.queue")

# Populated as heavy job types land. health_ping is not heavy.
# repository_clone does git + disk + profiling (step 2.B.2).
# website_crawl does HTTP fetch + Playwright + embeddings (step 3.D).
# audit does rule evaluation + hybrid retrieval (embeddings / rerank).
# agent_run does one or more LLM calls per planner/agent step (step 6.1).
# code_change does planner LLM calls plus a Docker sandbox build/test run
# and Playwright browser validation (step 7.1-7.6).
# site_report runs crawl + audit + measurement in one slot.
HEAVY_JOB_TYPES: set[str] = {
    "repository_clone",
    "website_crawl",
    "audit",
    "agent_run",
    "code_change",
    "cms_change",
    "site_report",
}

HEAVY_JOB_COUNTER_KEY = "architectos:heavy_job_count"

_redis_client: redis.Redis | None = None


def get_redis(settings: Settings | None = None) -> redis.Redis:
    """Process-wide Redis client, created lazily on first use."""
    global _redis_client
    if _redis_client is None:
        settings = settings or get_settings()
        _redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            decode_responses=True,
            socket_timeout=None,
            socket_connect_timeout=10,
            socket_keepalive=True,
        )
    return _redis_client


def progress_key(job_id: int) -> str:
    return f"architectos:job:{job_id}:progress"


def enqueue(db: Session, project_id: int, job_type: str, settings: Settings | None = None) -> Job:
    """Create the `jobs` row (status `queued`) and push it onto the Redis queue."""
    settings = settings or get_settings()
    job = Job(project_id=project_id, type=job_type, status=JobStatus.QUEUED)
    db.add(job)
    db.commit()
    db.refresh(job)

    get_redis(settings).lpush(settings.job_queue_key, str(job.id))
    logger.info("job enqueued (job_id=%s, type=%s)", job.id, job_type)
    return job


def mirror_progress(
    db: Session,
    job: Job,
    *,
    stage: str,
    percent: int,
    message: str,
    settings: Settings | None = None,
) -> None:
    """Write progress to Redis, then mirror the same payload into `jobs.progress_json`."""
    settings = settings or get_settings()
    payload = {"stage": stage, "percent": percent, "message": message, "job_status": job.status.value}

    get_redis(settings).set(progress_key(job.id), json.dumps(payload), ex=3600)

    job.progress_json = payload
    db.commit()
    logger.info("job progress (job_id=%s): %s (%s%%)", job.id, stage, percent)
