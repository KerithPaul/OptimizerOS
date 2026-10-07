"""Shared fixtures. Integration tests never load the real embedding model."""

import pytest

from app.services.embeddings import hash_embed


@pytest.fixture(autouse=True)
def _hash_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.embeddings.embed_texts", hash_embed)
    monkeypatch.setattr("app.services.vectors.embed_texts", hash_embed)


@pytest.fixture(autouse=True)
def _block_live_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args, **_kwargs):
        raise RuntimeError("SMTP is blocked in tests")

    monkeypatch.setattr("smtplib.SMTP", _blocked)
    monkeypatch.setattr("smtplib.SMTP_SSL", _blocked)
