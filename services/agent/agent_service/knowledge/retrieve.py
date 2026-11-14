"""Runtime retrieval over a prebuilt index: lexical (BM25), dense (bge-m3), RRF fusion, reranking.

The index is loaded once into memory at startup. No network access, no model download at
request time. Retrieval scores rank passages by relevance; they are not the probability that
any agricultural claim is true.
"""

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

from .glossary import CORPUS_NAME, rewrite, uk_prefixes
from .index import INDEX_SCHEMA_VERSION
from .models import Embedder, ModelUnavailable, Reranker
from .sources import PUBLICATIONS, SOURCES_BY_ID

log = logging.getLogger("agent_service.knowledge.retrieve")

Mode = Literal["lexical", "dense", "hybrid", "hybrid_rerank"]
RRF_K = 60
TOPICS: dict[str, set[str]] = {
    "evapotranspiration_basics": {"1", "5"},
    "crop_coefficients": {"5", "6", "7"},
    "growth_stages": {"5", "6"},
    "soil_evaporation": {"7"},
    "soil_water": {"7", "8"},
    "water_stress": {"8"},
    "salinity": {"8"},
}
# Ukrainian practice (sowing dates and depths, soil warmth, varieties, fertilizer, protection): the NAAS booklets.
PRACTICE_DOCS = ("naas-",)


