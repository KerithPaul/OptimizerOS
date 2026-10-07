"""Layer 7 — Rollback Planner (step 8.6, `[SPEC AGENTS.md §38]`).

Determines what changed, how it can be reverted, whether rollback is
safe, and which historical state should be restored. Unlike Layers 4-6,
this is fully deterministic: rollback safety is a function of whether the
target's current on-disk state still matches what its Change
Transaction/Item recorded as `hash_after`, not something that needs LLM
interpretation `[SPEC "Deterministic code before LLM reasoning"]`.

Confidence drops when the target cannot be located (renamed, moved,
merged, or split) or has drifted from its recorded post-change hash
(heavily refactored since). Below `settings.rollback_confidence_threshold`,
`requires_confirmation` is the mandated STOP.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings, get_settings

_HIGH_CONFIDENCE = 0.98
_FILE_MISSING_CONFIDENCE = 0.05
_SYMBOL_NOT_LOCATED_CONFIDENCE = 0.20
_HASH_DRIFTED_CONFIDENCE = 0.55
_UNKNOWN_CONFIDENCE = 0.50
_OTHER_TRANSACTION_CAP = 0.60


@dataclass(frozen=True)
class TargetState:
    """What the caller observed about the target's current on-disk state.

    Built by `app.changes.rollback` (which reads the workspace and the
    persisted Change Transaction/Item); this module never touches disk or
    the database itself, so its confidence rules stay unit-testable.
    """

    file_exists: bool
    symbol_located: bool
    current_hash_matches_after: bool | None
    other_transactions_touched_resource: bool = False


@dataclass(frozen=True)
class RollbackPlan:
    method: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    requires_confirmation: bool = False


def plan_rollback(
    *,
    method: str,
    state: TargetState,
    settings: Settings | None = None,
) -> RollbackPlan:
    settings = settings or get_settings()
    reasons: list[str] = []

    if not state.file_exists:
        reasons.append("target file no longer exists in the workspace")
        confidence = _FILE_MISSING_CONFIDENCE
    elif not state.symbol_located:
        reasons.append(
            "target symbol/block could not be located in the current content "
            "(renamed, moved, merged, or split)"
        )
        confidence = _SYMBOL_NOT_LOCATED_CONFIDENCE
    elif state.current_hash_matches_after is False:
        reasons.append(
            "current content no longer matches the recorded post-change hash "
            "(heavily refactored since this change was applied)"
        )
        confidence = _HASH_DRIFTED_CONFIDENCE
    elif state.current_hash_matches_after is None:
        reasons.append("current content could not be compared to the recorded hash")
        confidence = _UNKNOWN_CONFIDENCE
    else:
        confidence = _HIGH_CONFIDENCE

    if state.other_transactions_touched_resource:
        reasons.append("another Change Transaction has since modified the same resource")
        confidence = min(confidence, _OTHER_TRANSACTION_CAP)

    if not reasons:
        reasons.append("target located and content unchanged since this change was applied")

    return RollbackPlan(
        method=method,
        confidence=confidence,
        reasons=reasons,
        requires_confirmation=confidence < settings.rollback_confidence_threshold,
    )
