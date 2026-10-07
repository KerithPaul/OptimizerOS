"""Handshake job (step 1.C.3).

Proves `enqueue -> Redis -> worker -> MySQL status -> SSE` end to end.
`health_ping_fail` is a deliberate-failure variant used only by tests, to
prove a handler exception ends the job `failed` and never `succeeded`.
"""

import time

from sqlalchemy.orm import Session

from app.jobs.registry import ProgressReporter, register
from app.models.job import Job

_STAGES = [
    ("Starting", 0, "Handshake starting"),
    ("Pinging", 50, "Round-tripping through the queue"),
    ("Finishing", 100, "Handshake complete"),
]


def health_ping(job: Job, db: Session, report_progress: ProgressReporter) -> None:
    for stage, percent, message in _STAGES:
        report_progress(stage, percent, message)
        time.sleep(0.05)


def health_ping_fail(job: Job, db: Session, report_progress: ProgressReporter) -> None:
    report_progress("Starting", 0, "Handshake starting")
    time.sleep(0.05)
    raise RuntimeError("deliberate failure: health_ping_fail")


register("health_ping", health_ping)
register("health_ping_fail", health_ping_fail)
