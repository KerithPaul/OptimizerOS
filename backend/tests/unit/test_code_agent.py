"""Code Agent (step 7.3 verify, `[SPEC AGENTS.md §33]`).

A scope or content-change violation must write zero bytes to disk. A
dry run (`apply=False`, mode < APPLY_LOCALLY) must never write either,
even for an otherwise-clean patch — it only ever produces a diff preview.
"""

from __future__ import annotations

import json

from app.agents.code import run_code_agent
from app.agents.base import default_budget
from app.agents.runtime import AgentRunStatus
from app.changes.scope import ScopeEnvelope
from app.core.config import Settings
from app.knowledge.authority import AuthorityLevel
from app.models.finding import Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.planners.change import ChangePlan
from app.planners.execution import ExecutionPlan, ExecutionStep
from app.planners.optimization import Intervention


def _settings(**overrides: object) -> Settings:
    values = {
        "APP_SECRET_KEY": "unit-test-secret",
        "CREDENTIAL_ENCRYPTION_KEY": "cU5b7d2m9zQwErTyUiOpAsDfGhJkLzXcVbNmQwErTy8=",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class _FakeGateway:
    def __init__(self, patch_json: str) -> None:
        self.patch_json = patch_json

    def chat(self, messages, *, tier=None, response_format=None):
        class _Result:
            content = self.patch_json
            provider = "fake"
            model = "fake-strong"
            tokens = 33
            latency_ms = 5

        return _Result()


def _finding(affected_code_entity: str = "app/products/[id]/page.tsx") -> Finding:
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
        affected_code_entity=affected_code_entity,
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
        hypothesis="Missing canonical confuses crawlers.",
        intervention="Add a canonical link tag.",
        expected_mechanism="Resolves duplicate-content ambiguity.",
        risk="low",
    )


def _change_plan(target_files: list[str]) -> ChangePlan:
    return ChangePlan(
        finding_id="SEO-CANONICAL-001:abc123",
        target_files=target_files,
        target_symbols=["generateMetadata"],
        reuse_notes="reuse generateMetadata",
        expected_diff_summary="add canonical tag",
        required_tests=[],
        required_validation=["build"],
    )


def _execution_plan() -> ExecutionPlan:
    return ExecutionPlan(
        finding_id="SEO-CANONICAL-001:abc123",
        steps=[ExecutionStep(order=1, action="write_file", detail="add canonical tag")],
        dependencies=[],
        sandbox_operations=["build"],
    )


def _envelope(allowed: tuple[str, ...], forbidden: tuple[str, ...] = ()) -> ScopeEnvelope:
    return ScopeEnvelope(
        allowed_directories=allowed,
        forbidden_directories=forbidden,
        max_files_changed=10,
        max_lines_changed=1000,
        max_diff_bytes=50_000,
    )


def _patch_json(file_path: str, new_content: str) -> str:
    return json.dumps(
        {
            "finding_id": "SEO-CANONICAL-001:abc123",
            "files": [
                {
                    "file_path": file_path,
                    "new_content": new_content,
                    "change_summary": "added canonical link tag",
                }
            ],
            "notes": "minimal metadata-only change",
        }
    )


def test_in_scope_patch_is_written_to_disk(tmp_path) -> None:
    target = "app/products/[id]/page.tsx"
    (tmp_path / "app" / "products" / "[id]").mkdir(parents=True)
    (tmp_path / target).write_text("export default function Page() { return null; }", encoding="utf-8")

    gateway = _FakeGateway(_patch_json(target, "export const canonical = true;"))
    result = run_code_agent(
        finding=_finding(),
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("app/products/[id]",)),
        apply=True,
    )

    assert result.violation is None
    assert result.wrote_files is True
    assert (tmp_path / target).read_text(encoding="utf-8") == "export const canonical = true;"
    assert result.outcome.status is AgentRunStatus.SUCCEEDED
    assert result.outcome.files_modified == 1


