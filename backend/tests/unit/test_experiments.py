"""Experiment engine (step 11.4 verify): no causal claims."""

from app.services.experiments import infer_experiment_type
from app.services.measurement import (
    CAUSATION_NOT_CLAIMED,
    CAUSATION_NOTE,
    compare_metrics,
    unavailable_metric,
)
from app.models.search import ExperimentType


def _snapshot(impressions: int, ctr: float) -> dict:
    return {
        "causation": CAUSATION_NOT_CLAIMED,
        "causation_note": CAUSATION_NOTE,
        "metrics": {
            "impressions": {"status": "measured", "value": impressions, "source": "google_search_console"},
            "ctr": {"status": "measured", "value": ctr, "source": "google_search_console"},
            "clicks": {"status": "measured", "value": int(impressions * ctr), "source": "google_search_console"},
            "position": {"status": "measured", "value": 10.0, "source": "google_search_console"},
            "crawlability": unavailable_metric("no crawl run"),
            "technical_errors": unavailable_metric("no crawl run"),
            "answer_retrieval_benchmark": unavailable_metric("not measured"),
            "entity_recognition_benchmark": unavailable_metric("not measured"),
            "citation_retrieval_benchmark": unavailable_metric("not measured"),
            "structured_data_validity": unavailable_metric("no structured-data blocks"),
            "page_performance": unavailable_metric("no Lighthouse lab signal"),
        },
    }


def test_infer_experiment_type_from_rule_id() -> None:
    assert infer_experiment_type("SEO-TITLE-001") is ExperimentType.TITLE
    assert infer_experiment_type("AEO-FAQ-SCHEMA-001") is ExperimentType.FAQ
    assert infer_experiment_type("SEO-STRUCTUREDDATA-INVALID-001") is ExperimentType.SCHEMA
    assert infer_experiment_type("SEO-ORPHAN-PAGE-001") is ExperimentType.INTERNAL_LINKS
    assert infer_experiment_type("SEO-CONTENT-THIN-001") is ExperimentType.CONTENT_RESTRUCTURE
    assert infer_experiment_type("SEO-CANONICAL-001") is None


def test_compare_metrics_records_observed_delta_without_causation() -> None:
    result = compare_metrics(_snapshot(1000, 0.008), _snapshot(1200, 0.01))
    assert result["causation"] == "not_claimed"
    assert result["causation_note"] == CAUSATION_NOTE
    assert "correlation" in result["causation_note"].lower()
    assert "not evidence" in result["causation_note"].lower()
    assert result["observed_delta"]["impressions"]["delta"] == 200
    assert result["observed_delta"]["ctr"]["status"] == "observed"
    assert result["observed_delta"]["answer_retrieval_benchmark"]["status"] == "unavailable"


def test_unavailable_metrics_are_not_invented() -> None:
    empty = {
        "metrics": {
            "impressions": unavailable_metric("no Search Console credentials"),
            "ctr": unavailable_metric("no Search Console credentials"),
            "clicks": unavailable_metric("no Search Console credentials"),
            "position": unavailable_metric("no Search Console credentials"),
            "crawlability": unavailable_metric("no website attached"),
            "technical_errors": unavailable_metric("no website attached"),
            "answer_retrieval_benchmark": unavailable_metric("not measured"),
            "entity_recognition_benchmark": unavailable_metric("not measured"),
            "citation_retrieval_benchmark": unavailable_metric("not measured"),
            "structured_data_validity": unavailable_metric("no structured-data blocks"),
            "page_performance": unavailable_metric("no Lighthouse lab signal"),
        }
    }
    result = compare_metrics(empty, empty)
    for key, item in result["observed_delta"].items():
        assert item["status"] == "unavailable", key
        assert item.get("delta") is None
