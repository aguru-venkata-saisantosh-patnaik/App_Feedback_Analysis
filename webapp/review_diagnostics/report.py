"""Assembles the final ranked table (stats + robustness + keywords + zone,
ported from notebook cell 26, minus the neutral/positive cross-band columns
-- out of scope now that discovery only ever runs on the Negative band) and
renders it two ways: a class-based HTML block for the in-app instant path
(styled by the page's own stylesheet) and a fully inline-styled HTML email
for the async path (email clients don't reliably load external CSS). Both
renderers consume the same ReportData and share one row-building routine so
the two paths never duplicate report logic."""

import html as html_mod
import re
from dataclasses import dataclass, field
from io import StringIO

import numpy as np
import pandas as pd

from . import config
from . import stats as stats_mod


def build_final_ranked_table(stats_df: pd.DataFrame, robustness_flags: list[dict], category_defs: dict) -> pd.DataFrame:
    table = stats_df.copy()
    table["keywords"] = table["category"].apply(lambda c: ", ".join(category_defs[c]["keywords"][:6]))

    robustness_df = pd.DataFrame(robustness_flags) if robustness_flags else pd.DataFrame(columns=["category", "robust"])
    table = table.merge(robustness_df[["category", "robust"]] if len(robustness_df) else robustness_df, on="category", how="left")
    if "robust" not in table.columns:
        table["robust"] = None

    table["zone"] = table.apply(stats_mod.assign_zone, axis=1)
    return table.sort_values("cohens_h", ascending=False, key=abs).reset_index(drop=True)


def pick_top_quotes(final_ranked_table: pd.DataFrame, category_defs: dict, top_n: int = 5) -> dict:
    """Representative quotes for the top-|effect-size| categories, whether
    or not they cleared significance -- useful context either way."""
    quotes = {}
    for _, row in final_ranked_table.head(top_n).iterrows():
        examples = category_defs.get(row["category"], {}).get("examples", [])
        if examples:
            quotes[row["category"]] = examples[:3]
    return quotes


def cooccurrence(review_category_long: pd.DataFrame, category_a: str, category_b: str) -> dict:
    ids_a = set(review_category_long.loc[review_category_long.category == category_a, "review_id"])
    ids_b = set(review_category_long.loc[review_category_long.category == category_b, "review_id"])
    overlap = ids_a & ids_b
    return {
        "n_a": len(ids_a), "n_b": len(ids_b), "n_overlap": len(overlap),
        "pct_of_a_also_b": (len(overlap) / len(ids_a)) if ids_a else np.nan,
    }


def top_cooccurrence_pairs(review_category_long: pd.DataFrame, top_categories: list[str], max_pairs: int = 5) -> list[dict]:
    """Pairwise co-occurrence across the top-effect-size categories -- do
    complaints about one thing tend to also mention another."""
    rows = []
    for i in range(len(top_categories)):
        for j in range(i + 1, len(top_categories)):
            a, b = top_categories[i], top_categories[j]
            cooc = cooccurrence(review_category_long, a, b)
            if cooc["n_overlap"] > 0:
                rows.append({"category_a": a, "category_b": b, **cooc})
    rows.sort(key=lambda r: r["pct_of_a_also_b"], reverse=True)
    return rows[:max_pairs]


