"""Embedding service load/unload (step 2.D.2 verify)."""

import time

from app.services.embeddings import EmbeddingService, hash_embed


class _FakeModel:
    def encode(self, texts, **kwargs):
        return hash_embed(list(texts))


def test_model_unloads_after_idle_timeout() -> None:
    loaded = {"n": 0}

    def loader() -> _FakeModel:
        loaded["n"] += 1
        return _FakeModel()

    service = EmbeddingService(idle_seconds=0.05, loader=loader)
    vectors = service.embed(["hello"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 384
    assert service.is_loaded
    assert loaded["n"] == 1

    time.sleep(0.2)
    assert service.is_loaded is False


def test_hash_embed_is_deterministic() -> None:
    assert hash_embed(["abc"]) == hash_embed(["abc"])
    assert hash_embed(["abc"]) != hash_embed(["abd"])
