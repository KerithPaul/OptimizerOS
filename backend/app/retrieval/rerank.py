"""Cross-encoder reranker with RAM-pressure fallback (step 5.A.2).

Loads `BAAI/bge-reranker-base` on CPU on first use. Under RAM pressure,
on load failure, or when disabled, falls back to retrieval scores and
records `rerank: fallback`. A silently degraded rerank is a correctness bug.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.core.config import Settings, get_settings

logger = logging.getLogger("architectos.retrieval.rerank")

RERANK_MODE_APPLIED = "applied"
RERANK_MODE_FALLBACK = "fallback"

RERANK_TEXT_CHARS = 2000
_BATCH_SIZE = 16

PredictFn = Callable[[str, Sequence[str]], list[float]]
RamFn = Callable[[], float | None]

_service: Reranker | None = None
_service_lock = threading.Lock()


class RerankError(Exception):
    """Reranker failed in a way that cannot be recorded as fallback."""


@dataclass(frozen=True)
class RerankResult:
    mode: str
    indices: list[int]
    scores: list[float]
    reason: str | None = None


class Reranker:
    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-reranker-base",
        idle_seconds: int = 600,
        ram_pressure_threshold_mb: int = 2048,
        enabled: bool = True,
        predict_fn: PredictFn | None = None,
        loader: Callable[[], object] | None = None,
        ram_fn: RamFn | None = None,
    ) -> None:
        self.model_name = model_name
        self.idle_seconds = idle_seconds
        self.ram_pressure_threshold_mb = ram_pressure_threshold_mb
        self.enabled = enabled
        self._predict_fn = predict_fn
        self._loader = loader or _load_cross_encoder
        self._ram_fn = ram_fn or available_ram_mb
        self._model: object | None = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def rerank(self, query: str, texts: Sequence[str], scores: Sequence[float]) -> RerankResult:
        if len(texts) != len(scores):
            raise RerankError("texts and scores must be the same length")
        if not texts:
            return RerankResult(mode=RERANK_MODE_APPLIED, indices=[], scores=[])

        fallback = self._fallback_if_needed()
        if fallback is not None:
            return _order_by_scores(list(scores), mode=RERANK_MODE_FALLBACK, reason=fallback)

        try:
            ranked_scores = self._score(query, texts)
        except MemoryError:
            logger.warning("reranker OOM; falling back to retrieval scores")
            self.unload()
            return _order_by_scores(list(scores), mode=RERANK_MODE_FALLBACK, reason="oom")
        except Exception as exc:
            logger.warning("reranker failed; falling back to retrieval scores: %s", exc)
            self.unload()
            return _order_by_scores(list(scores), mode=RERANK_MODE_FALLBACK, reason="load_failed")
        finally:
            self._schedule_unload()

        if len(ranked_scores) != len(texts):
            return _order_by_scores(list(scores), mode=RERANK_MODE_FALLBACK, reason="score_count")
        return _order_by_scores(ranked_scores, mode=RERANK_MODE_APPLIED, reason=None)

    def unload(self) -> None:
        with self._lock:
            self._cancel_timer()
            if self._model is None:
                return
            self._model = None
            logger.info("reranker model unloaded")

    def _fallback_if_needed(self) -> str | None:
        if not self.enabled:
            return "disabled"
        if self._predict_fn is not None:
            return None
        if self._model is not None:
            return None
        available = self._ram_fn()
        if available is not None and available < self.ram_pressure_threshold_mb:
            logger.warning(
                "reranker skipped under RAM pressure (available_mb=%s, threshold_mb=%s)",
                available,
                self.ram_pressure_threshold_mb,
            )
            return "ram_pressure"
        return None

    def _score(self, query: str, texts: Sequence[str]) -> list[float]:
        clipped = [_clip(text) for text in texts]
        if self._predict_fn is not None:
            return [float(value) for value in self._predict_fn(query, clipped)]
        model = self._ensure_loaded()
        predict = getattr(model, "predict")
        pairs = [(query, text) for text in clipped]
        raw = predict(pairs, batch_size=_BATCH_SIZE, show_progress_bar=False)
        return [float(value) for value in raw]

    def _ensure_loaded(self) -> object:
        with self._lock:
            if self._model is None:
                logger.info("loading reranker model %s", self.model_name)
                self._model = self._loader()
            return self._model

    def _schedule_unload(self) -> None:
        if self._predict_fn is not None and self._loader is _load_cross_encoder:
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


def get_reranker(settings: Settings | None = None) -> Reranker:
    global _service
    with _service_lock:
        if _service is None:
            settings = settings or get_settings()
            _service = Reranker(
                model_name=getattr(settings, "reranker_model", "BAAI/bge-reranker-base"),
                idle_seconds=getattr(settings, "model_idle_unload_seconds", 600),
                ram_pressure_threshold_mb=getattr(settings, "ram_pressure_threshold_mb", 2048),
            )
        return _service


def reset_reranker() -> None:
    """Test helper."""
    global _service
    with _service_lock:
        if _service is not None:
            _service.unload()
        _service = None


def available_ram_mb() -> float | None:
    """Available physical RAM in MiB, or None if it cannot be determined."""
    if sys.platform == "win32":
        return _windows_available_ram_mb()
    return _linux_available_ram_mb()


def _windows_available_ram_mb() -> float | None:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return None
    return float(stat.ullAvailPhys) / (1024 * 1024)


def _linux_available_ram_mb() -> float | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        return None
    return None


def _clip(text: str) -> str:
    if len(text) <= RERANK_TEXT_CHARS:
        return text
    return text[:RERANK_TEXT_CHARS]


def _order_by_scores(scores: list[float], *, mode: str, reason: str | None) -> RerankResult:
    indexed = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
    return RerankResult(
        mode=mode,
        indices=[index for index, _score in indexed],
        scores=[score for _index, score in indexed],
        reason=reason,
    )


def _load_cross_encoder() -> object:
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RerankError(
            "sentence-transformers is not installed; cannot load BAAI/bge-reranker-base"
        ) from exc
    settings = get_settings()
    name = getattr(settings, "reranker_model", "BAAI/bge-reranker-base")
    return CrossEncoder(name, device="cpu")
