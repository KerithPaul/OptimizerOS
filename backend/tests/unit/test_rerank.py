"""Reranker RAM fallback (step 5.A.2 verify)."""

import time

import pytest

from app.retrieval.rerank import (
    RERANK_MODE_APPLIED,
    RERANK_MODE_FALLBACK,
    RerankError,
    Reranker,
)


def test_disabled_reranker_returns_fallback_and_keeps_retrieval_order() -> None:
    loaded = {"n": 0}

    def loader() -> object:
        loaded["n"] += 1
        raise AssertionError("disabled reranker must not load the model")

    reranker = Reranker(enabled=False, loader=loader)
    result = reranker.rerank("query", ["low", "high"], [0.1, 0.9])

    assert result.mode == RERANK_MODE_FALLBACK
    assert result.reason == "disabled"
    assert result.indices == [1, 0]
    assert result.scores == [0.9, 0.1]
    assert loaded["n"] == 0


def test_ram_pressure_falls_back_without_loading() -> None:
    loaded = {"n": 0}

    def loader() -> object:
        loaded["n"] += 1
        raise AssertionError("must not load under RAM pressure")

    reranker = Reranker(
        ram_pressure_threshold_mb=2048,
        ram_fn=lambda: 100.0,
        loader=loader,
    )
    result = reranker.rerank("query", ["a", "b"], [0.2, 0.8])

    assert result.mode == RERANK_MODE_FALLBACK
    assert result.reason == "ram_pressure"
    assert result.indices == [1, 0]
    assert loaded["n"] == 0


def test_injected_predict_fn_applies_rerank_scores() -> None:
    def predict(_query: str, texts: list[str]) -> list[float]:
        return [0.1 if text == "first" else 0.9 for text in texts]

    reranker = Reranker(predict_fn=predict)
    result = reranker.rerank("query", ["first", "second"], [0.99, 0.01])

    assert result.mode == RERANK_MODE_APPLIED
    assert result.reason is None
    assert result.indices == [1, 0]
    assert result.scores == [0.9, 0.1]


def test_load_failure_falls_back() -> None:
    def loader() -> object:
        raise RuntimeError("cannot allocate")

    reranker = Reranker(loader=loader, ram_fn=lambda: 8192.0)
    result = reranker.rerank("query", ["a", "b"], [0.4, 0.6])

    assert result.mode == RERANK_MODE_FALLBACK
    assert result.reason == "load_failed"
    assert result.indices == [1, 0]


def test_model_unloads_after_idle_timeout() -> None:
    class _FakeModel:
        def predict(self, pairs, **kwargs):
            return [float(i) for i in range(len(pairs))]

    loaded = {"n": 0}

    def loader() -> _FakeModel:
        loaded["n"] += 1
        return _FakeModel()

    reranker = Reranker(idle_seconds=0.05, loader=loader, ram_fn=lambda: 8192.0)
    result = reranker.rerank("query", ["a", "b"], [0.1, 0.2])
    assert result.mode == RERANK_MODE_APPLIED
    assert reranker.is_loaded
    assert loaded["n"] == 1

    time.sleep(0.2)
    assert reranker.is_loaded is False


def test_mismatched_inputs_are_an_error() -> None:
    reranker = Reranker(enabled=False)
    with pytest.raises(RerankError):
        reranker.rerank("query", ["a"], [0.1, 0.2])
