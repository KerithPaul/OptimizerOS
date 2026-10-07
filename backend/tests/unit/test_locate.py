"""Workspace locate / no-op gates (Phase 7: identity diffs and missing evidence)."""

from __future__ import annotations

from app.changes.locate import (
    STATUS_EMPTY,
    STATUS_MISSING,
    STATUS_PRESENT,
    WorkspaceFile,
    check_locate,
    check_noop,
    evidence_locators,
    list_workspace_sitemap_files,
    locator_in_content,
    read_workspace_files,
)
from app.changes.scope import diff_stats
from app.knowledge.authority import AuthorityLevel
from app.models.finding import Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity


def _finding(**overrides: object) -> Finding:
    values = {
        "id": 1,
        "project_id": 1,
        "analysis_run_id": 1,
        "finding_id": "SEO-BROKEN-INTERNAL-LINK-001:abc",
        "observation": "Broken internal link",
        "problem": "Broken internal link",
        "evidence": [
            {
                "source": "https://example.com/article -> https://example.com/gone",
                "excerpt": "href observed",
                "confidence": "direct",
                "value": {"href": "https://example.com/gone", "status_or_failed": True},
            }
        ],
        "source": "catalog",
        "source_url": "https://example.com/rules",
        "source_authority": AuthorityLevel.OFFICIAL_STANDARD,
        "rule": "SEO-BROKEN-INTERNAL-LINK-001",
        "rule_version": 1,
        "category": RuleCategory.TECHNICAL_SEO,
        "severity": RuleSeverity.MEDIUM,
        "confidence": RuleConfidence.HIGH,
        "affected_resource": "https://example.com/article -> https://example.com/gone",
        "affected_code_entity": "content/article.md",
        "expected_mechanism": "Fix or remove the link.",
        "recommended_action": "Replace or remove the broken href.",
        "recommendation": "Replace or remove the broken href.",
        "actionability": "code_change",
        "risk": "low",
        "will_validate": "re-check the href",
        "change_worked": "not_yet_applied",
        "rollback": "n/a",
        "status": FindingStatus.OPEN,
    }
    values.update(overrides)
    return Finding(**values)  # type: ignore[arg-type]


def test_evidence_locators_from_href_value_and_arrow_resource() -> None:
    locators = evidence_locators(_finding())
    assert locators == ["https://example.com/gone"]


def test_locator_matches_url_path_in_markdown() -> None:
    assert locator_in_content("See [x](/gone) for details.", "https://example.com/gone") is True
    assert locator_in_content("no links here", "https://example.com/gone") is False


def test_missing_target_stops_without_claiming_a_fix() -> None:
    files = [WorkspaceFile(path="content/article.md", status=STATUS_MISSING, content="")]
    violation = check_locate(files, ["https://example.com/gone"])
    assert violation is not None
    assert violation.reason == "target_missing"


def test_empty_target_stops_without_claiming_a_fix() -> None:
    files = [WorkspaceFile(path="content/article.md", status=STATUS_EMPTY, content="")]
    violation = check_locate(files, ["https://example.com/gone"])
    assert violation is not None
    assert violation.reason == "target_empty"


def test_present_file_without_locator_is_not_located() -> None:
    files = [
        WorkspaceFile(
            path="content/article.md",
            status=STATUS_PRESENT,
            content="# Article\n\nNo broken href here.\n",
        )
    ]
    violation = check_locate(files, ["https://example.com/gone"])
    assert violation is not None
    assert violation.reason == "implementation_not_located"


def test_present_file_with_locator_passes() -> None:
    files = [
        WorkspaceFile(
            path="content/article.md",
            status=STATUS_PRESENT,
            content="Read more: https://example.com/gone\n",
        )
    ]
    assert check_locate(files, ["https://example.com/gone"]) is None


def test_absence_finding_with_no_locators_skips_locate() -> None:
    finding = _finding(
        finding_id="SEO-CANONICAL-001:abc",
        rule="SEO-CANONICAL-001",
        evidence=[{"source": "page", "excerpt": "no canonical tag", "confidence": "direct"}],
        affected_resource="https://example.com/product",
        affected_code_entity="app/products/page.tsx",
    )
    assert evidence_locators(finding) == []
    files = [WorkspaceFile(path="app/products/page.tsx", status=STATUS_MISSING, content="")]
    assert check_locate(files, []) is None


def test_identity_diff_is_a_no_op() -> None:
    diff = diff_stats("content/article.md", "same\n", "same\n")
    violation = check_noop([diff])
    assert violation is not None
    assert violation.reason == "no_op"


def test_comment_only_diff_is_not_a_fix() -> None:
    before = "<html>\n  <body>hello</body>\n</html>\n"
    after = "<html>\n  <body>hello</body>\n</html>\n<!-- structured data already present -->\n"
    diff = diff_stats("client/index.html", before, after)
    violation = check_noop([diff])
    assert violation is not None
    assert violation.reason == "comment_only_diff"


def test_js_comment_only_diff_is_not_a_fix() -> None:
    before = "export const x = 1;\n"
    after = "export const x = 1;\n// no-op marker\n"
    diff = diff_stats("app/util.ts", before, after)
    violation = check_noop([diff])
    assert violation is not None
    assert violation.reason == "comment_only_diff"


def test_real_change_alongside_a_comment_is_not_flagged() -> None:
    before = "<html>\n  <body>hello</body>\n</html>\n"
    after = (
        "<html>\n  <body>hello</body>\n"
        '  <script type="application/ld+json">{"@type":"FAQPage"}</script>\n'
        "  <!-- added FAQ schema -->\n</html>\n"
    )
    diff = diff_stats("client/index.html", before, after)
    assert check_noop([diff]) is None


def test_read_workspace_files_distinguishes_missing_empty_present(tmp_path) -> None:
    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "empty.md").write_text("", encoding="utf-8")
    (tmp_path / "content" / "present.md").write_text("hello", encoding="utf-8")
    files = read_workspace_files(
        tmp_path, ["content/missing.md", "content/empty.md", "content/present.md"]
    )
    by_path = {item.path: item.status for item in files}
    assert by_path == {
        "content/missing.md": STATUS_MISSING,
        "content/empty.md": STATUS_EMPTY,
        "content/present.md": STATUS_PRESENT,
    }


def test_list_workspace_sitemap_files_skips_dist_and_node_modules(tmp_path) -> None:
    (tmp_path / "client" / "public").mkdir(parents=True)
    (tmp_path / "client" / "public" / "sitemap.xml").write_text("<urlset/>", encoding="utf-8")
    (tmp_path / "client" / "public" / "robots.txt").write_text("User-agent: *\n", encoding="utf-8")
    (tmp_path / "dist" / "public").mkdir(parents=True)
    (tmp_path / "dist" / "public" / "sitemap.xml").write_text("<urlset/>", encoding="utf-8")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "sitemap.xml").write_text("<urlset/>", encoding="utf-8")
    found = list_workspace_sitemap_files(tmp_path)
    assert set(found) == {"client/public/robots.txt", "client/public/sitemap.xml"}