def test_dry_run_never_writes_even_when_in_scope(tmp_path) -> None:
    target = "app/products/[id]/page.tsx"
    (tmp_path / "app" / "products" / "[id]").mkdir(parents=True)
    original = "export default function Page() { return null; }"
    (tmp_path / target).write_text(original, encoding="utf-8")

    gateway = _FakeGateway(_patch_json(target, "export const canonical = true;"))
    result = run_code_agent(
        finding=_finding(),
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("app/products/[id]",)),
        apply=False,
    )

    assert result.violation is None
    assert result.wrote_files is False
    assert (tmp_path / target).read_text(encoding="utf-8") == original
    assert result.patch is not None
    assert result.diffs[0].lines_added >= 1


def test_out_of_scope_file_is_rejected_and_never_written(tmp_path) -> None:
    (tmp_path / "auth").mkdir(parents=True)
    (tmp_path / "auth" / "login.ts").write_text("export function login() {}", encoding="utf-8")

    gateway = _FakeGateway(_patch_json("auth/login.ts", "export function login() { /* backdoor */ }"))
    result = run_code_agent(
        finding=_finding(),
        intervention=_intervention(),
        change_plan=_change_plan(["app/products/[id]/page.tsx"]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("app/products/[id]",), forbidden=("auth",)),
        apply=True,
    )

    assert result.violation is not None
    assert result.violation.reason == "forbidden_directory"
    assert result.wrote_files is False
    assert (tmp_path / "auth" / "login.ts").read_text(encoding="utf-8") == "export function login() {}"
    assert result.outcome.status is AgentRunStatus.PARTIAL
    assert result.outcome.stopped_reason == "forbidden_directory"


def test_keyword_stuffed_markdown_is_rejected_and_never_written(tmp_path) -> None:
    target = "content/product.md"
    (tmp_path / "content").mkdir(parents=True)
    before = "Buy our running shoes today. Great for daily training."
    (tmp_path / target).write_text(before, encoding="utf-8")

    stuffed = " ".join(["running shoes"] * 20)
    gateway = _FakeGateway(_patch_json(target, stuffed))
    result = run_code_agent(
        finding=_finding(affected_code_entity=target),
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("content",)),
        apply=True,
    )

    assert result.violation is not None
    assert result.violation.reason == "content_change_violation"
    assert result.wrote_files is False
    assert (tmp_path / target).read_text(encoding="utf-8") == before


def test_json_ld_html_patch_is_not_a_content_violation(tmp_path) -> None:
    target = "client/index.html"
    (tmp_path / "client").mkdir(parents=True)
    before = (
        "<!doctype html><html><head><title>Lex Fintech</title>"
        '<link rel="canonical" href="https://lexfintech.io/" /></head>'
        '<body><a href="https://lexfintech.io">Lex Fintech</a></body></html>'
    )
    after = before.replace(
        "</head>",
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@graph":['
        '{"@type":"Organization","name":"Lex Fintech","url":"https://lexfintech.io",'
        '"logo":"https://lexfintech.io/logo.png"},'
        '{"@type":"WebSite","name":"Lex Fintech","url":"https://lexfintech.io"}'
        "]}</script></head>",
    )
    (tmp_path / target).write_text(before, encoding="utf-8")
    gateway = _FakeGateway(_patch_json(target, after))
    result = run_code_agent(
        finding=_finding(affected_code_entity=target),
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("client",)),
        apply=True,
    )

    assert result.violation is None
    assert result.wrote_files is True
    assert "application/ld+json" in (tmp_path / target).read_text(encoding="utf-8")


class _BoomGateway:
    def chat(self, messages, *, tier=None, response_format=None):
        raise AssertionError("LLM must not run when implementation cannot be located")


