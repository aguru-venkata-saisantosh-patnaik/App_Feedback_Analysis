"""Auto-split scraped reviews into two comparison cohorts. No date inputs:
splits by review *count* at the midpoint (most recent half vs. prior half),
not by calendar time -- a calendar midpoint on a small or bursty review
stream can put almost everything in one cohort and starve the other below
the sample floor. A count-based split can't do that."""

import pandas as pd


def auto_split_cohorts(reviews_meta: pd.DataFrame, date_col: str = "date_parsed") -> dict:
    """reviews_meta: one row per review, with `review_id` and `date_col`.
    Returns cohort_a (older half) / cohort_b (more recent half) as review_id
    sets, plus each cohort's actual date range for display."""
    sorted_reviews = reviews_meta.sort_values(date_col).reset_index(drop=True)
    n = len(sorted_reviews)
    mid = n // 2
    cohort_a = sorted_reviews.iloc[:mid]
    cohort_b = sorted_reviews.iloc[mid:]

    return {
        "cohort_a_ids": set(cohort_a["review_id"]),
        "cohort_b_ids": set(cohort_b["review_id"]),
        "cohort_a_range": (cohort_a[date_col].min(), cohort_a[date_col].max()) if len(cohort_a) else (None, None),
        "cohort_b_range": (cohort_b[date_col].min(), cohort_b[date_col].max()) if len(cohort_b) else (None, None),
        "n_a": len(cohort_a),
        "n_b": len(cohort_b),
    }
