"""Layer 7 -- Rollback Planner confidence rules (step 8.6 verify, `[SPEC AGENTS.md §38]`)."""

from app.core.config import Settings
from app.planners.rollback import TargetState, plan_rollback


def _settings(threshold: float = 0.75) -> Settings:
    return Settings(  # type: ignore[call-arg]
        APP_SECRET_KEY="unit-test-secret",
        CREDENTIAL_ENCRYPTION_KEY="cU5b7d2m9zQwErTyUiOpAsDfGhJkLzXcVbNmQwErTy8=",
        ROLLBACK_CONFIDENCE_THRESHOLD=threshold,
    )


def test_unchanged_target_is_high_confidence_and_needs_no_confirmation() -> None:
    state = TargetState(file_exists=True, symbol_located=True, current_hash_matches_after=True)
    plan = plan_rollback(method="snapshot_restore", state=state, settings=_settings())

    assert plan.confidence >= 0.9
    assert plan.requires_confirmation is False


def test_renamed_or_moved_symbol_lowers_confidence_and_requires_confirmation() -> None:
    state = TargetState(file_exists=True, symbol_located=False, current_hash_matches_after=None)
    plan = plan_rollback(method="ast_patch_reconstruction", state=state, settings=_settings())

    assert plan.confidence < 0.75
    assert plan.requires_confirmation is True
    assert any("renamed" in reason or "moved" in reason for reason in plan.reasons)


def test_missing_file_is_lowest_confidence() -> None:
    state = TargetState(file_exists=False, symbol_located=False, current_hash_matches_after=None)
    plan = plan_rollback(method="snapshot_restore", state=state, settings=_settings())

    assert plan.confidence < 0.2
    assert plan.requires_confirmation is True


def test_content_drift_since_the_change_is_treated_as_heavily_refactored() -> None:
    state = TargetState(file_exists=True, symbol_located=True, current_hash_matches_after=False)
    plan = plan_rollback(method="ast_patch_reconstruction", state=state, settings=_settings())

    assert plan.requires_confirmation is True
    assert any("refactored" in reason for reason in plan.reasons)


def test_confidence_threshold_is_configurable() -> None:
    state = TargetState(file_exists=True, symbol_located=True, current_hash_matches_after=False)

    lenient = plan_rollback(method="snapshot_restore", state=state, settings=_settings(threshold=0.1))
    strict = plan_rollback(method="snapshot_restore", state=state, settings=_settings(threshold=0.99))

    assert lenient.requires_confirmation is False
    assert strict.requires_confirmation is True


def test_another_transaction_touching_the_same_resource_caps_confidence() -> None:
    state = TargetState(
        file_exists=True,
        symbol_located=True,
        current_hash_matches_after=True,
        other_transactions_touched_resource=True,
    )
    plan = plan_rollback(method="snapshot_restore", state=state, settings=_settings())

    assert plan.confidence <= 0.6
