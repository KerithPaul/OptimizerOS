"""Grounding of planner/agent file paths in the real workspace."""

from __future__ import annotations

from app.agents.code import CodeAgentResult
from app.agents.runtime import AgentRunOutcome, AgentRunStatus
from app.changes.grounding import (
    detect_cms,
    files_for_typescript_error,
    find_navigation_excerpts,
    heading_components_of,
    is_creatable_path,
    list_workspace_files,
    metadata_helpers_of,
    public_image_fallbacks,
    supporting_files_for,
    type_names_from_build_error,
    workspace_has_file,
)
from app.changes.validate import _plain
from app.jobs.handlers.code_change import (
    build_failure_detail,
    repair_feedback,
    repairable_failure,
    seo_failure_detail,
)


def _write(root, rel, text="export {};\n"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_list_workspace_files_skips_dependencies_lockfiles_and_binaries(tmp_path) -> None:
    _write(tmp_path, "src/components/Header.tsx")
    _write(tmp_path, "node_modules/pkg/index.js")
    _write(tmp_path, ".next/server/page.js")
    _write(tmp_path, "pnpm-lock.yaml", "lock")
    _write(tmp_path, "public/logo.png", "bin")

    assert list_workspace_files(tmp_path) == ["src/components/Header.tsx"]


def test_public_image_fallbacks_prefers_logo_over_hero(tmp_path) -> None:
    _write(tmp_path, "public/hero.png", "bin")
    _write(tmp_path, "public/logo.png", "bin")
    _write(tmp_path, "public/icon.png", "bin")
    _write(tmp_path, "src/notes.ts")

    found = public_image_fallbacks(tmp_path)

    assert found[0] == "public/logo.png"
    assert "public/icon.png" in found
    assert "src/notes.ts" not in found


def test_workspace_has_file_rejects_traversal_and_directories(tmp_path) -> None:
    _write(tmp_path, "src/a.ts")
    (tmp_path.parent / "outside.ts").write_text("x", encoding="utf-8")

    assert workspace_has_file(tmp_path, "src/a.ts")
    assert workspace_has_file(tmp_path, "./src/a.ts")
    assert not workspace_has_file(tmp_path, "src")
    assert not workspace_has_file(tmp_path, "../outside.ts")
    assert not workspace_has_file(tmp_path, "src/missing.ts")


def test_only_standard_site_files_are_creatable() -> None:
    assert is_creatable_path("public/llms.txt")
    assert is_creatable_path("public/sitemap-index.xml")
    assert not is_creatable_path("src/lib/navigation/siteNav.ts")


def test_navigation_data_files_come_before_navigation_components(tmp_path) -> None:
    _write(tmp_path, "src/components/Header.tsx", "export default function Header() { return null; }\n")
    _write(
        tmp_path,
        "src/lib/cms/defaults/global.ts",
        'export const g = {\n  nav: [\n    { label: "Home", href: "/" },\n  ],\n};\n',
    )
    _write(tmp_path, "src/lib/utils.ts")

    rows = find_navigation_excerpts(tmp_path)

    assert [row["locator"] for row in rows] == [
        "src/lib/cms/defaults/global.ts",
        "src/components/Header.tsx",
    ]
    assert 'label: "Home"' in rows[0]["summary"]


def test_detect_cms_reads_dependencies_and_ignores_plain_prose(tmp_path) -> None:
    _write(tmp_path, "src/notes.ts", "// a sanity check\n")
    assert detect_cms(tmp_path) is None
    _write(tmp_path, "src/lib/cms/fetch.ts", "const base = process.env.STRAPI_URL;\n")
    assert detect_cms(tmp_path) == "Strapi"


def test_ansi_colour_codes_are_stripped_from_tool_output() -> None:
    assert _plain("\x1b[31m\x1b[1m>\x1b[0m import") == "> import"


def test_build_failure_detail_ignores_install_and_timeouts() -> None:
    def row(status, detail):
        return {"check_type": "build", "status": status, "detail": detail}

    assert build_failure_detail([row("failed", "install: ERR_PNPM")]) is None
    assert build_failure_detail([row("failed", "timed out after 600s")]) is None
    assert build_failure_detail([row("passed", "ok")]) is None
    assert build_failure_detail(
        [row("passed", "install: done"), row("failed", "Type error: Cannot find module '../types'")]
    ) == "Type error: Cannot find module '../types'"
    assert build_failure_detail([{"check_type": "lint", "status": "failed", "detail": "x"}]) is None


def test_repair_feedback_names_the_failed_patch_and_the_error() -> None:
    from app.agents.code import CodePatch, FileChange

    patch = CodePatch(
        finding_id="f",
        files=[FileChange(file_path="src/a.ts", new_content="x", change_summary="add nav entry")],
        notes="",
    )
    result = CodeAgentResult(
        outcome=AgentRunOutcome(
            status=AgentRunStatus.SUCCEEDED, output=None, iterations_used=1, tool_calls_used=0,
            tokens_used=0, files_modified=1, stopped_reason=None, messages=[],
        ),
        patch=patch, diffs=[], violation=None, wrote_files=True,
    )

    text = repair_feedback(result, "Type error: Cannot find module '../types'")

    assert "src/a.ts (add nav entry)" in text
    assert "Cannot find module '../types'" in text
    assert "FAILED" in text


def test_seo_failure_detail_and_repairable_failure_prefer_build() -> None:
    seo = {"check_type": "seo", "status": "failed", "detail": "missing type, image"}
    build = {"check_type": "build", "status": "failed", "detail": "Type error: x"}
    assert seo_failure_detail([seo]) == "missing type, image"
    assert seo_failure_detail([{"check_type": "seo", "status": "passed", "detail": "ok"}]) is None
    assert repairable_failure([seo]) == ("seo", "missing type, image")
    assert repairable_failure([build, seo]) == ("build", "Type error: x")


def test_repair_feedback_seo_kind_names_required_og_tags() -> None:
    from app.agents.code import CodePatch, FileChange

    patch = CodePatch(
        finding_id="f",
        files=[FileChange(file_path="src/lib/seo/metadata.ts", new_content="x", change_summary="add og:type")],
        notes="",
    )
    result = CodeAgentResult(
        outcome=AgentRunOutcome(
            status=AgentRunStatus.SUCCEEDED, output=None, iterations_used=1, tool_calls_used=0,
            tokens_used=0, files_modified=1, stopped_reason=None, messages=[],
        ),
        patch=patch, diffs=[], violation=None, wrote_files=True,
    )

    text = repair_feedback(
        result,
        "https://drmoksha.com/contact: rule SEO-OG-INCOMPLETE-001 still fires (missing image)",
        kind="seo",
    )

    assert "SEO validation FAILED" in text
    assert "missing image" in text
    assert "images: []" in text
    assert "og:image" in text


_WRAPPER = """import { getContent } from "@/lib/cms/fetch";
import { CmsProvider } from "@/components/editor/CmsProvider";
import ContactPage from "@/components/pages/ContactPage";
import Plain from "./Plain";

export default async function Contact() {
  return (
    <CmsProvider>
      <ContactPage />
      <Plain />
    </CmsProvider>
  );
}
"""


def test_heading_components_of_follows_rendered_imports_that_hold_headings(tmp_path) -> None:
    _write(tmp_path, "src/app/contact/page.tsx", _WRAPPER)
    _write(tmp_path, "src/components/pages/ContactPage.tsx", '<EText as="h3" path="title" />')
    _write(tmp_path, "src/components/editor/CmsProvider.tsx", "export const CmsProvider = () => null;")
    _write(tmp_path, "src/app/contact/Plain.tsx", "export default () => <p>x</p>;")
    _write(tmp_path, "src/lib/cms/fetch.ts", "// <h1>")

    assert heading_components_of(tmp_path, "src/app/contact/page.tsx") == [
        "src/components/pages/ContactPage.tsx"
    ]


def test_heading_components_of_ignores_imports_that_are_not_rendered(tmp_path) -> None:
    _write(
        tmp_path,
        "src/app/page.tsx",
        'import Hero from "@/components/Hero";\nexport default () => null;\n',
    )
    _write(tmp_path, "src/components/Hero.tsx", "<h1>Hi</h1>")

    assert heading_components_of(tmp_path, "src/app/page.tsx") == []


_OG_PAGE = '''import type { Metadata } from "next";
import { getContent } from "@/lib/cms/fetch";
import ContactPage from "@/components/pages/ContactPage";
import { toNextMetadata } from "@/lib/seo/metadata";

export async function generateMetadata(): Promise<Metadata> {
  const content = await getContent("contact-page");
  return toNextMetadata({ seo: content.seo, path: "/contact", fallbackTitle: "Contact" });
}
'''

_OG_HELPER = '''import type { SeoComponent } from "@/lib/cms/types";
export function toNextMetadata({ seo }: { seo?: SeoComponent }) {
  const ogImage = seo?.openGraph?.ogImage?.url;
  return { openGraph: { ...(ogImage ? { images: [{ url: ogImage }] } : {}) } };
}
'''

_OG_TYPES = '''export interface CmsImage { mediaId: number | null; url: string; }
export interface SeoOpenGraph { ogTitle?: string; ogImage?: CmsImage | null; }
export interface SeoComponent { openGraph?: SeoOpenGraph; }
'''


def test_metadata_helpers_of_follows_toNextMetadata_import(tmp_path) -> None:
    _write(tmp_path, "src/app/contact/page.tsx", _OG_PAGE)
    _write(tmp_path, "src/lib/seo/metadata.ts", _OG_HELPER)
    _write(tmp_path, "src/lib/cms/types.ts", _OG_TYPES)
    _write(tmp_path, "src/lib/cms/fetch.ts", "export async function getContent() { return {}; }")
    _write(tmp_path, "src/components/pages/ContactPage.tsx", "export default function ContactPage() { return null; }")

    assert metadata_helpers_of(tmp_path, "src/app/contact/page.tsx") == ["src/lib/seo/metadata.ts"]


def test_supporting_files_include_imported_types_and_skip_large_pages(tmp_path) -> None:
    _write(tmp_path, "src/app/contact/page.tsx", _OG_PAGE)
    _write(tmp_path, "src/lib/seo/metadata.ts", _OG_HELPER)
    _write(tmp_path, "src/lib/cms/types.ts", _OG_TYPES)
    _write(tmp_path, "src/lib/cms/fetch.ts", "export async function getContent() { return {}; }")
    _write(tmp_path, "src/components/pages/ContactPage.tsx", "x" * 25_000)

    paths = supporting_files_for(tmp_path, ["src/app/contact/page.tsx"])

    assert "src/lib/seo/metadata.ts" in paths
    assert "src/lib/cms/types.ts" in paths
    assert "src/components/pages/ContactPage.tsx" not in paths


def test_type_names_from_build_error_keep_project_types() -> None:
    error = (
        "Failed to type check.\n"
        "./src/app/contact/page.tsx:19:9\n"
        "Type error: Type 'string | undefined' is not assignable to type "
        "'CmsImage | null | undefined'.\n"
    )
    assert type_names_from_build_error(error) == ["CmsImage"]


def test_files_for_typescript_error_finds_the_named_type(tmp_path) -> None:
    _write(tmp_path, "src/app/contact/page.tsx", _OG_PAGE)
    _write(tmp_path, "src/lib/cms/types.ts", _OG_TYPES)
    error = "Type error: Type 'string' is not assignable to type 'CmsImage'."

    assert files_for_typescript_error(tmp_path, error, ["src/app/contact/page.tsx"]) == [
        "src/lib/cms/types.ts"
    ]
