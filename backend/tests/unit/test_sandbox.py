"""Docker sandbox (step 7.5 verify, `[SPEC AGENTS.md §34]`).

Mocks `subprocess.run`/`shutil.which` — no real Docker daemon needed to
verify the sandbox's own safety invariants: it must refuse to run when
unconfigured, it must always pass CPU/memory/network limits, and a
timeout must kill the container rather than leave it running.
"""

from __future__ import annotations

import subprocess

import pytest

from app.changes.sandbox import SandboxError, _parse_published_port, run_in_sandbox, serve_in_sandbox
from app.core.config import Settings


def _env_from_args(args: list[str]) -> list[str]:
    values = []
    for i, arg in enumerate(args):
        if arg == "-e" and i + 1 < len(args):
            values.append(args[i + 1])
    return values


def _settings(**overrides: object) -> Settings:
    values = {
        "APP_SECRET_KEY": "unit-test-secret",
        "CREDENTIAL_ENCRYPTION_KEY": "cU5b7d2m9zQwErTyUiOpAsDfGhJkLzXcVbNmQwErTy8=",
        "SANDBOX_IMAGE": "architectos/sandbox:test",
        # Settings always loads the real backend/.env for any field not
        # given here (see ENV_FILE in app.core.config), so pin this
        # explicitly — otherwise these tests silently pick up whatever
        # SANDBOX_RUN_AS_UID happens to be set on the developer's machine.
        "SANDBOX_RUN_AS_UID": "",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_missing_sandbox_image_is_an_explicit_failure(tmp_path) -> None:
    with pytest.raises(SandboxError, match="SANDBOX_IMAGE"):
        run_in_sandbox(tmp_path, ["echo", "hi"], repository_id=1, settings=_settings(SANDBOX_IMAGE=""))


def test_missing_docker_executable_is_an_explicit_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.changes.sandbox.shutil.which", lambda _name: None)
    with pytest.raises(SandboxError, match="docker executable"):
        run_in_sandbox(tmp_path, ["echo", "hi"], repository_id=1, settings=_settings())


def test_missing_workspace_is_an_explicit_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.changes.sandbox.shutil.which", lambda _name: "/usr/bin/docker")
    with pytest.raises(SandboxError, match="workspace does not exist"):
        run_in_sandbox(tmp_path / "missing", ["echo", "hi"], repository_id=1, settings=_settings())


def test_run_passes_resource_limits_and_no_network_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.changes.sandbox.shutil.which", lambda _name: "/usr/bin/docker")
    captured: dict = {}

    def fake_run(args, **kwargs):
        if args[:2] == ["docker", "volume"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("app.changes.sandbox.subprocess.run", fake_run)
    result = run_in_sandbox(
        tmp_path, ["npm", "run", "build"], repository_id=42, settings=_settings(SANDBOX_CPU_LIMIT="1")
    )
    assert result.ok
    args = captured["args"]
    assert "--cpus" in args and args[args.index("--cpus") + 1] == "1"
    assert "--memory" in args
    assert "--network" in args and args[args.index("--network") + 1] == "none"
    assert any(f"{tmp_path}:/workspace:rw" in a for a in args)
    assert "architectos/sandbox:test" in args
    assert args[-3:] == ["npm", "run", "build"]
    env = _env_from_args(args)
    assert "NPM_CONFIG_CACHE=/cache/npm" in env
    assert "PNPM_HOME=/cache/pnpm" in env
    assert "COREPACK_HOME=/cache/corepack" in env
    assert "HOME=/cache/home" in env
    assert "XDG_DATA_HOME=/cache/xdg-data" in env
    assert "pnpm_config_pm_on_fail=ignore" not in env
    assert "COREPACK_ENABLE_NETWORK=0" in env


def test_run_decodes_output_as_utf8_not_locale_default(tmp_path, monkeypatch) -> None:
    """`text=True` alone decodes with `locale.getpreferredencoding()` (cp1252 on
    Windows), which raises `UnicodeDecodeError` inside subprocess's reader
    thread on non-cp1252 bytes a container can legitimately emit (e.g. pnpm
    build output) and silently leaves `stdout`/`stderr` as `None`. Pin
    `encoding="utf-8", errors="replace"` explicitly instead.
    """
    monkeypatch.setattr("app.changes.sandbox.shutil.which", lambda _name: "/usr/bin/docker")
    captured: dict = {}

    def fake_run(args, **kwargs):
        if args[:2] == ["docker", "volume"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("app.changes.sandbox.subprocess.run", fake_run)
    run_in_sandbox(tmp_path, ["npm", "run", "build"], repository_id=1, settings=_settings())
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"
    assert "text" not in captured["kwargs"]


def test_network_true_uses_bridge(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.changes.sandbox.shutil.which", lambda _name: "/usr/bin/docker")
    captured: dict = {}

    def fake_run(args, **kwargs):
        if args[:2] == ["docker", "volume"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("app.changes.sandbox.subprocess.run", fake_run)
    run_in_sandbox(tmp_path, ["npm", "ci"], repository_id=1, network=True, settings=_settings())
    assert captured["args"][captured["args"].index("--network") + 1] == "bridge"
    env = _env_from_args(captured["args"])
    assert "PNPM_HOME=/cache/pnpm" in env
    assert "pnpm_config_pm_on_fail=ignore" not in env
    assert "COREPACK_ENABLE_NETWORK=0" not in env


def test_nonzero_exit_is_a_result_not_an_exception(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.changes.sandbox.shutil.which", lambda _name: "/usr/bin/docker")

    def fake_run(args, **kwargs):
        if args[:2] == ["docker", "volume"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "build failed")

    monkeypatch.setattr("app.changes.sandbox.subprocess.run", fake_run)
    result = run_in_sandbox(tmp_path, ["npm", "run", "build"], repository_id=1, settings=_settings())
    assert not result.ok
    assert result.exit_code == 1
    assert "build failed" in result.stderr


def test_timeout_kills_the_container(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.changes.sandbox.shutil.which", lambda _name: "/usr/bin/docker")
    killed: list[list[str]] = []

    def fake_run(args, **kwargs):
        if args[:2] == ["docker", "volume"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["docker", "kill"]:
            killed.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")
        raise subprocess.TimeoutExpired(cmd=args, timeout=1)

    monkeypatch.setattr("app.changes.sandbox.subprocess.run", fake_run)
    result = run_in_sandbox(
        tmp_path, ["npm", "test"], repository_id=1, settings=_settings(SANDBOX_TIMEOUT_SECONDS=1)
    )
    assert result.timed_out is True
    assert not result.ok
    assert killed, "docker kill must be called on timeout"


def test_run_as_uid_chowns_cache_volume_before_running(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.changes.sandbox.shutil.which", lambda _name: "/usr/bin/docker")
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("app.changes.sandbox.subprocess.run", fake_run)
    result = run_in_sandbox(
        tmp_path,
        ["npm", "ci"],
        repository_id=1,
        network=True,
        settings=_settings(SANDBOX_RUN_AS_UID="1000:1000"),
    )
    assert result.ok

    chown_call = next(args for args in calls if args[:2] == ["docker", "run"] and "chown" in args)
    assert chown_call.index("chown") > chown_call.index("--network")
    assert chown_call[chown_call.index("--network") + 1] == "none"
    assert "architectos_sandbox_cache_1:/cache" in chown_call
    assert chown_call[-2:] == ["1000:1000", "/cache"]

    real_call = next(args for args in calls if args[-2:] == ["npm", "ci"])
    assert real_call[real_call.index("--user") + 1] == "1000:1000"
    # chown must run before the real command, not after.
    assert calls.index(chown_call) < calls.index(real_call)


def test_run_as_uid_not_set_skips_chown(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.changes.sandbox.shutil.which", lambda _name: "/usr/bin/docker")
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("app.changes.sandbox.subprocess.run", fake_run)
    run_in_sandbox(tmp_path, ["npm", "ci"], repository_id=1, settings=_settings())
    assert not any("chown" in args for args in calls)


def test_chown_failure_is_an_explicit_sandbox_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.changes.sandbox.shutil.which", lambda _name: "/usr/bin/docker")

    def fake_run(args, **kwargs):
        if args[:2] == ["docker", "volume"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "chown" in args:
            return subprocess.CompletedProcess(args, 1, "", "chown: permission denied")
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("app.changes.sandbox.subprocess.run", fake_run)
    with pytest.raises(SandboxError, match="chown cache volume"):
        run_in_sandbox(
            tmp_path,
            ["npm", "ci"],
            repository_id=1,
            settings=_settings(SANDBOX_RUN_AS_UID="1000:1000"),
        )


def test_parse_published_port() -> None:
    assert _parse_published_port("127.0.0.1:49152\n") == 49152
    assert _parse_published_port("0.0.0.0:3000") == 3000


def test_serve_publishes_loopback_and_cleans_up(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.changes.sandbox.shutil.which", lambda _name: "/usr/bin/docker")
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["docker", "volume"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(args, 0, "abc123", "")
        if args[:2] == ["docker", "port"]:
            return subprocess.CompletedProcess(args, 0, "127.0.0.1:49152\n", "")
        if args[:2] == ["docker", "rm"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["docker", "logs"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            assert url.startswith("http://127.0.0.1:49152")
            return _Resp()

    monkeypatch.setattr("app.changes.sandbox.subprocess.run", fake_run)
    monkeypatch.setattr("app.changes.sandbox.httpx.Client", _Client)

    with serve_in_sandbox(
        tmp_path, ["pnpm", "run", "start"], repository_id=7, settings=_settings()
    ) as preview:
        assert preview.base_url == "http://127.0.0.1:49152"
        run_args = next(args for args in calls if args[:2] == ["docker", "run"])
        assert "-d" in run_args
        assert "--rm" not in run_args
        assert "127.0.0.1:0:3000" in run_args
        assert run_args[run_args.index("--network") + 1] == "bridge"
        assert "HOSTNAME=0.0.0.0" in run_args
        assert "PORT=3000" in run_args
        env = _env_from_args(run_args)
        assert "PNPM_HOME=/cache/pnpm" in env
        assert "HOME=/cache/home" in env
        assert "COREPACK_ENABLE_NETWORK=0" not in env

    assert any(args[:3] == ["docker", "rm", "-f"] for args in calls)
