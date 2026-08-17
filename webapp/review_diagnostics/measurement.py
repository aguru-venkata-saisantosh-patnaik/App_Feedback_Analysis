"""Assigns every Negative-band snippet to its nearest discovered category,
if the match clears that category's own calibrated threshold. Ported from
notebook cells 13-14/16, made fully in-memory -- no parquet chunk files,
since each run is one ephemeral process with nothing to resume across."""

import numpy as np
import pandas as pd

from . import config


def _calibrate_thresholds(category_ids, category_centroids_norm, discovery_embeddings):
    """Per-category 10th-percentile threshold from the discovery snippets'
    own members (recovered via nearest-centroid, reproducing BERTopic's own
    assignment). Categories with too few members for a stable percentile
    fall back to the median of the other categories' thresholds."""
    own_sims = (
        discovery_embeddings / np.linalg.norm(discovery_embeddings, axis=1, keepdims=True)
    ) @ category_centroids_norm.T
    own_best_idx = own_sims.argmax(axis=1)
    own_best_sim = own_sims.max(axis=1)

    thresholds = {}
    small = []
    for i, tid in enumerate(category_ids):
        member_sims = own_best_sim[own_best_idx == i]
        if len(member_sims) >= config.MIN_MEMBERS_FOR_CALIBRATION:
            thresholds[tid] = float(np.percentile(member_sims, 10))
        else:
            thresholds[tid] = None
            small.append(tid)

    stable = [v for v in thresholds.values() if v is not None]
    fallback = float(np.median(stable)) if stable else 0.5
    for tid in small:
        thresholds[tid] = fallback
    return thresholds


def measure_categories(negative_snippets: pd.DataFrame, category_defs: dict) -> pd.DataFrame:
    """negative_snippets: all Negative-band snippets (both cohorts). Assigns
    each row to its nearest category if it clears that category's
    calibrated threshold. Returns negative_snippets with `category` and
    `similarity` columns added. Re-embeds the full input uniformly rather
    than reusing discovery.py's embeddings -- simpler and less bug-prone
    than index-aligning a subset back in, at the cost of a bit of repeat
    work that's trivial at this tool's data scale (capped at 1,000 reviews).
    """
    from fastembed import TextEmbedding

    category_ids = list(category_defs.keys())
    category_centroids = np.array([category_defs[tid]["centroid"] for tid in category_ids])
    category_centroids_norm = category_centroids / np.linalg.norm(category_centroids, axis=1, keepdims=True)

    embed_model = TextEmbedding(model_name=config.EMBED_MODEL_NAME, providers=config.EMBED_PROVIDERS, threads=1)
    all_snips = negative_snippets.reset_index(drop=True)

    embeddings = np.array(list(embed_model.embed(all_snips["snippet"].astype(str).tolist(), batch_size=16)))
    thresholds = _calibrate_thresholds(category_ids, category_centroids_norm, embeddings)
    threshold_array = np.array([thresholds[tid] for tid in category_ids])

    emb_norm = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
    sims = emb_norm @ category_centroids_norm.T
    best_idx = sims.argmax(axis=1)
    best_sim = sims.max(axis=1)
    passed = best_sim >= threshold_array[best_idx]

    result = all_snips.copy()
    result["category"] = [category_ids[i] if ok else None for i, ok in zip(best_idx, passed)]
    result["similarity"] = best_sim
    return result


def build_review_category_table(measurement_table: pd.DataFrame) -> pd.DataFrame:
    """Long-format review x category table: one row per (review_id,
    category) a review actually touches (drops "no match" rows)."""
    return (
        measurement_table.dropna(subset=["category"])
        .drop_duplicates(subset=["review_id", "category"])[["review_id", "category"]]
        .reset_index(drop=True)
    )


def build_reviews_meta(measurement_table: pd.DataFrame) -> pd.DataFrame:
    """Review-level metadata (one row per review) for cohort/sample-floor bookkeeping."""
    return (
        measurement_table.drop_duplicates(subset="review_id")[["review_id", "rating", "date_parsed", "app_version"]]
        .reset_index(drop=True)
    )
