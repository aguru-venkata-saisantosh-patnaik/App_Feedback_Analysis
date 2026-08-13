"""Shared constants for the review-diagnostic pipeline. No Blinkit-specific
values here — every constant is either a genuine methodology parameter
(validated during the Blinkit run) or a product limit set for this tool."""

# --- Product limits ---
INSTANT_MAX_REVIEWS = 200      # <= this: synchronous, shown in-browser, no email
ASYNC_MAX_REVIEWS = 1000       # hard cap regardless of what's requested
MIN_VIABLE_REVIEWS = 100       # below this many *scraped* reviews, abort before expensive stages
# Only ~15-30% of an app's reviews are typically 1-2 star, so gating the
# Negative-band count at MIN_VIABLE_REVIEWS would reject nearly every demo
# run at the low end of the instant path. Matches 2x SAMPLE_FLOOR_MIN_N
# (both cohorts need to clear that floor) and discovery's own internal
# 2x MIN_MEMBERS_FOR_CALIBRATION snippet floor.
MIN_VIABLE_NEGATIVE_REVIEWS = 30

# --- Scraping ---
SCRAPE_LANG = "en"
SCRAPE_COUNTRY = "in"
SCRAPE_MAX_RETRIES = 3

# --- Snippet splitting ---
MIN_SNIPPET_WORDS = 3

# --- Rating bands --- strict, non-overlapping; every integer 1-5 maps to exactly one.
BAND_NEGATIVE = (1, 2)
BAND_NEUTRAL = (3, 3)
BAND_POSITIVE = (4, 5)
ANALYSIS_BAND = "Negative"  # locked scope: complaint-focused only

# --- Discovery (BERTopic resolution search) ---
# min_cluster_size candidates as a fraction of n (floor of 5, so this still
# works on small demo-sized inputs); min_samples candidates are then
# derived as fractions of each min_cluster_size rather than fixed absolute
# values, so they scale whether n is 85 or 588,832 -- fixed absolute
# min_samples values (the Blinkit run used 10-100) silently produced zero
# valid combinations at small n, since every candidate had min_samples >
# min_cluster_size and got skipped.
MIN_CLUSTER_SIZE_PCTS = [0.02, 0.03, 0.05, 0.075, 0.1]
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
TOPIC_COUNT_RANGE = (4, 25)
MAX_NOISE_PCT = 40

# --- Measurement ---
MIN_MEMBERS_FOR_CALIBRATION = 30

# --- Stats ---
SAMPLE_FLOOR_MIN_N = 15   # lower than the Blinkit run's 30: small-sample demo runs need a lower floor
ALPHA = 0.05
MIN_VERSION_COVERAGE = 0.5

# --- Email ---
RESEND_FROM_ADDRESS = "Review Diagnostic <onboarding@resend.dev>"
