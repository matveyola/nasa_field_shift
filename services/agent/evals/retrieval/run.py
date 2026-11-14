"""Retrieval benchmark: BM25 vs dense vs hybrid (RRF) vs hybrid + rerank, per language.

    uv run python -m evals.retrieval.run --model-cache D:/field-shift-models/hf
    uv run python -m evals.retrieval.run --lexical-only      # no models needed

Gold evidence anchors are resolved against the index at run time; ranking never sees them.
Models are loaded one after another (embedder, then reranker) to fit small machines: the
reranking variant re-scores the same fused top-20 the runtime would.
"""

import argparse
import gc
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from agent_service.knowledge.retrieve import KnowledgeBase, rrf

BENCHMARK = Path(__file__).parent / "benchmark.json"
REPORTS = Path(__file__).parents[1] / "reports"
CANDIDATES, RERANK_TOP, KS = 50, 20, (1, 3, 5, 10)


def _norm(text: str) -> str:
    return " ".join(text.split())


def resolve_gold(kb: KnowledgeBase, case: dict) -> list[set[str]]:
    """Each anchor -> the set of chunk ids that contain all of its phrases (in that page)."""
    anchors = []
    for anchor in case["gold"]:
        phrases = [_norm(p) for p in anchor["contains"]]
        ids = {cid for cid, c in kb.chunks.items() if c.doc_id == anchor["doc"] and all(p in _norm(c.text) for p in phrases)}
        anchors.append(ids)
    return anchors


def metrics(ranking: list[str], anchors: list[set[str]]) -> dict:
    out = {}
    first = next((i + 1 for i, cid in enumerate(ranking[:10]) if any(cid in a for a in anchors)), None)
    out["mrr@10"] = 1.0 / first if first else 0.0
    for k in KS:
        top = set(ranking[:k])
        out[f"hit@{k}"] = float(any(top & a for a in anchors))
        out[f"recall@{k}"] = sum(bool(top & a) for a in anchors) / len(anchors)
    return out


