# Retrieval benchmark (51 answerable + 6 unanswerable questions)

Index: 349 chunks, built 2026-11-06T12:01:26+00:00; embeddings BAAI/bge-m3@5617a9f6.

| Variant | Lang | n | Hit@1 | Hit@3 | Hit@5 | Recall@5 | Recall@10 | MRR@10 |
|---|---|---|---|---|---|---|---|---|
| bm25_no_rewrite | en | 27 | 0.444 | 0.667 | 0.741 | 0.741 | 0.852 | 0.574 |
| bm25_no_rewrite | uk | 24 | 0.125 | 0.208 | 0.25 | 0.25 | 0.25 | 0.177 |
| bm25_no_rewrite | all | 51 | 0.294 | 0.451 | 0.51 | 0.51 | 0.569 | 0.387 |
| bm25 | en | 27 | 0.444 | 0.667 | 0.741 | 0.741 | 0.87 | 0.579 |
| bm25 | uk | 24 | 0.417 | 0.583 | 0.625 | 0.625 | 0.792 | 0.519 |
| bm25 | all | 51 | 0.431 | 0.627 | 0.686 | 0.686 | 0.833 | 0.551 |
| dense | en | 27 | 0.63 | 0.741 | 0.815 | 0.796 | 0.87 | 0.706 |
| dense | uk | 24 | 0.458 | 0.708 | 0.708 | 0.708 | 0.812 | 0.594 |
| dense | all | 51 | 0.549 | 0.725 | 0.765 | 0.755 | 0.843 | 0.653 |
| hybrid | en | 27 | 0.556 | 0.778 | 0.778 | 0.778 | 0.87 | 0.674 |
| hybrid | uk | 24 | 0.542 | 0.792 | 0.833 | 0.792 | 0.875 | 0.659 |
| hybrid | all | 51 | 0.549 | 0.784 | 0.804 | 0.784 | 0.873 | 0.667 |
| hybrid_rerank | en | 27 | 0.778 | 0.852 | 0.852 | 0.815 | 0.926 | 0.826 |
| hybrid_rerank | uk | 24 | 0.583 | 0.833 | 0.875 | 0.833 | 0.833 | 0.719 |
| hybrid_rerank | all | 51 | 0.686 | 0.843 | 0.863 | 0.824 | 0.882 | 0.776 |

Latency per query (this machine, CPU): {"bm25": {"median_ms": 0.6, "p95_ms": 1.1}, "dense": {"median_ms": 519.4, "p95_ms": 4685.5}, "rerank": {"median_ms": 33549.1, "p95_ms": 45073.8}}
Memory (RSS MB): {"start_rss_mb": 52.375552, "after_embedder_load_rss_mb": 773.476352, "peak_after_dense_rss_mb": 1961.04192, "after_reranker_load_rss_mb": 2899.468288, "peak_after_rerank_rss_mb": 1665.26976}
Model load: {"embedder_load_s": 56.9, "reranker_load_s": 10.9}

Top-1 rerank logits, answerable: [0.0, 0.01, 0.02, 0.12, 0.23, 0.24, 0.44, 0.44, 0.49, 0.68, 0.69, 0.75, 0.79, 0.83, 0.86, 0.87, 0.87, 0.88, 0.89, 0.9, 0.9, 0.91, 0.93, 0.94, 0.95, 0.95, 0.96, 0.96, 0.97, 0.97, 0.97, 0.97, 0.98, 0.98, 0.98, 0.98, 0.98, 0.99, 0.99, 0.99, 0.99, 0.99, 0.99, 0.99, 0.99, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
Top-1 rerank logits, unanswerable: {'neg01': 0.01, 'neg02': 0.01, 'neg03': 0.01, 'neg04': 0.0, 'neg05': 0.0, 'neg06': 0.0}
