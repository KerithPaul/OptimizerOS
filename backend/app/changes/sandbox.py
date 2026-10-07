"""Docker sandbox (step 7.5, `[SPEC AGENTS.md §34]`).

Runs one command inside `docker run --rm`, matching the Git-CLI-via-
subprocess style already used by `app.intelligence.repository.clone`
rather than adding a `docker` SDK dependency. Executing untrusted
generated code on the host is `[FORBIDDEN]` — this module is the only
place a Code Agent's patch is ever built, tested, or run.

Dependency caching `[SPEC §38]`: a per-repository named Docker volume is
mounted at `/cache` so `install_command` targets (npm/pip/etc.) can point
their own caches there and a second run does not reinstall the world.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger("architectos.changes.sandbox")

_WORKSPACE_MOUNT = "/workspace"
_CACHE_MOUNT = "/cache"
PREVIEW_CONTAINER_PORT = 3000


def _cache_env_args(*, network: bool) -> list[str]:
    """Env for every sandbox container.

    The image ships a global pnpm. Projects often pin a different version
    via `package.json` `packageManager` (lex-fintech: pnpm@10.4.1). pnpm
    10+ downloads that pinned binary before `install`/`run`. That fetch
    is legitimate during install (`network=True`). Lint/unit_test run
    with `--network none`, so the binary must already be on the
    per-repository `/cache` volume from install — do not ignore
    `packageManager`, or pnpm 12 will skip `package.json` `pnpm.overrides`
    and fail frozen install with ERR_PNPM_LOCKFILE_CONFIG_MISMATCH.
    """

    pairs = [
        f"HOME={_CACHE_MOUNT}/home",
        f"XDG_DATA_HOME={_CACHE_MOUNT}/xdg-data",
        f"XDG_CACHE_HOME={_CACHE_MOUNT}/xdg-cache",
        f"NPM_CONFIG_CACHE={_CACHE_MOUNT}/npm",
        f"PIP_CACHE_DIR={_CACHE_MOUNT}/pip",
        f"PNPM_HOME={_CACHE_MOUNT}/pnpm",
        f"COREPACK_HOME={_CACHE_MOUNT}/corepack",
        "COREPACK_ENABLE_DOWNLOAD_PROMPT=0",
    ]
    if not network:
        pairs.append("COREPACK_ENABLE_NETWORK=0")
    args: list[str] = []
    for pair in pairs:
        args += ["-e", pair]
    return args


class SandboxError(Exception):
    """The sandbox could not run at all (not configured, docker missing, ...).

    Distinct from a nonzero exit code, which is a normal `SandboxResult`.
    """


@dataclass(frozen=True)
class SandboxResult:
    command: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0


@dataclass(frozen=True)
class PreviewServer:
    """A running sandbox HTTP server bound to loopback on the host."""

    container_name: str
    base_url: str
    host_port: int


def cache_volume_name(repository_id: int) -> str:
    return f"architectos_sandbox_cache_{repository_id}"


def _ensure_cache_volume(name: str, *, run_as_uid: str | None, image: str) -> None:
    """Create the per-repository cache volume and make it writable by the sandbox user.

    A freshly created Docker named volume is owned `root:root`. When
    `SANDBOX_RUN_AS_UID` runs the real command as a non-root uid, that uid
    can read `/cache` but cannot create new entries under it (no write bit
    for "other"), so the first `mkdir` under `/cache` (e.g. pnpm setting up
    `PNPM_HOME`) fails with EACCES before any real command runs. Chowning
    the mount root once (as root, non-recursively — anything the sandbox
    user creates under it, it already owns) fixes that without touching
    the cached contents.
    """

    subprocess.run(
        ["docker", "volume", "create", name],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if not run_as_uid:
        return
    chown = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{name}:{_CACHE_MOUNT}",
            image,
            "chown",
            run_as_uid,
            _CACHE_MOUNT,
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if chown.returncode != 0:
        raise SandboxError(
            f"could not chown cache volume {name} to {run_as_uid}: "
            f"{(chown.stderr or chown.stdout).strip()}"
        )


def run_in_sandbox(
    workspace: Path,
    command: list[str],
    *,
    repository_id: int,
    network: bool = False,
    settings: Settings | None = None,
) -> SandboxResult:
    """Run `command` in `/workspace` inside a throwaway, resource-limited container.

    Raises `SandboxError` when the sandbox itself cannot run (no image
    configured, docker missing, daemon unreachable) — this must never be
    silently treated as a passed check.
    """

    settings = settings or get_settings()
    if not settings.sandbox_image:
        raise SandboxError("SANDBOX_IMAGE is not configured")
    if shutil.which("docker") is None:
        raise SandboxError("docker executable not found on PATH")
    if not workspace.is_dir():
        raise SandboxError(f"workspace does not exist: {workspace}")

    cache_name = cache_volume_name(repository_id)
    _ensure_cache_volume(cache_name, run_as_uid=settings.sandbox_run_as_uid or None, image=settings.sandbox_image)

    container_name = f"architectos-sandbox-{uuid.uuid4().hex}"
    docker_args = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--cpus",
        settings.sandbox_cpu_limit,
        "--memory",
        settings.sandbox_memory_limit,
        "--network",
        "bridge" if network else "none",
        "-v",
        f"{workspace}:{_WORKSPACE_MOUNT}:rw",
        "-v",
        f"{cache_name}:{_CACHE_MOUNT}:rw",
        "-w",
        _WORKSPACE_MOUNT,
        *_cache_env_args(network=network),
    ]
    if settings.sandbox_run_as_uid:
        docker_args += ["--user", settings.sandbox_run_as_uid]
    docker_args += [settings.sandbox_image, *command]

    started = time.monotonic()
    try:
        completed = subprocess.run(
            docker_args,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.sandbox_timeout_seconds,
            check=False,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "sandbox run finished (container=%s, exit_code=%s, duration_ms=%s, command=%s)",
            container_name,
            completed.returncode,
            duration_ms,
            command,
        )
        return SandboxResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=duration_ms,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        subprocess.run(["docker", "kill", container_name], capture_output=True, check=False)
        logger.warning(
            "sandbox run timed out (container=%s, timeout_s=%s, command=%s)",
            container_name,
            settings.sandbox_timeout_seconds,
            command,
        )
        return SandboxResult(
            command=command,
            exit_code=None,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
            duration_ms=duration_ms,
            timed_out=True,
        )


def _require_sandbox(workspace: Path, settings: Settings) -> None:
    if not settings.sandbox_image:
        raise SandboxError("SANDBOX_IMAGE is not configured")
    if shutil.which("docker") is None:
        raise SandboxError("docker executable not found on PATH")
    if not workspace.is_dir():
        raise SandboxError(f"workspace does not exist: {workspace}")


def _parse_published_port(docker_port_output: str) -> int:
    line = docker_port_output.strip().splitlines()[0] if docker_port_output.strip() else ""
    if ":" not in line:
        raise SandboxError(f"docker port returned no host port: {docker_port_output!r}")
    try:
        return int(line.rsplit(":", 1)[1].strip())
    except ValueError as exc:
        raise SandboxError(f"docker port returned no host port: {docker_port_output!r}") from exc


def _wait_for_http(url: str, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "no attempt"
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=2.0, follow_redirects=False) as client:
                client.get(url)
            return
        except httpx.HTTPError as exc:
            last_error = str(exc)
            time.sleep(0.25)
    raise SandboxError(f"preview server did not become ready at {url}: {last_error}")


@contextmanager
def serve_in_sandbox(
    workspace: Path,
    command: list[str],
    *,
    repository_id: int,
    settings: Settings | None = None,
) -> Iterator[PreviewServer]:
    """Start `command` as a long-running HTTP server inside Docker.

    Publishes the container port to 127.0.0.1 only. Generated code still
    does not run on the host; Playwright on the host talks to the mapped
    loopback port. Always killed in `finally`.
    """

    settings = settings or get_settings()
    _require_sandbox(workspace, settings)
    cache_name = cache_volume_name(repository_id)
    _ensure_cache_volume(cache_name, run_as_uid=settings.sandbox_run_as_uid or None, image=settings.sandbox_image)

    container_port = settings.sandbox_preview_port
    container_name = f"architectos-preview-{uuid.uuid4().hex}"
    docker_args = [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "--cpus",
        settings.sandbox_cpu_limit,
        "--memory",
        settings.sandbox_memory_limit,
        "--network",
        "bridge",
        "-p",
        f"127.0.0.1:0:{container_port}",
        "-v",
        f"{workspace}:{_WORKSPACE_MOUNT}:rw",
        "-v",
        f"{cache_name}:{_CACHE_MOUNT}:rw",
        "-w",
        _WORKSPACE_MOUNT,
        *_cache_env_args(network=True),
        "-e",
        f"PORT={container_port}",
        "-e",
        "HOSTNAME=0.0.0.0",
        "-e",
        "HOST=0.0.0.0",
    ]
    if settings.sandbox_run_as_uid:
        docker_args += ["--user", settings.sandbox_run_as_uid]
    docker_args += [settings.sandbox_image, *command]

    started = subprocess.run(
        docker_args, capture_output=True, encoding="utf-8", errors="replace", check=False
    )
    if started.returncode != 0:
        raise SandboxError(
            f"preview container failed to start: {(started.stderr or started.stdout).strip()}"
        )

    try:
        published = subprocess.run(
            ["docker", "port", container_name, f"{container_port}/tcp"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if published.returncode != 0 or not published.stdout.strip():
            logs = subprocess.run(
                ["docker", "logs", container_name],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            raise SandboxError(
                "preview container published no host port: "
                f"{(published.stderr or logs.stderr or logs.stdout).strip()}"
            )
        host_port = _parse_published_port(published.stdout)
        base_url = f"http://127.0.0.1:{host_port}"
        try:
            _wait_for_http(base_url + "/", timeout_seconds=settings.sandbox_preview_ready_seconds)
        except SandboxError:
            logs = subprocess.run(
                ["docker", "logs", container_name],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            detail = (logs.stderr or logs.stdout or "").strip()[-2000:]
            raise SandboxError(
                f"preview server did not become ready at {base_url}"
                + (f": {detail}" if detail else "")
            ) from None
        logger.info(
            "sandbox preview ready (container=%s, base_url=%s, command=%s)",
            container_name,
            base_url,
            command,
        )
        yield PreviewServer(
            container_name=container_name, base_url=base_url, host_port=host_port
        )
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)
