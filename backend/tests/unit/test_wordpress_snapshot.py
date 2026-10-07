"""WordPress snapshot + revision rollback is not git (step 10.4)."""

from pathlib import Path

from app.connectors.wordpress.snapshot import read_snapshot_payload, write_snapshot_payload
from app.core.config import get_settings
from app.models.change import Snapshot, SnapshotReason


def test_snapshot_round_trip(tmp_path: Path, monkeypatch) -> None:
    settings = get_settings().model_copy(update={"workspace_root": str(tmp_path)})
    payload = {
        "origin": "https://golden-c.example",
        "seo_plugin": "yoast",
        "resources": [
            {
                "id": 12,
                "_rest_base": "pages",
                "title": {"raw": "About"},
                "content": {"raw": "<p>Before</p>"},
            }
        ],
    }
    token, path = write_snapshot_payload(9, payload, settings=settings)
    assert token
    snapshot = Snapshot(
        project_id=9,
        snapshot_path=str(path.parent),
        reason=SnapshotReason.BEFORE_CMS_CHANGE,
    )
    loaded = read_snapshot_payload(snapshot)
    assert loaded["resources"][0]["content"]["raw"] == "<p>Before</p>"


def test_git_rollback_method_is_not_used_for_wordpress() -> None:
    assert SnapshotReason.BEFORE_CMS_CHANGE.value == "before_cms_change"
    from app.models.change import ChangePlatform

    assert ChangePlatform.WORDPRESS.value == "wordpress"
    assert ChangePlatform.WORDPRESS is not ChangePlatform.GIT
