"""Job-type -> handler registry (step 1.C.2), keyed by `jobs.type`.

Handlers self-register via `register()` when their module is imported —
see `app.jobs.handlers` for the import that triggers this.
"""

from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session

from app.models.job import Job


class ProgressReporter(Protocol):
    def __call__(self, stage: str, percent: int, message: str) -> None: ...


JobHandler = Callable[[Job, Session, ProgressReporter], None]

_REGISTRY: dict[str, JobHandler] = {}


def register(job_type: str, handler: JobHandler) -> None:
    _REGISTRY[job_type] = handler


def get_handler(job_type: str) -> JobHandler | None:
    return _REGISTRY.get(job_type)