def test_missing_file_with_href_locator_stops_without_llm(tmp_path) -> None:
    target = "content/article.md"
    finding = _finding(affected_code_entity=target)
    finding.finding_id = "SEO-BROKEN-INTERNAL-LINK-001:abc"
    finding.rule = "SEO-BROKEN-INTERNAL-LINK-001"
    finding.affected_resource = "https://example.com/article -> https://example.com/gone"
    finding.evidence = [
        {
            "source": finding.affected_resource,
            "excerpt": "broken href",
            "confidence": "direct",
            "value": {"href": "https://example.com/gone", "status_or_failed": True},
        }
    ]
    result = run_code_agent(
        finding=finding,
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=_BoomGateway(),  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("content",)),
        apply=True,
    )
    assert result.wrote_files is False
    assert result.violation is not None
    assert result.violation.reason == "target_missing"
    assert result.outcome.status is AgentRunStatus.PARTIAL


def test_identity_patch_is_a_no_op_stop(tmp_path) -> None:
    target = "content/article.md"
    (tmp_path / "content").mkdir()
    original = "See https://example.com/gone for details.\n"
    (tmp_path / target).write_text(original, encoding="utf-8")
    finding = _finding(affected_code_entity=target)
    finding.finding_id = "SEO-BROKEN-INTERNAL-LINK-001:abc"
    finding.rule = "SEO-BROKEN-INTERNAL-LINK-001"
    finding.affected_resource = "https://example.com/article -> https://example.com/gone"
    finding.evidence = [
        {
            "source": finding.affected_resource,
            "excerpt": "broken href",
            "confidence": "direct",
            "value": {"href": "https://example.com/gone", "status_or_failed": True},
        }
    ]
    gateway = _FakeGateway(_patch_json(target, original))
    result = run_code_agent(
        finding=finding,
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("content",)),
        apply=True,
    )
    assert result.wrote_files is False
    assert result.violation is not None
    assert result.violation.reason == "no_op"
    assert (tmp_path / target).read_text(encoding="utf-8") == original


def test_trusted_payload_includes_evidence_locators(tmp_path) -> None:
    target = "content/article.md"
    (tmp_path / "content").mkdir()
    original = "See https://example.com/gone for details.\n"
    (tmp_path / target).write_text(original, encoding="utf-8")
    finding = _finding(affected_code_entity=target)
    finding.finding_id = "SEO-BROKEN-INTERNAL-LINK-001:abc"
    finding.rule = "SEO-BROKEN-INTERNAL-LINK-001"
    finding.affected_resource = "https://example.com/article -> https://example.com/gone"
    finding.evidence = [
        {
            "source": finding.affected_resource,
            "excerpt": "broken href",
            "confidence": "direct",
            "value": {"href": "https://example.com/gone", "status_or_failed": True},
        }
    ]
    captured: list[list[dict[str, str]]] = []

    class _CaptureGateway(_FakeGateway):
        def chat(self, messages, *, tier=None, response_format=None):
            captured.append(list(messages))
            return super().chat(messages, tier=tier, response_format=response_format)

    gateway = _CaptureGateway(_patch_json(target, original + "fixed\n"))
    run_code_agent(
        finding=finding,
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("content",)),
        apply=False,
    )
    blob = "\n".join(m["content"] for m in captured[0])
    assert "https://example.com/gone" in blob
    assert "evidence_locators" in blob


def test_new_file_reads_as_empty_current_content(tmp_path) -> None:
    target = "public/robots.txt"
    gateway = _FakeGateway(_patch_json(target, "User-agent: *\nAllow: /\n"))
    result = run_code_agent(
        finding=_finding(affected_code_entity=target),
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("public",)),
        apply=True,
    )

    assert result.violation is None
    assert result.wrote_files is True
    assert (tmp_path / target).is_file()
    assert result.diffs[0].lines_removed == 0


