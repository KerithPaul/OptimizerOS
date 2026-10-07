"""CMS validation: no sandbox. Re-fetch REST and re-run the Finding's rule."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.model import Page
from app.connectors.wordpress.connector import WordPressConnector
from app.knowledge.evaluator import EvaluableRule, evaluate_page
from app.models.change import (
    ValidationCheckStatus,
    ValidationCheckType,
    ValidationResult,
    ValidationRun,
    ValidationRunStatus,
)
from app.models.finding import Finding
from app.models.knowledge import OptimizationRule, RuleCategory

_CATEGORY_CHECK = {
    RuleCategory.TECHNICAL_SEO: ValidationCheckType.SEO,
    RuleCategory.CONTENT_SEO: ValidationCheckType.SEO,
    RuleCategory.AEO: ValidationCheckType.AEO,
    RuleCategory.GEO: ValidationCheckType.GEO,
    RuleCategory.AGENT_ACCESSIBILITY: ValidationCheckType.AGENT_ACCESSIBILITY,
}

_NOT_APPLICABLE = (
    ValidationCheckType.LINT,
    ValidationCheckType.TYPECHECK,
    ValidationCheckType.BUILD,
    ValidationCheckType.UNIT_TEST,
    ValidationCheckType.INTEGRATION_TEST,
)


def run_cms_validation(
    db: Session,
    *,
    finding: Finding,
    connector: WordPressConnector,
    urls: list[str],
    agent_run_id: int | None,
    snapshot_id: int | None,
    live: bool,
    overlay_pages: list[Page] | None = None,
) -> ValidationRun:
    run = ValidationRun(
        project_id=finding.project_id,
        agent_run_id=agent_run_id,
        snapshot_id=snapshot_id,
        status=ValidationRunStatus.RUNNING,
        affected_urls_json=urls,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    for check_type in _NOT_APPLICABLE:
        db.add(
            ValidationResult(
                validation_run_id=run.id,
                check_type=check_type,
                status=ValidationCheckStatus.NOT_APPLICABLE,
                detail="WordPress REST mutations do not run host sandbox checks",
            )
        )
    db.add(
        ValidationResult(
            validation_run_id=run.id,
            check_type=ValidationCheckType.BROWSER,
            status=ValidationCheckStatus.NOT_APPLICABLE,
            detail="APPLY_LOCALLY validates the snapshot, not production render"
            if not live
            else "REST re-fetch is the post-change source of truth for WordPress",
        )
    )

    pages: list[Page] = list(overlay_pages or [])
    if not pages:
        for url in urls:
            try:
                pages.append(connector.fetch_content(url))
            except Exception as exc:  # noqa: BLE001
                db.add(
                    ValidationResult(
                        validation_run_id=run.id,
                        check_type=ValidationCheckType.SEO,
                        status=ValidationCheckStatus.FAILED,
                        detail=f"could not re-fetch {url}: {exc}",
                    )
                )
                run.status = ValidationRunStatus.FAILED
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(run)
                return run

    rule_row = db.scalar(
        select(OptimizationRule)
        .where(
            OptimizationRule.rule_id == finding.rule,
            OptimizationRule.version == finding.rule_version,
        )
        .order_by(OptimizationRule.id.desc())
    )
    if rule_row is None:
        rule_row = db.scalar(
            select(OptimizationRule)
            .where(OptimizationRule.rule_id == finding.rule)
            .order_by(OptimizationRule.version.desc())
        )

    check_type = _CATEGORY_CHECK.get(finding.category, ValidationCheckType.SEO)
    if rule_row is None:
        db.add(
            ValidationResult(
                validation_run_id=run.id,
                check_type=check_type,
                status=ValidationCheckStatus.SKIPPED,
                detail=f"rule {finding.rule} is not in the catalog",
            )
        )
        run.status = ValidationRunStatus.PARTIAL
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
        return run

    evaluable = EvaluableRule.from_row(rule_row)
    still_fires = False
    for page in pages:
        result = evaluate_page(
            page,
            sibling_pages=pages,
            rules=[evaluable],
        )
        if any(hit.rule_id == finding.rule for hit in result.hits):
            still_fires = True
            break

    if still_fires:
        seo_status = ValidationCheckStatus.FAILED
        detail = f"rule {finding.rule} still fires on fetched WordPress resources"
        run_status = ValidationRunStatus.FAILED
    else:
        seo_status = ValidationCheckStatus.PASSED
        detail = f"rule {finding.rule} no longer fires on fetched WordPress resources"
        run_status = ValidationRunStatus.PASSED

    db.add(
        ValidationResult(
            validation_run_id=run.id,
            check_type=check_type,
            status=seo_status,
            detail=detail,
        )
    )
    title_ok = all(page.title for page in pages)
    db.add(
        ValidationResult(
            validation_run_id=run.id,
            check_type=ValidationCheckType.REGRESSION,
            status=ValidationCheckStatus.PASSED
            if title_ok
            else ValidationCheckStatus.FAILED,
            detail="title present after mutation"
            if title_ok
            else "a mutated page lost its title",
        )
    )
    if not title_ok:
        run_status = ValidationRunStatus.FAILED
    run.status = run_status
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run