def _rss() -> float | None:
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=Path("data/knowledge/index/knowledge.sqlite"))
    parser.add_argument("--model-cache")
    parser.add_argument("--lexical-only", action="store_true")
    args = parser.parse_args()
    cases = json.loads(BENCHMARK.read_text(encoding="utf-8"))["cases"]
    kb = KnowledgeBase(args.index, mode="lexical", load_models=False, model_cache=args.model_cache)
    gold = {c["id"]: resolve_gold(kb, c) for c in cases}
    bad = [cid for cid, anchors in gold.items() if any(not a for a in anchors)]
    if bad:
        print(f"gold anchors that match no chunk: {bad}", file=sys.stderr)
        return 2

    runs: dict[str, dict[str, list[str]]] = {"bm25_no_rewrite": {}, "bm25": {}}
    timings: dict[str, list[float]] = {k: [] for k in ("bm25", "dense", "rerank")}
    memory: dict[str, float | None] = {"start_rss_mb": _rss()}
    for c in cases:
        runs["bm25_no_rewrite"][c["id"]] = kb.lexical(c["q"], CANDIDATES, None, use_rewrite=False)[0]
        t = time.perf_counter()
        runs["bm25"][c["id"]] = kb.lexical(c["q"], CANDIDATES, None)[0]
        timings["bm25"].append((time.perf_counter() - t) * 1000)

    rerank_scores: dict[str, list[float]] = {}
    model_info = {}
    if not args.lexical_only:
        from agent_service.knowledge.models import Embedder, Reranker

        embedder = Embedder(kb.meta["embedding_model"], kb.meta["embedding_revision"], cache_folder=args.model_cache,
                            dtype=kb.meta.get("embedding_dtype") or "float32")
        kb.embedder = embedder
        model_info["embedder_load_s"] = round(embedder.load_stats.seconds, 1)
        memory["after_embedder_load_rss_mb"] = _rss()
        kb.dense("warm-up", 1, None)
        runs["dense"], runs["hybrid"] = {}, {}
        for c in cases:
            t = time.perf_counter()
            dense = kb.dense(c["q"], CANDIDATES, None)
            timings["dense"].append((time.perf_counter() - t) * 1000)
            runs["dense"][c["id"]] = dense
            runs["hybrid"][c["id"]] = [cid for cid, _ in rrf(runs["bm25"][c["id"]], dense)]
        memory["peak_after_dense_rss_mb"] = _rss()
        kb.embedder = None
        del embedder
        gc.collect()

        reranker = Reranker(cache_folder=args.model_cache)
        model_info["reranker_load_s"] = round(reranker.load_stats.seconds, 1)
        memory["after_reranker_load_rss_mb"] = _rss()
        runs["hybrid_rerank"] = {}
        for c in cases:
            pool = runs["hybrid"][c["id"]][:RERANK_TOP]
            t = time.perf_counter()
            scores = reranker.score(c["q"], [kb.chunks[cid].index_text for cid in pool], batch_size=4)
            timings["rerank"].append((time.perf_counter() - t) * 1000)
            order = sorted(zip(pool, scores), key=lambda x: -x[1])
            runs["hybrid_rerank"][c["id"]] = [cid for cid, _ in order] + runs["hybrid"][c["id"]][RERANK_TOP:]
            rerank_scores[c["id"]] = [s for _, s in order]
            gc.collect()  # keep peak memory flat on small machines
        memory["peak_after_rerank_rss_mb"] = _rss()

    answerable = [c for c in cases if c["answerable"]]
    table = {}
    for variant, ranking in runs.items():
        for lang in ("en", "uk", "all"):
            subset = [c for c in answerable if lang == "all" or c["lang"] == lang]
            per = [metrics(ranking[c["id"]], gold[c["id"]]) for c in subset]
            table[(variant, lang)] = {k: round(statistics.mean(p[k] for p in per), 3) for k in per[0]} | {"n": len(per)}

    negatives = [c for c in cases if not c["answerable"]]
    abstention = {}
    if rerank_scores:
        top1 = lambda ids: [rerank_scores[i][0] for i in ids]  # noqa: E731
        abstention = {
            "answerable_top1_rerank": sorted(round(s, 2) for s in top1([c["id"] for c in answerable])),
            "unanswerable_top1_rerank": {c["id"]: round(rerank_scores[c["id"]][0], 2) for c in negatives},
        }
    latency = {k: {"median_ms": round(statistics.median(v), 1), "p95_ms": round(sorted(v)[int(0.95 * (len(v) - 1))], 1)}
               for k, v in timings.items() if v}
    report = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"), "index_built_at": kb.meta.get("built_at"),
        "chunks": len(kb.chunks), "embedding_model": f"{kb.meta.get('embedding_model')}@{kb.meta.get('embedding_revision', '')[:8]}",
        "cases": len(cases), "answerable": len(answerable), "unanswerable": len(negatives),
        "metrics": {f"{v}/{lang}": m for (v, lang), m in table.items()}, "latency": latency, "memory": memory,
        "models": model_info, "abstention": abstention,
        "rankings": {v: {cid: r[:10] for cid, r in rk.items()} for v, rk in runs.items()},
    }
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "retrieval-latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    lines = [f"# Retrieval benchmark ({len(answerable)} answerable + {len(negatives)} unanswerable questions)", "",
             f"Index: {len(kb.chunks)} chunks, built {kb.meta.get('built_at')}; embeddings {report['embedding_model']}.", "",
             "| Variant | Lang | n | Hit@1 | Hit@3 | Hit@5 | Recall@5 | Recall@10 | MRR@10 |", "|---|---|---|---|---|---|---|---|---|"]
    for (v, lang), m in table.items():
        lines.append(f"| {v} | {lang} | {m['n']} | {m['hit@1']} | {m['hit@3']} | {m['hit@5']} | {m['recall@5']} | "
                     f"{m['recall@10']} | {m['mrr@10']} |")
    lines += ["", f"Latency per query (this machine, CPU): {json.dumps(latency)}", f"Memory (RSS MB): {json.dumps(memory)}",
              f"Model load: {json.dumps(model_info)}"]
    if abstention:
        lines += ["", f"Top-1 rerank logits, answerable: {abstention['answerable_top1_rerank']}",
                  f"Top-1 rerank logits, unanswerable: {abstention['unanswerable_top1_rerank']}"]
    (REPORTS / "retrieval-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
