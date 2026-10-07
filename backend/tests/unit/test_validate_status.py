"""Validation-run status: NOT_APPLICABLE must not make the run PARTIAL."""

from app.changes.validate import prewrite_skip_check_types, seo_recheck_detail, status_from_check_statuses
from app.knowledge.evaluator import RuleHit
from app.models.change import ValidationCheckStatus, ValidationCheckType, ValidationRunStatus
from app.planners.validation import ValidationPlan


def test_all_passed_is_passed() -> None:
    assert (
        status_from_check_statuses(
            [ValidationCheckStatus.PASSED, ValidationCheckStatus.PASSED]
        )
        is ValidationRunStatus.PASSED
    )


def test_failed_wins() -> None:
    assert (
        status_from_check_statuses(
            [ValidationCheckStatus.PASSED, ValidationCheckStatus.FAILED, ValidationCheckStatus.SKIPPED]
        )
        is ValidationRunStatus.FAILED
    )


def test_skipped_is_partial() -> None:
    assert (
        status_from_check_statuses(
            [ValidationCheckStatus.PASSED, ValidationCheckStatus.SKIPPED]
        )
        is ValidationRunStatus.PARTIAL
    )


def test_not_applicable_with_passes_is_passed() -> None:
    assert (
        status_from_check_statuses(
            [ValidationCheckStatus.PASSED, ValidationCheckStatus.NOT_APPLICABLE]
        )
        is ValidationRunStatus.PASSED
    )


def test_empty_is_partial() -> None:
    assert status_from_check_statuses([]) is ValidationRunStatus.PARTIAL


def test_prewrite_skip_checks_include_sandbox_layers() -> None:
    types = prewrite_skip_check_types(None)
    assert ValidationCheckType.BUILD in types
    assert ValidationCheckType.LINT in types
    assert ValidationCheckType.UNIT_TEST in types
    assert ValidationCheckType.BROWSER in types


def test_prewrite_skip_checks_add_aeo_when_planned() -> None:
    plan = ValidationPlan(
        finding_id="SEO-STRUCTUREDDATA-MISSING-001:abc",
        tests=[],
        build=True,
        lint=True,
        browser_checks=["json-ld present"],
        seo_checks=["structured data"],
        aeo_checks=["entity markup"],
        geo_checks=[],
        regression_checks=["title preserved"],
    )
    types = prewrite_skip_check_types(plan)
    assert ValidationCheckType.AEO in types
    assert ValidationCheckType.GEO not in types


def test_seo_recheck_detail_includes_missing_og_keys() -> None:
    hit = RuleHit(
        rule_id="SEO-OG-INCOMPLETE-001",
        rule_version=1,
        severity="low",
        affected_resource="https://drmoksha.com/contact",
        observed_value={"present": ["description", "title", "url"], "missing": ["image", "type"]},
        expected_condition="open_graph includes title, type, image, url",
        source_url="https://ogp.me/",
    )
    detail = seo_recheck_detail(
        "https://drmoksha.com/contact", "SEO-OG-INCOMPLETE-001", [hit]
    )
    assert "still fires" in detail
    assert "missing image, type" in detail
    assert "no longer fires" in seo_recheck_detail(
        "https://drmoksha.com/contact", "SEO-OG-INCOMPLETE-001", []
    )