def test_invented_new_file_is_refused_even_when_the_plan_names_it(tmp_path) -> None:
    target = "src/lib/navigation/siteNav.ts"
    gateway = _SequenceGateway(
        _patch_json(target, 'import type { NavigationItem } from "../types";\nexport const nav = [];'),
        _patch_json(target, "export const nav = [];"),
    )
    result = _run(tmp_path, target, gateway)

    assert result.outcome.status is AgentRunStatus.FAILED
    assert result.wrote_files is False
    assert not (tmp_path / target).exists()
    assert len(gateway.prompts) == 2
    assert "only standard site files" in gateway.prompts[1][-1]["content"]


def test_og_finding_lists_public_image_fallbacks(tmp_path) -> None:
    target = "src/lib/seo/metadata.ts"
    (tmp_path / "src/lib/seo").mkdir(parents=True)
    (tmp_path / target).write_text(
        "export function toNextMetadata() {\n  return { openGraph: { title: 'x', url: 'y' } };\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "logo.png").write_bytes(b"png")
    finding = _finding(affected_code_entity=target)
    finding.finding_id = "SEO-OG-INCOMPLETE-001:abc123"
    finding.rule = "SEO-OG-INCOMPLETE-001"
    captured: list[list[dict[str, str]]] = []

    class _CaptureGateway(_FakeGateway):
        def chat(self, messages, *, tier=None, response_format=None):
            captured.append(list(messages))
            return super().chat(messages, tier=tier, response_format=response_format)

    gateway = _CaptureGateway(
        json.dumps(
            {
                "finding_id": "SEO-OG-INCOMPLETE-001:abc123",
                "files": [
                    {
                        "file_path": target,
                        "edits": [{"find": "title: 'x'", "replace": "title: 'x', type: 'website'"}],
                        "change_summary": "add og:type",
                    }
                ],
                "notes": "",
            }
        )
    )
    run_code_agent(
        finding=finding,
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("src/lib/seo",)),
        apply=False,
    )
    blob = "\n".join(m["content"] for m in captured[0])
    assert "og_image_fallbacks" in blob
    assert "public/logo.png" in blob
    assert "images: []" in blob


def test_repair_feedback_is_appended_after_the_file_contents(tmp_path) -> None:
    target = "app/products/page.tsx"
    (tmp_path / "app/products").mkdir(parents=True)
    (tmp_path / target).write_text("export const a = 1;\n", encoding="utf-8")
    gateway = _SequenceGateway(_edits_json(target, [{"find": "a = 1", "replace": "a = 2"}]))
    run_code_agent(
        finding=_finding(affected_code_entity=target),
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("app/products",)),
        apply=True,
        feedback="build FAILED: Cannot find module '../types'",
    )

    assert gateway.prompts[0][-1] == {
        "role": "user",
        "content": "build FAILED: Cannot find module '../types'",
    }


def _edits_json(file_path: str, edits: list[dict[str, str]]) -> str:
    return json.dumps(
        {
            "finding_id": "SEO-CANONICAL-001:abc123",
            "files": [{"file_path": file_path, "edits": edits, "change_summary": "edit"}],
            "notes": "",
        }
    )


class _SequenceGateway:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[list[dict[str, str]]] = []

    def chat(self, messages, *, tier=None, response_format=None):
        self.prompts.append(list(messages))
        content = self.answers.pop(0)

        class _Result:
            provider = "fake"
            model = "fake-strong"
            tokens = 10
            latency_ms = 1

        _Result.content = content
        return _Result()


def _run(tmp_path, target, gateway, *, apply=True):
    return run_code_agent(
        finding=_finding(affected_code_entity=target),
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope((str(target).rsplit("/", 1)[0],)),
        apply=apply,
    )


