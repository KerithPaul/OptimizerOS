"""Versioned rule catalog loader + validator (step 4.B.1).

`knowledge/seo/`, `knowledge/aeo/`, `knowledge/geo/`, and `knowledge/agent/`
hold one YAML file per rule. This module is the record schema those files must validate against
`[SPEC — step 4.B.1 verify]`: a rule row cannot be inserted without a source
and an authority (step 4.A.1), and unattributed content is rejected outright
(step 4.A.2, `app.knowledge.authority.validate_attribution`).

The ingest job (step 4.B.2, `app.knowledge.ingest`) is the only intended
caller of `load_all_rule_files`. Loading is fail-closed: if any file in
`knowledge/` is malformed, `load_all_rule_files` raises one aggregated
`RuleFileError` naming every bad file rather than partially ingesting a
knowledge base with silent gaps.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.knowledge.authority import AuthorityLevel, UnattributedSourceError, validate_attribution
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[3] / "knowledge"

# `SEO-CANONICAL-001` is the [SPEC] example format: an all-caps, hyphenated
# rule family followed by a zero-padded 3-digit sequence number.
_RULE_ID_RE = re.compile(r"^[A-Z]{2,6}(?:-[A-Z0-9]+)+-\d{3}$")

_DIR_CATEGORIES: dict[str, tuple[RuleCategory, ...]] = {
    "seo": (RuleCategory.TECHNICAL_SEO, RuleCategory.CONTENT_SEO),
    "aeo": (RuleCategory.AEO,),
    "geo": (RuleCategory.GEO,),
    "agent": (RuleCategory.AGENT_ACCESSIBILITY,),
}


class RuleFileError(Exception):
    """One or more rule files under `knowledge/` are malformed or unattributed.

    Carries every failure found, not just the first, so a single ingest
    attempt reports the full list of files to fix.
    """

    def __init__(self, problems: list[tuple[Path, str]]) -> None:
        self.problems = problems
        lines = "\n".join(f"  - {path}: {reason}" for path, reason in problems)
        super().__init__(f"{len(problems)} rule file(s) failed validation:\n{lines}")


class RuleSource(BaseModel):
    """The `source` / `source_url` / `authority` triple, before it is
    resolved against the `optimization_sources` registry at ingest time."""

    model_config = ConfigDict(extra="forbid")

    name: str
    source_url: str
    authority: AuthorityLevel

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source.name must not be blank")
        return value


class RuleConditions(BaseModel):
    """A form the deterministic evaluator (step 4.C.1) can execute.

    `field` + `operator` (+ `value`) cover a check against one field of the
    evaluation unit named by `applies_to` (dotted paths into the Common
    Website Model for `page`, e.g. `canonical`, `headings`, `images`; into
    the recorded crawl-run stats for `crawl_run`, e.g. `robots.state`,
    `sitemap.records`). A check that compares across pages or records
    (duplicates, orphan pages, hreflang reciprocity) cannot be expressed as
    one field/operator pair — those set `note` instead, describing the
    comparison in prose the evaluator's dispatch-by-`check` implementation
    follows. `evaluation: llm_interpreted` marks a rule whose condition
    genuinely needs interpretation `[SPEC step 4.B.1]`; it still carries a
    `note` describing what the Phase 6 LLM evaluation must decide.
    """

    model_config = ConfigDict(extra="forbid")

    applies_to: Literal["page", "crawl_run", "website"]
    evaluation: Literal["mechanical", "llm_interpreted"]
    check: str
    field: str | None = None
    operator: (
        Literal[
            "is_null",
            "not_null",
            "equals",
            "not_equals",
            "gt",
            "gte",
            "lt",
            "lte",
            "contains",
            "not_contains",
            "length_gt",
            "length_lt",
            "count_gt",
            "count_eq",
            "count_lt",
            "regex_match",
            "in",
            "not_in",
        ]
        | None
    ) = None
    value: Any = None
    note: str | None = None

    @field_validator("check")
    @classmethod
    def _check_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("conditions.check must not be blank")
        return value

    @model_validator(mode="after")
    def _has_enough_to_execute(self) -> RuleConditions:
        if self.field is not None and self.operator is None:
            raise ValueError("conditions.field requires conditions.operator")
        if self.field is None and self.evaluation == "mechanical" and not self.note:
            raise ValueError(
                "a mechanical condition needs either field+operator or a note "
                "describing the cross-record comparison"
            )
        if self.evaluation == "llm_interpreted" and not self.note:
            raise ValueError("an llm_interpreted condition needs a note describing what is judged")
        return self


class RuleFile(BaseModel):
    """One `knowledge/**/*.yaml` rule, validated against the step 4.A.1
    record shape before the ingest job resolves it into `OptimizationRule`."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    category: RuleCategory
    severity: RuleSeverity
    confidence: RuleConfidence
    source: RuleSource
    published_at: date | None = None
    retrieved_at: date
    content: str
    conditions: RuleConditions
    recommendation: str

    @field_validator("rule_id")
    @classmethod
    def _rule_id_format(cls, value: str) -> str:
        if not _RULE_ID_RE.match(value):
            raise ValueError(
                f"rule_id {value!r} does not match the SEO-CANONICAL-001 format "
                "(FAMILY-WORDS-NNN, all caps)"
            )
        return value

    @field_validator("content", "recommendation")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class LoadedRule(BaseModel):
    """A validated rule plus the file it came from, for ingest-time errors
    to point back at a real path."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    rule: RuleFile


def iter_rule_files(root: Path = KNOWLEDGE_ROOT) -> list[Path]:
    paths: list[Path] = []
    for dir_name in _DIR_CATEGORIES:
        paths.extend(sorted((root / dir_name).glob("*.yaml")))
    return paths


def load_rule_file(path: Path) -> LoadedRule:
    """Parse and validate one rule file. Raises `RuleFileError` (one problem)
    on any failure: bad YAML, schema mismatch, wrong category for its
    directory, filename/rule_id mismatch, or unattributed source.
    """

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuleFileError([(path, f"invalid YAML: {exc}")]) from exc

    if not isinstance(raw, dict):
        raise RuleFileError([(path, "top-level YAML content must be a mapping")])

    try:
        rule = RuleFile.model_validate(raw)
    except ValidationError as exc:
        raise RuleFileError([(path, str(exc))]) from exc

    if rule.rule_id != path.stem:
        raise RuleFileError(
            [(path, f"filename {path.stem!r} does not match rule_id {rule.rule_id!r}")]
        )

    allowed = _DIR_CATEGORIES.get(path.parent.name, ())
    if rule.category not in allowed:
        raise RuleFileError(
            [
                (
                    path,
                    f"category {rule.category.value!r} is not valid under "
                    f"knowledge/{path.parent.name}/ (expected one of "
                    f"{[c.value for c in allowed]})",
                )
            ]
        )

    try:
        validate_attribution(rule.source.name, rule.source.source_url)
    except UnattributedSourceError as exc:
        raise RuleFileError([(path, str(exc))]) from exc

    return LoadedRule(path=path, rule=rule)


def load_all_rule_files(root: Path = KNOWLEDGE_ROOT) -> list[LoadedRule]:
    """Load and validate every rule file under `root`. All-or-nothing: a
    single malformed file fails the whole batch, with every problem found
    named in the raised `RuleFileError`.
    """

    loaded: list[LoadedRule] = []
    problems: list[tuple[Path, str]] = []
    seen_rule_ids: dict[str, Path] = {}

    for path in iter_rule_files(root):
        try:
            item = load_rule_file(path)
        except RuleFileError as exc:
            problems.extend(exc.problems)
            continue
        duplicate = seen_rule_ids.get(item.rule.rule_id)
        if duplicate is not None:
            problems.append(
                (path, f"duplicate rule_id {item.rule.rule_id!r}, already defined in {duplicate}")
            )
            continue
        seen_rule_ids[item.rule.rule_id] = path
        loaded.append(item)

    if problems:
        raise RuleFileError(problems)

    return loaded
