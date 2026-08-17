"""Orchestrates the full diagnostic run: scrape -> clean -> band -> snippets
-> cohort split -> discover categories -> measure -> stats -> robustness ->
report. Single entry point for both the instant and emailed paths in app.py
-- the only difference between them is who calls run() and what happens
with the result (rendered in-browser vs. emailed)."""

import pandas as pd

from . import config
from . import cohorts as cohorts_mod
from . import discovery
from . import measurement
from . import report
from . import scrape
from . import snippets as snippets_mod
from . import stats as stats_mod


def _assign_band(rating: int) -> str:
    lo, hi = config.BAND_NEGATIVE
    if lo <= rating <= hi:
        return "Negative"
    lo, hi = config.BAND_NEUTRAL
    if lo <= rating <= hi:
        return "Neutral"
    return "Positive"


def _insufficient(
    app_info, package_id, requested_count, actual_count, reason,
    neutral_count=0, positive_count=0, rating_distribution=None,
) -> report.ReportData:
    return report.ReportData(
        app_title=(app_info or {}).get("title", package_id),
        app_icon_url=(app_info or {}).get("icon", ""),
        package_id=package_id,
        requested_count=requested_count,
        actual_review_count=actual_count,
        negative_review_count=0,
        neutral_review_count=neutral_count,
        positive_review_count=positive_count,
        rating_distribution=rating_distribution or {},
        n_a=0,
        n_b=0,
        cohort_a_range=(None, None),
        cohort_b_range=(None, None),
        insufficient_data=True,
        insufficient_reason=reason,
    )


def run(app_input: str, review_count: int) -> report.ReportData:
    package_id = scrape.resolve_package_id(app_input)
    review_count = min(review_count, config.ASYNC_MAX_REVIEWS)

    print(f"[pipeline] fetching app info for {package_id}...", flush=True)
    app_info = scrape.scrape_app_info(package_id)
    print(f"[pipeline] scraping {review_count} reviews...", flush=True)
    df_reviews = scrape.scrape_app_reviews(package_id, review_count)
    print(f"[pipeline] scraped {len(df_reviews)} reviews", flush=True)
    actual_count = len(df_reviews)

    if actual_count < config.MIN_VIABLE_REVIEWS:
        return _insufficient(
            app_info, package_id, review_count, actual_count,
            f"Only {actual_count} reviews were available (need at least "
            f"{config.MIN_VIABLE_REVIEWS}) for a reliable comparison.",
        )

    print("[pipeline] cleaning text...", flush=True)
    df_reviews["clean_review"] = df_reviews["content"].apply(scrape.clean_text)
    print("[pipeline] parsing dates...", flush=True)
    df_reviews["date_parsed"] = pd.to_datetime(df_reviews["at"], errors="coerce")
    print("[pipeline] assigning bands...", flush=True)
    df_reviews["band"] = df_reviews["score"].apply(_assign_band)
    print("[pipeline] bands assigned", flush=True)

    band_counts = df_reviews["band"].value_counts().to_dict()
    neutral_count = int(band_counts.get("Neutral", 0))
    positive_count = int(band_counts.get("Positive", 0))
    rating_distribution = {int(k): int(v) for k, v in df_reviews["score"].value_counts().sort_index().to_dict().items()}

    negative_reviews = df_reviews[df_reviews["band"] == config.ANALYSIS_BAND].reset_index(drop=True)
    negative_count = len(negative_reviews)
    print(f"[pipeline] {negative_count} negative reviews", flush=True)

    if negative_count < config.MIN_VIABLE_NEGATIVE_REVIEWS:
        print("[pipeline] insufficient negative reviews, returning early", flush=True)
        result = _insufficient(
            app_info, package_id, review_count, actual_count,
            f"Only {negative_count} negative (1-2 star) reviews were found among "
            f"{actual_count} scraped (need at least {config.MIN_VIABLE_NEGATIVE_REVIEWS}) "
            f"for a reliable comparison. Try a larger review count.",
            neutral_count=neutral_count, positive_count=positive_count, rating_distribution=rating_distribution,
        )
        print("[pipeline] insufficient result built, returning", flush=True)
        return result

    print(f"[pipeline] splitting {negative_count} negative reviews into snippets...", flush=True)
    df_snippets = snippets_mod.split_into_snippets(negative_reviews)
    print(f"[pipeline] {len(df_snippets)} snippets", flush=True)

    reviews_meta = negative_reviews[["review_id", "score", "date_parsed", "reviewCreatedVersion"]].rename(
        columns={"score": "rating", "reviewCreatedVersion": "app_version"}
    )
    split = cohorts_mod.auto_split_cohorts(reviews_meta)

    print("[pipeline] discovering categories...", flush=True)
    category_defs = discovery.discover_categories(df_snippets)
    print(f"[pipeline] {len(category_defs)} categories discovered", flush=True)
    if not category_defs:
        return _insufficient(
            app_info, package_id, review_count, actual_count,
            "Not enough distinct complaint patterns were found to build category "
            "comparisons. Try a larger review count.",
            neutral_count=neutral_count, positive_count=positive_count, rating_distribution=rating_distribution,
        )

    print("[pipeline] measuring categories...", flush=True)
    measured = measurement.measure_categories(df_snippets, category_defs)
    review_category = measurement.build_review_category_table(measured)

    print("[pipeline] running stats engine...", flush=True)
    stats_df = stats_mod.stats_engine(review_category, split["cohort_a_ids"], split["cohort_b_ids"])
    stats_df = stats_mod.apply_correction(stats_df)

    significant_categories = stats_df.loc[stats_df.significant, "category"].tolist()
    robustness_flags = [
        stats_mod.version_robustness_check(reviews_meta, review_category, cat, split["cohort_a_ids"], split["cohort_b_ids"])
        for cat in significant_categories
    ]

    final_table = report.build_final_ranked_table(stats_df, robustness_flags, category_defs)
    quotes = report.pick_top_quotes(final_table, category_defs)
    top_categories = final_table.head(5)["category"].tolist()
    cooc_pairs = report.top_cooccurrence_pairs(review_category, top_categories)
    keyword_labels = {tid: ", ".join(d["keywords"][:3]) for tid, d in category_defs.items()}

    return report.ReportData(
        app_title=(app_info or {}).get("title", package_id),
        app_icon_url=(app_info or {}).get("icon", ""),
        package_id=package_id,
        requested_count=review_count,
        actual_review_count=actual_count,
        negative_review_count=negative_count,
        neutral_review_count=neutral_count,
        positive_review_count=positive_count,
        rating_distribution=rating_distribution,
        n_a=split["n_a"],
        n_b=split["n_b"],
        cohort_a_range=split["cohort_a_range"],
        cohort_b_range=split["cohort_b_range"],
        ranked_table=final_table,
        quotes=quotes,
        cooccurrence_pairs=cooc_pairs,
        keyword_labels=keyword_labels,
    )