def test_edits_are_applied_to_the_full_file_and_written(tmp_path) -> None:
    target = "client/index.html"
    (tmp_path / "client").mkdir()
    before = "<html>\n<head>\n<title>T</title>\n</head>\n<body>hi</body>\n</html>\n"
    (tmp_path / target).write_text(before, encoding="utf-8")

    gateway = _SequenceGateway(
        _edits_json(target, [{"find": "</head>", "replace": '<link rel="canonical" href="/">\n</head>'}])
    )
    result = _run(tmp_path, target, gateway)

    assert result.violation is None
    assert result.wrote_files is True
    assert (tmp_path / target).read_text(encoding="utf-8") == before.replace(
        "</head>", '<link rel="canonical" href="/">\n</head>'
    )
    assert result.diffs[0].lines_added == 1
    assert result.diffs[0].lines_removed == 0


def test_large_file_prompt_is_windowed_and_edits_keep_the_rest_of_the_file(tmp_path) -> None:
    target = "client/index.html"
    (tmp_path / "client").mkdir()
    body = "".join(f"<p>paragraph {n} " + "word " * 30 + "</p>\n" for n in range(1, 600))
    before = f"<html>\n<head>\n<title>T</title>\n</head>\n<body>\n{body}</body>\n</html>\n"
    assert len(before) > 60_000
    (tmp_path / target).write_text(before, encoding="utf-8")

    gateway = _SequenceGateway(
        _edits_json(target, [{"find": "</head>", "replace": '<link rel="canonical" href="/">\n</head>'}])
    )
    result = _run(tmp_path, target, gateway)

    prompt_chars = sum(len(m["content"]) for m in gateway.prompts[0])
    assert prompt_chars < 20_000, "a 60k-char file must not be sent whole"
    assert any("omitted ..." in m["content"] for m in gateway.prompts[0])
    assert result.violation is None
    written = (tmp_path / target).read_text(encoding="utf-8")
    # Nothing past the shown window was lost, which a whole-file rewrite would risk.
    assert written == before.replace("</head>", '<link rel="canonical" href="/">\n</head>')


def test_unmatched_edit_gets_one_corrective_round(tmp_path) -> None:
    target = "client/index.html"
    (tmp_path / "client").mkdir()
    (tmp_path / target).write_text("<html><head></head></html>\n", encoding="utf-8")

    gateway = _SequenceGateway(
        _edits_json(target, [{"find": "<HEAD>", "replace": "<head><meta>"}]),
        _edits_json(target, [{"find": "<head>", "replace": "<head><meta>"}]),
    )
    result = _run(tmp_path, target, gateway)

    assert result.violation is None
    assert len(gateway.prompts) == 2
    assert "could not be applied" in gateway.prompts[1][-1]["content"]
    assert "<head><meta>" in (tmp_path / target).read_text(encoding="utf-8")


def test_persistently_unmatched_edit_fails_without_writing(tmp_path) -> None:
    target = "client/index.html"
    (tmp_path / "client").mkdir()
    original = "<html><head></head></html>\n"
    (tmp_path / target).write_text(original, encoding="utf-8")

    bad = _edits_json(target, [{"find": "<HEAD>", "replace": "x"}])
    result = _run(tmp_path, target, _SequenceGateway(bad, bad))

    assert result.outcome.status is AgentRunStatus.FAILED
    assert result.wrote_files is False
    assert (tmp_path / target).read_text(encoding="utf-8") == original
    # Both unusable answers stay inspectable instead of vanishing with the failure.
    assert [m.role for m in result.outcome.messages] == ["assistant", "assistant"]
    assert "<HEAD>" in result.outcome.messages[0].content
    assert "starts with '<HEAD>'" in (result.outcome.error or "")


_AP_DESCRIPTION = (
    "Growing a business means managing legal risk before it becomes a dispute. "
    "Dr. Moksha advises startups in Andhra Pradesh, working alongside local counsel "
    "in Visakhapatnam, Vijayawada and Guntur, and builds ironclad legal frameworks."
)