@dataclass
class ReportData:
    app_title: str
    app_icon_url: str
    package_id: str
    requested_count: int
    actual_review_count: int
    negative_review_count: int
    n_a: int
    n_b: int
    cohort_a_range: tuple
    cohort_b_range: tuple
    ranked_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    quotes: dict = field(default_factory=dict)
    cooccurrence_pairs: list = field(default_factory=list)
    keyword_labels: dict = field(default_factory=dict)
    neutral_review_count: int = 0
    positive_review_count: int = 0
    rating_distribution: dict = field(default_factory=dict)
    insufficient_data: bool = False
    insufficient_reason: str = ""

    def _display_name(self, category_id: str) -> str:
        return self.keyword_labels.get(category_id, category_id)

    def _prepared_rows(self) -> list[dict]:
        """Common per-row display values (escaped, percentage-scaled) shared
        by both the in-app HTML report and the email HTML -- only the markup
        wrapping them differs between the two."""
        rows = []
        for i, (_, r) in enumerate(self.ranked_table.iterrows(), start=1):
            rate_a_pct, rate_b_pct = r["rate_a"] * 100, r["rate_b"] * 100
            bar_max = max(rate_a_pct, rate_b_pct, 1)
            zone = r["zone"]
            rows.append(
                {
                    "rank": i,
                    "category_id": r["category"],
                    "name": html_mod.escape(self._display_name(r["category"])),
                    "keywords": html_mod.escape(r["keywords"]),
                    "keywords_list": [html_mod.escape(k) for k in r["keywords"].split(", ") if k],
                    "keywords_raw": [k for k in r["keywords"].split(", ") if k],
                    "rate_a_pct": rate_a_pct,
                    "rate_b_pct": rate_b_pct,
                    "bar_a_width": rate_a_pct / bar_max * 100,
                    "bar_b_width": rate_b_pct / bar_max * 100,
                    "RR": r["RR"],
                    "zone": zone,
                    "zone_class": zone.lower().replace(" ", "-"),
                    "significant": bool(r["significant"]),
                    "cohens_h": r["cohens_h"],
                    "p_value": r["p_value"],
                    "p_value_adjusted": r["p_value_adjusted"],
                    "sample_floor_ok": bool(r["sample_floor_ok"]),
                    "robust": r["robust"],  # True / False / None (not tested)
                }
            )
        return rows

    @staticmethod
    def _highlight_quote(quote: str, keywords_raw: list[str]) -> str:
        """Escapes the quote, then bolds whichever of the category's own
        keyword phrases actually appear in it -- a single regex pass over
        the escaped text (longest phrase first) so overlapping matches
        (e.g. "delivery" inside "delivery partner") can't double-wrap."""
        escaped = html_mod.escape(quote)
        terms = sorted({html_mod.escape(k) for k in keywords_raw if k.strip()}, key=len, reverse=True)
        if not terms:
            return escaped
        pattern = "|".join(re.escape(t) for t in terms)
        return re.sub(rf"(?i)\b({pattern})\b", r'<mark class="kw-hl">\1</mark>', escaped)

    @staticmethod
    def _rank_badge_html(row: dict) -> str:
        """A small numbered link from a card/spotlight to its row in the
        effect-size-vs-significance table -- h and p live there now, in a
        comparable view across every category, instead of crammed into a
        one-off text tag on each card."""
        return f'<a href="#es-row-{row["rank"]:02d}" class="rank-badge" title="See stats for #{row["rank"]:02d}">#{row["rank"]:02d}</a>'

    @staticmethod
    def _fmt_range(rng) -> str:
        start, end = rng
        if start is None or end is None:
            return "n/a"
        fmt = lambda d: pd.Timestamp(d).strftime("%b %d, %Y")
        return f"{fmt(start)} - {fmt(end)}"

    # ------------------------------------------------------------ in-app UI
    def to_html_report(self) -> str:
        """Class-based HTML (no inline styles) for the in-app instant path
        -- styled by the page's own stylesheet, see webapp/theme.css. Leads
        with a scannable overview (stat strip + zone donut + a ranked shift
        chart) before the per-category detail, rather than dropping the
        reader straight into a dense table."""
        if self.insufficient_data:
            return (
                '<div class="report-empty">'
                '<p class="report-empty-title">Not enough data</p>'
                f'<p class="report-empty-body">{html_mod.escape(self.insufficient_reason)}</p>'
                "</div>"
            )

        header = f"""
        <div class="report-header">
          <p class="report-meta">
            Older half: <strong>{self.n_a}</strong> reviews ({html_mod.escape(self._fmt_range(self.cohort_a_range))}).
            Recent half: <strong>{self.n_b}</strong> reviews ({html_mod.escape(self._fmt_range(self.cohort_b_range))}).
          </p>
        </div>
        """

        if self.ranked_table.empty:
            return header + '<p class="report-empty-body">No complaint categories cleared the discovery threshold.</p>'

        rows = self._prepared_rows()
        top_row = rows[0]
        return (
            header
            + self._headline_html(top_row)
            + self._spotlight_html(top_row)
            + self._overview_html(rows)
            + self._composition_html()
            + self._impact_chart_html(rows)
            + self._confidence_html(rows)
            + self._cards_html(rows)
            + self._cooccurrence_html_web()
            + self._method_html()
        )

    def _headline_html(self, top_row: dict) -> str:
        """A one-line natural-language lead, the way an analyst opens a
        report with the finding rather than the table -- generated from the
        #1 ranked row (already sorted by |Cohen's h|), not a separate model
        call, so it can't say anything the data doesn't back."""
        delta = top_row["rate_b_pct"] - top_row["rate_a_pct"]
        verb = "risen" if delta >= 0 else "fallen"
        tail = (
            "the largest shift found, and it holds up after correcting for multiple comparisons."
            if top_row["significant"]
            else "the largest shift found, though not yet statistically significant at this sample size."
        )
        return (
            '<p class="headline">'
            f'<strong>{top_row["name"]}</strong> complaints have {verb} <strong>{abs(delta):.1f} points</strong> '
            f'({top_row["rate_a_pct"]:.1f}% to {top_row["rate_b_pct"]:.1f}% of negative reviews) between the two halves. '
            f"That's {tail}"
            "</p>"
        )

    def _spotlight_html(self, top_row: dict) -> str:
        chips = "".join(f'<span class="chip">{kw}</span>' for kw in top_row["keywords_list"])

        rank_html = self._rank_badge_html(top_row)
        robust_html = ""
        if top_row["robust"] is True:
            robust_html = '<span class="stat-tag stat-tag-robust">version-robust</span>'
        elif top_row["robust"] is False:
            robust_html = '<span class="stat-tag stat-tag-fragile">not version-robust</span>'

        examples = self.quotes.get(top_row["category_id"])
        quote_html = ""
        if examples:
            quote_html = f'<blockquote class="quote spotlight-quote">{self._highlight_quote(examples[0], top_row["keywords_raw"])}</blockquote>'

        return f"""
        <div class="report-section">
          <div class="spotlight">
            <span class="spotlight-eyebrow">Biggest mover</span>
            <div class="spotlight-body">
              <div class="spotlight-main">
                <div class="spotlight-head">
                  <div class="spotlight-name">{top_row['name']}</div>
                  <span class="zone-badge zone-{top_row['zone_class']}">{top_row['zone']}</span>
                </div>
                <div class="spotlight-chips">{chips}</div>
                <div class="cat-card-bars">
                  <div class="bar-row"><span class="bar-tag">older</span><div class="bar-track"><div class="bar-fill bar-a" style="width:{top_row['bar_a_width']:.0f}%"></div></div><span class="bar-val">{top_row['rate_a_pct']:.1f}%</span></div>
                  <div class="bar-row"><span class="bar-tag">recent</span><div class="bar-track"><div class="bar-fill bar-b" style="width:{top_row['bar_b_width']:.0f}%"></div></div><span class="bar-val">{top_row['rate_b_pct']:.1f}%</span></div>
                </div>
                <div class="cat-card-foot"><span class="rr-tag">{top_row['RR']:.2f}&times; rate ratio</span>{robust_html}{rank_html}</div>
              </div>
              {quote_html}
            </div>
          </div>
        </div>
        """

    def _overview_html(self, rows: list[dict]) -> str:
        n_categories = len(rows)
        n_priority = sum(1 for r in rows if r["zone"] == "Priority")
        n_watch = sum(1 for r in rows if r["zone"] == "Watch")
        n_no_action = n_categories - n_priority - n_watch

        kpi_html = f"""
        <div class="kpi-strip">
          <div class="kpi"><div class="kpi-val">{self.actual_review_count:,}</div><div class="kpi-lbl">Reviews analyzed</div></div>
          <div class="kpi"><div class="kpi-val">{self.negative_review_count:,}</div><div class="kpi-lbl">Negative reviews</div></div>
          <div class="kpi"><div class="kpi-val">{n_categories}</div><div class="kpi-lbl">Categories found</div></div>
          <div class="kpi"><div class="kpi-val kpi-accent">{n_priority}</div><div class="kpi-lbl">Flagged priority</div></div>
        </div>
        """

        total = max(n_categories, 1)
        p1 = n_priority / total * 100
        p2 = p1 + (n_watch / total * 100)
        donut_html = f"""
        <div class="donut-block">
          <div class="zone-donut" style="background: conic-gradient(var(--success) 0% {p1:.2f}%, var(--warn) {p1:.2f}% {p2:.2f}%, var(--muted-badge) {p2:.2f}% 100%);">
            <div class="zone-donut-hole"><span class="zone-donut-num">{n_categories}</span><span class="zone-donut-lbl">categories</span></div>
          </div>
          <div class="donut-legend">
            <div class="legend-row"><span class="legend-dot" style="background:var(--success)"></span>Priority<b>{n_priority}</b></div>
            <div class="legend-row"><span class="legend-dot" style="background:var(--warn)"></span>Watch<b>{n_watch}</b></div>
            <div class="legend-row"><span class="legend-dot" style="background:var(--muted-badge)"></span>No action<b>{n_no_action}</b></div>
          </div>
        </div>
        """

        return f'<div class="report-section"><div class="overview-grid">{kpi_html}{donut_html}</div></div>'

    def _composition_html(self) -> str:
        """Full sample composition (all bands, not just Negative) -- the
        pipeline computes this while banding reviews regardless of scope, so
        it costs nothing extra to show, and it's useful context: a category
        shift only in the Negative band, seen against how big that band is
        relative to Neutral/Positive, is a different story than one where
        Negative dominates."""
        neg, neu, pos = self.negative_review_count, self.neutral_review_count, self.positive_review_count
        total = max(neg + neu + pos, 1)
        mix_html = f"""
        <div class="mix-card">
          <div class="mix-bar">
            <div class="mix-seg mix-neg" style="width:{neg / total * 100:.2f}%"></div>
            <div class="mix-seg mix-neu" style="width:{neu / total * 100:.2f}%"></div>
            <div class="mix-seg mix-pos" style="width:{pos / total * 100:.2f}%"></div>
          </div>
          <div class="mix-legend">
            <span><i class="legend-dot mix-dot-neg"></i>Negative<b>{neg:,}</b></span>
            <span><i class="legend-dot mix-dot-neu"></i>Neutral<b>{neu:,}</b></span>
            <span><i class="legend-dot mix-dot-pos"></i>Positive<b>{pos:,}</b></span>
          </div>
        </div>
        """

        max_count = max(self.rating_distribution.values(), default=1) or 1
        rating_rows = ""
        for star in range(5, 0, -1):
            count = self.rating_distribution.get(star, 0)
            width = count / max_count * 100
            rating_rows += f"""
            <div class="rating-row">
              <span class="rating-label">{star}&#9733;</span>
              <div class="bar-track"><div class="bar-fill rating-fill" style="width:{width:.0f}%"></div></div>
              <span class="rating-count">{count:,}</span>
            </div>
            """
        rating_html = f'<div class="rating-hist">{rating_rows}</div>'

        return (
            '<div class="report-section">'
            '<h3 class="section-title">Sample composition &middot; all bands</h3>'
            f'<div class="composition-grid">{mix_html}{rating_html}</div>'
            "</div>"
        )

    def _method_html(self) -> str:
        return f"""
        <div class="report-section">
          <h3 class="section-title">Method</h3>
          <p class="method-note">
            Complaint categories are discovered directly from review text (embedding + clustering),
            not matched against a fixed keyword list. Each category's share of the Negative band is
            compared between the two cohorts with a two-proportion z-test, using Cohen's h as the
            effect size and Benjamini-Hochberg correction across every category tested at once
            (&alpha;={config.ALPHA}). A category needs at least {config.SAMPLE_FLOOR_MIN_N} reviews in
            <em>each</em> cohort before a p-value is reported at all. <b>Priority</b> additionally
            requires the shift to hold up after reweighting for app-version mix, not significance alone.
          </p>
        </div>
        """

    def _impact_chart_html(self, rows: list[dict]) -> str:
        """Diverging bar chart centered on a zero line: bars grow left for a
        rate that fell (better) and right for a rate that rose (worse),
        colored by that direction -- not by zone, which is an orthogonal
        concept (a category can be a large improvement or a large
        regression and still land in the same zone). A same-color bar
        pointing the same way regardless of sign was the previous bug."""
        magnitudes = [abs(r["rate_b_pct"] - r["rate_a_pct"]) for r in rows]
        max_mag = max(magnitudes) or 1
        impact_rows = ""
        for i, r in enumerate(rows, start=1):
            delta = r["rate_b_pct"] - r["rate_a_pct"]
            pct = abs(delta) / max_mag * 100
            worse = delta >= 0
            sign = "+" if worse else "−"
            color_class = "impact-fill-worse" if worse else "impact-fill-better"
            left_fill = f'<div class="impact-fill {color_class}" style="width:{pct:.0f}%"></div>' if not worse else ""
            right_fill = f'<div class="impact-fill {color_class}" style="width:{pct:.0f}%"></div>' if worse else ""
            sig_dot = '<span class="sig-dot" title="Statistically significant after correction"></span>' if r["significant"] else ""
            impact_rows += f"""
            <div class="impact-row">
              <span class="impact-rank">{i:02d}</span>
              <span class="impact-name">{r['name']}{sig_dot}</span>
              <div class="impact-track">
                <div class="impact-half impact-half-left">{left_fill}</div>
                <div class="impact-zero"></div>
                <div class="impact-half impact-half-right">{right_fill}</div>
              </div>
              <span class="impact-val {'impact-val-worse' if worse else 'impact-val-better'}">{sign}{abs(delta):.1f}pp</span>
            </div>
            """
        return (
            '<div class="report-section">'
            '<h3 class="section-title">Shift by category &middot; older half &rarr; recent half</h3>'
            '<div class="impact-legend"><span><i class="impact-swatch impact-fill-worse"></i>Rate went up</span>'
            '<span><i class="impact-swatch impact-fill-better"></i>Rate went down</span></div>'
            f'<div class="impact-chart">{impact_rows}</div>'
            "</div>"
        )

    @staticmethod
    def _effect_magnitude_class(abs_h: float) -> str:
        """Cohen's own convention treats h >= 0.5 as a medium-or-larger
        effect. A single clean threshold reads better than a four-step
        opacity/weight/size ramp (small-medium-large-negligible all in one
        column looked like inconsistent styling, not a signal) -- one bold,
        colored state for "notable," one calm, muted state otherwise."""
        return "mag-notable" if abs_h >= 0.5 else "mag-modest"

    def _confidence_html(self, rows: list[dict]) -> str:
        """A real table -- the compact, scannable, column-aligned format a
        reader actually wants for comparing many rows of numbers -- but with
        the two numbers that matter most (effect size, confidence) carrying
        their own visual weight instead of sitting as flat text: an in-cell
        bar for confidence (capped at the significance threshold, so
        clearing it is literally a bar reaching a line), and bolder/more
        saturated text for Cohen's h as its magnitude crosses Cohen's own
        small/medium/large thresholds. Significant rows get a left-edge
        accent stripe so the ones worth trusting are findable at a glance."""
        testable = [r for r in rows if r["sample_floor_ok"]]
        untested = [r for r in rows if not r["sample_floor_ok"]]

        intro = (
            '<p class="es-intro">How big each shift was, and how confident the test is that it\'s not just '
            "noise. The confidence bar is capped at the dashed line -- the significance threshold -- so a bar "
            "that doesn't reach it is a shift worth watching, not yet one worth trusting.</p>"
        )

        if not testable:
            body = (
                f'<p class="report-empty-body">No category had at least {config.SAMPLE_FLOOR_MIN_N} reviews '
                f"in both cohorts, so none could be tested for significance ({len(rows)} categories total).</p>"
            )
        else:
            threshold_pct = (1 - config.ALPHA) * 100
            table_rows = ""
            for i, r in enumerate(testable):
                confidence_pct = (1 - r["p_value_adjusted"]) * 100
                direction_class = "h-worse" if r["cohens_h"] < 0 else "h-better"
                magnitude_class = self._effect_magnitude_class(abs(r["cohens_h"]))
                sig = r["significant"]
                verdict_class = "conf-verdict-yes" if sig else "conf-verdict-no"
                verdict_icon = "&check;" if sig else "&ndash;"
                verdict_text = "Significant" if sig else "Not yet"
                zebra_class = " conf-tr-odd" if i % 2 else ""

                table_rows += f"""
                <tr class="conf-tr{zebra_class}{' conf-tr-sig' if sig else ''}" id="es-row-{r['rank']:02d}">
                  <td class="conf-rank">{r['rank']:02d}</td>
                  <td class="conf-cat">{r['name']}</td>
                  <td class="conf-h {direction_class} {magnitude_class}">{r['cohens_h']:+.2f}</td>
                  <td class="conf-bar-cell">
                    <div class="mini-bar-track">
                      <div class="mini-bar-fill {'mini-bar-sig' if sig else 'mini-bar-unsig'}" style="width:{confidence_pct:.0f}%"></div>
                      <div class="mini-bar-threshold" style="left:{threshold_pct:.0f}%" title="{config.ALPHA * 100:.0f}% significance threshold"></div>
                    </div>
                    <span class="mini-bar-val">{confidence_pct:.0f}%</span>
                  </td>
                  <td class="conf-p">{r['p_value_adjusted']:.3f}</td>
                  <td class="conf-verdict-cell"><span class="conf-verdict {verdict_class}"><span class="conf-verdict-icon">{verdict_icon}</span>{verdict_text}</span></td>
                </tr>
                """

            body = f"""
            <div class="conf-table-wrap">
              <table class="conf-table">
                <thead>
                  <tr>
                    <th class="conf-th-rank">#</th>
                    <th class="conf-th-cat">Category</th>
                    <th class="conf-th-num">Effect (h)</th>
                    <th class="conf-th-bar">Confidence</th>
                    <th class="conf-th-num">Adj. p</th>
                    <th class="conf-th-center">Verdict</th>
                  </tr>
                </thead>
                <tbody>{table_rows}</tbody>
              </table>
            </div>
            """

        if untested:
            names = ", ".join(r["name"] for r in untested)
            plural = "y" if len(untested) == 1 else "ies"
            body += f'<p class="conf-untested-note">Also found, but too few reviews in one or both cohorts to test: {names} ({len(untested)} categor{plural}).</p>'

        return (
            '<div class="report-section">'
            '<h3 class="section-title">How confident is each finding?</h3>'
            f"{intro}{body}"
            "</div>"
        )

    def _cards_html(self, rows: list[dict]) -> str:
        """Every category, including the one already shown in the spotlight
        above -- excluding it here previously made the filter counts (and
        the grid itself) disagree with the KPI strip and donut, which count
        all of them. Filterable by zone via a CSS-only radio-button trick
        (no JS needed, so it can't silently fail if the host page blocks
        script execution on injected HTML)."""
        if not rows:
            return ""

        n_priority = sum(1 for r in rows if r["zone"] == "Priority")
        n_watch = sum(1 for r in rows if r["zone"] == "Watch")
        n_no_action = len(rows) - n_priority - n_watch

        cards = ""
        for i, r in enumerate(rows):
            chips = "".join(f'<span class="chip">{kw}</span>' for kw in r["keywords_list"])
            sig_dot = '<span class="sig-dot" title="Statistically significant after correction"></span>' if r["significant"] else ""
            examples = self.quotes.get(r["category_id"])
            quote_html = ""
            if examples and i < 3:
                quote_html = f'<blockquote class="quote card-quote">{self._highlight_quote(examples[0], r["keywords_raw"])}</blockquote>'

            robust_html = ""
            if r["robust"] is True:
                robust_html = '<span class="stat-tag stat-tag-robust">version-robust</span>'
            elif r["robust"] is False:
                robust_html = '<span class="stat-tag stat-tag-fragile">not version-robust</span>'

            cards += f"""
            <div class="cat-card" data-zone="{r['zone_class']}">
              <div class="cat-card-head">
                <div class="cat-card-title">
                  <div class="cat-card-name">{r['name']}{sig_dot}</div>
                  <div class="cat-card-chips">{chips}</div>
                </div>
                <span class="zone-badge zone-{r['zone_class']}">{r['zone']}</span>
              </div>
              <div class="cat-card-bars">
                <div class="bar-row"><span class="bar-tag">older</span><div class="bar-track"><div class="bar-fill bar-a" style="width:{r['bar_a_width']:.0f}%"></div></div><span class="bar-val">{r['rate_a_pct']:.1f}%</span></div>
                <div class="bar-row"><span class="bar-tag">recent</span><div class="bar-track"><div class="bar-fill bar-b" style="width:{r['bar_b_width']:.0f}%"></div></div><span class="bar-val">{r['rate_b_pct']:.1f}%</span></div>
              </div>
              <div class="cat-card-foot"><span class="rr-tag">{r['RR']:.2f}&times; rate ratio</span>{robust_html}{self._rank_badge_html(r)}</div>
              {quote_html}
            </div>
            """

        filter_inputs = (
            '<input type="radio" name="zone-filter" id="filter-all" class="filter-radio" checked>'
            '<input type="radio" name="zone-filter" id="filter-priority" class="filter-radio">'
            '<input type="radio" name="zone-filter" id="filter-watch" class="filter-radio">'
            '<input type="radio" name="zone-filter" id="filter-no-action" class="filter-radio">'
        )
        filter_bar = f"""
        <div class="filter-bar">
          <label for="filter-all" class="filter-chip">All<span class="filter-count">{len(rows)}</span></label>
          <label for="filter-priority" class="filter-chip">Priority<span class="filter-count">{n_priority}</span></label>
          <label for="filter-watch" class="filter-chip">Watch<span class="filter-count">{n_watch}</span></label>
          <label for="filter-no-action" class="filter-chip">No action<span class="filter-count">{n_no_action}</span></label>
        </div>
        """

        return (
            '<div class="report-section">'
            '<h3 class="section-title">Category detail</h3>'
            f'<div class="filter-wrap">{filter_inputs}{filter_bar}<div class="cat-card-grid">{cards}</div></div>'
            "</div>"
        )

    def _cooccurrence_html_web(self) -> str:
        if not self.cooccurrence_pairs:
            return ""
        rows_html = ""
        for pair in self.cooccurrence_pairs:
            a = html_mod.escape(self._display_name(pair["category_a"]))
            b = html_mod.escape(self._display_name(pair["category_b"]))
            pct = pair["pct_of_a_also_b"] * 100
            rows_html += (
                '<div class="cooc-row">'
                f'<span class="chip chip-outline">{a}</span>'
                f'<span class="cooc-link">{pct:.0f}% also mention</span>'
                f'<span class="chip chip-outline">{b}</span>'
                "</div>"
            )
        return f'<div class="report-section"><h3 class="section-title">Complaints that travel together</h3><div class="cooc-grid">{rows_html}</div></div>'

    # ------------------------------------------------------------- email/csv
    def to_csv_bytes(self) -> bytes:
        table = self.ranked_table.copy()
        if not table.empty:
            table["category"] = table["category"].apply(self._display_name)
        buf = StringIO()
        table.to_csv(buf, index=False)
        return buf.getvalue().encode("utf-8")

    def to_html_email(self) -> tuple[str, str]:
        """Returns (subject, html_body). Fully inline-styled -- email clients
        don't reliably load external/embedded stylesheets."""
        subject = f"Review diagnostic ready: {self.app_title}"

        if self.insufficient_data:
            body = f"""
            <div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:24px;">
              <h2 style="color:#1C1C1C;">{html_mod.escape(self.app_title)}</h2>
              <p style="color:#B4472A;">Not enough data to run a diagnostic.</p>
              <p style="color:#55503F;">{html_mod.escape(self.insufficient_reason)}</p>
            </div>
            """
            return subject, body

        zone_colors = {"priority": "#B0771A", "watch": "#1F5C99", "no-action": "#8A8270"}
        rows_html = ""
        for r in self._prepared_rows():
            zone_color = zone_colors.get(r["zone_class"], "#8A8270")
            rows_html += f"""
            <tr>
              <td style="padding:8px;border-bottom:1px solid #EAE3D2;">
                <div style="font-weight:600;color:#1C1C1C;">{r['name']}</div>
                <div style="font-size:11px;color:#8A8270;">{r['keywords']}</div>
              </td>
              <td style="padding:8px;border-bottom:1px solid #EAE3D2;">
                <div style="height:8px;background:#1F5C99;width:{r['bar_a_width'] * 0.6:.0f}px;border-radius:2px;margin-bottom:3px;"></div>
                <div style="height:8px;background:#B0771A;width:{r['bar_b_width'] * 0.6:.0f}px;border-radius:2px;"></div>
              </td>
              <td style="padding:8px;border-bottom:1px solid #EAE3D2;text-align:right;font-family:monospace;">{r['rate_a_pct']:.1f}% &rarr; {r['rate_b_pct']:.1f}%</td>
              <td style="padding:8px;border-bottom:1px solid #EAE3D2;text-align:right;font-family:monospace;">{r['RR']:.2f}x</td>
              <td style="padding:8px;border-bottom:1px solid #EAE3D2;text-align:center;">
                <span style="background:{zone_color};color:white;padding:2px 8px;border-radius:3px;font-size:11px;">{r['zone']}</span>
              </td>
            </tr>
            """

        quotes_html = ""
        if self.quotes:
            quotes_html = "<h3 style='color:#1C1C1C;margin-top:24px;'>Representative quotes</h3>"
            for cat, examples in self.quotes.items():
                quotes_html += f"<p style='margin:4px 0;font-weight:600;color:#1C1C1C;'>{html_mod.escape(self._display_name(cat))}</p>"
                for ex in examples:
                    quotes_html += f"<p style='margin:2px 0 8px 12px;color:#55503F;font-style:italic;border-left:2px solid #B0771A;padding-left:8px;'>{html_mod.escape(ex)}</p>"

        cooc_html = ""
        if self.cooccurrence_pairs:
            cooc_html = "<h3 style='color:#1C1C1C;margin-top:24px;'>Complaints that travel together</h3><ul style='color:#55503F;font-size:13px;'>"
            for pair in self.cooccurrence_pairs:
                a = html_mod.escape(self._display_name(pair["category_a"]))
                b = html_mod.escape(self._display_name(pair["category_b"]))
                pct = pair["pct_of_a_also_b"] * 100
                cooc_html += f"<li><b>{pct:.0f}%</b> of reviews mentioning {a} also mention {b}</li>"
            cooc_html += "</ul>"

        body = f"""
        <div style="font-family:Arial,sans-serif;max-width:680px;margin:0 auto;padding:24px;color:#1C1C1C;">
          <h2 style="margin-bottom:4px;">{html_mod.escape(self.app_title)}</h2>
          <p style="color:#55503F;font-size:13px;">
            {self.actual_review_count:,} reviews scraped ({self.negative_review_count:,} negative).
            Older half: {self.n_a} reviews ({html_mod.escape(self._fmt_range(self.cohort_a_range))}).
            Recent half: {self.n_b} reviews ({html_mod.escape(self._fmt_range(self.cohort_b_range))}).
          </p>
          <table style="width:100%;border-collapse:collapse;margin-top:16px;">
            <thead>
              <tr style="text-align:left;font-size:11px;text-transform:uppercase;color:#8A8270;">
                <th style="padding:8px;border-bottom:1px solid #DDD4BE;">Category</th>
                <th style="padding:8px;border-bottom:1px solid #DDD4BE;">Older &rarr; Recent</th>
                <th style="padding:8px;border-bottom:1px solid #DDD4BE;text-align:right;">Rate</th>
                <th style="padding:8px;border-bottom:1px solid #DDD4BE;text-align:right;">Rate ratio</th>
                <th style="padding:8px;border-bottom:1px solid #DDD4BE;text-align:center;">Verdict</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
          {quotes_html}
          {cooc_html}
          <p style="color:#8A8270;font-size:11px;margin-top:24px;border-top:1px solid #EAE3D2;padding-top:8px;">
            Full ranked table attached as CSV. Generated automatically -- no data was stored.
          </p>
        </div>
        """
        return subject, body
