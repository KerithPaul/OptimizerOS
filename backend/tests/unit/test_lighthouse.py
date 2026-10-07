"""Lighthouse lab signals carry provenance (step 3.C.3 verify)."""

from app.intelligence.website.lighthouse import (
    PROVENANCE,
    PROVENANCE_LABEL,
    label_lighthouse_result,
    lab_signal_to_record,
    run_lighthouse,
)


def test_stored_record_carries_lab_signal_not_ranking_flag() -> None:
    raw = {
        "categories": {
            "performance": {"score": 0.5},
            "accessibility": {"score": 0.8},
            "best-practices": {"score": 0.7},
            "seo": {"score": 0.92},
        },
        "audits": {
            "meta-description": {
                "id": "meta-description",
                "title": "Document has a meta description",
                "score": 1,
                "displayValue": None,
            }
        },
    }
    signal = label_lighthouse_result("https://example.com/", raw)
    assert signal.provenance == PROVENANCE
    assert signal.provenance == "lab_signal_not_ranking"
    assert signal.provenance_label == PROVENANCE_LABEL
    assert "lab signal, not ranking" == signal.provenance_label
    assert signal.state == "observed"
    assert signal.categories["seo"] == 0.92


def test_persisted_record_omits_raw_and_keeps_provenance() -> None:
    raw = {
        "categories": {"seo": {"score": 0.5}},
        "audits": {},
        "lighthouseVersion": "12.0.0",
    }
    signal = label_lighthouse_result("https://example.com/", raw)
    record = lab_signal_to_record(signal)
    assert record["provenance"] == "lab_signal_not_ranking"
    assert record["provenance_label"] == "lab signal, not ranking"
    assert "raw" not in record


def test_unavailable_and_blocked_states_still_carry_the_flag() -> None:
    missing = run_lighthouse(
        "https://example.com/",
        lookup=lambda host: ["93.184.216.34"],
        runner=_raise_missing,
    )
    assert missing.state == "unavailable"
    assert missing.provenance == "lab_signal_not_ranking"
    assert missing.provenance_label == "lab signal, not ranking"

    blocked = run_lighthouse("http://127.0.0.1/")
    assert blocked.state == "blocked"
    assert blocked.provenance == "lab_signal_not_ranking"


def _raise_missing(url: str) -> dict:
    raise FileNotFoundError("Lighthouse CLI is not installed; lab signals are unavailable.")