def _metadesc_finding(target: str) -> Finding:
    finding = _finding(affected_code_entity=target)
    finding.finding_id = "SEO-METADESC-LENGTH-001:abc"
    finding.rule = "SEO-METADESC-LENGTH-001"
    finding.affected_url = "https://example.com/corporate-lawyer-in-andhra-pradesh"
    finding.evidence = [
        {
            "source": finding.affected_url,
            "excerpt": "meta description too long",
            "selector": "meta_description length_gt 160",
            "confidence": "direct",
            "value": _AP_DESCRIPTION,
        }
    ]
    return finding


def _cms_workspace(tmp_path, defaults: str):
    target = "src/lib/cms/defaults/practicePages.ts"
    (tmp_path / "src/lib/cms/defaults").mkdir(parents=True)
    (tmp_path / target).write_text(defaults, encoding="utf-8")
    (tmp_path / "package.json").write_text('{"dependencies": {"@strapi/client": "1"}}', encoding="utf-8")
    return target


def _run_metadesc(tmp_path, target, gateway):
    return run_code_agent(
        finding=_metadesc_finding(target),
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("src",)),
        apply=True,
    )


def test_cms_served_text_missing_from_the_repo_stops_without_llm(tmp_path) -> None:
    defaults = 'export const pages = [{ slug: "corporate-lawyer-in-hyderabad", metaDescription: "Hyderabad only." }];\n'
    target = _cms_workspace(tmp_path, defaults)

    result = _run_metadesc(tmp_path, target, _BoomGateway())

    assert result.violation is not None
    assert result.violation.reason == "content_not_in_repository"
    assert "Strapi" in result.violation.detail
    assert result.outcome.status is AgentRunStatus.PARTIAL
    assert result.wrote_files is False
    assert (tmp_path / target).read_text(encoding="utf-8") == defaults


def test_cms_site_whose_repo_holds_the_live_text_still_runs_the_agent(tmp_path) -> None:
    defaults = f'export const pages = [{{ metaDescription:\n  "{_AP_DESCRIPTION}" }}];\n'
    target = _cms_workspace(tmp_path, defaults)
    shorter = "Growing a business means managing legal risk. Corporate counsel in Andhra Pradesh."
    gateway = _SequenceGateway(
        _edits_json(target, [{"find": _AP_DESCRIPTION, "replace": shorter}])
    )

    result = _run_metadesc(tmp_path, target, gateway)

    assert result.violation is None
    assert shorter in (tmp_path / target).read_text(encoding="utf-8")


def test_text_missing_from_the_repo_is_not_a_stop_without_a_cms(tmp_path) -> None:
    target = "src/lib/defaults.ts"
    (tmp_path / "src/lib").mkdir(parents=True)
    (tmp_path / target).write_text("export const title = `${name} | Advocate`;\n", encoding="utf-8")
    gateway = _SequenceGateway(
        _edits_json(target, [{"find": "| Advocate", "replace": "| Law"}])
    )

    result = _run_metadesc(tmp_path, target, gateway)

    assert result.violation is None
    assert len(gateway.prompts) == 1


def test_whole_file_rewrite_of_a_partially_shown_file_is_refused(tmp_path) -> None:
    target = "client/index.html"
    (tmp_path / "client").mkdir()
    body = "".join(f"<p>paragraph {n} " + "word " * 30 + "</p>\n" for n in range(1, 600))
    before = f"<html>\n<head>\n</head>\n<body>\n{body}</body>\n</html>\n"
    (tmp_path / target).write_text(before, encoding="utf-8")

    rewrite = _patch_json(target, "<html><head><link></head></html>")
    result = _run(tmp_path, target, _SequenceGateway(rewrite, rewrite))

    assert result.outcome.status is AgentRunStatus.FAILED
    assert (tmp_path / target).read_text(encoding="utf-8") == before


_CONTACT = (
    '<h1>Get In Touch</h1>\n'
    '<EText as="h3" path="title" />\n'
    '<p>Call us</p>\n'
    '<h2>Offices</h2>\n'
    '<h3>London</h3>\n'
)


