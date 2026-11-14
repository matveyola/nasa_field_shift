"""Build the knowledge index (offline): parse cached pages, chunk, FTS5, embeddings.

One SQLite file. Rebuilds are written to a temporary file and swapped in atomically, so a
reader never sees a half-built index. Embeddings of unchanged chunks are reused by content hash.
"""

import hashlib
import json
import logging
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from .chunk import CHUNKER_VERSION, Chunk, chunk_page
from .models import Embedder
from .parse import ParseError, parse_page
from .parse_pdf import parse_pdf
from .sources import PUBLICATIONS, SOURCES, SourcePage

log = logging.getLogger("agent_service.knowledge.index")

INDEX_SCHEMA_VERSION = "1"
PARSER_VERSION = "2"

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE documents (
    doc_id TEXT PRIMARY KEY, publication_id TEXT NOT NULL, chapter TEXT NOT NULL, title TEXT NOT NULL,
    url TEXT NOT NULL, sha256 TEXT NOT NULL, retrieved_at TEXT, text TEXT NOT NULL);
CREATE TABLE chunks (
    chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(doc_id), ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL, chapter TEXT NOT NULL, section TEXT NOT NULL, heading TEXT NOT NULL, anchor TEXT,
    table_label TEXT, table_complete INTEGER NOT NULL, transcribed TEXT NOT NULL, url TEXT NOT NULL,
    text TEXT NOT NULL, index_text TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
    content_hash TEXT NOT NULL, language TEXT NOT NULL, embedding BLOB);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    chunk_id UNINDEXED, heading, body, tokenize = 'porter unicode61 remove_diacritics 2');
