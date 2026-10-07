"""Layers 4-6 (step 7.1 verify, `[SPEC AGENTS.md §28]`).

Same pattern as `test_planners.py`: each planner emits a Pydantic-validated
structure; malformed output is rejected via `app.llm.validation`, never
coerced.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.knowledge.authority import AuthorityLevel
from app.llm.validation import StructuredOutputError
from app.models.finding import Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.planners.change import ChangePlan, PlanGroundingError, constrain_target_files, plan_change
from app.core.config import get_settings
from app.planners.execution import ExecutionPlan, ExecutionStep, plan_execution
from app.planners.optimization import Intervention
from app.planners.validation import ValidationPlan, plan_validation


class _FakeGateway:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict[str, str]]] = []
        self.max_tokens: list[int | None] = []

    def chat(self, messages, *, tier=None, response_format=None, max_tokens=None):
        self.calls.append(list(messages))
        self.max_tokens.append(max_tokens)

        class _Result:
            content = self.content
            provider = "fake"
            model = "fake-strong"
            tokens = 21
            latency_ms = 5

        return _Result()


def _finding() -> Finding:
    return Finding(
        id=1,
        project_id=1,
        analysis_run_id=1,
        finding_id="SEO-CANONICAL-001:abc123",
        observation="Missing canonical tag",
        problem="Missing canonical tag",
        evidence=[{"source": "page.html", "excerpt": "no canonical tag", "confidence": "direct"}],
        source="ArchitectOS rule catalog",
        source_url="https://example.com/rules/seo-canonical-001",
        source_authority=AuthorityLevel.OFFICIAL_STANDARD,
        rule="SEO-CANONICAL-001",
        rule_version=1,
        category=RuleCategory.TECHNICAL_SEO,
        severity=RuleSeverity.HIGH,
        confidence=RuleConfidence.HIGH,
        affected_resource="https://example.com/product",
        affected_code_entity="app/products/[id]/page.tsx",
        expected_mechanism="Canonical tags prevent duplicate-content signals.",
        recommended_action="Add a canonical link tag.",
        recommendation="Add a canonical link tag.",
        actionability="code_change",
        risk="Low risk; metadata-only change.",
        will_validate="Re-crawl and confirm the canonical tag is present.",
        change_worked="not_yet_applied",
        rollback="Remove the added tag.",
        status=FindingStatus.OPEN,
    )


def _intervention() -> Intervention:
    return Intervention(
        finding_id="SEO-CANONICAL-001:abc123",
        hypothesis="Missing canonical confuses crawlers about the preferred URL.",
        intervention="Add a canonical <link> tag pointing at the product URL.",
        expected_mechanism="Canonical resolves duplicate-content ambiguity.",
        risk="low",
    )


def test_change_plan_requires_at_least_one_target_file() -> None:
    with pytest.raises(ValidationError):
        ChangePlan(
            finding_id="x",
            target_files=[],
            target_symbols=[],
            reuse_notes="reuse existing helper",
            expected_diff_summary="add tag",
            required_tests=[],
            required_validation=[],
        )


def test_plan_change_drops_unrelated_types_file_from_llm_output() -> None:
    gateway = _FakeGateway(
        '{"finding_id": "SEO-CANONICAL-001:abc123", '
        '"target_files": ["content/article.md", "src/lib/cms/types.ts"], '
        '"target_symbols": [], '
        '"reuse_notes": "edit markdown", "expected_diff_summary": "fix href", '
        '"required_tests": [], "required_validation": []}'
    )
    finding = _finding()
    finding.affected_resource = "https://example.com/article -> https://example.com/gone"
    finding.affected_code_entity = None
    plan, _chat = plan_change(gateway, finding, _intervention())  # type: ignore[arg-type]
    assert plan.target_files == ["content/article.md"]


def test_plan_change_wraps_finding_and_intervention_as_trusted_data() -> None:
    gateway = _FakeGateway(
        '{"finding_id": "SEO-CANONICAL-001:abc123", '
        '"target_files": ["app/products/[id]/page.tsx"], "target_symbols": ["generateMetadata"], '
        '"reuse_notes": "reuse generateMetadata", "expected_diff_summary": "add canonical", '
        '"required_tests": [], "required_validation": ["build"]}'
    )

    plan, chat_result = plan_change(gateway, _finding(), _intervention())  # type: ignore[arg-type]

    assert plan.target_files == ["app/products/[id]/page.tsx"]
    assert chat_result.tokens == 21
    assert gateway.max_tokens == [None], "Layer 4 is STRONG and keeps LLM_MAX_TOKENS"
    system = [m for m in gateway.calls[0] if m["role"] == "system"][0]["content"]
    assert "Missing canonical tag" not in system
    non_system = "\n".join(m["content"] for m in gateway.calls[0] if m["role"] != "system")
    assert "Missing canonical tag" in non_system


def test_constrain_target_files_drops_shared_types_for_url_findings() -> None:
    finding = _finding()
    finding.affected_resource = "https://example.com/article -> https://example.com/gone"
    finding.affected_code_entity = None
    plan = ChangePlan(
        finding_id="SEO-CANONICAL-001:abc123",
        target_files=[
            "content/publications/article.md",
            "src/lib/cms/types.ts",
        ],
        target_symbols=[],
        reuse_notes="edit markdown",
        expected_diff_summary="fix href",
        required_tests=[],
        required_validation=[],
    )
    constrained = constrain_target_files(plan, finding)
    assert constrained.target_files == ["content/publications/article.md"]


def test_constrain_target_files_keeps_types_when_they_are_the_entity() -> None:
    finding = _finding()
    finding.affected_resource = "https://example.com/article"
    finding.affected_code_entity = "src/lib/cms/types.ts"
    plan = ChangePlan(
        finding_id="SEO-CANONICAL-001:abc123",
        target_files=["src/lib/cms/types.ts"],
        target_symbols=[],
        reuse_notes="edit types",
        expected_diff_summary="n/a",
        required_tests=[],
        required_validation=[],
    )
    constrained = constrain_target_files(plan, finding)
    assert constrained.target_files == ["src/lib/cms/types.ts"]


def _orphan_finding() -> Finding:
    finding = _finding()
    finding.finding_id = "SEO-ORPHAN-PAGE-001:c12d573b27c362bc"
    finding.rule = "SEO-ORPHAN-PAGE-001"
    finding.affected_resource = "https://drmoksha.com/"
    finding.affected_url = "https://drmoksha.com/"
    finding.affected_code_entity = "src/app/page.tsx"
    finding.recommended_action = (
        "Add at least one internal link to this page from elsewhere on the site."
    )
    return finding


def test_constrain_target_files_does_not_pin_affected_entity_for_orphan_page() -> None:
    """The affected page needs a link *from elsewhere*; pinning it as the
    target would force a self-referential, no-op edit `[see conversation:
    SEO-ORPHAN-PAGE-001 self-link bug]`."""
    finding = _orphan_finding()
    plan = ChangePlan(
        finding_id=finding.finding_id,
        target_files=["src/components/layout/Header.tsx"],
        target_symbols=[],
        reuse_notes="reuse existing Link component",
        expected_diff_summary="add nav link to homepage",
        required_tests=[],
        required_validation=["seo"],
    )
    constrained = constrain_target_files(plan, finding)
    assert constrained.target_files == ["src/components/layout/Header.tsx"]


def test_constrain_target_files_drops_self_link_for_orphan_page_when_llm_named_it() -> None:
    finding = _orphan_finding()
    plan = ChangePlan(
        finding_id=finding.finding_id,
        target_files=["src/app/page.tsx", "src/components/layout/Header.tsx"],
        target_symbols=[],
        reuse_notes="reuse existing Link component",
        expected_diff_summary="add nav link to homepage",
        required_tests=[],
        required_validation=["seo"],
    )
    constrained = constrain_target_files(plan, finding)
    assert constrained.target_files == ["src/components/layout/Header.tsx"]


def test_constrain_target_files_replaces_invented_sitemap_generator() -> None:
    finding = _finding()
    finding.finding_id = "SEO-SITEMAP-INVALID-001:abc"
    finding.rule = "SEO-SITEMAP-INVALID-001"
    finding.affected_resource = "https://lexfintech.io/sitemap_index.xml"
    finding.affected_code_entity = None
    plan = ChangePlan(
        finding_id=finding.finding_id,
        target_files=["server/routes/sitemap.ts", "scripts/generate-sitemap.ts"],
        target_symbols=[],
        reuse_notes="invent a generator",
        expected_diff_summary="add sitemap route",
        required_tests=[],
        required_validation=["build"],
    )
    constrained = constrain_target_files(
        plan, finding, existing_sitemap_files=["client/public/sitemap.xml"]
    )
    assert constrained.target_files == ["client/public/sitemap.xml"]


def test_constrain_target_files_keeps_server_when_static_sitemap_exists() -> None:
    finding = _finding()
    finding.finding_id = "SEO-SITEMAP-INVALID-001:abc"
    finding.rule = "SEO-SITEMAP-INVALID-001"
    finding.affected_resource = "https://lexfintech.io/sitemap.xml"
    finding.affected_code_entity = None
    plan = ChangePlan(
        finding_id=finding.finding_id,
        target_files=["server/index.ts", "scripts/generate-sitemap.ts"],
        target_symbols=[],
        reuse_notes="serve static xml",
        expected_diff_summary="stop catch-all from swallowing sitemap.xml",
        required_tests=[],
        required_validation=["build"],
    )
    constrained = constrain_target_files(
        plan, finding, existing_sitemap_files=["client/public/sitemap.xml"]
    )
    assert constrained.target_files[0] == "client/public/sitemap.xml"
    assert "server/index.ts" in constrained.target_files
    assert "scripts/generate-sitemap.ts" not in constrained.target_files


def test_constrain_target_files_keeps_self_link_for_orphan_page_when_llm_named_only_that() -> None:
    """No other candidate was proposed; dropping it would violate the
    `ChangePlan` at-least-one-file invariant, so it is left as-is for the
    Reviewer Agent / SEO recheck to reject."""
    finding = _orphan_finding()
    plan = ChangePlan(
        finding_id=finding.finding_id,
        target_files=["src/app/page.tsx"],
        target_symbols=[],
        reuse_notes="reuse existing Link component",
        expected_diff_summary="add link to homepage",
        required_tests=[],
        required_validation=["seo"],
    )
    constrained = constrain_target_files(plan, finding)
    assert constrained.target_files == ["src/app/page.tsx"]


def test_retrieve_query_for_sitemap_finding_uses_filename() -> None:
    from app.jobs.handlers.code_change import _retrieve_query

    finding = _finding()
    finding.rule = "SEO-SITEMAP-INVALID-001"
    finding.affected_resource = "https://lexfintech.io/sitemap_index.xml"
    finding.affected_url = "https://lexfintech.io/sitemap_index.xml"
    query = _retrieve_query(finding)
    assert "sitemap.xml" in query
    assert "robots.txt" in query
    assert "sitemap_index.xml" in query


def test_plan_change_malformed_output_raises_after_retries() -> None:
    gateway = _FakeGateway("not json")
    with pytest.raises(StructuredOutputError):
        plan_change(gateway, _finding(), _intervention())  # type: ignore[arg-type]


def _change_plan() -> ChangePlan:
    return ChangePlan(
        finding_id="SEO-CANONICAL-001:abc123",
        target_files=["app/products/[id]/page.tsx"],
        target_symbols=["generateMetadata"],
        reuse_notes="reuse generateMetadata",
        expected_diff_summary="add canonical",
        required_tests=[],
        required_validation=["build"],
    )


def test_execution_plan_requires_at_least_one_step() -> None:
    with pytest.raises(ValidationError):
        ExecutionPlan(finding_id="x", steps=[], dependencies=[], sandbox_operations=[])


def test_execution_step_rejects_blank_fields() -> None:
    with pytest.raises(ValidationError):
        ExecutionStep(order=1, action="", detail="write file")


def test_plan_execution_sequences_target_files_without_an_llm() -> None:
    plan, chat_result = plan_execution(_change_plan())

    assert [step.model_dump() for step in plan.steps] == [
        {
            "order": 1,
            "action": "write_file",
            "detail": "app/products/[id]/page.tsx",
        }
    ]
    assert plan.finding_id == "SEO-CANONICAL-001:abc123"
    assert plan.dependencies == []
    assert plan.sandbox_operations == ["build"]
    assert chat_result.provider == "deterministic"
    assert chat_result.tokens == 0


def test_plan_execution_maps_only_known_sandbox_operations() -> None:
    change_plan = ChangePlan(
        finding_id="SEO-CANONICAL-001:abc123",
        target_files=["app/a.tsx", "app/b.tsx"],
        target_symbols=[],
        reuse_notes="reuse existing helper",
        expected_diff_summary="add tag",
        required_tests=[],
        required_validation=["lint", "browser", "build", "seo", "lint", "unit_test"],
    )

    plan, _chat = plan_execution(change_plan)

    assert [step.detail for step in plan.steps] == ["app/a.tsx", "app/b.tsx"]
    assert [step.order for step in plan.steps] == [1, 2]
    assert plan.sandbox_operations == ["lint", "build", "unit_test"]


def test_execution_plan_wraps_a_string_dependencies_field() -> None:
    """Stored/legacy JSON sometimes has `dependencies` as one English sentence."""
    plan = ExecutionPlan.model_validate(
        {
            "finding_id": "SEO-ORPHAN-PAGE-001:c12d573b27c362bc",
            "steps": [
                {
                    "order": 1,
                    "action": "write_file",
                    "detail": "src/lib/cms/defaults/global.ts",
                }
            ],
            "dependencies": (
                "No cross-file dependencies; src/lib/cms/defaults/global.ts is independent."
            ),
            "sandbox_operations": ["install", "lint", "typecheck", "unit_test", "build"],
        }
    )

    assert plan.dependencies == [
        "No cross-file dependencies; src/lib/cms/defaults/global.ts is independent."
    ]
    assert plan.sandbox_operations == ["install", "lint", "typecheck", "unit_test", "build"]


def _execution_plan() -> ExecutionPlan:
    return ExecutionPlan(
        finding_id="SEO-CANONICAL-001:abc123",
        steps=[ExecutionStep(order=1, action="write_file", detail="add canonical tag")],
        dependencies=[],
        sandbox_operations=["build"],
    )


def test_plan_validation_wraps_change_and_execution_plans() -> None:
    gateway = _FakeGateway(
        '{"finding_id": "SEO-CANONICAL-001:abc123", "tests": [], "build": true, "lint": true, '
        '"browser_checks": ["canonical tag present"], "seo_checks": ["canonical present"], '
        '"aeo_checks": [], "geo_checks": [], "regression_checks": ["other product pages unaffected"]}'
    )

    plan, chat_result = plan_validation(gateway, _change_plan(), _execution_plan())  # type: ignore[arg-type]

    assert isinstance(plan, ValidationPlan)
    assert plan.build is True
    assert plan.browser_checks == ["canonical tag present"]
    assert chat_result.model == "fake-strong"
    assert gateway.max_tokens == [get_settings().llm_planner_max_tokens]


def _plan_json(*target_files: str) -> str:
    return json.dumps(
        {
            "finding_id": "SEO-CANONICAL-001:abc123",
            "target_files": list(target_files),
            "target_symbols": [],
            "reuse_notes": "reuse existing helper",
            "expected_diff_summary": "add tag",
            "required_tests": [],
            "required_validation": [],
        }
    )


class _SequenceGateway:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages, *, tier=None, response_format=None, max_tokens=None):
        self.calls.append(list(messages))
        content = self.answers.pop(0)

        class _Result:
            provider = "fake"
            model = "fake-strong"
            tokens = 7
            latency_ms = 1

        _Result.content = content
        return _Result()


def _workspace(tmp_path, *files: str):
    for rel in files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("export {};\n", encoding="utf-8")
    return tmp_path


def test_plan_change_shows_the_real_file_list_and_keeps_only_existing_targets(tmp_path) -> None:
    workspace = _workspace(tmp_path, "src/components/Header.tsx", "src/lib/cms/defaults/global.ts")
    gateway = _SequenceGateway(
        _plan_json("src/components/Header.tsx", "src/components/layouts/Navbar.tsx")
    )
    plan, _ = plan_change(gateway, _finding(), _intervention(), workspace=workspace)  # type: ignore[arg-type]

    assert plan.target_files == ["src/components/Header.tsx"]
    blob = "\n".join(m["content"] for m in gateway.calls[0])
    assert "src/lib/cms/defaults/global.ts" in blob
    assert "workspace_files" in blob


def test_plan_change_gets_one_corrective_round_when_no_target_exists(tmp_path) -> None:
    workspace = _workspace(tmp_path, "src/components/Header.tsx")
    gateway = _SequenceGateway(
        _plan_json("src/components/layouts/Navbar.tsx", "src/lib/navigation/siteNav.ts"),
        _plan_json("src/components/Header.tsx"),
    )
    plan, chat = plan_change(gateway, _finding(), _intervention(), workspace=workspace)  # type: ignore[arg-type]

    assert plan.target_files == ["src/components/Header.tsx"]
    assert chat.tokens == 14
    assert "None of those target_files exist" in gateway.calls[1][-1]["content"]


def test_plan_change_raises_when_the_planner_never_names_a_real_file(tmp_path) -> None:
    workspace = _workspace(tmp_path, "src/components/Header.tsx")
    gateway = _SequenceGateway(_plan_json("a/b.tsx"), _plan_json("c/d.tsx"))

    with pytest.raises(PlanGroundingError):
        plan_change(gateway, _finding(), _intervention(), workspace=workspace)  # type: ignore[arg-type]


def test_constrain_target_files_allows_creating_standard_site_files(tmp_path) -> None:
    plan = ChangePlan(
        finding_id="SEO-CANONICAL-001:abc123",
        target_files=["public/llms.txt", "src/made/up.tsx"],
        target_symbols=[],
        reuse_notes="x",
        expected_diff_summary="y",
        required_tests=[],
        required_validation=[],
    )
    finding = _finding()
    finding.affected_code_entity = None
    constrained = constrain_target_files(plan, finding, path_exists=lambda path: False)
    assert constrained.target_files == ["public/llms.txt"]


def _heading_skip_finding() -> Finding:
    finding = _finding()
    finding.rule = "SEO-HEADING-SKIP-001"
    finding.affected_code_entity = None
    return finding


def _heading_plan(*files: str) -> ChangePlan:
    return ChangePlan(
        finding_id="SEO-HEADING-SKIP-001:abc123",
        target_files=list(files),
        target_symbols=[],
        reuse_notes="x",
        expected_diff_summary="y",
        required_tests=[],
        required_validation=[],
    )


def _contact_workspace(tmp_path):
    for rel, text in {
        "src/app/contact/page.tsx": (
            'import ContactPage from "@/components/pages/ContactPage";\n'
            "export default function Contact() { return <ContactPage />; }\n"
        ),
        "src/components/pages/ContactPage.tsx": '<h1>Hi</h1><EText as="h3" path="title" />',
    }.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_constrain_target_files_retargets_a_heading_free_route_wrapper(tmp_path) -> None:
    workspace = _contact_workspace(tmp_path)
    constrained = constrain_target_files(
        _heading_plan("src/app/contact/page.tsx"),
        _heading_skip_finding(),
        workspace=workspace,
    )
    assert constrained.target_files == ["src/components/pages/ContactPage.tsx"]


def test_constrain_target_files_keeps_a_file_that_already_has_headings(tmp_path) -> None:
    workspace = _contact_workspace(tmp_path)
    constrained = constrain_target_files(
        _heading_plan("src/components/pages/ContactPage.tsx"),
        _heading_skip_finding(),
        workspace=workspace,
    )
    assert constrained.target_files == ["src/components/pages/ContactPage.tsx"]


def test_constrain_target_files_leaves_other_rules_alone(tmp_path) -> None:
    workspace = _contact_workspace(tmp_path)
    finding = _finding()
    finding.affected_code_entity = None
    constrained = constrain_target_files(
        _heading_plan("src/app/contact/page.tsx"), finding, workspace=workspace
    )
    assert constrained.target_files == ["src/app/contact/page.tsx"]


def _og_finding() -> Finding:
    finding = _finding()
    finding.finding_id = "SEO-OG-INCOMPLETE-001:abc123"
    finding.rule = "SEO-OG-INCOMPLETE-001"
    finding.affected_code_entity = "src/app/contact/page.tsx"
    finding.affected_resource = "https://drmoksha.com/contact"
    finding.recommended_action = "Add the missing Open Graph tags (title, type, image, url)."
    return finding


def _og_workspace(tmp_path):
    files = {
        "src/app/contact/page.tsx": (
            'import { toNextMetadata } from "@/lib/seo/metadata";\n'
            "export async function generateMetadata() {\n"
            "  return toNextMetadata({ seo: content.seo, path: '/contact' });\n"
            "}\n"
        ),
        "src/lib/seo/metadata.ts": (
            "export function toNextMetadata({ seo }: { seo?: { openGraph?: { ogImage?: { url: string } } } }) {\n"
            "  return { openGraph: { title: 'x' } };\n"
            "}\n"
        ),
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_constrain_target_files_retargets_og_page_to_metadata_helper(tmp_path) -> None:
    workspace = _og_workspace(tmp_path)
    plan = ChangePlan(
        finding_id="SEO-OG-INCOMPLETE-001:abc123",
        target_files=["src/app/contact/page.tsx"],
        target_symbols=["generateMetadata"],
        reuse_notes="reuse toNextMetadata",
        expected_diff_summary="add og:type",
        required_tests=[],
        required_validation=["build"],
    )
    constrained = constrain_target_files(plan, _og_finding(), workspace=workspace)
    assert constrained.target_files == ["src/lib/seo/metadata.ts"]


def test_constrain_target_files_og_retarget_does_not_apply_to_other_rules(tmp_path) -> None:
    workspace = _og_workspace(tmp_path)
    finding = _finding()
    finding.affected_code_entity = "src/app/contact/page.tsx"
    plan = ChangePlan(
        finding_id="SEO-CANONICAL-001:abc123",
        target_files=["src/app/contact/page.tsx"],
        target_symbols=["generateMetadata"],
        reuse_notes="reuse toNextMetadata",
        expected_diff_summary="add canonical",
        required_tests=[],
        required_validation=["build"],
    )
    constrained = constrain_target_files(plan, finding, workspace=workspace)
    assert constrained.target_files == ["src/app/contact/page.tsx"]