class _HeadingGateway:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages, *, tier=None, response_format=None, max_tokens=None):
        self.calls.append(list(messages))

        class _Result:
            provider = "fake"
            model = "fake-strong"
            tokens = 5
            latency_ms = 1

        _Result.content = self.answers.pop(0)
        return _Result()


def _heading_skip_run(tmp_path, gateway, *, apply: bool = True):
    target = "src/components/pages/ContactPage.tsx"
    (tmp_path / "src/components/pages").mkdir(parents=True)
    (tmp_path / target).write_text(_CONTACT, encoding="utf-8")
    finding = _finding(affected_code_entity=target)
    finding.finding_id = "SEO-HEADING-SKIP-001:abc"
    finding.rule = "SEO-HEADING-SKIP-001"
    finding.affected_resource = "https://example.com/contact"
    finding.evidence = [
        {
            "source": finding.affected_resource,
            "excerpt": "levels",
            "confidence": "direct",
            "value": [1, 3, 2, 3],
        }
    ]
    result = run_code_agent(
        finding=finding,
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("src/components/pages",)),
        apply=apply,
    )
    return result, tmp_path / target


def test_heading_skip_prompt_names_the_offending_source_line(tmp_path) -> None:
    fixed = _CONTACT.replace('as="h3" path="title"', 'as="h2" path="title"')
    gateway = _HeadingGateway(_patch_json("src/components/pages/ContactPage.tsx", fixed))
    _heading_skip_run(tmp_path, gateway)

    blob = "\n".join(m["content"] for m in gateway.calls[0])
    assert "heading_skip_diagnosis" in blob
    assert '"line": 2' in blob and '"set_level_to": 2' in blob
    assert "live_first_skip" in blob


def test_heading_skip_fix_by_changing_the_heading_level_is_written(tmp_path) -> None:
    fixed = _CONTACT.replace('as="h3" path="title"', 'as="h2" path="title"')
    gateway = _HeadingGateway(_patch_json("src/components/pages/ContactPage.tsx", fixed))
    result, path = _heading_skip_run(tmp_path, gateway)

    assert result.violation is None
    assert result.wrote_files is True
    assert path.read_text(encoding="utf-8") == fixed


def test_heading_skip_paragraph_to_heading_is_rejected_then_retried(tmp_path) -> None:
    bad = _CONTACT.replace("<p>Call us</p>", "<h4>Call us</h4>")
    fixed = _CONTACT.replace('as="h3" path="title"', 'as="h2" path="title"')
    gateway = _HeadingGateway(
        _patch_json("src/components/pages/ContactPage.tsx", bad),
        _patch_json("src/components/pages/ContactPage.tsx", fixed),
    )
    result, path = _heading_skip_run(tmp_path, gateway)

    assert result.violation is None
    assert path.read_text(encoding="utf-8") == fixed
    assert "rejected before it was written" in gateway.calls[1][-1]["content"]


def test_heading_skip_unresolved_after_retry_writes_nothing(tmp_path) -> None:
    bad = _CONTACT.replace("<p>Call us</p>", "<h4>Call us</h4>")
    gateway = _HeadingGateway(
        _patch_json("src/components/pages/ContactPage.tsx", bad),
        _patch_json("src/components/pages/ContactPage.tsx", bad),
    )
    result, path = _heading_skip_run(tmp_path, gateway)

    assert result.violation is not None
    assert result.violation.reason == "heading_skip_unresolved"
    assert result.wrote_files is False
    assert path.read_text(encoding="utf-8") == _CONTACT


