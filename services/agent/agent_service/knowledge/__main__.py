"""Knowledge base commands.

    uv run python -m agent_service.knowledge fetch                 # download allowlisted FAO pages
    uv run python -m agent_service.knowledge build [--no-embed]    # parse, chunk, index (+ bge-m3 embeddings)
    uv run python -m agent_service.knowledge search "query" [--mode lexical|dense|hybrid|hybrid_rerank]
    uv run python -m agent_service.knowledge status
"""

import argparse
import json
import os
import logging
import sys
from pathlib import Path

DEFAULT_DIR = Path("data/knowledge")


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_service.knowledge")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--model-cache", default=os.environ.get("AGENT_RAG_MODEL_CACHE"),
                        help="Hugging Face cache folder holding the pinned models (env AGENT_RAG_MODEL_CACHE)")
    parser.add_argument("--dtype", default=os.environ.get("AGENT_RAG_MODEL_DTYPE", "float32"), choices=["float32", "bfloat16"])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch")
    build = sub.add_parser("build")
    build.add_argument("--no-embed", action="store_true", help="lexical-only index (no model needed)")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--mode", default="lexical", choices=["lexical", "dense", "hybrid", "hybrid_rerank"])
    search.add_argument("-k", type=int, default=5)
    search.add_argument("--topic")
    sub.add_parser("status")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    raw_dir, index_path = args.data_dir / "raw", args.data_dir / "index" / "knowledge.sqlite"

    if args.command == "fetch":
        from .fetch import fetch_all
        records = fetch_all(raw_dir)
        print(json.dumps([r.__dict__ for r in records], indent=2))
        return 0 if all(r.status != "failed" for r in records) else 1
    if args.command == "build":
        from .index import build_index
        print(json.dumps(build_index(raw_dir, index_path, embed=not args.no_embed, model_cache=args.model_cache,
                                     dtype=args.dtype), indent=2))
        return 0
    from .retrieve import KnowledgeBase
    kb = KnowledgeBase(index_path, mode=getattr(args, "mode", "lexical"), model_cache=args.model_cache,
                       reranker_dtype=args.dtype)
    if args.command == "status":
        print(json.dumps(kb.status(), indent=2))
        return 0
    result = kb.search(args.query, k=args.k, topic=args.topic)
    print(f"mode={result.mode} terms={result.lexical_terms} timings_ms={result.timings_ms} degraded={result.degraded}")
    for h in result.hits:
        print(f"\n#{h.rank} {h.chunk.chunk_id} [{h.chunk.kind}] {h.chunk.table or ''} lex={h.lexical_rank} dense={h.dense_rank}"
              f" rerank={h.rerank_score}\n   {h.chunk.section}\n   {h.chunk.text[:300]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
