"""Rule catalog schema + loader (step 4.B.1 verify)."""

from pathlib import Path

import pytest
import yaml

from app.knowledge.rulefile import KNOWLEDGE_ROOT, RuleFileError, load_all_rule_files, load_rule_file

_VALID_RULE = {
    "rule_id": "TEST-DUMMY-001",
    "category": "technical_seo",
    "severity": "low",
    "confidence": "medium",
    "source": {
        "name": "Google Search Central — Title links",
        "source_url": "https://developers.google.com/search/docs/appearance/title-link",
        "authority": "official_vendor_docs",
    },
    "retrieved_at": "2026-09-09",
    "content": "A page should have a descriptive title.",
    "conditions": {
        "applies_to": "page",
        "evaluation": "mechanical",
        "check": "title_missing",
        "field": "title",
        "operator": "is_null",
    },
    "recommendation": "Add a <title> element.",
}


def _write(tmp_path: Path, dir_name: str, filename: str, data: dict) -> Path:
    target_dir = tmp_path / dir_name
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_the_real_knowledge_base_validates() -> None:
    loaded = load_all_rule_files(KNOWLEDGE_ROOT)
    assert len(loaded) > 0
    for item in loaded:
        assert item.rule.source.source_url.startswith("https://")
        assert item.rule.content.strip()
        assert item.rule.recommendation.strip()


def test_spec_example_rule_id_exists() -> None:
    loaded = load_all_rule_files(KNOWLEDGE_ROOT)
    ids = {item.rule.rule_id for item in loaded}
    assert "SEO-CANONICAL-001" in ids


def test_a_well_formed_rule_loads(tmp_path: Path) -> None:
    path = _write(tmp_path, "seo", "TEST-DUMMY-001.yaml", _VALID_RULE)
    loaded = load_rule_file(path)
    assert loaded.rule.rule_id == "TEST-DUMMY-001"
    assert loaded.rule.category.value == "technical_seo"


def test_a_rule_with_no_source_url_is_rejected(tmp_path: Path) -> None:
    data = {**_VALID_RULE, "source": {**_VALID_RULE["source"], "source_url": ""}}
    path = _write(tmp_path, "seo", "TEST-DUMMY-001.yaml", data)
    with pytest.raises(RuleFileError):
        load_rule_file(path)


def test_a_rule_with_a_non_http_source_url_is_rejected(tmp_path: Path) -> None:
    data = {**_VALID_RULE, "source": {**_VALID_RULE["source"], "source_url": "not-a-url"}}
    path = _write(tmp_path, "seo", "TEST-DUMMY-001.yaml", data)
    with pytest.raises(RuleFileError):
        load_rule_file(path)


def test_a_rule_id_that_does_not_match_the_spec_format_is_rejected(tmp_path: Path) -> None:
    data = {**_VALID_RULE, "rule_id": "lowercase-not-allowed-001"}
    path = _write(tmp_path, "seo", "lowercase-not-allowed-001.yaml", data)
    with pytest.raises(RuleFileError):
        load_rule_file(path)


def test_filename_must_match_rule_id(tmp_path: Path) -> None:
    path = _write(tmp_path, "seo", "WRONG-FILENAME-001.yaml", _VALID_RULE)
    with pytest.raises(RuleFileError, match="does not match rule_id"):
        load_rule_file(path)


def test_category_must_be_valid_for_its_directory(tmp_path: Path) -> None:
    data = {**_VALID_RULE, "category": "aeo"}
    path = _write(tmp_path, "seo", "TEST-DUMMY-001.yaml", data)
    with pytest.raises(RuleFileError, match="is not valid under"):
        load_rule_file(path)


def test_a_mechanical_condition_with_no_field_and_no_note_is_rejected(tmp_path: Path) -> None:
    data = {
        **_VALID_RULE,
        "conditions": {"applies_to": "page", "evaluation": "mechanical", "check": "x"},
    }
    path = _write(tmp_path, "seo", "TEST-DUMMY-001.yaml", data)
    with pytest.raises(RuleFileError):
        load_rule_file(path)


def test_an_llm_interpreted_condition_needs_a_note(tmp_path: Path) -> None:
    data = {
        **_VALID_RULE,
        "conditions": {"applies_to": "page", "evaluation": "llm_interpreted", "check": "x"},
    }
    path = _write(tmp_path, "seo", "TEST-DUMMY-001.yaml", data)
    with pytest.raises(RuleFileError):
        load_rule_file(path)


def test_malformed_yaml_is_rejected(tmp_path: Path) -> None:
    target_dir = tmp_path / "seo"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "TEST-DUMMY-001.yaml"
    path.write_text("rule_id: [unterminated", encoding="utf-8")
    with pytest.raises(RuleFileError):
        load_rule_file(path)


def test_duplicate_rule_ids_across_files_are_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "seo", "TEST-DUMMY-001.yaml", _VALID_RULE)
    dup = {
        **_VALID_RULE,
        "category": "aeo",
        "conditions": {**_VALID_RULE["conditions"], "applies_to": "page"},
    }
    _write(tmp_path, "aeo", "TEST-DUMMY-001.yaml", dup)
    with pytest.raises(RuleFileError, match="duplicate rule_id"):
        load_all_rule_files(tmp_path)
