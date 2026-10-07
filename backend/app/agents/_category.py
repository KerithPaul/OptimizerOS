"""Shared loop for the SEO / AEO / GEO agents (step 6.4).

Each of these three agents takes the same shape: rank the Findings in its
categories by priority (step 5.C.2), keep only the necessary context, then
ask the Layer 3 Optimization Planner to turn remaining ones into ranked
hypothesised interventions, bounded by the agent's budget. `seo.py` /
`aeo.py` / `geo.py` each supply only their category set and agent name.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.agents.runtime import (
    AgentBudget,
    AgentMessageRecord,
    AgentRunOutcome,
    AgentRuntime,
    AgentState,
    AgentStepResult,
)
from app.llm.gateway import LLMGateway
from app.models.finding import Finding
from app.planners.intent import IntentObjective
from app.planners.optimization import (
    Intervention,
    can_template_intervention,
    estimate_batch_request_tokens,
    plan_optimizations_batch,
    template_intervention,
)
from app.retrieval.prioritize import prioritize_findings

# Batching trades a larger single prompt for far fewer requests, which is
# what actually keeps a free-tier rate ceiling from tripping on a run with
# many findings. Pack by the *full* request estimate (system + wrappers +
# payload + reserved completion), not a finding count, and stay well under
# the smallest observed free-tier TPM window (~6000) so three sequential
# category agents can share one minute.
_MAX_BATCH_REQUEST_TOKENS = 2500
_MAX_BATCH_FINDINGS = 5

# Same numeric cap as retrieval.hybrid.EVIDENCE_ITEM_CAP (AGENTS.md §21).
_MAX_FINDINGS_PER_AGENT = 8

# Tokens too generic to identify a URL, rule, or topic. "product pages" →
# {"product"}; "pages" / "seo" / "improve" would match almost everything.
_SCOPE_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "page",
        "pages",
        "site",
        "website",
        "only",
        "all",
        "seo",
        "aeo",
        "geo",
        "improve",
        "improving",
        "look",
        "looking",
        "find",
        "finding",
        "findings",
        "optimize",
        "optimization",
        "please",
        "want",
        "need",
        "check",
        "search",
        "request",
        "agent",
        "technical",
        "content",
        "quality",
        "issue",
        "issues",
        "problem",
        "problems",
        "high",
        "low",
        "risk",
        "confidence",
    }
)

_RULE_ID_RE = re.compile(r"\b(?:SEO|AEO|GEO)-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}\b", re.IGNORECASE)


# A published or in-flight finding is not a new proposal. REJECTED and
# ROLLED_BACK stay actionable: the problem is still there. OPEN is the
# normal case. FIXED / VALIDATED / PLANNED / IN_PROGRESS are not.
_ACTIONABLE_FINDING_STATUSES = frozenset({"OPEN", "REJECTED", "ROLLED_BACK"})


def _category_of(finding: Finding) -> str:
    return finding.category.value if hasattr(finding.category, "value") else str(finding.category)


def _status_value(finding: Finding) -> str:
    status = getattr(finding, "status", None)
    if status is None:
        return "OPEN"
    return status.value if hasattr(status, "value") else str(status)


def _is_actionable(finding: Finding) -> bool:
    return _status_value(finding) in _ACTIONABLE_FINDING_STATUSES


def _scope_tokens(scope: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", scope.lower())
    return [token for token in tokens if len(token) >= 3 and token not in _SCOPE_STOPWORDS]


def _finding_haystack(finding: Finding) -> str:
    return " ".join(
        part
        for part in (
            finding.affected_resource,
            finding.rule,
            finding.observation,
            finding.problem,
            finding.recommendation,
            finding.recommended_action,
        )
        if part
    ).lower()


def _matches_scope(finding: Finding, tokens: list[str]) -> bool:
    """Match topic tokens against the finding, not only its URL.

    Scope used to search `affected_resource` only, so "orphan pages" never
    matched `https://example.com/` and the filter silently fell back to the
    same top-N audit findings on every prompt.
    """
    if not tokens:
        return True
    haystack = _finding_haystack(finding)
    return any(token in haystack for token in tokens)


def _is_urlish(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _normalize_url(value: str) -> str:
    return value.lower().rstrip("/")


def _urls_equal(resource: str, url: str) -> bool:
    return bool(resource) and bool(url) and _normalize_url(resource) == _normalize_url(url)


def _research_needles(package: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """URLs and rule ids the Research Agent actually retrieved.

    Used as a filter ("which resources/rules matter"), not pasted into the
    Layer 3 prompt.
    """
    if not package:
        return [], []
    urls: list[str] = []
    rules: list[str] = []

    def _take(raw: object, *, as_url: bool | None = None) -> None:
        text = str(raw or "").strip()
        if not text:
            return
        rule_ids = [match.lower() for match in _RULE_ID_RE.findall(text)]
        if rule_ids:
            rules.extend(rule_ids)
            if as_url is not True:
                return
        if as_url is True or (as_url is None and _is_urlish(text)):
            urls.append(_normalize_url(text))
        else:
            rules.append(text.lower())

    for page in package.get("pages") or []:
        if isinstance(page, dict):
            _take(page.get("locator"), as_url=True)
            metadata = page.get("metadata") or {}
            if isinstance(metadata, dict):
                _take(metadata.get("url"), as_url=True)
    for item in package.get("knowledge") or []:
        if isinstance(item, dict):
            metadata = item.get("metadata") or {}
            rule_id = None
            if isinstance(metadata, dict):
                rule_id = metadata.get("rule_id")
            _take(rule_id or item.get("locator"), as_url=False)
    for source in package.get("sources") or []:
        _take(source)
    return urls, rules


def _matches_research(finding: Finding, urls: list[str], rules: list[str]) -> bool:
    if not urls and not rules:
        return True
    resource = finding.affected_resource or ""
    if any(_urls_equal(resource, url) for url in urls):
        return True
    rule = (finding.rule or "").lower()
    return any(rule == needle or needle in rule or rule in needle for needle in rules if rule and needle)


def _narrow(ordered: list[Finding], matched: list[Finding]) -> list[Finding]:
    """Keep `matched` when it is non-empty; otherwise do not empty the run."""
    return matched if matched else ordered


def _topic_text(objective: IntentObjective | None, request_text: str | None) -> str:
    parts: list[str] = []
    if request_text and request_text.strip():
        parts.append(request_text)
    if objective is not None:
        parts.append(objective.scope)
        parts.append(objective.objective)
        parts.extend(objective.allowed_actions)
    return " ".join(part for part in parts if part)


def select_findings(
    findings: list[Finding],
    *,
    categories: tuple[str, ...],
    objective: IntentObjective | None = None,
    research_package: dict[str, Any] | None = None,
    request_text: str | None = None,
    limit: int = _MAX_FINDINGS_PER_AGENT,
) -> list[Finding]:
    """Category → priority → request topic → research locators → top-N.

    Findings that are already fixed, validated, planned, or in progress
    are dropped first. Re-running an agent must not propose the same
    change again. Topic tokens come from the original UI request (and
    the structured objective). A hit on rule/observation is kept — that
    is the prompt doing its job. A total miss still falls back so a
    lexical miss (scope "product pages" vs `/p/123`) cannot zero out a
    grounded analysis. Top-N always applies.
    """
    findings = [finding for finding in findings if _is_actionable(finding)]
    scoped = [finding for finding in findings if _category_of(finding) in categories]
    ranked_ids = [result.finding_id for result in prioritize_findings(scoped)]
    by_id = {finding.finding_id: finding for finding in scoped}
    ordered = [by_id[fid] for fid in ranked_ids if fid in by_id]

    tokens = _scope_tokens(_topic_text(objective, request_text))
    if tokens:
        ordered = _narrow(ordered, [finding for finding in ordered if _matches_scope(finding, tokens)])

    urls, rules = _research_needles(research_package)
    if urls or rules:
        ordered = _narrow(
            ordered, [finding for finding in ordered if _matches_research(finding, urls, rules)]
        )

    if limit < 0:
        raise ValueError("limit must be >= 0")
    return ordered[:limit]


def _omitted_findings(findings: list[Finding], categories: tuple[str, ...]) -> list[dict[str, str]]:
    """Closed findings in this agent's categories, for the run result.

    The intervention list stays the actionable proposals. This sidecar
    tells the UI why a previously shown finding disappeared.
    """
    omitted: list[dict[str, str]] = []
    for finding in findings:
        if _category_of(finding) not in categories or _is_actionable(finding):
            continue
        omitted.append(
            {
                "finding_id": finding.finding_id,
                "status": _status_value(finding),
                "rule": finding.rule or "",
            }
        )
    return omitted


def _public_output(items: list[dict[str, Any]], omitted: list[dict[str, str]]) -> Any:
    if not omitted:
        return items
    return {
        "interventions": items,
        "omitted_findings": omitted[:20],
        "omitted_count": len(omitted),
    }


def _batch_findings(ordered: list[Finding]) -> list[list[Finding]]:
    """Greedily pack findings so each batch stays under the request ceiling.

    A finding whose own estimated cost already exceeds the ceiling is not
    split further — it is placed alone in its own batch instead.
    Split-on-413 in `plan_optimizations_batch` is what recovers if that
    singleton is still rejected.
    """
    batches: list[list[Finding]] = []
    current: list[Finding] = []
    for finding in ordered:
        candidate = current + [finding]
        tokens = estimate_batch_request_tokens(candidate)
        if current and (
            len(current) >= _MAX_BATCH_FINDINGS or tokens > _MAX_BATCH_REQUEST_TOKENS
        ):
            batches.append(current)
            current = [finding]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def run_category_agent(
    *,
    agent_name: str,
    categories: tuple[str, ...],
    findings: list[Finding],
    gateway: LLMGateway,
    budget: AgentBudget,
    objective: IntentObjective | None = None,
    research_package: dict[str, Any] | None = None,
    request_text: str | None = None,
) -> AgentRunOutcome:
    omitted = _omitted_findings(findings, categories)
    selected = select_findings(
        findings,
        categories=categories,
        objective=objective,
        research_package=research_package,
        request_text=request_text,
    )

    templated: list[Intervention] = []
    llm_findings: list[Finding] = []
    for finding in selected:
        if can_template_intervention(finding):
            templated.append(template_intervention(finding))
        else:
            llm_findings.append(finding)
    batches = _batch_findings(llm_findings)
    interventions: list[Intervention] = list(templated)

    def step(state: AgentState) -> AgentStepResult:
        if not batches:
            messages: list[AgentMessageRecord] = []
            if templated:
                messages.append(
                    AgentMessageRecord(
                        role="assistant",
                        content=json.dumps(
                            {
                                "source": "finding_fields",
                                "items": [item.model_dump() for item in templated],
                            }
                        ),
                        tokens=0,
                    )
                )
            return AgentStepResult(
                done=True,
                output=_public_output([item.model_dump() for item in interventions], omitted),
                messages=messages,
            )

        index = state.iteration
        if index >= len(batches):
            return AgentStepResult(
                done=True,
                output=_public_output([item.model_dump() for item in interventions], omitted),
            )

        batch_interventions, chat_result = plan_optimizations_batch(gateway, batches[index])
        interventions.extend(batch_interventions)
        messages = []
        if index == 0 and templated:
            messages.append(
                AgentMessageRecord(
                    role="assistant",
                    content=json.dumps(
                        {
                            "source": "finding_fields",
                            "items": [item.model_dump() for item in templated],
                        }
                    ),
                    tokens=0,
                )
            )
        messages.append(
            AgentMessageRecord(
                role="assistant",
                content=json.dumps([item.model_dump() for item in batch_interventions]),
                provider=chat_result.provider,
                model=chat_result.model,
                tokens=chat_result.tokens,
            )
        )
        return AgentStepResult(
            done=index + 1 >= len(batches),
            output=_public_output([item.model_dump() for item in interventions], omitted),
            tool_calls_made=0,
            tokens_used=chat_result.tokens,
            messages=messages,
        )

    runtime = AgentRuntime(agent_name=agent_name, budget=budget)
    return runtime.run(step)
