"""Edit, inspect, or fire the site health report schedule.

From backend/:

    python -m app.scripts.site_report_schedule show
    python -m app.scripts.site_report_schedule projects
    python -m app.scripts.site_report_schedule init
    python -m app.scripts.site_report_schedule set --id drmoksha-weekly --project-id 404 --weekday monday --at 09:00 --to you@example.com
    python -m app.scripts.site_report_schedule fire --id drmoksha-weekly
    python -m app.scripts.site_report_schedule fire --project-id 404
    python -m app.scripts.site_report_schedule resend --project-id 404
    python -m app.scripts.site_report_schedule disable --id drmoksha-weekly
    python -m app.scripts.site_report_schedule enable --id drmoksha-weekly
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.mailer import deliver_site_report, parse_recipients
from app.services.site_report_schedule import (
    ScheduleConfig,
    ScheduleItem,
    dump_config,
    enqueue_site_report,
    example_path,
    load_config,
    mark_fired,
    load_state,
    next_fire_at,
    list_projects,
    latest_report,
    recipients_for,
    save_state,
    schedule_path,
    tick_due_schedules,
)


def _print(text: str) -> None:
    sys.stdout.write(text + "\n")


def cmd_show(_args: argparse.Namespace) -> int:
    settings = get_settings()
    path = schedule_path(settings)
    config = load_config(settings)
    state = load_state(settings)
    now = datetime.now(timezone.utc)
    _print(f"file: {path}")
    _print(f"exists: {path.is_file()}")
    _print(f"timezone: {config.timezone or '(system local)'}")
    _print(f"default email_to: {', '.join(config.email_to) or settings.site_report_email_to or '(none)'}")
    if not config.schedules:
        _print("schedules: (none) — run init, then set")
        return 0
    for item in config.schedules:
        nxt = None
        try:
            nxt = next_fire_at(item, config, now)
        except Exception as exc:
            nxt_text = f"invalid: {exc}"
        else:
            nxt_text = nxt.isoformat() if nxt else "none in next 14 days"
        last = state.get(item.id) if isinstance(state.get(item.id), dict) else {}
        to_list = recipients_for(item, config, settings)
        _print("")
        _print(f"id: {item.id}")
        _print(f"  enabled: {item.enabled}")
        _print(f"  project_id: {item.project_id}")
        _print(f"  weekday: {item.weekday}")
        _print(f"  at: {item.at}")
        _print(f"  cron: {item.cron_expr}")
        _print(f"  email_to: {', '.join(to_list) or '(none)'}")
        _print(f"  next: {nxt_text}")
        _print(f"  last_slot: {last.get('last_slot') or '-'}")
        _print(f"  last_job_id: {last.get('job_id') or '-'}")
    return 0


def cmd_projects(_args: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        rows = list_projects(db)
    finally:
        db.close()
    if not rows:
        _print("no projects")
        return 0
    for row in rows:
        _print(f"{row['id']}\t{row['name']}\t{row['url'] or '—'}")
    return 0


def cmd_init(_args: argparse.Namespace) -> int:
    settings = get_settings()
    path = schedule_path(settings)
    if path.is_file():
        _print(f"already exists: {path}")
        return 0
    example = example_path(settings)
    if example.is_file():
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        _print(f"copied {example.name} -> {path}")
        return 0
    dump_config(
        ScheduleConfig(
            timezone="Asia/Kolkata",
            email_to=parse_recipients(settings.site_report_email_to),
            schedules=[],
        ),
        settings,
    )
    _print(f"wrote empty schedule: {path}")
    return 0


def _upsert_item(config: ScheduleConfig, ident: str) -> ScheduleItem:
    for item in config.schedules:
        if item.id == ident:
            return item
    item = ScheduleItem(id=ident, project_id=0)
    config.schedules.append(item)
    return item


def cmd_set(args: argparse.Namespace) -> int:
    settings = get_settings()
    config = load_config(settings)
    if args.timezone:
        config.timezone = args.timezone
    if args.default_to is not None:
        config.email_to = parse_recipients(args.default_to)
    if not args.id:
        dump_config(config, settings)
        _print(f"updated defaults in {schedule_path(settings)}")
        return 0
    item = _upsert_item(config, args.id)
    if args.project_id is not None:
        item.project_id = args.project_id
    if args.weekday is not None:
        item.weekday = args.weekday
        item.cron = ""
    if args.at is not None:
        item.at = args.at
        item.cron = ""
    if args.cron is not None:
        item.cron = args.cron
    if args.to is not None:
        item.email_to = parse_recipients(args.to)
    if args.enabled is not None:
        item.enabled = args.enabled
    if item.project_id <= 0:
        _print("project_id is required the first time you set a schedule")
        return 2
    try:
        _ = item.cron_expr
    except Exception as exc:
        _print(str(exc))
        return 2
    dump_config(config, settings)
    _print(f"saved {item.id} project_id={item.project_id} cron={item.cron_expr} enabled={item.enabled}")
    return 0


def cmd_enable(args: argparse.Namespace, enabled: bool) -> int:
    settings = get_settings()
    config = load_config(settings)
    found = False
    for item in config.schedules:
        if item.id == args.id:
            item.enabled = enabled
            found = True
    if not found:
        _print(f"unknown schedule id {args.id!r}")
        return 2
    dump_config(config, settings)
    _print(f"{args.id} enabled={enabled}")
    return 0


def cmd_fire(args: argparse.Namespace) -> int:
    settings = get_settings()
    config = load_config(settings)
    item = None
    project_id = args.project_id
    if args.id:
        for row in config.schedules:
            if row.id == args.id:
                item = row
                project_id = row.project_id
                break
        if item is None:
            _print(f"unknown schedule id {args.id!r}")
            return 2
    if project_id is None:
        _print("pass --id or --project-id")
        return 2
    db = SessionLocal()
    try:
        job = enqueue_site_report(db, project_id, settings)
    except Exception as exc:
        _print(str(exc))
        return 1
    finally:
        db.close()
    if item is not None:
        state = load_state(settings)
        mark_fired(item, config, datetime.now(timezone.utc), job.id, state)
        save_state(state, settings)
    _print(f"enqueued site_report job_id={job.id} project_id={project_id}")
    _print("the worker sends email when that job finishes with a stored HTML report")
    return 0


def cmd_resend(args: argparse.Namespace) -> int:
    settings = get_settings()
    config = load_config(settings)
    db = SessionLocal()
    try:
        report = latest_report(db, args.project_id)
        if report is None or not report.html:
            _print(f"no stored HTML report for project {args.project_id}")
            return 1
        item = next((row for row in config.schedules if row.project_id == args.project_id), None)
        to_list = recipients_for(item, config, settings)
        result = deliver_site_report(
            html=report.html,
            document=report.document_json if isinstance(report.document_json, dict) else {},
            recipients=to_list,
            settings=settings,
        )
        report.email_status = result.status
        report.email_detail = result.detail
        db.commit()
    finally:
        db.close()
    _print(f"email_status={result.status.value} {result.detail}")
    return 0 if result.status.value == "sent" else 1


def cmd_tick(_args: argparse.Namespace) -> int:
    job_ids = tick_due_schedules()
    if not job_ids:
        _print("no due schedules")
        return 0
    _print("enqueued " + ", ".join(str(job_id) for job_id in job_ids))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.scripts.site_report_schedule",
        description="Edit timings or fire the site health report job.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show", help="print the current schedule")
    sub.add_parser("projects", help="list project ids (needed by set/fire)")
    sub.add_parser("init", help="create site_report_schedule.yaml from the example")
    sub.add_parser("tick", help="enqueue any schedule that is due right now")

    set_p = sub.add_parser("set", help="create or update a named schedule")
    set_p.add_argument("--id", help="schedule id, e.g. drmoksha-weekly")
    set_p.add_argument("--project-id", type=int)
    set_p.add_argument("--weekday", help="monday..sunday or *")
    set_p.add_argument("--at", help="HH:MM, e.g. 09:00")
    set_p.add_argument("--cron", help="5-field crontab; overrides weekday/at")
    set_p.add_argument("--to", help="comma-separated recipients for this schedule")
    set_p.add_argument("--default-to", help="default recipients for every schedule")
    set_p.add_argument("--timezone", help="IANA timezone, e.g. Asia/Kolkata")
    set_p.add_argument(
        "--enabled",
        type=lambda value: str(value).lower() in {"1", "true", "yes", "on"},
        default=None,
    )

    fire_p = sub.add_parser("fire", help="enqueue a site_report job now")
    fire_p.add_argument("--id")
    fire_p.add_argument("--project-id", type=int)

    resend_p = sub.add_parser("resend", help="email the latest stored report without recrawling")
    resend_p.add_argument("--project-id", type=int, required=True)

    enable_p = sub.add_parser("enable", help="turn a schedule on")
    enable_p.add_argument("--id", required=True)
    disable_p = sub.add_parser("disable", help="turn a schedule off")
    disable_p.add_argument("--id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging(get_settings().log_level)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "show":
        return cmd_show(args)
    if args.command == "projects":
        return cmd_projects(args)
    if args.command == "init":
        return cmd_init(args)
    if args.command == "set":
        return cmd_set(args)
    if args.command == "fire":
        return cmd_fire(args)
    if args.command == "resend":
        return cmd_resend(args)
    if args.command == "tick":
        return cmd_tick(args)
    if args.command == "enable":
        return cmd_enable(args, True)
    if args.command == "disable":
        return cmd_enable(args, False)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