def rrf(*rankings: list[str], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: rank-based, so BM25 and cosine score scales are never mixed."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, start=1):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda kv: -kv[1])


class KnowledgeUnavailable(Exception):
    """The index is missing, stale or incompatible; the caller must not serve results."""


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    doc_id: str
    publication_id: str
    chapter: str
    section: str
    heading: str
    anchor: str | None
    table: str | None
    table_complete: bool
    kind: str
    url: str
    text: str
    index_text: str
    content_hash: str


@dataclass
class Hit:
    chunk: ChunkRecord
    rank: int
    lexical_rank: int | None = None
    dense_rank: int | None = None
    fused_score: float | None = None
    rerank_score: float | None = None


@dataclass
class SearchResult:
    hits: list[Hit]
    mode: Mode                       # the mode actually used (may be lower than requested)
    lexical_terms: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    degraded: str | None = None      # why a lower mode was used


class KnowledgeBase:
    """Loaded index + optional models. Thread-safe for concurrent reads."""

    def __init__(self, index_path: Path, *, mode: Mode = "lexical", embedder: Embedder | None = None,
                 reranker: Reranker | None = None, load_models: bool = True, model_cache: str | None = None,
                 reranker_dtype: str = "float32", rerank_top: int = 20):
        self.model_cache, self.reranker_dtype, self.rerank_top = model_cache, reranker_dtype, rerank_top
        if not index_path.exists():
            raise KnowledgeUnavailable(f"index not found at {index_path}: run `python -m agent_service.knowledge build`")
        self.path = index_path
        # Copy into memory: no file handle stays open, so the index can be rebuilt while serving.
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        source = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
        try:
            source.backup(self._db)
        finally:
            source.close()
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.meta = dict(self._db.execute("SELECT key, value FROM meta"))
        self._check_version()
        rows = self._db.execute("SELECT * FROM chunks ORDER BY doc_id, ordinal").fetchall()
        self.chunks = {r["chunk_id"]: ChunkRecord(
            chunk_id=r["chunk_id"], doc_id=r["doc_id"], publication_id=SOURCES_BY_ID[r["doc_id"]].publication_id,
            chapter=r["chapter"], section=r["section"], heading=r["heading"], anchor=r["anchor"], table=r["table_label"],
            table_complete=bool(r["table_complete"]), kind=r["kind"], url=r["url"], text=r["text"],
            index_text=r["index_text"], content_hash=r["content_hash"]) for r in rows}
        self._order = [r["chunk_id"] for r in rows]
        blobs = [r["embedding"] for r in rows]
        self.matrix = None
        if all(b is not None for b in blobs) and blobs:
            dim = int(self.meta.get("embedding_dim", "0"))
            self.matrix = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(blobs), dim)
        self.requested_mode = mode
        self.embedder, self.reranker, self.model_errors = embedder, reranker, []
        if load_models and mode in ("dense", "hybrid", "hybrid_rerank"):
            self._load_models(mode)
        self.mode: Mode = self._effective_mode(mode)

    # ------------------------------------------------------------------ status

    def _check_version(self) -> None:
        if self.meta.get("schema_version") != INDEX_SCHEMA_VERSION:
            raise KnowledgeUnavailable(f"index schema {self.meta.get('schema_version')} != {INDEX_SCHEMA_VERSION}: rebuild")
        built = set(json.loads(self.meta.get("sources", "{}")))
        if built != set(SOURCES_BY_ID):
            raise KnowledgeUnavailable(f"stale index: built for {sorted(built)}, registry has {sorted(SOURCES_BY_ID)}")

    def _load_models(self, mode: Mode) -> None:
        if self.matrix is None:
            self.model_errors.append("index has no embeddings (built with --no-embed)")
            return
        if self.embedder is None:
            try:
                self.embedder = Embedder(self.meta["embedding_model"], self.meta["embedding_revision"],
                                         cache_folder=self.model_cache, dtype=self.meta.get("embedding_dtype") or "float32")
            except ModelUnavailable as e:
                self.model_errors.append(str(e))
        if self.embedder is not None and (self.embedder.model_id != self.meta["embedding_model"]
                                          or self.embedder.revision != self.meta["embedding_revision"]):
            self.model_errors.append("query embedder differs from the index embedding model: dense disabled")
            self.embedder = None
        if mode == "hybrid_rerank" and self.reranker is None:
            try:
                self.reranker = Reranker(cache_folder=self.model_cache, dtype=self.reranker_dtype)
            except ModelUnavailable as e:
                self.model_errors.append(str(e))

    def _effective_mode(self, requested: Mode) -> Mode:
        dense_ok = self.embedder is not None and self.matrix is not None
        if requested == "hybrid_rerank" and dense_ok and self.reranker is not None:
            return "hybrid_rerank"
        if requested in ("hybrid", "hybrid_rerank") and dense_ok:
            return "hybrid"
        if requested == "dense" and dense_ok:
            return "dense"
        return "lexical"

    def status(self) -> dict:
        return {
            "requested_mode": self.requested_mode, "mode": self.mode, "chunks": len(self.chunks),
            "built_at": self.meta.get("built_at"), "embedding_model": self.meta.get("embedding_model") or None,
            "embedding_dtype": self.meta.get("embedding_dtype") or None,
            "reranker": self.reranker.model_id if self.reranker else None,
            "lexical_only": self.mode == "lexical", "problems": self.model_errors,
        }

    def publication(self, publication_id: str):
        return PUBLICATIONS[publication_id]

    # ------------------------------------------------------------------ search

    def _allowed(self, topic: str | None, content: str) -> set[str] | None:
        if topic is None and content == "any":
            return None
        chapters = TOPICS.get(topic) if topic and topic != "crop_practice" else None
        out = set()
        for cid, c in self.chunks.items():
            if topic == "crop_practice" and not c.doc_id.startswith(PRACTICE_DOCS):
                continue
            if chapters is not None and c.chapter not in chapters:
                continue
            if content == "tables" and c.kind not in ("table", "table_notes"):
                continue
            if content == "text" and c.kind in ("table", "table_notes"):
                continue
            out.add(cid)
        return out

    def lexical(self, query: str, limit: int, allowed: set[str] | None, use_rewrite: bool = True) -> tuple[list[str], list[str]]:
        original, expanded = rewrite(query)
        terms = [t for t in dict.fromkeys(original + (expanded if use_rewrite else [])) if t.lower() not in CORPUS_NAME]
        # Every term is a quoted FTS5 string: user text can never become FTS syntax. Ukrainian words also search by
        # their stem (a quoted prefix, "соняшни"*), so inflected forms in the Ukrainian sources match.
        prefixes = uk_prefixes(query)
        quoted = lambda w: '"' + w.replace('"', '""') + '"'  # noqa: E731
        match = " OR ".join([quoted(t) for t in terms] + [quoted(p[:-1]) + "*" if p.endswith("*") else quoted(p) for p in prefixes])
        terms = terms + prefixes
        if not match:
            return [], []
        with self._lock:
            rows = self._db.execute(
                "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, 0.0, 2.0, 1.0) LIMIT ?",
                (match, limit * 4 if allowed is not None else limit)).fetchall()
        ids = [r["chunk_id"] for r in rows if allowed is None or r["chunk_id"] in allowed]
        return ids[:limit], terms

    def dense(self, query: str, limit: int, allowed: set[str] | None) -> list[str]:
        vector = self.embedder.encode([query])[0]
        scores = self.matrix @ vector
        order = np.argsort(-scores)
        ids = [self._order[i] for i in order if allowed is None or self._order[i] in allowed]
        return ids[:limit]

    def search(self, query: str, *, k: int = 5, mode: Mode | None = None, topic: str | None = None,
               content: str = "any", candidates: int = 50, rerank_top: int | None = None) -> SearchResult:
        rerank_top = rerank_top or self.rerank_top
        requested = mode or self.requested_mode  # report degradation against what was configured
        effective = requested if self._effective_mode(requested) == requested else self._effective_mode(requested)
        degraded = None if effective == requested else f"requested {requested}, ran {effective}: {'; '.join(self.model_errors) or 'model not loaded'}"
        allowed = self._allowed(topic, content)
        timings: dict[str, float] = {}
        t = time.perf_counter()
        lexical_ids, terms = ([], [])
        if effective != "dense":
            lexical_ids, terms = self.lexical(query, candidates, allowed)
        timings["lexical"] = (time.perf_counter() - t) * 1000
        dense_ids: list[str] = []
        if effective in ("dense", "hybrid", "hybrid_rerank"):
            t = time.perf_counter()
            try:
                dense_ids = self.dense(query, candidates, allowed)
            except Exception as e:  # a model failure must not fail the farmer's turn: fall back, and say so
                log.error("dense retrieval failed, falling back to lexical: %s", type(e).__name__)
                if not lexical_ids:
                    lexical_ids, terms = self.lexical(query, candidates, allowed)
                effective, degraded = "lexical", f"dense retrieval failed ({type(e).__name__}); ran lexical"
            timings["dense"] = (time.perf_counter() - t) * 1000

        lex_rank = {cid: i + 1 for i, cid in enumerate(lexical_ids)}
        den_rank = {cid: i + 1 for i, cid in enumerate(dense_ids)}
        if effective == "lexical":
            ordered = [(cid, None) for cid in lexical_ids]
        elif effective == "dense":
            ordered = [(cid, None) for cid in dense_ids]
        else:
            ordered = rrf(lexical_ids, dense_ids)
        hits = [Hit(self.chunks[cid], 0, lex_rank.get(cid), den_rank.get(cid), score) for cid, score in ordered]

        if effective == "hybrid_rerank" and hits:
            t = time.perf_counter()
            pool = hits[:rerank_top]
            try:
                scores = self.reranker.score(query, [h.chunk.index_text for h in pool])
                for h, s in zip(pool, scores):
                    h.rerank_score = s
                hits = sorted(pool, key=lambda h: -h.rerank_score) + hits[rerank_top:]
            except Exception as e:
                log.error("reranking failed, keeping fused order: %s", type(e).__name__)
                effective, degraded = "hybrid", f"reranker failed ({type(e).__name__}); ran hybrid without reranking"
            timings["rerank"] = (time.perf_counter() - t) * 1000
        for i, h in enumerate(hits):
            h.rank = i + 1
        return SearchResult(hits=hits[:k], mode=effective, lexical_terms=terms, timings_ms=timings, degraded=degraded)
