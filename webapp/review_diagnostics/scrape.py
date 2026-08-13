"""Play Store scraping. Ported from data_prep.py, generalized for any app."""

import re
import time

import pandas as pd

from . import config


def resolve_package_id(app_input: str) -> str:
    """Accepts either a bare package id ('com.example.app') or a full Play
    Store URL and returns the package id."""
    app_input = app_input.strip()
    match = re.search(r"[?&]id=([^&]+)", app_input)
    if match:
        return match.group(1)
    return app_input


def scrape_app_info(package_name: str) -> dict:
    """Fetch app metadata (title, icon, rating, install count)."""
    from google_play_scraper import app as gp_app

    return gp_app(package_name, lang=config.SCRAPE_LANG, country=config.SCRAPE_COUNTRY)


def scrape_app_reviews(package_name: str, review_count: int) -> pd.DataFrame:
    """Scrape up to review_count newest reviews. Retries with exponential
    backoff on transient failures; raises after exhausting retries so the
    caller can report a specific failure rather than hang silently."""
    from google_play_scraper import Sort, reviews as gp_reviews

    last_err = None
    for attempt in range(config.SCRAPE_MAX_RETRIES):
        try:
            result, _ = gp_reviews(
                package_name,
                lang=config.SCRAPE_LANG,
                country=config.SCRAPE_COUNTRY,
                sort=Sort.NEWEST,
                count=review_count,
            )
            df = pd.DataFrame(result).reset_index(drop=True)
            df["review_id"] = df.index
            return df
        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            time.sleep(wait)
    raise RuntimeError(f"Scraping {package_name} failed after {config.SCRAPE_MAX_RETRIES} attempts") from last_err


def clean_text(text) -> str:
    text = str(text)
    text = re.sub(r"http\S+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
