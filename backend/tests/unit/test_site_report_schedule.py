from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.scripts.site_report_schedule import main as schedule_cli
from app.services.site_report_schedule import (
    ScheduleConfig,
    ScheduleItem,
    cron_matches,
    dump_config,
    is_due,
    load_config,
    next_fire_at,
)


IST = timezone(timedelta(hours=5, minutes=30))


def test_cron_matches_monday_0900() -> None:
    when = datetime(2026, 10, 5, 9, 0, tzinfo=IST)  # Monday
    assert cron_matches("0 9 * * 1", when)
    assert not cron_matches("0 9 * * 1", when.replace(hour=10))
    assert not cron_matches("0 9 * * 1", datetime(2026, 10, 6, 9, 0, tzinfo=IST))


def test_cron_sunday_zero_and_seven() -> None:
    sunday = datetime(2026, 10, 4, 9, 0, tzinfo=IST)
    assert cron_matches("0 9 * * 0", sunday)
    assert cron_matches("0 9 * * 7", sunday)
    assert not cron_matches("0 9 * * 1", sunday)


def test_weekday_at_compiles_and_is_due(tmp_path, monkeypatch) -> None:
    settings = get_settings().model_copy(
        update={"site_report_schedule_path": str(tmp_path / "site_report_schedule.yaml")}
    )
    config = ScheduleConfig(
        timezone="Asia/Kolkata",
        email_to=["ops@example.com"],
        schedules=[
            ScheduleItem(
                id="drmoksha-weekly",
                project_id=404,
                weekday="monday",
                at="09:00",
            )
        ],
    )
    dump_config(config, settings)
    loaded = load_config(settings)
    assert loaded.schedules[0].cron_expr == "0 9 * * 1"
    monday = datetime(2026, 10, 5, 9, 0, tzinfo=IST)
    assert is_due(loaded.schedules[0], loaded, monday, {})
    state = {"drmoksha-weekly": {"last_slot": "2026-10-05T09:00"}}
    assert not is_due(loaded.schedules[0], loaded, monday, state)
    nxt = next_fire_at(loaded.schedules[0], loaded, monday.replace(hour=10))
    assert nxt is not None
    assert nxt.weekday() == 0  # Monday


def test_cli_set_and_show(tmp_path, capsys, monkeypatch) -> None:
    yaml_path = tmp_path / "site_report_schedule.yaml"
    monkeypatch.setenv("SITE_REPORT_SCHEDULE_PATH", str(yaml_path))
    from app.core.config import get_settings as _get

    _get.cache_clear()
    try:
        assert schedule_cli(
            [
                "set",
                "--id",
                "drmoksha-weekly",
                "--project-id",
                "404",
                "--weekday",
                "monday",
                "--at",
                "09:00",
                "--to",
                "kerith.perla@lexfintech.io",
                "--timezone",
                "Asia/Kolkata",
            ]
        ) == 0
        assert yaml_path.is_file()
        assert schedule_cli(["show"]) == 0
        out = capsys.readouterr().out
        assert "drmoksha-weekly" in out
        assert "project_id: 404" in out
        assert "0 9 * * 1" in out
    finally:
        _get.cache_clear()
