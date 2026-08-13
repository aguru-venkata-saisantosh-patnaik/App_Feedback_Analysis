"""Two-proportion z-test + Cohen's h per category, single-tier Benjamini-
Hochberg correction (no pre-registered/exploratory split -- there's no
hand-written hypothesis dict left to split against; every category here is
discovery-surfaced). Ported from notebook cells 18/19/21, adapted to
operate on cohort review_id sets (from cohorts.auto_split_cohorts) rather
than date-range tuples."""

import numpy as np
import pandas as pd
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import proportions_ztest

from . import config


def cohens_h(p1, p2):
    return 2 * np.arcsin(np.sqrt(p1)) - 2 * np.arcsin(np.sqrt(p2))


def stats_engine(
    review_category_long: pd.DataFrame,
    cohort_a_ids: set,
    cohort_b_ids: set,
    categories=None,
    min_n: int = config.SAMPLE_FLOOR_MIN_N,
) -> pd.DataFrame:
    """Per category: count/rate in each cohort, two-proportion z-test,
    Cohen's h. Sample floor: both cohort sizes >= min_n AND >= 5 expected
    occurrences both ways (count and non-count) -- below floor, rate is
    still reported, no p-value/h claimed."""
    n_a, n_b = len(cohort_a_ids), len(cohort_b_ids)
    if categories is None:
        categories = sorted(review_category_long["category"].unique().tolist())

    rows = []
    for cat in categories:
        cat_ids = set(review_category_long.loc[review_category_long.category == cat, "review_id"])
        count_a = len(cohort_a_ids & cat_ids)
        count_b = len(cohort_b_ids & cat_ids)
        rate_a = count_a / n_a if n_a else np.nan
        rate_b = count_b / n_b if n_b else np.nan

        floor_ok = (
            n_a >= min_n and n_b >= min_n
            and count_a >= 5 and (n_a - count_a) >= 5
            and count_b >= 5 and (n_b - count_b) >= 5
        )

        p_value, h = np.nan, np.nan
        if floor_ok:
            _, p_value = proportions_ztest([count_a, count_b], [n_a, n_b])
            h = cohens_h(rate_a, rate_b)

        rows.append(
            {
                "category": cat, "n_a": n_a, "count_a": count_a, "rate_a": rate_a,
                "n_b": n_b, "count_b": count_b, "rate_b": rate_b,
                "RR": (rate_b / rate_a) if rate_a else np.nan,
                "cohens_h": h, "p_value": p_value, "sample_floor_ok": floor_ok,
            }
        )
    return pd.DataFrame(rows)


def apply_correction(results_df: pd.DataFrame, alpha: float = config.ALPHA) -> pd.DataFrame:
    """Benjamini-Hochberg FDR correction across every category that cleared
    the sample floor."""
    results_df = results_df.copy()
    results_df["significant"] = False
    results_df["p_value_adjusted"] = np.nan

    testable = results_df[results_df.sample_floor_ok]
    if len(testable) > 0:
        reject, pvals_corrected, _, _ = multipletests(testable.p_value, alpha=alpha, method="fdr_bh")
        results_df.loc[testable.index, "significant"] = reject
        results_df.loc[testable.index, "p_value_adjusted"] = pvals_corrected
    return results_df


def get_major_minor(v) -> str:
    """'18.9.3' -> '18.9'; falls back to the raw value if parsing fails."""
    try:
        parts = str(v).split(".")
        return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else str(v)
    except Exception:
        return str(v)


def version_robustness_check(
    measurement_table: pd.DataFrame,
    review_category_long: pd.DataFrame,
    category: str,
    cohort_a_ids: set,
    cohort_b_ids: set,
) -> dict:
    """Reweights cohort_b's version-specific rates to cohort_a's version-
    group mix (direct standardization). robust=True if the reweighted
    difference keeps the same sign and at least half the raw magnitude --
    the movement isn't mostly explained by which app version people
    happened to be on. robust=None if there's no usable version overlap."""
    pop = measurement_table.drop_duplicates(subset="review_id")[["review_id", "app_version"]].copy()
    pop["version_group"] = pop["app_version"].apply(get_major_minor)

    pop_a = pop[pop.review_id.isin(cohort_a_ids)]
    pop_b = pop[pop.review_id.isin(cohort_b_ids)]

    cat_ids = set(review_category_long.loc[review_category_long.category == category, "review_id"])
    pop_a = pop_a.assign(has_cat=pop_a.review_id.isin(cat_ids))
    pop_b = pop_b.assign(has_cat=pop_b.review_id.isin(cat_ids))

    mix_a = pop_a.version_group.value_counts(normalize=True)
    rate_by_version_b = pop_b.groupby("version_group")["has_cat"].mean()

    common = set(mix_a.index) & set(rate_by_version_b.index)
    if not common:
        return {"category": category, "robust": None, "weight_sum": 0.0, "note": "no overlapping version groups"}

    weight_sum = sum(mix_a.get(v, 0) for v in common)
    if weight_sum < config.MIN_VERSION_COVERAGE:
        return {
            "category": category, "robust": None, "weight_sum": weight_sum,
            "note": f"low coverage: overlap represents only {weight_sum * 100:.0f}% of cohort_a's version mix, not trusted",
        }

    reweighted_rate_b = sum(rate_by_version_b.get(v, 0) * mix_a.get(v, 0) for v in common) / weight_sum

    raw_rate_a, raw_rate_b = pop_a.has_cat.mean(), pop_b.has_cat.mean()
    raw_diff = raw_rate_b - raw_rate_a
    reweighted_diff = reweighted_rate_b - raw_rate_a
    robust = True if raw_diff == 0 else (
        np.sign(raw_diff) == np.sign(reweighted_diff) and abs(reweighted_diff) >= 0.5 * abs(raw_diff)
    )

    return {
        "category": category, "raw_rate_a": raw_rate_a, "raw_rate_b": raw_rate_b,
        "reweighted_rate_b": reweighted_rate_b, "raw_diff": raw_diff,
        "reweighted_diff": reweighted_diff, "weight_sum": weight_sum, "robust": robust,
    }


def assign_zone(row) -> str:
    """Conservative by construction: Priority requires an *explicit* robust
    True. Anything else (robust False, robust None/unresolved, or not
    significant) falls to Watch/No Action rather than defaulting to
    Priority."""
    if not row["significant"]:
        return "No Action"
    if row.get("robust") is True:
        return "Priority"
    return "Watch"
