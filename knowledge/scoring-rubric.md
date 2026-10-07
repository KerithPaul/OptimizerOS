# Scoring rubric (`[PROPOSED]`)

This document records the weights used by `backend/app/retrieval/scoring.py`
and `backend/app/retrieval/prioritize.py`. The specs (`AGENTS.md`,
`about-ArchitectOS.md`) require that scores be decomposable
(`Score -> Signal -> Rule -> Evidence -> Affected resource`, Q4). They do
not specify numbers. Everything below is a proposed rubric, not a spec
requirement, and can be re-tuned without touching the decomposition
contract.

## Three scores

| Score | Rule categories rolled in |
|---|---|
| Technical SEO Health | `technical_seo` |
| Content / AEO Readiness | `content_seo`, `aeo` |
| AI Search / GEO Readiness | `geo` |

`agent_accessibility` is not rolled into any of these three scores. A page with no WebMCP surface produces no finding in that category.

Each score starts at 100 and is reduced by a deduction for every `Signal`
(one signal per rule that fired), floored at 0. A signal's deduction is a
per-severity base amount, scaled by how many findings share that rule:

| Severity | Deduction per finding (base, count = 1) |
|---|---|
| low | 2 |
| medium | 5 |
| high | 10 |
| critical | 18 |

`signal_deduction = round(base_deduction x (1 + log1p(finding_count - 1)))`.
At `finding_count = 1` the multiplier is exactly 1 (log1p(0) = 0), so a
single finding deducts exactly the base amount above. Each *additional*
finding of the same rule adds a shrinking increment on a log scale instead
of the same flat amount again — this mirrors the breadth normalisation
already used for prioritisation (`reach_for_count` in `prioritize.py`).
Without this, a single low-severity rule repeated across dozens of pages
(a template-level issue, most commonly) deducted linearly and floored the
score at 0 by itself, indistinguishable from a site with many severe,
independent problems. Deduction is capped only implicitly by the log
curve — a rule would need an unrealistic finding count to zero the score
alone at low/medium severity; high/critical severity fired broadly can
still legitimately drive a score to 0.

Findings are grouped by rule to form a `Signal`. Each signal carries its
rule id, the worst severity observed, the total deduction, the affected
resources, and the evidence rows of every finding that produced it — the
UI drill-down path in step 5.C.1's verify criterion walks this structure
directly, with no separate lookup needed.

## Prioritisation

`priority = impact x confidence x reach x actionability / risk`, all
factors normalised to `[0, 1]` except `risk`:

- **impact** — from rule severity: low 0.25, medium 0.5, high 0.75,
  critical 1.0.
- **confidence** — from rule confidence: low 0.34, medium 0.67, high 1.0.
- **reach** — `log1p(affected_page_count) / log1p(50)`, capped at 1.0.
  `affected_page_count` is how many findings in the run share the same
  rule (the breadth of that issue class).
- **actionability** — `recommend_only` 0.6, `code_change` 1.0 (a finding
  ArchitectOS can act on is weighted higher because it is more likely to
  ship).
- **risk** (denominator) — `recommend_only` 0.15, `code_change` 0.6: no
  change has been applied for a recommend-only finding, so it carries
  almost no regression risk; a code-change candidate does.

Search impressions (GSC) are a listed input but are not available before
Phase 11 and are omitted here.

This is an ArchitectOS prioritisation model for ordering triage, not a
ranking predictor of search outcomes.