def _og_page_workspace(tmp_path) -> str:
    target = "src/lib/seo/metadata.ts"
    (tmp_path / "src/app/contact").mkdir(parents=True)
    (tmp_path / "src/lib/seo").mkdir(parents=True)
    (tmp_path / "src/lib/cms").mkdir(parents=True)
    (tmp_path / "src/app/contact/page.tsx").write_text(
        'import { toNextMetadata } from "@/lib/seo/metadata";\n'
        "export async function generateMetadata() {\n"
        "  return toNextMetadata({ seo: content.seo, path: '/contact' });\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / target).write_text(
        'import type { SeoComponent } from "@/lib/cms/types";\n'
        "export function toNextMetadata({ seo }: { seo?: SeoComponent }) {\n"
        "  return { title: 'x' };\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "src/lib/cms/types.ts").write_text(
        "export interface CmsImage { mediaId: number | null; url: string; }\n"
        "export interface SeoOpenGraph { ogTitle?: string; ogImage?: CmsImage | null; }\n"
        "export interface SeoComponent { openGraph?: SeoOpenGraph; }\n",
        encoding="utf-8",
    )
    return target


def test_code_agent_prompt_includes_imported_type_files(tmp_path) -> None:
    target = _og_page_workspace(tmp_path)
    captured: list[list[dict[str, str]]] = []

    class _Capture(_FakeGateway):
        def chat(self, messages, *, tier=None, response_format=None):
            captured.append(list(messages))
            return super().chat(messages, tier=tier, response_format=response_format)

    gateway = _Capture(
        _edits_json(target, [{"find": "title: 'x'", "replace": "title: 'x', openGraph: { type: 'website' }"}])
    )
    run_code_agent(
        finding=_finding(affected_code_entity=target),
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("src/lib/seo",)),
        apply=True,
    )
    blob = "\n".join(m["content"] for m in captured[0])
    assert "export interface CmsImage" in blob
    assert "read_only" in blob


def test_code_agent_rejects_edits_to_supporting_type_files(tmp_path) -> None:
    target = _og_page_workspace(tmp_path)
    types = "src/lib/cms/types.ts"
    original = (tmp_path / types).read_text(encoding="utf-8")
    gateway = _SequenceGateway(
        _edits_json(types, [{"find": "ogTitle?: string;", "replace": "ogTitle?: string; ogType?: string;"}]),
        _edits_json(target, [{"find": "title: 'x'", "replace": "title: 'x', openGraph: { type: 'website' }"}]),
    )
    result = run_code_agent(
        finding=_finding(affected_code_entity=target),
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("src/lib/seo", "src/lib/cms")),
        apply=True,
    )
    assert result.wrote_files is True
    assert (tmp_path / types).read_text(encoding="utf-8") == original
    assert "read-only" in gateway.prompts[1][-1]["content"]


def test_repair_prompt_includes_type_file_named_in_the_build_error(tmp_path) -> None:
    target = "src/app/contact/page.tsx"
    (tmp_path / "src/app/contact").mkdir(parents=True)
    (tmp_path / "src/lib/cms").mkdir(parents=True)
    (tmp_path / target).write_text("export const page = true;\n", encoding="utf-8")
    (tmp_path / "src/lib/cms/types.ts").write_text(
        "export interface CmsImage { url: string; }\n",
        encoding="utf-8",
    )
    captured: list[list[dict[str, str]]] = []

    class _Capture(_FakeGateway):
        def chat(self, messages, *, tier=None, response_format=None):
            captured.append(list(messages))
            return super().chat(messages, tier=tier, response_format=response_format)

    gateway = _Capture(_edits_json(target, [{"find": "true", "replace": "false"}]))
    run_code_agent(
        finding=_finding(affected_code_entity=target),
        intervention=_intervention(),
        change_plan=_change_plan([target]),
        execution_plan=_execution_plan(),
        workspace=tmp_path,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        envelope=_envelope(("src/app/contact",)),
        apply=True,
        feedback=(
            "Type error: Type 'string | undefined' is not assignable to type "
            "'CmsImage | null | undefined'."
        ),
    )
    blob = "\n".join(m["content"] for m in captured[0])
    assert "export interface CmsImage" in blob
