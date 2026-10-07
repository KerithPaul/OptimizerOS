"""Tree-sitter extraction on golden project A (step 2.C.1 verify)."""

from pathlib import Path

from app.intelligence.repository.ast import (
    ExtractResult,
    _maybe_api_route,
    _next_app_route,
    extract_repository,
)
from app.intelligence.repository.filter import filter_repository

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN_A = _REPO_ROOT / "testdata" / "golden-projects" / "a-nextjs-ts-mysql"


def _extract_a():
    filtered = filter_repository(_GOLDEN_A)
    return extract_repository(_GOLDEN_A, filtered.included_paths)


def _symbol(result, kind: str, name: str):
    matches = [s for s in result.symbols if s.kind == kind and s.name == name]
    assert matches, f"missing {kind} {name}"
    return matches[0]


def test_golden_a_symbols_have_correct_file_and_line_spans() -> None:
    result = _extract_a()
    assert result.unparsed_count == 0
    assert result.parsed_count >= 5

    product_page = _symbol(result, "Component", "ProductPage")
    assert product_page.file_path == "app/products/[slug]/page.tsx"
    assert product_page.start_line == 12
    assert product_page.end_line == 23

    metadata_fn = _symbol(result, "Function", "generateMetadata")
    assert metadata_fn.file_path == "app/products/[slug]/page.tsx"
    assert metadata_fn.start_line == 3
    assert metadata_fn.end_line == 10
    assert metadata_fn.role == "metadata"

    get_product = _symbol(result, "Function", "getProduct")
    assert get_product.file_path == "lib/product-service.ts"
    assert get_product.start_line == 3
    assert get_product.end_line == 5

    query = _symbol(result, "Function", "query")
    assert query.file_path == "lib/db.ts"
    assert query.start_line == 3
    assert query.end_line == 10

    route = _symbol(result, "Route", "/products/[slug]")
    assert route.file_path == "app/products/[slug]/page.tsx"


def test_golden_a_extracts_calls_imports_and_routes() -> None:
    result = _extract_a()
    calls = {(r.from_name, r.to_name) for r in result.relations if r.type == "CALLS"}
    assert ("ProductPage", "getProduct") in calls
    assert ("generateMetadata", "getProduct") in calls
    assert ("getProduct", "query") in calls
    assert ("query", "query") not in calls

    routes = {(r.from_name, r.to_name) for r in result.relations if r.type == "ROUTES_TO"}
    assert ("ProductPage", "/products/[slug]") in routes

    imports = {(r.from_name, r.to_name) for r in result.relations if r.type == "IMPORTS"}
    assert ("ProductPage", "getProduct") in imports
    assert ("getProduct", "query") in imports


def test_next_app_route_supports_src_app_layout() -> None:
    """Next.js projects commonly nest the app router under `src/` (as in Dr. Moksha)."""
    assert _next_app_route("src/app/page.tsx") == "/"
    assert _next_app_route("src/app/admin/(authed)/dashboard/page.tsx") == "/admin/dashboard"
    assert _next_app_route("app/products/[slug]/page.tsx") == "/products/[slug]"
    assert _next_app_route("lib/product-service.ts") is None


def test_maybe_api_route_supports_src_app_layout() -> None:
    result = ExtractResult()
    _maybe_api_route("src/app/api/widgets/route.ts", result)
    apis = [s for s in result.symbols if s.kind == "API"]
    assert len(apis) == 1
    assert apis[0].name == "/api/widgets"


def test_parse_failure_is_recorded_and_does_not_abort(tmp_path: Path) -> None:
    (tmp_path / "ok.ts").write_text("export function fine() { return 1; }\n", encoding="utf-8")
    (tmp_path / "bad.ts").write_text("export function (\n", encoding="utf-8")

    result = extract_repository(tmp_path, ["ok.ts", "bad.ts"])
    assert result.unparsed_count == 1
    assert result.unparsed_files[0]["path"] == "bad.ts"
    names = {s.name for s in result.symbols if s.kind == "Function"}
    assert "fine" in names
    assert result.parsed_count == 1
