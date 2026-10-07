"""Validation layers (step 7.6, `[SPEC AGENTS.md §35-§36]`).

Code (sandbox build/lint/test) -> preview server in Docker -> Browser
(Playwright on the patched workspace, targeted URLs only) -> SEO/AEO/GEO
(re-run the exact rule against freshly rendered preview HTML, with
sibling pages for site-scoped checks) -> regression (title smoke check).

A green compile is not success — the SEO/AEO/GEO layer answers "did the
change actually work?" from the Nine Questions. Live production URLs are
never the post-change source of truth.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.changes import commands
from app.changes.preview import (
    extra_sibling_urls,
    is_site_scoped_check,
    overlay_pages,
    page_key,
    rewrite_live_url_to_preview,
)
from app.changes.sandbox import SandboxError, SandboxResult, run_in_sandbox, serve_in_sandbox
from app.changes.targeted import resolve_affected_urls
from app.connectors.model import Page
from app.core.config import Settings, get_settings
from app.intelligence.repository.clone import workspace_path
from app.intelligence.website.extract import extract_page
from app.intelligence.website.render import RenderResult, Renderer
from app.knowledge.evaluator import EvaluableRule, RuleHit, evaluate_page
from app.models.change import (
    ValidationCheckStatus,
    ValidationCheckType,
    ValidationResult,
    ValidationRun,
    ValidationRunStatus,
)
from app.models.finding import Finding
from app.models.knowledge import OptimizationRule, RuleCategory
from app.models.repository import Repository
from app.models.website import WebsitePage
from app.planners.validation import ValidationPlan

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _plain(text: str | None) -> str:
    """Tool output without terminal colour codes, which are noise in the UI and the LLM prompt."""

    return _ANSI_ESCAPE.sub("", text or "")


_CATEGORY_CHECK: dict[RuleCategory, ValidationCheckType] = {
    RuleCategory.TECHNICAL_SEO: ValidationCheckType.SEO,
    RuleCategory.CONTENT_SEO: ValidationCheckType.SEO,
    RuleCategory.AEO: ValidationCheckType.AEO,
    RuleCategory.GEO: ValidationCheckType.GEO,
    RuleCategory.AGENT_ACCESSIBILITY: ValidationCheckType.AGENT_ACCESSIBILITY,
}


def status_from_check_statuses(
    statuses: list[ValidationCheckStatus],
) -> ValidationRunStatus:
    """FAILED wins; SKIPPED (could not run) is PARTIAL; NOT_APPLICABLE is not."""

    if not statuses:
        return ValidationRunStatus.PARTIAL
    if any(status is ValidationCheckStatus.FAILED for status in statuses):
        return ValidationRunStatus.FAILED
    if any(status is ValidationCheckStatus.SKIPPED for status in statuses):
        return ValidationRunStatus.PARTIAL
    return ValidationRunStatus.PASSED


_PREWRITE_SKIP_TYPES = (
    ValidationCheckType.BUILD,
    ValidationCheckType.LINT,
    ValidationCheckType.UNIT_TEST,
    ValidationCheckType.BROWSER,
    ValidationCheckType.SEO,
    ValidationCheckType.REGRESSION,
)


def prewrite_skip_check_types(plan: ValidationPlan | None) -> list[ValidationCheckType]:
    """Sandbox layers that never ran because a pre-write gate stopped the job."""

    types = list(_PREWRITE_SKIP_TYPES)
    if plan is None:
        return types
    if plan.aeo_checks and ValidationCheckType.AEO not in types:
        types.append(ValidationCheckType.AEO)
    if plan.geo_checks and ValidationCheckType.GEO not in types:
        types.append(ValidationCheckType.GEO)
    return types


def record_prewrite_stop(
    db: Session,
    *,
    project_id: int,
    violation_reason: str,
    violation_detail: str,
    validation_plan: ValidationPlan | None = None,
    agent_run_id: int | None = None,
    snapshot_id: int | None = None,
) -> ValidationRun:
    """Persist the scope/content gate plus skipped sandbox checks.

    `ValidationCheckType.SCOPE` / `CONTENT` exist so a pre-write stop is
    inspectable on the change-review page instead of a silent missing
    Validation panel.
    """

    run = ValidationRun(
        project_id=project_id,
        agent_run_id=agent_run_id,
        snapshot_id=snapshot_id,
        status=ValidationRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    gate_type = (
        ValidationCheckType.CONTENT
        if violation_reason == "content_change_violation"
        else ValidationCheckType.SCOPE
    )
    detail = f"{violation_reason}: {violation_detail}"
    _record(run, db, check_type=gate_type, status=ValidationCheckStatus.FAILED, detail=detail)

    skip_detail = f"stopped before sandbox: {violation_reason}"
    gaps = [skip_detail]
    for check_type in prewrite_skip_check_types(validation_plan):
        _record(
            run, db, check_type=check_type, status=ValidationCheckStatus.SKIPPED, detail=skip_detail
        )
        gaps.append(f"{check_type.value} skipped: {skip_detail}")

    results = _existing_results(db, run.id)
    run.gaps_json = gaps
    run.error = detail
    run.status = status_from_check_statuses([row.status for row in results])
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def _record(run: ValidationRun, db: Session, *, check_type, status, detail=None, duration_ms=None):
    row = ValidationResult(
        validation_run_id=run.id,
        check_type=check_type,
        status=status,
        detail=detail,
        duration_ms=duration_ms,
    )
    db.add(row)
    db.commit()
    return row


def _run_sandbox_check(
    run: ValidationRun,
    db: Session,
    *,
    workspace,
    repository_id: int,
    command: commands.SandboxCommand,
    check_type: ValidationCheckType,
    network: bool,
    settings: Settings,
    gaps: list[str],
) -> SandboxResult | None:
    if command.command is None:
        status = (
            ValidationCheckStatus.NOT_APPLICABLE
            if command.not_applicable
            else ValidationCheckStatus.SKIPPED
        )
        _record(run, db, check_type=check_type, status=status, detail=command.skip_reason)
        if not command.not_applicable:
            gaps.append(f"{check_type.value} skipped: {command.skip_reason}")
        return None
    try:
        result = run_in_sandbox(
            workspace, command.command, repository_id=repository_id, network=network, settings=settings
        )
    except SandboxError as exc:
        _record(run, db, check_type=check_type, status=ValidationCheckStatus.SKIPPED, detail=str(exc))
        gaps.append(f"{check_type.value} skipped: {exc}")
        return None
    status = ValidationCheckStatus.PASSED if result.ok else ValidationCheckStatus.FAILED
    detail = _plain(result.stdout)[-2000:] if result.ok else _plain(result.stderr or result.stdout)[-2000:]
    if result.timed_out:
        detail = f"timed out after {settings.sandbox_timeout_seconds}s"
    _record(run, db, check_type=check_type, status=status, detail=detail, duration_ms=result.duration_ms)
    return result


def run_validation(
    db: Session,
    *,
    repository: Repository,
    website_id: int | None,
    finding: Finding,
    validation_plan: ValidationPlan,
    changed_files: list[str],
    agent_run_id: int | None = None,
    snapshot_id: int | None = None,
    settings: Settings | None = None,
) -> ValidationRun:
    settings = settings or get_settings()
    run = ValidationRun(
        project_id=repository.project_id,
        agent_run_id=agent_run_id,
        snapshot_id=snapshot_id,
        status=ValidationRunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    gaps: list[str] = []
    workspace = workspace_path(repository.project_id, repository.id, settings)
    profile = repository.architecture_profile or {}

    install_cmd = commands.install_command(profile)
    if install_cmd.command is None:
        _record(
            run, db, check_type=ValidationCheckType.BUILD, status=ValidationCheckStatus.SKIPPED,
            detail=f"install: {install_cmd.skip_reason}",
        )
        gaps.append(f"install skipped: {install_cmd.skip_reason}")
        can_run_sandbox = False
    else:
        try:
            install_result = run_in_sandbox(
                workspace, install_cmd.command, repository_id=repository.id, network=True, settings=settings,
            )
        except SandboxError as exc:
            _record(run, db, check_type=ValidationCheckType.BUILD, status=ValidationCheckStatus.SKIPPED, detail=f"install: {exc}")
            gaps.append(f"install skipped: {exc}")
            can_run_sandbox = False
        else:
            can_run_sandbox = install_result.ok
            _record(
                run, db, check_type=ValidationCheckType.BUILD,
                status=ValidationCheckStatus.PASSED if install_result.ok else ValidationCheckStatus.FAILED,
                detail=f"install: {_plain(install_result.stdout if install_result.ok else install_result.stderr)[-2000:]}",
                duration_ms=install_result.duration_ms,
            )

    build_ok_for_preview = can_run_sandbox
    if validation_plan.build:
        if can_run_sandbox:
            # network=True (unlike lint/unit_test below): a framework build
            # step can have a legitimate network dependency the patch had no
            # part in — e.g. next/font/google fetching font CSS from Google
            # at build time — and install already runs arbitrary postinstall
            # scripts with network on, so this extends an existing trust
            # boundary rather than opening a new one.
            build_result = _run_sandbox_check(
                run, db, workspace=workspace, repository_id=repository.id,
                command=commands.build_command(profile, workspace), check_type=ValidationCheckType.BUILD,
                network=True, settings=settings, gaps=gaps,
            )
            build_ok_for_preview = build_result is None or build_result.ok
        else:
            _record(run, db, check_type=ValidationCheckType.BUILD, status=ValidationCheckStatus.SKIPPED, detail="dependency install did not succeed")
            gaps.append("build skipped: dependency install did not succeed")
            build_ok_for_preview = False

    if validation_plan.lint:
        if can_run_sandbox:
            _run_sandbox_check(
                run, db, workspace=workspace, repository_id=repository.id,
                command=commands.lint_command(profile, workspace), check_type=ValidationCheckType.LINT,
                network=False, settings=settings, gaps=gaps,
            )
        else:
            _record(run, db, check_type=ValidationCheckType.LINT, status=ValidationCheckStatus.SKIPPED, detail="dependency install did not succeed")
            gaps.append("lint skipped: dependency install did not succeed")

    if validation_plan.tests:
        if can_run_sandbox:
            _run_sandbox_check(
                run, db, workspace=workspace, repository_id=repository.id,
                command=commands.unit_test_command(profile, workspace), check_type=ValidationCheckType.UNIT_TEST,
                network=False, settings=settings, gaps=gaps,
            )
        else:
            _record(run, db, check_type=ValidationCheckType.UNIT_TEST, status=ValidationCheckStatus.SKIPPED, detail="dependency install did not succeed")
            gaps.append("unit_test skipped: dependency install did not succeed")

    targeted = resolve_affected_urls(
        db,
        project_id=repository.project_id,
        repository_id=repository.id,
        website_id=website_id,
        changed_files=changed_files,
        settings=settings,
        fallback_url=finding.affected_url,
    )
    gaps.extend(targeted.gaps)

    if targeted.urls:
        _run_preview_browser_seo(
            run,
            db,
            finding=finding,
            urls=targeted.urls,
            gaps=gaps,
            workspace=workspace,
            profile=profile,
            repository_id=repository.id,
            website_id=website_id,
            can_run_sandbox=can_run_sandbox and build_ok_for_preview,
            settings=settings,
        )
    else:
        _record(run, db, check_type=ValidationCheckType.BROWSER, status=ValidationCheckStatus.SKIPPED, detail="no affected URL resolved")
        gaps.append("browser skipped: no affected URL resolved")

    results = _existing_results(db, run.id)
    run.affected_urls_json = targeted.urls
    run.gaps_json = gaps
    if not results:
        gaps.append("no validation checks executed")
        run.gaps_json = gaps
        run.status = ValidationRunStatus.PARTIAL
    else:
        run.status = status_from_check_statuses([row.status for row in results])
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def _existing_results(db: Session, validation_run_id: int):
    return list(
        db.scalars(select(ValidationResult).where(ValidationResult.validation_run_id == validation_run_id))
    )


def _skip_browser_layers(
    run: ValidationRun,
    db: Session,
    *,
    finding: Finding,
    gaps: list[str],
    reason: str,
) -> None:
    gaps.append(reason)
    _record(run, db, check_type=ValidationCheckType.BROWSER, status=ValidationCheckStatus.SKIPPED, detail=reason)
    check_type = _CATEGORY_CHECK.get(finding.category, ValidationCheckType.SEO)
    _record(run, db, check_type=check_type, status=ValidationCheckStatus.SKIPPED, detail=reason)
    _record(run, db, check_type=ValidationCheckType.REGRESSION, status=ValidationCheckStatus.SKIPPED, detail=reason)


def _run_preview_browser_seo(
    run: ValidationRun,
    db: Session,
    *,
    finding: Finding,
    urls: list[str],
    gaps: list[str],
    workspace,
    profile: dict,
    repository_id: int,
    website_id: int | None,
    can_run_sandbox: bool,
    settings: Settings,
) -> None:
    if not can_run_sandbox:
        _skip_browser_layers(
            run, db, finding=finding, gaps=gaps,
            reason="preview server skipped: dependency install or build did not succeed",
        )
        return
    start_cmd = commands.start_command(profile, workspace)
    if start_cmd.command is None:
        _skip_browser_layers(
            run, db, finding=finding, gaps=gaps,
            reason=f"preview server skipped: {start_cmd.skip_reason}",
        )
        return
    try:
        with serve_in_sandbox(
            workspace, start_cmd.command, repository_id=repository_id, settings=settings
        ) as preview:
            _browser_and_seo_checks(
                run,
                db,
                finding=finding,
                urls=urls,
                gaps=gaps,
                preview_base_url=preview.base_url,
                website_id=website_id,
                settings=settings,
            )
    except SandboxError as exc:
        _skip_browser_layers(
            run, db, finding=finding, gaps=gaps,
            reason=f"preview server skipped: {exc}",
        )


def _crawled_pages(db: Session, website_id: int | None) -> list[Page]:
    if website_id is None:
        return []
    pages: list[Page] = []
    rows = db.scalars(select(WebsitePage).where(WebsitePage.website_id == website_id))
    for row in rows:
        if not row.model_json:
            continue
        try:
            pages.append(Page.model_validate(row.model_json))
        except ValidationError:
            continue
    return pages


def seo_recheck_detail(live_url: str, rule_id: str, hits: list[RuleHit]) -> str:
    """Human-readable SEO re-check line, including which OG keys are still missing."""

    if not hits:
        return f"{live_url}: rule {rule_id} no longer fires"
    parts: list[str] = []
    for hit in hits:
        observed = hit.observed_value
        if isinstance(observed, dict):
            missing = observed.get("missing")
            if missing:
                parts.append("missing " + ", ".join(str(item) for item in missing))
            present = observed.get("present")
            if present:
                parts.append("present " + ", ".join(str(item) for item in present))
    suffix = f" ({'; '.join(parts)})" if parts else ""
    return f"{live_url}: rule {rule_id} still fires{suffix}"


def _browser_and_seo_checks(
    run: ValidationRun,
    db: Session,
    *,
    finding: Finding,
    urls: list[str],
    gaps: list[str],
    preview_base_url: str,
    website_id: int | None,
    settings: Settings,
) -> None:
    rule_row = db.scalar(
        select(OptimizationRule).where(
            OptimizationRule.rule_id == finding.rule,
            OptimizationRule.version == finding.rule_version,
        )
    )
    evaluable = EvaluableRule.from_row(rule_row) if rule_row is not None else None
    if evaluable is None:
        gaps.append(f"rule row not found for re-check: {finding.rule} v{finding.rule_version}")

    check_type = _CATEGORY_CHECK.get(finding.category, ValidationCheckType.SEO)
    stored = _crawled_pages(db, website_id)
    stored_urls = [page.url for page in stored]
    render_urls = list(urls)
    if evaluable is not None and is_site_scoped_check(evaluable.conditions.check):
        render_urls.extend(
            extra_sibling_urls(
                stored_urls,
                urls,
                limit=settings.targeted_validation_sibling_urls,
            )
        )

    fresh: dict[str, Page] = {}
    renders: dict[str, RenderResult] = {}
    targeted_set = {page_key(url) for url in urls}

    with Renderer(preview_base_url=preview_base_url) as renderer:
        for live_url in render_urls:
            preview_url = rewrite_live_url_to_preview(live_url, preview_base_url)
            result = renderer.render_url(preview_url)
            renders[live_url] = result
            is_targeted = page_key(live_url) in targeted_set
            if result.state != "observed":
                if is_targeted:
                    _record(
                        run, db, check_type=ValidationCheckType.BROWSER,
                        status=ValidationCheckStatus.FAILED,
                        detail=f"{live_url}: render {result.state} ({result.message})",
                    )
                else:
                    gaps.append(
                        f"sibling preview skipped {live_url}: render {result.state} ({result.message})"
                    )
                continue
            fresh[live_url] = extract_page(result.rendered_html or "", url=live_url).page

    siblings = overlay_pages(stored, fresh)
    site_scoped = evaluable is not None and is_site_scoped_check(evaluable.conditions.check)

    for live_url in urls:
        result = renders.get(live_url)
        if result is None or result.state != "observed":
            continue
        extracted = fresh.get(live_url)
        if extracted is None:
            continue
        browser_ok = not result.console_errors
        _record(
            run, db, check_type=ValidationCheckType.BROWSER,
            status=ValidationCheckStatus.PASSED if browser_ok else ValidationCheckStatus.FAILED,
            detail=f"{live_url}: {len(result.console_errors)} console error(s)",
        )
        if evaluable is not None:
            evaluation = evaluate_page(
                extracted,
                sibling_pages=siblings if site_scoped else None,
                rules=[evaluable],
            )
            matching = [
                hit
                for hit in evaluation.hits
                if page_key(hit.affected_resource) == page_key(live_url)
                or hit.affected_resource == live_url
            ]
            _record(
                run, db, check_type=check_type,
                status=ValidationCheckStatus.PASSED if not matching else ValidationCheckStatus.FAILED,
                detail=seo_recheck_detail(live_url, finding.rule, matching),
            )
        baseline = next((page for page in stored if page_key(page.url) == page_key(live_url)), None)
        baseline_title = baseline.title if baseline is not None else None
        if baseline_title is None:
            baseline_row = db.scalar(select(WebsitePage).where(WebsitePage.url == live_url))
            baseline_title = baseline_row.title if baseline_row is not None else None
        if baseline_title:
            regressed = not (result.rendered_title or "").strip()
            _record(
                run, db, check_type=ValidationCheckType.REGRESSION,
                status=ValidationCheckStatus.FAILED if regressed else ValidationCheckStatus.PASSED,
                detail=f"{live_url}: title {'lost' if regressed else 'preserved'}",
            )
