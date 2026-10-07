"""Deterministic repository profiler (step 2.B.4).

Inspects manifests and framework config first. Remaining ambiguity is the
only path that may call an LLM, and any file content sent there goes through
the untrusted project-content layer. Golden project A must profile without
an LLM call.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.intelligence.repository.filter import filter_repository
from app.llm.prompts import PromptBuilder
from app.llm.validation import parse_structured_with_retry

# Completer receives the already-layered chat messages and returns raw JSON.
AmbiguityCompleter = Callable[[list[dict[str, str]]], str]

_SNIPPET_CHAR_BUDGET = 8000


class ArchitectureProfile(BaseModel):
    language: str | None = None
    framework: str | None = None
    frontend: str | None = None
    backend: str | None = None
    database: str | None = None
    cms: str | None = None
    package_manager: str | None = None
    build_system: str | None = None
    rendering: list[str] = Field(default_factory=list)
    routing: str | None = None
    metadata_implementation: str | None = None
    schema_implementation: str | None = None
    seo_libraries: list[str] = Field(default_factory=list)
    deployment_configuration: str | None = None
    used_llm: bool = False
    unresolved: list[str] = Field(default_factory=list)


class ArchitectureLLMFill(BaseModel):
    """Optional fields an LLM may fill. Deterministic values are never overwritten."""

    language: str | None = None
    framework: str | None = None
    frontend: str | None = None
    backend: str | None = None
    database: str | None = None
    cms: str | None = None
    package_manager: str | None = None
    build_system: str | None = None
    rendering: list[str] | None = None
    routing: str | None = None
    metadata_implementation: str | None = None
    schema_implementation: str | None = None
    seo_libraries: list[str] | None = None
    deployment_configuration: str | None = None


_SEO_PACKAGES = (
    "next-seo",
    "react-helmet",
    "react-helmet-async",
    "@unhead/vue",
)
_SCHEMA_PACKAGES = (
    "schema-dts",
    "next-json-ld",
    "jsonld",
)


def profile_repository(
    root: Path,
    *,
    completer: AmbiguityCompleter | None = None,
) -> ArchitectureProfile:
    root = root.resolve()
    profile = ArchitectureProfile()
    for manifest_root in _manifest_roots(root):
        _apply_package_json(manifest_root, profile)
        _apply_python_manifests(manifest_root, profile)
        _apply_other_manifests(manifest_root, profile)
        _apply_lockfiles(manifest_root, profile)
    _apply_next_layout(root, profile)
    _apply_deployment(root, profile)
    _apply_compose_database(root, profile)
    _apply_language_fallback(root, profile)

    profile.unresolved = _unresolved_fields(profile)
    # Optional fields (CMS, schema, deployment) being empty is not ambiguity.
    # The LLM is only consulted when language or framework could not be read
    # from manifests — AGENTS.md §10 / step 2.B.4.
    if (
        completer is not None
        and (profile.language is None or profile.framework is None)
    ):
        _fill_from_llm(root, profile, completer)
        profile.used_llm = True
        profile.unresolved = _unresolved_fields(profile)
    return profile


def _manifest_roots(root: Path) -> list[Path]:
    roots = [root]
    for name in ("frontend", "backend", "client", "server", "web", "api"):
        sub = root / name
        if sub.is_dir():
            roots.append(sub)
    return roots


def _apply_package_json(root: Path, profile: ArchitectureProfile) -> None:
    data = _read_json(root / "package.json")
    if data is None:
        return
    deps = _deps(data)
    if profile.package_manager is None:
        profile.package_manager = "npm"

    if "next" in deps:
        profile.framework = "Next.js"
        profile.frontend = profile.frontend or "React"
        profile.build_system = profile.build_system or "Next.js"
        profile.rendering = profile.rendering or ["SSR", "SSG"]
        if (root / "app").is_dir():
            profile.routing = profile.routing or "App Router"
        elif (root / "pages").is_dir():
            profile.routing = profile.routing or "Pages Router"
            profile.rendering = ["SSR", "SSG", "CSR"]
        if _next_output_export(root):
            profile.rendering = ["SSG"]
    elif "react" in deps:
        profile.frontend = profile.frontend or "React"
        profile.framework = profile.framework or "React"

    if "vue" in deps and profile.framework is None:
        profile.framework = "Vue"
        profile.frontend = "Vue"
    if "nuxt" in deps:
        profile.framework = "Nuxt"
        profile.frontend = "Vue"

    if "typescript" in deps or (root / "tsconfig.json").exists():
        profile.language = profile.language or "TypeScript"
    elif profile.language is None:
        profile.language = "JavaScript"

    if any(name in deps for name in ("mysql2", "mysql", "promise-mysql")):
        profile.database = profile.database or "MySQL"
    if "pg" in deps or "postgres" in deps:
        profile.database = profile.database or "PostgreSQL"

    seo = [name for name in _SEO_PACKAGES if name in deps]
    if seo:
        profile.seo_libraries = seo
    schema = [name for name in _SCHEMA_PACKAGES if name in deps]
    if schema:
        profile.schema_implementation = ", ".join(schema)

    if (root / "next.config.ts").exists() or (root / "next.config.js").exists() or (
        root / "next.config.mjs"
    ).exists():
        profile.framework = profile.framework or "Next.js"
        profile.build_system = profile.build_system or "Next.js"


def _apply_python_manifests(root: Path, profile: ArchitectureProfile) -> None:
    pyproject = _read_toml(root / "pyproject.toml")
    requirements = _read_text(root / "requirements.txt") or ""
    python_deps = set()
    if pyproject:
        python_deps.update(_pyproject_deps(pyproject))
        if pyproject.get("tool", {}).get("uv") is not None:
            profile.package_manager = profile.package_manager or "uv"
        if pyproject.get("tool", {}).get("poetry") is not None:
            profile.package_manager = profile.package_manager or "poetry"
    if requirements:
        python_deps.update(_requirement_names(requirements))
        profile.package_manager = profile.package_manager or "pip"
    if (root / "uv.lock").exists():
        profile.package_manager = profile.package_manager or "uv"

    if not python_deps and pyproject is None and not requirements:
        return

    profile.language = profile.language or "Python"
    if "fastapi" in python_deps:
        profile.backend = profile.backend or "FastAPI"
        profile.framework = profile.framework or "FastAPI"
    if "django" in python_deps:
        profile.framework = profile.framework or "Django"
        profile.backend = profile.backend or "Django"
        profile.cms = profile.cms or "Django"
    if any(name in python_deps for name in ("pymysql", "mysqlclient", "mysql-connector-python")):
        profile.database = profile.database or "MySQL"
    if any(name in python_deps for name in ("psycopg", "psycopg2", "psycopg2-binary")):
        profile.database = profile.database or "PostgreSQL"
    if "sqlalchemy" in python_deps and profile.backend is None:
        profile.backend = profile.backend or "Python"


def _apply_other_manifests(root: Path, profile: ArchitectureProfile) -> None:
    if (root / "go.mod").exists():
        profile.language = profile.language or "Go"
        profile.build_system = profile.build_system or "Go"
    if (root / "Cargo.toml").exists():
        profile.language = profile.language or "Rust"
        profile.build_system = profile.build_system or "Cargo"
        profile.package_manager = profile.package_manager or "cargo"
    if (root / "pom.xml").exists():
        profile.language = profile.language or "Java"
        profile.build_system = profile.build_system or "Maven"
    composer = _read_json(root / "composer.json")
    if composer is not None:
        profile.language = profile.language or "PHP"
        profile.package_manager = profile.package_manager or "composer"
        extras = json.dumps(composer).lower()
        if "wordpress" in extras or composer.get("type") == "wordpress-theme":
            profile.cms = profile.cms or "WordPress"
            profile.framework = profile.framework or "WordPress"


def _apply_lockfiles(root: Path, profile: ArchitectureProfile) -> None:
    if (root / "pnpm-lock.yaml").exists():
        profile.package_manager = "pnpm"
    elif (root / "yarn.lock").exists():
        profile.package_manager = "yarn"
    elif (root / "package-lock.json").exists():
        profile.package_manager = "npm"
    elif (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        profile.package_manager = "bun"


def _apply_next_layout(root: Path, profile: ArchitectureProfile) -> None:
    if profile.framework != "Next.js":
        return
    layout = root / "app" / "layout.tsx"
    if not layout.exists():
        layout = root / "app" / "layout.js"
    text = _read_text(layout) or ""
    if "generateMetadata" in text or re.search(r"export const metadata", text):
        profile.metadata_implementation = "Next.js metadata API"


def _apply_deployment(root: Path, profile: ArchitectureProfile) -> None:
    found: list[str] = []
    if (root / "Dockerfile").exists() or (root / "dockerfile").exists():
        found.append("Dockerfile")
    if (root / "docker-compose.yml").exists() or (root / "docker-compose.yaml").exists():
        found.append("docker-compose")
    if (root / "vercel.json").exists():
        found.append("Vercel")
    if (root / "fly.toml").exists():
        found.append("Fly.io")
    if (root / ".github" / "workflows").is_dir():
        found.append("GitHub Actions")
    if found:
        profile.deployment_configuration = ", ".join(found)


def _apply_compose_database(root: Path, profile: ArchitectureProfile) -> None:
    if profile.database is not None:
        return
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml"):
        text = _read_text(root / name)
        if text and re.search(r"image:\s*mysql", text, re.I):
            profile.database = "MySQL"
            return
        if text and re.search(r"image:\s*postgres", text, re.I):
            profile.database = "PostgreSQL"
            return


def _apply_language_fallback(root: Path, profile: ArchitectureProfile) -> None:
    if profile.language is not None:
        return
    filtered = filter_repository(root)
    counts: dict[str, int] = {}
    for rel in filtered.included_paths:
        suffix = Path(rel).suffix.lower()
        if suffix in {".ts", ".tsx"}:
            counts["TypeScript"] = counts.get("TypeScript", 0) + 1
        elif suffix in {".js", ".jsx"}:
            counts["JavaScript"] = counts.get("JavaScript", 0) + 1
        elif suffix == ".py":
            counts["Python"] = counts.get("Python", 0) + 1
        elif suffix == ".go":
            counts["Go"] = counts.get("Go", 0) + 1
        elif suffix == ".rs":
            counts["Rust"] = counts.get("Rust", 0) + 1
        elif suffix == ".php":
            counts["PHP"] = counts.get("PHP", 0) + 1
    if counts:
        profile.language = max(counts, key=counts.get)  # type: ignore[arg-type]


def _unresolved_fields(profile: ArchitectureProfile) -> list[str]:
    unresolved: list[str] = []
    for name in (
        "language",
        "framework",
        "frontend",
        "backend",
        "database",
        "cms",
        "package_manager",
        "build_system",
        "routing",
        "metadata_implementation",
        "schema_implementation",
        "deployment_configuration",
    ):
        if getattr(profile, name) in (None, "", []):
            unresolved.append(name)
    if not profile.rendering:
        unresolved.append("rendering")
    return unresolved


def _fill_from_llm(
    root: Path,
    profile: ArchitectureProfile,
    completer: AmbiguityCompleter,
) -> None:
    snippets = _untrusted_snippets(root)
    builder = (
        PromptBuilder()
        .set_system(
            "You complete a repository architecture profile. Reply with a single "
            "JSON object. Keys you may set: language, framework, frontend, backend, "
            "database, cms, package_manager, build_system, rendering (array of "
            "SSR/SSG/CSR), routing, metadata_implementation, schema_implementation, "
            "seo_libraries (array), deployment_configuration. Use null when unknown. "
            "Do not invent files or dependencies that are not in the content."
        )
        .add_trusted_tool_output(
            json.dumps(
                {
                    "current_profile": profile.model_dump(),
                    "unresolved": profile.unresolved,
                },
                indent=2,
            ),
            source="deterministic_profiler",
        )
        .add_untrusted_project_content(snippets, source="repository-files")
    )
    messages = builder.build_messages()
    fill = parse_structured_with_retry(
        lambda: completer(messages),
        ArchitectureLLMFill,
    )
    _merge_fill(profile, fill)


def _merge_fill(profile: ArchitectureProfile, fill: ArchitectureLLMFill) -> None:
    for name in (
        "language",
        "framework",
        "frontend",
        "backend",
        "database",
        "cms",
        "package_manager",
        "build_system",
        "routing",
        "metadata_implementation",
        "schema_implementation",
        "deployment_configuration",
    ):
        current = getattr(profile, name)
        incoming = getattr(fill, name)
        if current in (None, "") and incoming not in (None, ""):
            setattr(profile, name, incoming)
    if not profile.rendering and fill.rendering:
        profile.rendering = fill.rendering
    if not profile.seo_libraries and fill.seo_libraries:
        profile.seo_libraries = fill.seo_libraries


def _untrusted_snippets(root: Path) -> str:
    filtered = filter_repository(root)
    parts: list[str] = []
    remaining = _SNIPPET_CHAR_BUDGET
    preferred = ("README.md", "README", "notes.txt", "package.json")
    ordered = [p for p in preferred if p in filtered.included_paths]
    ordered.extend(p for p in filtered.included_paths if p not in ordered)
    for rel in ordered:
        if remaining <= 0:
            break
        text = _read_text(root / rel)
        if not text:
            continue
        chunk = text[: min(len(text), remaining)]
        parts.append(f"--- {rel} ---\n{chunk}")
        remaining -= len(chunk)
    return "\n\n".join(parts) if parts else "(no readable files)"


def _next_output_export(root: Path) -> bool:
    for name in ("next.config.ts", "next.config.js", "next.config.mjs"):
        text = _read_text(root / name)
        if text and re.search(r"output\s*:\s*['\"]export['\"]", text):
            return True
    return False


def _deps(package: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = package.get(key) or {}
        if isinstance(section, dict):
            names.update(section)
    return names


def _pyproject_deps(data: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    project = data.get("project") or {}
    for item in project.get("dependencies") or []:
        if isinstance(item, str):
            names.add(re.split(r"[<>=\[]", item, maxsplit=1)[0].strip().lower())
    optional = project.get("optional-dependencies") or {}
    if isinstance(optional, dict):
        for group in optional.values():
            for item in group or []:
                if isinstance(item, str):
                    names.add(re.split(r"[<>=\[]", item, maxsplit=1)[0].strip().lower())
    return names


def _requirement_names(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        names.add(re.split(r"[<>=\[]", line, maxsplit=1)[0].strip().lower())
    return names


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _read_toml(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
    except UnicodeDecodeError:
        return None
