"""Clause-level snippet splitting. Ported verbatim from data_prep.py."""

import pandas as pd

from . import config


def split_into_snippets(df_reviews: pd.DataFrame, min_words: int = config.MIN_SNIPPET_WORDS) -> pd.DataFrame:
    """Sentence-splits each review's cleaned text, keeping review_id/rating/
    date/app_version attached to every resulting snippet, filtering out
    fragments shorter than min_words."""
    import nltk
    from nltk.tokenize import sent_tokenize

    for pkg in ("punkt", "punkt_tab"):
        try:
            nltk.data.find(f"tokenizers/{pkg}")
        except LookupError:
            nltk.download(pkg)

    rows = []
    for _, row in df_reviews.iterrows():
        for sent in sent_tokenize(str(row["clean_review"])):
            sent = sent.strip()
            if len(sent.split()) >= min_words:
                rows.append(
                    {
                        "review_id": row["review_id"],
                        "snippet": sent,
                        "rating": row["score"],
                        "date": row["at"],
                        "app_version": row["reviewCreatedVersion"],
                    }
                )
    return pd.DataFrame(rows)