"""


class IndexBuildError(Exception):
    pass


def _previous_embeddings(path: Path, model_id: str, revision: str, dtype: str) -> dict[str, bytes]:
    """content_hash -> embedding blob from the existing index, if built with the same model."""
    if not path.exists():
        return {}
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as old:
            meta = dict(old.execute("SELECT key, value FROM meta"))
            if meta.get("embedding_model") != model_id or meta.get("embedding_revision") != revision                     or meta.get("embedding_dtype") != dtype:
                return {}
            return {h: e for h, e in old.execute("SELECT content_hash, embedding FROM chunks WHERE embedding IS NOT NULL")}
    except sqlite3.Error:
        return {}


def build_index(raw_dir: Path, index_path: Path, *, embed: bool = True, embedder: Embedder | None = None,
                sources: tuple[SourcePage, ...] = SOURCES, model_cache: str | None = None,
                dtype: str = "float32") -> dict:
    """Parse every allowlisted page from `raw_dir` and write the index. Returns a build report."""
    manifest_path = raw_dir / "fetch_manifest.json"
    fetched = {r["id"]: r for r in json.loads(manifest_path.read_text(encoding="utf-8"))} if manifest_path.exists() else {}
    documents, chunks, problems = [], [], []
    for source in sources:
        raw_path = raw_dir / f"{source.id}.{source.fmt}"
        if not raw_path.exists():
            raise IndexBuildError(f"missing source page {source.id}: run `python -m agent_service.knowledge fetch` first")
        raw = raw_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if fetched.get(source.id, {}).get("sha256") not in (None, digest):
            raise IndexBuildError(f"{source.id}: cached file does not match the fetch manifest checksum")
        try:
            blocks = parse_pdf(raw) if source.fmt == "pdf" else parse_page(raw)[1]
        except ParseError as e:
            raise IndexBuildError(f"{source.id}: {e}") from e
        doc_text, page_chunks = chunk_page(source, blocks)
        if not page_chunks:
            raise IndexBuildError(f"{source.id}: no chunks produced")
        documents.append((source, digest, fetched.get(source.id, {}).get("retrieved_at"), doc_text))
        chunks.extend(page_chunks)
        problems += [f"{source.id} {c.table}: incomplete table" for c in page_chunks if not c.table_complete]

    ids = [c.chunk_id for c in chunks]
    if len(ids) != len(set(ids)):
        raise IndexBuildError("duplicate chunk ids")

    model_id, revision, dim, vectors = None, None, None, {}
    if embed:
        embedder = embedder or Embedder(cache_folder=model_cache, dtype=dtype)
        model_id, revision, dim = embedder.model_id, embedder.revision, embedder.dim
        reuse = _previous_embeddings(index_path, model_id, revision, embedder.dtype)
        titles = {s.id: s.title for s in sources}
        todo = [c for c in chunks if c.content_hash not in reuse]
        if todo:
            matrix = embedder.encode([c.index_text(titles[c.doc_id]) for c in todo])
            for c, v in zip(todo, matrix):
                reuse[c.content_hash] = v.astype(np.float32).tobytes()
        vectors = {c.chunk_id: reuse[c.content_hash] for c in chunks}
        log.info("embeddings: %d computed, %d reused", len(todo), len(chunks) - len(todo))

    tmp = index_path.with_suffix(".building")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        tmp.unlink()
    titles = {s.id: s.title for s in sources}
    with closing(sqlite3.connect(tmp)) as db:
        db.executescript(SCHEMA)
        meta = {
            "schema_version": INDEX_SCHEMA_VERSION, "parser_version": PARSER_VERSION, "chunker_version": CHUNKER_VERSION,
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"), "chunk_count": str(len(chunks)),
            "sources": json.dumps({s.id: {"url": s.url, "sha256": d, "retrieved_at": r} for s, d, r, _ in documents}),
            "publications": json.dumps({k: asdict(v) for k, v in PUBLICATIONS.items()}),
            "embedding_model": model_id or "", "embedding_revision": revision or "", "embedding_dim": str(dim or 0),
            "embedding_dtype": (embedder.dtype if embed else ""),
        }
        db.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
        db.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            (s.id, s.publication_id, s.chapter, s.title, s.url, d, r, t) for s, d, r, t in documents])
        for c in chunks:
            index_text = c.index_text(titles[c.doc_id])
            db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                c.chunk_id, c.doc_id, c.ordinal, c.kind, c.chapter, c.section, c.heading, c.anchor, c.table,
                int(c.table_complete), json.dumps(c.transcribed_images), c.url, c.text, index_text, c.start, c.end,
                c.content_hash, c.language, vectors.get(c.chunk_id)))
            db.execute("INSERT INTO chunks_fts (chunk_id, heading, body) VALUES (?, ?, ?)",
                       (c.chunk_id, f"{titles[c.doc_id]} {c.section} {c.table or ''}", c.text))
        db.commit()
    os.replace(tmp, index_path)  # atomic on the same filesystem
    report = {"index": str(index_path), "documents": len(documents), "chunks": len(chunks),
              "by_kind": {k: sum(c.kind == k for c in chunks) for k in sorted({c.kind for c in chunks})},
              "embedded": bool(embed), "embedding_model": model_id, "problems": problems}
    log.info("index built %s", report)
    return report


def chunks_of(index_path: Path) -> list[Chunk]:
    """Read chunks back (tests and tooling)."""
    with closing(sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        docs = dict(db.execute("SELECT doc_id, publication_id FROM documents"))
        return [Chunk(chunk_id=r["chunk_id"], doc_id=r["doc_id"], publication_id=docs[r["doc_id"]], chapter=r["chapter"],
                      section=r["section"], heading=r["heading"], anchor=r["anchor"], url=r["url"], kind=r["kind"],
                      text=r["text"], table=r["table_label"], table_complete=bool(r["table_complete"]),
                      transcribed_images=json.loads(r["transcribed"]), ordinal=r["ordinal"], start=r["start"], end=r["end"])
                for r in db.execute("SELECT * FROM chunks ORDER BY doc_id, ordinal")]
