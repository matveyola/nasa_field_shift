"""Embedding and reranking models (loaded once; never per request).

Heavy imports (torch, sentence-transformers) happen only when a model is actually loaded, so
lexical-only mode works without the `rag` dependency group.
"""

import logging
import time
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("agent_service.knowledge.models")

EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


class ModelUnavailable(Exception):
    """The model cannot be loaded (dependency group missing, weights not downloaded, no memory)."""


@dataclass
class LoadStats:
    seconds: float
    rss_mb_before: float | None
    rss_mb_after: float | None


def _rss_mb() -> float | None:
    try:
        import psutil  # optional
        return psutil.Process().memory_info().rss / 1e6
    except Exception:
        return None


class Embedder:
    """bge-m3 dense embeddings (CLS pooling, L2-normalised, 1024 dims). No query instruction needed."""

    def __init__(self, model_id: str = EMBEDDING_MODEL, revision: str = EMBEDDING_REVISION,
                 offline: bool = True, max_seq_length: int = 512, cache_folder: str | None = None,
                 dtype: str = "float32"):
        before, started = _rss_mb(), time.monotonic()
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ModelUnavailable("sentence-transformers is not installed (uv sync --group rag)") from e
        try:
            self.model = SentenceTransformer(model_id, revision=revision, device="cpu", local_files_only=offline,
                                             cache_folder=cache_folder, model_kwargs={"dtype": _torch_dtype(dtype)})
        except Exception as e:  # missing weights, corrupted cache, out of memory
            raise ModelUnavailable(f"cannot load {model_id}@{revision[:8]}: {type(e).__name__}: {e}") from e
        self.model.max_seq_length = max_seq_length
        self.model_id, self.revision, self.dtype = model_id, revision, dtype
        self.dim = int(self.model.get_sentence_embedding_dimension())
        self.load_stats = LoadStats(time.monotonic() - started, before, _rss_mb())
        log.info("embedder loaded model=%s dim=%d seconds=%.1f", model_id, self.dim, self.load_stats.seconds)

    def encode(self, texts: list[str], batch_size: int = 8) -> np.ndarray:
        vectors = self.model.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                                    convert_to_numpy=True, show_progress_bar=False)
        return np.asarray(vectors, dtype=np.float32)  # stored and searched in float32 whatever the model dtype


class Reranker:
    """bge-reranker-v2-m3 cross-encoder. Scores are the model's default sigmoid outputs in [0, 1]:
    relevance for ordering passages, not the probability that a statement is true."""

    def __init__(self, model_id: str = RERANKER_MODEL, revision: str = RERANKER_REVISION,
                 offline: bool = True, max_length: int = 512, cache_folder: str | None = None, dtype: str = "float32"):
        before, started = _rss_mb(), time.monotonic()
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as e:
            raise ModelUnavailable("sentence-transformers is not installed (uv sync --group rag)") from e
        try:
            self.model = CrossEncoder(model_id, revision=revision, device="cpu", max_length=max_length,
                                      local_files_only=offline, cache_folder=cache_folder,
                                      model_kwargs={"dtype": _torch_dtype(dtype)})
        except Exception as e:
            raise ModelUnavailable(f"cannot load {model_id}@{revision[:8]}: {type(e).__name__}: {e}") from e
        self.model_id, self.revision, self.dtype = model_id, revision, dtype
        self.load_stats = LoadStats(time.monotonic() - started, before, _rss_mb())
        log.info("reranker loaded model=%s seconds=%.1f", model_id, self.load_stats.seconds)

    def score(self, query: str, passages: list[str], batch_size: int = 8) -> list[float]:
        scores = self.model.predict([(query, p) for p in passages], batch_size=batch_size,
                                    show_progress_bar=False, activation_fn=None)
        return [float(s) for s in np.asarray(scores).reshape(-1)]


def _torch_dtype(name: str):
    import torch

    return {"float32": torch.float32, "bfloat16": torch.bfloat16}[name]
