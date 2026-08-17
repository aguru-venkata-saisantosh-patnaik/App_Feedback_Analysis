"""Unsupervised topic discovery over Negative-band snippets. Collapses the
notebook's embed -> resolution-search -> BERTopic-fit -> extract-definitions
stages (previously copy-pasted per band) into one function, called once
since analysis scope is locked to the Negative band. No disk caching --
every run is a fresh, ephemeral process, so there's nothing to reload from
a prior run."""

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
    import hdbscan

    lo, hi = config.TOPIC_COUNT_RANGE
    rows = []
    for pct in config.MIN_CLUSTER_SIZE_PCTS:
        mcs = max(5, int(n * pct))
        if mcs >= n:
            continue
        ms_candidates = sorted({max(3, mcs // 4), max(3, mcs // 2), mcs})
        for ms in ms_candidates:
            clusterer = hdbscan.HDBSCAN(
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


def _fit_bertopic(docs, reduced, mcs, ms):
    """(given UMAP/HDBSCAN params) -> BERTopic c-TF-IDF labels ->
    similarity-based auto-merge. No hand-assigned labels anywhere.

    Takes the UMAP-reduced coordinates already computed by the resolution
    search, via BERTopic's documented pass-through reducer, instead of
    letting BERTopic re-run its own full UMAP fit on the raw embeddings --
    on the free-tier host this second fit was the single biggest memory/CPU
    cost, since two full UMAP models (with their own numba-compiled nearest-
    neighbor graphs) ended up alive at once for no benefit; the resolution
    search already found the coordinates BERTopic would recompute here."""
    import hdbscan
    from bertopic import BERTopic
    from bertopic.dimensionality import BaseDimensionalityReduction
    from sklearn.feature_extraction.text import CountVectorizer

    hdbscan_model = hdbscan.HDBSCAN(
        min_cluster_size=mcs, min_samples=ms, metric="euclidean",
        cluster_selection_method="eom", prediction_data=True,
    )
    vectorizer_model = CountVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1)
    topic_model = BERTopic(
        umap_model=BaseDimensionalityReduction(), hdbscan_model=hdbscan_model,
        vectorizer_model=vectorizer_model, calculate_probabilities=False, verbose=False,
    )
    topic_model.fit_transform(docs, embeddings=reduced)
    topic_model.reduce_topics(docs, nr_topics="auto")
    return topic_model


def _extract_category_definitions(topic_model, embeddings) -> dict:
    """{topic_id: {keywords, count, examples, centroid}}. Centroid lives in
    the original embedding space (not the 5-dim UMAP space used for
    clustering) since measurement.py compares fresh embeddings against it
    via cosine similarity."""
    info = topic_model.get_topic_info()
    final_labels = np.array(topic_model.topics_)
    defs = {}
    for _, row in info.iterrows():
        tid = row["Topic"]
        if tid == -1:
            continue
        keywords = [w for w, _ in topic_model.get_topic(tid)]
        reps = topic_model.get_representative_docs(tid) or []
        centroid = embeddings[final_labels == tid].mean(axis=0)
        defs[str(tid)] = {
            "keywords": keywords[:12],
            "count": int(row["Count"]),
            "examples": reps[:5],
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

    import umap
    from sentence_transformers import SentenceTransformer

    texts = negative_snippets["snippet"].astype(str).tolist()
    n = len(texts)
    if n < config.MIN_MEMBERS_FOR_CALIBRATION * 2:
        return {}

    embed_model = SentenceTransformer(config.EMBED_MODEL_NAME, device="cpu")
    embeddings = embed_model.encode(texts, batch_size=128, show_progress_bar=False)
    del embed_model
    gc.collect()

    reducer = umap.UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric="cosine", random_state=42)
    reduced = reducer.fit_transform(embeddings)
    del reducer
    gc.collect()

    _, best = _resolution_search(reduced, n)
    if best is None:
        return {}

    topic_model = _fit_bertopic(texts, reduced, int(best.mcs), int(best.ms))
    return _extract_category_definitions(topic_model, embeddings)
