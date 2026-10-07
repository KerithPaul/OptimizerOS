"""CPU embedding service (step 2.D.2).

Loads `BAAI/bge-small-en-v1.5` on first use and unloads it after
`MODEL_IDLE_UNLOAD_SECONDS` so peak RAM stays in the 0.5–1.5 GB target.
Tests inject `embed_fn` and never load the real model.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable, Sequence

from app.core.config import Settings, get_settings

logger = logging.getLogger("architectos.services.embeddings")

EmbedFn = Callable[[Sequence[str]], list[list[float]]]

_DEFAULT_DIM = 384
_BATCH_SIZE = 32
_service: EmbeddingService | None = None
_service_lock = threading.Lock()


class EmbeddingError(Exception):
    """Embedding failed. Must not be converted into a successful index."""


class EmbeddingService:
    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-small-en-v1.5",
        dim: int = _DEFAULT_DIM,
        idle_seconds: int = 600,
        embed_fn: EmbedFn | None = None,
        loader: Callable[[], object] | None = None,
    ) -> None:
        self.model_name = model_name
        self.dim = dim
        self.idle_seconds = idle_seconds
        self._embed_fn = embed_fn
        self._loader = loader or _load_sentence_transformer
        self._model: object | None = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._embed_fn is not None:
            self._schedule_unload()
            return self._embed_fn(texts)
        try:
            model = self._ensure_loaded()
            encode = getattr(model, "encode")
            vectors: list[list[float]] = []
            for start in range(0, len(texts), _BATCH_SIZE):
                batch = list(texts[start : start + _BATCH_SIZE])
                encoded = encode(
                    batch,
                    batch_size=_BATCH_SIZE,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                    normalize_embeddings=True,
                )
                for row in encoded:
                    vectors.append(row.tolist() if hasattr(row, "tolist") else list(row))
            return vectors
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"embedding failed: {exc}") from exc
        finally:
            self._schedule_unload()

    def unload(self) -> None:
        with self._lock:
            self._cancel_timer()
            if self._model is None:
                return
            self._model = None
            logger.info("embedding model unloaded")

    def _ensure_loaded(self) -> object:
        with self._lock:
            if self._model is None:
                logger.info("loading embedding model %s", self.model_name)
                self._model = self._loader()
            return self._model

    def _schedule_unload(self) -> None:
        if self._embed_fn is not None and self._loader is _load_sentence_transformer:
            return
        with self._lock:
            self._cancel_timer()
            if self.idle_seconds <= 0:
                self._model = None
                return
            timer = threading.Timer(self.idle_seconds, self.unload)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


def hash_embed(texts: Sequence[str], dim: int = _DEFAULT_DIM) -> list[list[float]]:
    """Deterministic 384-d stand-in used by tests. Not the production model."""
    vectors: list[list[float]] = []
    for text in texts:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = (digest * ((dim // len(digest)) + 1))[:dim]
        vec = [(byte / 127.5) - 1.0 for byte in raw]
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        vectors.append([v / norm for v in vec])
    return vectors


def get_embedding_service(settings: Settings | None = None) -> EmbeddingService:
    global _service
    with _service_lock:
        if _service is None:
            settings = settings or get_settings()
            _service = EmbeddingService(
                model_name=getattr(settings, "embedding_model", "BAAI/bge-small-en-v1.5"),
                dim=getattr(settings, "embedding_dim", _DEFAULT_DIM),
                idle_seconds=getattr(settings, "model_idle_unload_seconds", 600),
            )
        return _service


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    return get_embedding_service().embed(texts)


def reset_embedding_service() -> None:
    """Test helper."""
    global _service
    with _service_lock:
        if _service is not None:
            _service.unload()
        _service = None


def _load_sentence_transformer() -> object:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EmbeddingError(
            "sentence-transformers is not installed; cannot load BAAI/bge-small-en-v1.5"
        ) from exc
    settings = get_settings()
    name = getattr(settings, "embedding_model", "BAAI/bge-small-en-v1.5")
    return SentenceTransformer(name, device="cpu")
