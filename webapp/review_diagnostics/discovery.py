"""Unsupervised topic discovery over Negative-band snippets. Collapses the
notebook's embed -> resolution-search -> cluster -> extract-definitions
stages (previously copy-pasted per band) into one function, called once
since analysis scope is locked to the Negative band. No disk caching --
every run is a fresh, ephemeral process, so there's nothing to reload from
a prior run.

Deliberately lighter-weight than the case-study pipeline
(final_pipeline_V2.ipynb, unchanged, still uses BERTopic/UMAP/HDBSCAN
faithfully): this generalized webapp runs on free-tier hosting with a
512MB ceiling, where the PyTorch + numba stack (sentence-transformers,
umap-learn, standalone hdbscan) alone pushed peak memory to the ceiling
before any real clustering happened. Swapped for:
  - fastembed (ONNX runtime) instead of sentence-transformers (PyTorch) --
    same MiniLM model, no torch dependency at all.
  - PCA instead of UMAP for pre-clustering dimensionality reduction --
    linear rather than manifold-learning, but avoids numba entirely
    (UMAP has no non-numba implementation).
  - scikit-learn's built-in HDBSCAN (Cython, no numba) instead of the
    standalone hdbscan package -- same algorithm, same API, different
    (much lighter) implementation.
  - A direct class-based TF-IDF keyword extraction instead of pulling in
    bertopic as a library -- bertopic hard-depends on hdbscan, umap-learn,
    and sentence-transformers regardless of which parts are actually
    used, so keeping it as a dependency alone would undo all of the above."""

import numpy as np
import pandas as pd

from . import config


def _evaluate_clustering(reduced, labels):
    """n_topics, noise%, separation ratio (inter-centroid dist / mean intra-cluster dist)."""
    from scipy.spatial.distance import pdist

    unique = set(labels)
    unique.discard(-1)
    n_topics = len(unique)
    noise_pct = (labels == -1).sum() / len(labels) * 100
    if n_topics < 2:
        return n_topics, noise_pct, np.nan
    centroids = np.array([reduced[labels == c].mean(axis=0) for c in unique])
    inter = pdist(centroids).mean()
    intra = np.mean(
        [
            np.linalg.norm(reduced[labels == c] - reduced[labels == c].mean(axis=0), axis=1).mean()
            for c in unique
        ]
    )
    return n_topics, noise_pct, (inter / intra if intra > 0 else np.nan)


def _resolution_search(reduced, n):
    """Small grid search over min_cluster_size/min_samples. Returns a
    results dataframe and the row judged best: topic count in a human-
    labelable range, low noise, maximum separation. mcs floors at 5 (not the
    Blinkit run's 20) so this still works on small demo-sized inputs; rows
    where mcs >= n are skipped as degenerate. min_samples candidates are
    derived as fractions of each mcs (not fixed absolute values) so there's
    always at least one valid combination regardless of n."""
    from sklearn.cluster import HDBSCAN

    lo, hi = config.TOPIC_COUNT_RANGE
    rows = []
    for pct in config.MIN_CLUSTER_SIZE_PCTS:
        mcs = max(5, int(n * pct))
        if mcs >= n:
            continue
        ms_candidates = sorted({max(3, mcs // 4), max(3, mcs // 2), mcs})
        for ms in ms_candidates:
            clusterer = HDBSCAN(
                min_cluster_size=mcs, min_samples=ms, metric="euclidean", cluster_selection_method="eom"
            )
            labels = clusterer.fit_predict(reduced)
            n_topics, noise_pct, sep_ratio = _evaluate_clustering(reduced, labels)
            rows.append(
                {
                    "mcs": mcs, "ms": ms, "n_topics": n_topics,
                    "noise_pct": round(noise_pct, 1),
                    "sep_ratio": round(sep_ratio, 3) if not np.isnan(sep_ratio) else None,
                }
            )
    results = pd.DataFrame(rows)
    if results.empty:
        return results, None
    candidates = results[(results.n_topics >= lo) & (results.n_topics <= hi) & (results.noise_pct < config.MAX_NOISE_PCT)]
    if len(candidates) == 0:
        candidates = results[results.n_topics >= 2]
    if len(candidates) == 0:
        return results, None
    best = candidates.sort_values("sep_ratio", ascending=False).iloc[0]
    return results, best


def _extract_category_definitions(texts, embeddings, labels) -> dict:
    """{topic_id: {keywords, count, examples, centroid}}. Centroid lives in
    the original embedding space (not the PCA-reduced space used for
    clustering) since measurement.py compares fresh embeddings against it
    via cosine similarity.

    Keywords come from a class-based TF-IDF: each cluster's per-term count
    weighted against how common that term is corpus-wide, the same idea
    BERTopic's c-TF-IDF uses -- terms frequent in one cluster but rare
    elsewhere score highest. Labels are never hand-assigned; only c-TF-IDF
    terms computed from this run's actual data feed into keywords below."""
    from sklearn.feature_extraction.text import CountVectorizer

    unique = sorted(set(labels) - {-1})
    if not unique:
        return {}

    vectorizer = CountVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1)
    doc_term = vectorizer.fit_transform(texts)
    terms = vectorizer.get_feature_names_out()
    tf_corpus = np.asarray(doc_term.sum(axis=0)).flatten()
    avg_words_per_class = doc_term.sum() / len(unique)

    defs = {}
    for tid in unique:
        mask = labels == tid
        tf_class = np.asarray(doc_term[mask].sum(axis=0)).flatten()
        score = tf_class * np.log1p(avg_words_per_class / (tf_corpus + 1))
        ranked = np.argsort(score)[::-1]
        keywords = [terms[i] for i in ranked if tf_class[i] > 0][:12]

        cluster_embeddings = embeddings[mask]
        centroid = cluster_embeddings.mean(axis=0)
        cluster_texts = [t for t, m in zip(texts, mask) if m]
        dists = np.linalg.norm(cluster_embeddings - centroid, axis=1)
        examples = [cluster_texts[i] for i in np.argsort(dists)[:5]]

        defs[str(tid)] = {
            "keywords": keywords,
            "count": int(mask.sum()),
            "examples": examples,
            "centroid": centroid,
        }
    return defs


def discover_categories(negative_snippets: pd.DataFrame) -> dict:
    """negative_snippets: df with a 'snippet' text column, already filtered
    to the Negative band (both cohorts combined -- categories are discovered
    once over the full window, then measured per-cohort). Returns
    {topic_id: {keywords, count, examples, centroid}}, or {} if the sample
    is too small/degenerate to cluster meaningfully."""
    import gc

    from fastembed import TextEmbedding
    from sklearn.decomposition import PCA

    texts = negative_snippets["snippet"].astype(str).tolist()
    n = len(texts)
    if n < config.MIN_MEMBERS_FOR_CALIBRATION * 2:
        return {}

    embed_model = TextEmbedding(model_name=config.EMBED_MODEL_NAME, providers=config.EMBED_PROVIDERS, threads=1)
    embeddings = np.array(list(embed_model.embed(texts, batch_size=16)))
    del embed_model
    gc.collect()

    n_components = min(5, embeddings.shape[1], n - 1)
    reducer = PCA(n_components=n_components, random_state=42)
    reduced = reducer.fit_transform(embeddings)
    del reducer
    gc.collect()

    _, best = _resolution_search(reduced, n)
    if best is None:
        return {}

    from sklearn.cluster import HDBSCAN

    clusterer = HDBSCAN(
        min_cluster_size=int(best.mcs), min_samples=int(best.ms),
        metric="euclidean", cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(reduced)
    return _extract_category_definitions(texts, embeddings, labels)
