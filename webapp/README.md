# Review Diagnostic

A generalized, self-serve version of this project's review-diagnostic
methodology: paste any Play Store app and get a statistical read on what's
actually driving negative reviews, and whether it's gotten better or worse
recently — no app-specific hardcoding, works on any app.

- **Up to 200 reviews**: instant, rendered in-browser.
- **201–1,000 reviews**: queued as a background job, results emailed (via
  [Resend](https://resend.com)) since the run is too slow to wait on.

Reviews are split into two cohorts by recency (most recent half vs. prior
half), complaint categories are discovered directly from review text
(embedding + clustering, not a fixed keyword list), and each category's share
of the Negative band is compared across cohorts with a two-proportion
z-test, Cohen's h effect size, and Benjamini–Hochberg correction. Nothing is
stored — every run is stateless.

## Running it locally

```bash
pip install -r requirements.txt
python app.py
```

Opens at `http://localhost:7860`. The async/email path needs a `RESEND_API_KEY`
environment variable to actually send mail; without it, the instant path
(≤200 reviews) still works fully, and async jobs will queue and run but fail
silently at the send step.

## Deploying

This app is host-agnostic (checks both `SPACE_HOST` and `RENDER_EXTERNAL_URL`
for its own keep-alive self-ping) and binds to `0.0.0.0:$PORT`. See
[`render.yaml`](../render.yaml) at the repo root for a ready-to-use Render
Blueprint. Hugging Face Spaces also works if you have a PRO subscription
(free-tier Spaces no longer host Gradio/Docker apps as of this writing).

## Layout

```
app.py                      Gradio UI, job queue, keep-alive thread
theme.css                    Design system (light, no external chart libs)
review_diagnostics/
  scrape.py                    Play Store scraping
  snippets.py                   Clause-level snippet splitting
  cohorts.py                     Auto-split into older/recent cohorts
  discovery.py                    Unsupervised category discovery (BERTopic)
  measurement.py                   Assigns every snippet to its category
  stats.py                          z-test, Cohen's h, BH correction
  report.py                         Renders both the in-app HTML and the email
  pipeline.py                        Orchestrates the whole run
  email_delivery.py                  Resend API calls
```
