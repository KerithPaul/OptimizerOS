"""YAML schedule for site health reports.

The worker checks due rows on idle Redis pops. Operators edit the file
directly or through `python -m app.scripts.site_report_schedule`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.jobs.queue import enqueue
from app.models.job import Job, JobStatus
from app.models.project import Project
from app.models.report import SiteReport
from app.models.website import Website
from app.services.mailer import parse_recipients

logger = logging.getLogger("architectos.site_report_schedule")

# Windows CPython often has no `tzdata` package. Keep IST working for the
# default drmoksha schedule without adding a hard runtime dependency.
_FIXED_OFFSETS: dict[str, timezone] = {
    "UTC": timezone.utc,
    "Etc/UTC": timezone.utc,
    "Asia/Kolkata": timezone(timedelta(hours=5, minutes=30)),
    "Asia/Calcutta": timezone(timedelta(hours=5, minutes=30)),
}

WEEKDAY_NAMES = {
    "sun": 0,
    "sunday": 0,
    "mon": 1,
    "monday": 1,
    "tue": 2,
    "tues": 2,
    "tuesday": 2,
    "wed": 3,
    "wednesday": 3,
    "thu": 4,
    "thur": 4,
    "thurs": 4,
    "thursday": 4,
    "fri": 5,
    "friday": 5,
    "sat": 6,
    "saturday": 6,
}

ACTIVE_JOB_STATUSES = {JobStatus.QUEUED, JobStatus.RUNNING}

HEADER = """# ArchitectOS site health report schedule
# Edit this file, or from backend/:
#   python -m app.scripts.site_report_schedule --help
#
# weekday: monday..sunday or '*' (every day; the * MUST be quoted in YAML)
# at: HH:MM in the timezone below
# cron: optional 5-field crontab (minute hour day-of-month month day-of-week)
#       If set, cron wins over weekday/at. Sunday=0 or 7, Monday=1.
#
"""


@dataclass
class ScheduleItem:
    id: str
    project_id: int
    enabled: bool = True
    weekday: str = "monday"
    at: str = "09:00"
    cron: str = ""
    email_to: list[str] = field(default_factory=list)
    timezone: str = ""

    @property
    def cron_expr(self) -> str:
        if self.cron.strip():
            return self.cron.strip()
        hour, minute = parse_clock(self.at)
        dow = _weekday_to_cron(self.weekday)
        return f"{minute} {hour} * * {dow}"


@dataclass
class ScheduleConfig:
    timezone: str = ""
    email_to: list[str] = field(default_factory=list)
    schedules: list[ScheduleItem] = field(default_factory=list)


def parse_clock(raw: str) -> tuple[int, int]:
    text = (raw or "09:00").strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"time must be HH:MM, got {raw!r}")
    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time out of range: {raw!r}")
    return hour, minute


def _weekday_to_cron(raw: str) -> str:
    text = (raw or "*").strip().lower()
    if text in {"*", "all", "everyday", "every-day", "daily"}:
        return "*"
    if text in WEEKDAY_NAMES:
        return str(WEEKDAY_NAMES[text])
    if text.isdigit() and 0 <= int(text) <= 7:
        return str(int(text))
    raise ValueError(f"unknown weekday {raw!r}")


def resolve_timezone(name: str | None) -> timezone | ZoneInfo:
    text = (name or "").strip()
    if not text:
        local = datetime.now().astimezone().tzinfo
        return local or timezone.utc
    try:
        return ZoneInfo(text)
    except ZoneInfoNotFoundError:
        if text in _FIXED_OFFSETS:
            return _FIXED_OFFSETS[text]
        raise ValueError(
            f"unknown timezone {text!r}; install the tzdata package or use Asia/Kolkata / UTC"
        )


def _match_field(expr: str, value: int) -> bool:
    expr = expr.strip()
    if expr == "*":
        return True
    for part in expr.split(","):
        token = part.strip()
        if not token:
            continue
        step = 1
        if "/" in token:
            base, step_s = token.split("/", 1)
            step = int(step_s)
            token = base or "*"
        if token == "*":
            if value % step == 0:
                return True
            continue
        if "-" in token:
            lo_s, hi_s = token.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if lo <= value <= hi and (value - lo) % step == 0:
                return True
            continue
        number = int(token)
        if step == 1 and value == number:
            return True
        if step > 1 and value >= number and (value - number) % step == 0:
            return True
    return False


def cron_matches(expr: str, when: datetime) -> bool:
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron must have 5 fields, got {expr!r}")
    minute, hour, dom, month, dow = fields
    cron_dow = (when.weekday() + 1) % 7  # Sunday=0 ... Saturday=6
    dow_ok = _match_field(dow, cron_dow) or (cron_dow == 0 and _match_field(dow, 7))
    return (
        _match_field(minute, when.minute)
        and _match_field(hour, when.hour)
        and _match_field(dom, when.day)
        and _match_field(month, when.month)
        and dow_ok
    )


def schedule_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.resolved_site_report_schedule_path


def state_path(settings: Settings | None = None) -> Path:
    path = schedule_path(settings)
    return path.with_suffix(".state.json")


def example_path(settings: Settings | None = None) -> Path:
    return schedule_path(settings).with_name("site_report_schedule.example.yaml")


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return parse_recipients(value)
    if isinstance(value, list):
        return parse_recipients([str(item) for item in value])
    return []


def load_config(settings: Settings | None = None) -> ScheduleConfig:
    path = schedule_path(settings)
    if not path.is_file():
        return ScheduleConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    items: list[ScheduleItem] = []
    rows = raw.get("schedules") or []
    if not isinstance(rows, list):
        raise ValueError("schedules must be a list")
    for row in rows:
        if not isinstance(row, dict):
            continue
        ident = str(row.get("id") or "").strip()
        project_id = int(row.get("project_id"))
        if not ident:
            raise ValueError("each schedule needs an id")
        items.append(
            ScheduleItem(
                id=ident,
                project_id=project_id,
                enabled=bool(row.get("enabled", True)),
                weekday=str(row.get("weekday") or "monday"),
                at=str(row.get("at") or "09:00"),
                cron=str(row.get("cron") or ""),
                email_to=_as_list(row.get("email_to")),
                timezone=str(row.get("timezone") or ""),
            )
        )
    return ScheduleConfig(
        timezone=str(raw.get("timezone") or ""),
        email_to=_as_list(raw.get("email_to")),
        schedules=items,
    )


def dump_config(config: ScheduleConfig, settings: Settings | None = None) -> Path:
    path = schedule_path(settings)
    payload: dict[str, Any] = {
        "timezone": config.timezone,
        "email_to": config.email_to,
        "schedules": [asdict(item) for item in config.schedules],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def load_state(settings: Settings | None = None) -> dict[str, Any]:
    path = state_path(settings)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_state(state: dict[str, Any], settings: Settings | None = None) -> None:
    path = state_path(settings)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def recipients_for(item: ScheduleItem | None, config: ScheduleConfig, settings: Settings) -> list[str]:
    if item is not None and item.email_to:
        return parse_recipients(item.email_to)
    if config.email_to:
        return parse_recipients(config.email_to)
    return parse_recipients(settings.site_report_email_to)


def _item_timezone(item: ScheduleItem, config: ScheduleConfig) -> timezone | ZoneInfo:
    return resolve_timezone(item.timezone or config.timezone)


def is_due(item: ScheduleItem, config: ScheduleConfig, now: datetime, state: dict[str, Any]) -> bool:
    if not item.enabled:
        return False
    tz = _item_timezone(item, config)
    local = now.astimezone(tz)
    if not cron_matches(item.cron_expr, local):
        return False
    stamp = f"{local.date().isoformat()}T{local.hour:02d}:{local.minute:02d}"
    previous = ((state.get(item.id) or {}) if isinstance(state.get(item.id), dict) else {}).get(
        "last_slot"
    )
    return previous != stamp


def mark_fired(item: ScheduleItem, config: ScheduleConfig, now: datetime, job_id: int, state: dict[str, Any]) -> None:
    tz = _item_timezone(item, config)
    local = now.astimezone(tz)
    stamp = f"{local.date().isoformat()}T{local.hour:02d}:{local.minute:02d}"
    state[item.id] = {
        "last_slot": stamp,
        "last_enqueued_at": now.astimezone(timezone.utc).isoformat(),
        "job_id": job_id,
        "project_id": item.project_id,
    }


def project_has_active_site_report(db: Session, project_id: int) -> bool:
    row = db.scalar(
        select(Job.id).where(
            Job.project_id == project_id,
            Job.type == "site_report",
            Job.status.in_(ACTIVE_JOB_STATUSES),
        )
    )
    return row is not None


def enqueue_site_report(db: Session, project_id: int, settings: Settings | None = None) -> Job:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"project {project_id} not found")
    if project_has_active_site_report(db, project_id):
        raise ValueError(f"project {project_id} already has a queued or running site_report")
    return enqueue(db, project_id, "site_report", settings=settings)


def tick_due_schedules(settings: Settings | None = None, *, now: datetime | None = None) -> list[int]:
    settings = settings or get_settings()
    path = schedule_path(settings)
    if not path.is_file():
        return []
    from app.db.session import SessionLocal

    now = now or datetime.now(timezone.utc)
    config = load_config(settings)
    state = load_state(settings)
    job_ids: list[int] = []
    db = SessionLocal()
    try:
        dirty = False
        for item in config.schedules:
            try:
                due = is_due(item, config, now, state)
            except Exception:
                logger.exception("schedule %s is invalid", item.id)
                continue
            if not due:
                continue
            if project_has_active_site_report(db, item.project_id):
                logger.info("schedule %s skipped; site_report already active", item.id)
                continue
            try:
                job = enqueue_site_report(db, item.project_id, settings)
            except Exception:
                logger.exception("schedule %s failed to enqueue", item.id)
                continue
            mark_fired(item, config, now, job.id, state)
            dirty = True
            job_ids.append(job.id)
            logger.info(
                "schedule %s enqueued site_report job_id=%s project_id=%s",
                item.id,
                job.id,
                item.project_id,
            )
        if dirty:
            save_state(state, settings)
    finally:
        db.close()
    return job_ids


def next_fire_at(item: ScheduleItem, config: ScheduleConfig, now: datetime) -> datetime | None:
    tz = _item_timezone(item, config)
    cursor = now.astimezone(tz).replace(second=0, microsecond=0)
    for _ in range(14 * 24 * 60):
        if cron_matches(item.cron_expr, cursor):
            return cursor
        cursor += timedelta(minutes=1)
    return None


def list_projects(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Project.id, Project.name, Website.url)
        .select_from(Project)
        .outerjoin(Website, Website.project_id == Project.id)
        .order_by(Project.id)
    ).all()
    return [{"id": row[0], "name": row[1], "url": row[2]} for row in rows]


def latest_report(db: Session, project_id: int) -> SiteReport | None:
    return db.scalar(
        select(SiteReport)
        .where(SiteReport.project_id == project_id)
        .order_by(SiteReport.id.desc())
    )
