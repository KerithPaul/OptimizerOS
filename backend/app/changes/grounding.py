"""Ground LLM-chosen file paths in the real workspace.

The Change Planner only sees a few retrieved snippets, so left alone it names
plausible-looking files (`Navbar.tsx`, `siteNav.ts`) that the repository does
not have, and the Code Agent then writes them from scratch with imports that
resolve to nothing. This module gives both a deterministic view of what is
actually on disk, and the one narrow exception: standard site files a Finding
can legitimately ask to be created.
"""

from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path

WORKSPACE_EXCLUDE_DIRS = frozenset(
    {".git", "node_modules", ".next", ".turbo", "dist", "build", ".venv", "__pycache__", "coverage"}
)

# Files a Finding may ask to be *created*. Everything else must already exist.
_CREATABLE_FILE = re.compile(
    r"(^|/)(robots\.txt|llms(?:-full)?\.txt|humans\.txt|security\.txt|ads\.txt|sitemap[^/]*\.xml)$",
    re.IGNORECASE,
)

_LISTED_EXTENSIONS = frozenset(
    {
        ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".astro",
        ".html", ".htm", ".md", ".mdx", ".php", ".py", ".njk", ".liquid", ".hbs", ".ejs",
        ".xml", ".txt", ".json", ".yaml", ".yml", ".toml",
    }
)
_LOCKFILES = frozenset({"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "composer.lock"})
_MAX_FILE_BYTES = 200_000

_NAV_CODE_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte", ".astro", ".html", ".php"})
_NAV_PATH = re.compile(r"(header|footer|navbar|nav|menu|sidebar)", re.IGNORECASE)
_NAV_DATA = re.compile(
    r"\bnav(?:Items|Links|igation)?\s*[:=]\s*\[|\bmenuItems\b|\bfooterLinks\b|\bnavLinks\b"
)
_NAV_EXCERPT_CHARS = 600
_NAV_CONTEXT_LINES = 8

# Specific enough not to fire on prose such as "sanity check".
_CMS_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Strapi", re.compile(r"strapi", re.IGNORECASE)),
    ("Contentful", re.compile(r"contentful", re.IGNORECASE)),
    ("Sanity", re.compile(r"@sanity/|sanity\.io")),
    ("Prismic", re.compile(r"prismic", re.IGNORECASE)),
    ("DatoCMS", re.compile(r"datocms", re.IGNORECASE)),
    ("Storyblok", re.compile(r"storyblok", re.IGNORECASE)),
    ("Directus", re.compile(r"directus", re.IGNORECASE)),
)
_CMS_SCAN_FILES = 300


def normalize_path(path: str) -> str:
    cleaned = posixpath.normpath(path.replace("\\", "/").strip())
    return cleaned[2:] if cleaned.startswith("./") else cleaned.lstrip("/")


def is_creatable_path(path: str) -> bool:
    return _CREATABLE_FILE.search(normalize_path(path)) is not None


def workspace_has_file(workspace: Path, path: str) -> bool:
    """True when `path` is a regular file inside `workspace` (never outside it)."""

    normalized = normalize_path(path)
    if not normalized or normalized == "." or ".." in normalized.split("/") or re.match(r"^[A-Za-z]:", normalized):
        return False
    return (workspace / normalized).is_file()


def _walk(workspace: Path):
    for root, dirs, files in os.walk(workspace):
        dirs[:] = sorted(d for d in dirs if d not in WORKSPACE_EXCLUDE_DIRS)
        for name in sorted(files):
            yield Path(root) / name


def list_workspace_files(workspace: Path, *, limit: int = 400) -> list[str]:
    """Repo-relative paths of the source/content files the planner may target."""

    if not workspace.is_dir():
        return []
    found: list[str] = []
    for path in _walk(workspace):
        if path.name in _LOCKFILES or path.suffix.lower() not in _LISTED_EXTENSIONS:
            continue
        found.append(path.relative_to(workspace).as_posix())
        if len(found) >= limit:
            break
    return found


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


_JS_SUFFIXES = (".tsx", ".jsx", ".ts", ".js")
_IMPORT_STATEMENT = re.compile(r"""^\s*import\s+([^;]*?)\s+from\s+["']([^"']+)["']""", re.MULTILINE)
_IMPORT_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
# A literal heading tag, or a component that takes the tag name (`<EText as="h3">`).
_HEADING_MARKUP = re.compile(r"""<h[1-6]\b|\bas=["']h[1-6]["']""")


def has_heading_markup(text: str) -> bool:
    return _HEADING_MARKUP.search(text) is not None


def _resolve_import(workspace: Path, route_file: str, spec: str) -> str | None:
    """Repo-relative path of a relative or `@/` import that exists in the workspace."""

    if spec.startswith("@/"):
        bases = [f"src/{spec[2:]}", spec[2:]]
    elif spec.startswith("."):
        bases = [posixpath.normpath(posixpath.join(posixpath.dirname(normalize_path(route_file)), spec))]
    else:
        return None
    for base in bases:
        for candidate in (*(base + ext for ext in _JS_SUFFIXES), *(f"{base}/index{ext}" for ext in _JS_SUFFIXES)):
            if workspace_has_file(workspace, candidate):
                return candidate
    return None


def heading_components_of(workspace: Path, route_file: str, *, limit: int = 3) -> list[str]:
    """Components a route file renders that hold heading markup.

    An App Router `page.tsx` is often a thin server wrapper that only renders an
    imported component (`<ContactPage />`); the page's headings live in that
    component. A heading fix aimed at the wrapper has nothing to change, so the
    Code Agent returns an identity patch (`no_op`). Only components the file
    actually renders as JSX are considered, so helper imports are ignored.
    """

    route = normalize_path(route_file)
    if not route.endswith(_JS_SUFFIXES) or not workspace_has_file(workspace, route):
        return []
    text = _read_text(workspace / route)
    if text is None:
        return []
    found: list[str] = []
    for names, spec in _IMPORT_STATEMENT.findall(text):
        identifiers = _IMPORT_IDENTIFIER.findall(names)
        if not any(re.search(rf"<{re.escape(name)}\b", text) for name in identifiers):
            continue
        resolved = _resolve_import(workspace, route, spec)
        if resolved is None or resolved in found:
            continue
        content = _read_text(workspace / resolved)
        if content is not None and has_heading_markup(content):
            found.append(resolved)
            if len(found) >= limit:
                break
    return found


_OG_HELPER_IDENT = re.compile(
    r"^(toNextMetadata|buildMetadata|createMetadata|seoMetadata|generatePageMetadata)$"
)
_OG_HELPER_SPEC = re.compile(r"(?:^|/)(?:seo/)?metadata$")
_MAX_SUPPORT_FILES = 8
_MAX_SUPPORT_BYTES = 20_000
_IMPORT_DEPTH = 2
_TYPE_SCAN_FILES = 400
_TYPES_PATH = re.compile(
    r"(^|/)(types?|schema)(\.d)?\.(ts|tsx)$|/(seo|metadata|jsonld)(/|$)|metadata\.(ts|tsx)$",
    re.IGNORECASE,
)
_PASCAL_TYPE = re.compile(r"\b([A-Z][A-Za-z0-9]*)\b")
_MODULE_SPEC = re.compile(r"Cannot find module ['\"]([^'\"]+)['\"]")
_TYPE_NOISE = frozenset(
    {
        "Type",
        "Error",
        "Failed",
        "Property",
        "IntrinsicAttributes",
        "IntrinsicClassAttributes",
        "Array",
        "Record",
        "Promise",
        "Partial",
        "Required",
        "Pick",
        "Omit",
        "Readonly",
        "ReturnType",
        "Parameters",
        "Component",
        "Function",
        "Object",
        "String",
        "Number",
        "Boolean",
        "Undefined",
        "Null",
        "Never",
        "Unknown",
        "Void",
        "Any",
        "Element",
        "ReactNode",
        "ReactElement",
        "Module",
        "JSX",
        "FC",
        "PropsWithChildren",
    }
)


def metadata_helpers_of(workspace: Path, route_file: str, *, limit: int = 3) -> list[str]:
    """Metadata/OG helpers a route file imports (e.g. `toNextMetadata`).

    Open Graph tags are emitted by the helper, not by stuffing CMS fields into
    `generateMetadata`. Targeting the wrapper page makes the Code Agent invent
    `ogType` / string `ogImage` that fail TypeScript and never reach the HTML.
    """

    route = normalize_path(route_file)
    if not route.endswith(_JS_SUFFIXES) or not workspace_has_file(workspace, route):
        return []
    text = _read_text(workspace / route)
    if text is None:
        return []
    found: list[str] = []
    for names, spec in _IMPORT_STATEMENT.findall(text):
        identifiers = _IMPORT_IDENTIFIER.findall(names)
        spec_base = spec.split("?")[0].rstrip("/")
        if not (
            any(_OG_HELPER_IDENT.match(name) for name in identifiers)
            or _OG_HELPER_SPEC.search(spec_base)
        ):
            continue
        resolved = _resolve_import(workspace, route, spec)
        if resolved is None or resolved in found:
            continue
        found.append(resolved)
        if len(found) >= limit:
            break
    return found


def local_imports_of(workspace: Path, rel: str) -> list[str]:
    """Repo-relative paths of local / `@/` imports from one source file."""

    route = normalize_path(rel)
    if not workspace_has_file(workspace, route):
        return []
    text = _read_text(workspace / route)
    if text is None:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for _names, spec in _IMPORT_STATEMENT.findall(text):
        resolved = _resolve_import(workspace, route, spec)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        found.append(resolved)
    return found


def _looks_like_types_path(path: str) -> bool:
    return _TYPES_PATH.search(path.replace("\\", "/")) is not None


def _keep_supporting(workspace: Path, path: str) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return False
    full = workspace / path
    try:
        size = full.stat().st_size
    except OSError:
        return False
    if size > _MAX_SUPPORT_BYTES and not _looks_like_types_path(path):
        return False
    return True


def type_names_from_build_error(text: str) -> list[str]:
    """PascalCase type names mentioned in a `tsc` / `next build` type error."""

    names: list[str] = []
    seen: set[str] = set()
    for name in _PASCAL_TYPE.findall(text or ""):
        if name in _TYPE_NOISE or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names[:12]


def find_type_definitions(
    workspace: Path,
    names: list[str],
    *,
    exclude: set[str] | None = None,
    limit: int = 4,
) -> list[str]:
    """Workspace files that export one of the named types/functions."""

    if not names or not workspace.is_dir():
        return []
    skip = exclude or set()
    patterns = [
        re.compile(
            rf"(?:export\s+)?(?:interface|type|class|enum|function|const)\s+{re.escape(name)}\b"
        )
        for name in names
    ]
    found: list[str] = []
    scanned = 0
    for path in _walk(workspace):
        if path.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        rel = path.relative_to(workspace).as_posix()
        if rel in skip:
            continue
        text = _read_text(path)
        if text is None:
            continue
        scanned += 1
        if any(pattern.search(text) for pattern in patterns):
            found.append(rel)
            if len(found) >= limit:
                break
        if scanned >= _TYPE_SCAN_FILES:
            break
    return found


def files_for_typescript_error(
    workspace: Path, error: str, target_files: list[str]
) -> list[str]:
    """Type-definition files and missing modules named in a TypeScript build error."""

    exclude = {normalize_path(path) for path in target_files}
    extra = find_type_definitions(
        workspace, type_names_from_build_error(error), exclude=exclude
    )
    seen = set(extra)
    for spec in _MODULE_SPEC.findall(error or ""):
        for target in target_files:
            resolved = _resolve_import(workspace, target, spec)
            if resolved is None or resolved in seen or resolved in exclude:
                continue
            extra.append(resolved)
            seen.add(resolved)
            break
    return extra


def supporting_files_for(
    workspace: Path,
    target_files: list[str],
    *,
    extra_type_names: list[str] | None = None,
    extra_paths: list[str] | None = None,
    max_files: int = _MAX_SUPPORT_FILES,
) -> list[str]:
    """Read-only type/helper files the Code Agent must see to type-check a patch.

    Walks local imports of each target (depth 2), then files that define type
    names from a prior TypeScript error. Large page components are skipped so
    the prompt keeps `types.ts` / metadata helpers rather than a 30kB UI file.
    """

    targets = [normalize_path(path) for path in target_files]
    seen: set[str] = set(targets)
    ordered: list[str] = []
    queue: list[tuple[str, int]] = [
        (path, 0) for path in targets if workspace_has_file(workspace, path)
    ]
    while queue and len(ordered) < max_files:
        path, depth = queue.pop(0)
        if depth >= _IMPORT_DEPTH:
            continue
        for child in local_imports_of(workspace, path):
            if child in seen:
                continue
            seen.add(child)
            if not _keep_supporting(workspace, child):
                continue
            ordered.append(child)
            if depth + 1 < _IMPORT_DEPTH:
                queue.append((child, depth + 1))
            if len(ordered) >= max_files:
                break
    if extra_paths:
        for raw in extra_paths:
            path = normalize_path(raw)
            if path in seen or not workspace_has_file(workspace, path):
                continue
            if not _keep_supporting(workspace, path) and not _looks_like_types_path(path):
                continue
            seen.add(path)
            ordered.append(path)
            if len(ordered) >= max_files:
                break
    if extra_type_names and len(ordered) < max_files:
        for found in find_type_definitions(
            workspace, extra_type_names, exclude=seen, limit=max_files - len(ordered)
        ):
            if found in seen:
                continue
            seen.add(found)
            ordered.append(found)
            if len(ordered) >= max_files:
                break
    return ordered


def find_navigation_excerpts(workspace: Path, *, limit: int = 5) -> list[dict]:
    """Files that define or render site navigation, as planner `existing_code` rows.

    A page nothing links to needs an inbound link from navigation data or a
    header/footer component. Retrieval seeded by the orphan page's URL never
    surfaces those, so look for them directly. Files holding a nav-items array
    come first: that is where the link actually has to be added.
    """

    if not workspace.is_dir():
        return []
    data_hits: list[dict] = []
    path_hits: list[dict] = []
    for path in _walk(workspace):
        if path.suffix.lower() not in _NAV_CODE_EXTENSIONS:
            continue
        rel = path.relative_to(workspace).as_posix()
        by_path = _NAV_PATH.search(path.stem) is not None
        text = _read_text(path)
        if text is None:
            continue
        match = _NAV_DATA.search(text)
        if match is not None:
            lines = text.splitlines()
            line_no = text.count("\n", 0, match.start())
            window = lines[max(0, line_no - 2) : line_no + _NAV_CONTEXT_LINES]
            data_hits.append(
                {"locator": rel, "summary": "navigation data:\n" + "\n".join(window)[:_NAV_EXCERPT_CHARS]}
            )
        elif by_path:
            path_hits.append({"locator": rel, "summary": "navigation component:\n" + text[:_NAV_EXCERPT_CHARS]})
    return (data_hits + path_hits)[:limit]


_PUBLIC_IMAGE_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico"})
_PUBLIC_IMAGE_DIRS = ("public", "static", "src/app", "app")
_PUBLIC_IMAGE_NAME = re.compile(
    r"(logo|icon|favicon|opengraph|\bog[-_]|social|share|default[-_]?og)", re.IGNORECASE
)
_MAX_PUBLIC_IMAGES = 6


def public_image_fallbacks(workspace: Path, *, limit: int = _MAX_PUBLIC_IMAGES) -> list[str]:
    """Existing static images the Code Agent may use as an og:image fallback.

    CMS defaultOgImage is empty in sandbox preview (no Strapi). A helper that
    only emits images when the CMS field is set leaves og:image missing and
    SEO-OG-INCOMPLETE-001 still fires. Paths are repo-relative; `public/x.png`
    is served at `/x.png`.
    """

    if not workspace.is_dir():
        return []
    scored: list[tuple[int, str]] = []
    for folder in _PUBLIC_IMAGE_DIRS:
        root = workspace / folder
        if not root.is_dir():
            continue
        for path in _walk(root):
            if path.suffix.lower() not in _PUBLIC_IMAGE_EXT:
                continue
            try:
                rel = path.relative_to(workspace).as_posix()
            except ValueError:
                continue
            name = path.name.lower()
            score = 0
            if _PUBLIC_IMAGE_NAME.search(name):
                score += 10
            if "logo" in name:
                score += 5
            if folder in {"public", "static"}:
                score += 2
            scored.append((-score, rel))
    scored.sort()
    found: list[str] = []
    for _, rel in scored:
        if rel not in found:
            found.append(rel)
        if len(found) >= limit:
            break
    return found


def detect_cms(workspace: Path) -> str | None:
    """Name of the headless CMS the site reads its content from, if the code shows one."""

    if not workspace.is_dir():
        return None
    scanned = 0
    for path in _walk(workspace):
        if path.name != "package.json" and path.suffix.lower() not in _NAV_CODE_EXTENSIONS:
            continue
        text = _read_text(path)
        if text is None:
            continue
        for name, marker in _CMS_MARKERS:
            if marker.search(text):
                return name
        scanned += 1
        if scanned >= _CMS_SCAN_FILES:
            break
    return None
