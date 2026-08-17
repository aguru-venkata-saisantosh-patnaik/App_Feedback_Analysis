"""Gradio Blocks UI. Routes <=200 reviews to a synchronous, in-browser run;
201-1,000 to a background job delivered by email. Owns the single-worker
job queue and the keep-alive self-ping meant to stop a free host from
sleeping mid-job (see the plan's disclosed residual risk: this reduces but
doesn't eliminate that risk). Host-agnostic on purpose -- built for HF
Spaces originally, but HF's free tier turned out to require a PRO
subscription for Gradio Spaces, so this also runs as a plain Render.com
web service; the self-ping and launch binding branch on whichever
platform's env vars are actually present."""

import html
import os
import re
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

# Bounds huggingface_hub's per-request download timeout -- fastembed's model
# fetch goes through this on first use, and a stalled response (same failure
# mode as the google-play-scraper hang scrape.py works around) would
# otherwise hang a live request instead of failing fast. Set before any
# huggingface_hub/fastembed import so it's read at import time.
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

import gradio as gr
import requests

from review_diagnostics import config, email_delivery, pipeline, scrape

MAX_QUEUE_DEPTH = 3
RATE_LIMIT_WINDOW_SECONDS = 3600
RATE_LIMIT_MAX_PER_EMAIL = 3
KEEPALIVE_INTERVAL_SECONDS = 240

THEME_CSS = (Path(__file__).parent / "theme.css").read_text()

_executor = ThreadPoolExecutor(max_workers=1)
_active_jobs = 0
_active_jobs_lock = threading.Lock()
_email_submissions = defaultdict(list)
_email_lock = threading.Lock()
_keepalive_thread: Optional[threading.Thread] = None
_keepalive_lock = threading.Lock()


def _space_url() -> Optional[str]:
    host = os.environ.get("SPACE_HOST")
    if host:
        return f"https://{host}"
    return os.environ.get("RENDER_EXTERNAL_URL")


def _keepalive_loop():
    url = _space_url()
    while True:
        with _active_jobs_lock:
            still_active = _active_jobs > 0
        if not still_active:
            return
        if url:
            try:
                requests.get(url, timeout=10)
            except Exception:
                pass
        time.sleep(KEEPALIVE_INTERVAL_SECONDS)


def _ensure_keepalive_running():
    global _keepalive_thread
    with _keepalive_lock:
        if _keepalive_thread is None or not _keepalive_thread.is_alive():
            _keepalive_thread = threading.Thread(target=_keepalive_loop, daemon=True)
            _keepalive_thread.start()


def _rate_limit_ok(email: str) -> bool:
    now = time.time()
    with _email_lock:
        recent = [t for t in _email_submissions[email] if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if len(recent) >= RATE_LIMIT_MAX_PER_EMAIL:
            _email_submissions[email] = recent
            return False
        recent.append(now)
        _email_submissions[email] = recent
        return True


def _valid_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()))


def _error_card(message: str) -> str:
    return f'<div class="app-card app-card-error">{html.escape(message)}</div>'


def _write_temp_csv(data, package_id: str) -> str:
    """A transient OS-temp file purely so gr.File has a path to serve --
    not persistence in the sense the product ruled out (nothing server-side
    keeps track of it, and it's gone with the container). Uniqueness lives
    in the directory name (mkdtemp), not the filename, so the displayed
    download name stays clean instead of carrying a random hash suffix."""
    directory = tempfile.mkdtemp(prefix=f"{package_id.replace('.', '_')}_")
    path = os.path.join(directory, "ranked_categories.csv")
    with open(path, "wb") as f:
        f.write(data.to_csv_bytes())
    return path


# ---------------------------------------------------------------- lookup step

def lookup_app(app_input: str):
    if not app_input.strip():
        return "", None, gr.update(visible=False), gr.update(visible=False)
    package_id = scrape.resolve_package_id(app_input)
    try:
        info = scrape.scrape_app_info(package_id)
    except Exception:
        return (
            _error_card(f"Couldn't find an app at “{app_input.strip()}”. Check the URL or package ID."),
            None, gr.update(visible=False), gr.update(visible=False),
        )

    title = html.escape(info.get("title", package_id))
    icon = html.escape(info.get("icon", ""))
    score = info.get("score")
    score_str = f"{score:.1f}" if isinstance(score, (int, float)) else "?"
    installs = html.escape(str(info.get("installs", "?")))

    card = f"""
    <div class="app-card">
      <img class="app-icon" src="{icon}" alt="" onerror="this.style.visibility='hidden'" />
      <div class="app-meta">
        <div class="app-name">{title}</div>
        <div class="app-stats">{score_str} rating &nbsp;&middot;&nbsp; {installs} installs</div>
        <code class="app-pkg">{html.escape(package_id)}</code>
      </div>
    </div>
    """
    return card, package_id, gr.update(visible=True), gr.update(visible=True)


def on_count_change(count):
    return gr.update(visible=count > config.INSTANT_MAX_REVIEWS)


# ---------------------------------------------------------------- run step

def run_diagnostic(package_id, review_count, email_addr):
    global _active_jobs

    if not package_id:
        return "Look up an app first.", "", gr.update(visible=False)

    review_count = int(review_count)

    if review_count <= config.INSTANT_MAX_REVIEWS:
        try:
            data = pipeline.run(package_id, review_count)
        except Exception as e:
            return f"Run failed: {e}", "", gr.update(visible=False)
        if data.insufficient_data or data.ranked_table.empty:
            return "", data.to_html_report(), gr.update(visible=False)
        csv_path = _write_temp_csv(data, package_id)
        return "", data.to_html_report(), gr.update(value=csv_path, visible=True)

    email_addr = (email_addr or "").strip()
    if not _valid_email(email_addr):
        return "Enter a valid email address to run more than 200 reviews.", "", gr.update(visible=False)
    if not _rate_limit_ok(email_addr):
        return "Too many runs requested from this email recently. Please try again later.", "", gr.update(visible=False)

    with _active_jobs_lock:
        if _active_jobs >= MAX_QUEUE_DEPTH:
            return "The queue is busy right now. Please try again in a few minutes.", "", gr.update(visible=False)
        _active_jobs += 1

    eta_minutes = max(2, review_count // 150)
    try:
        email_delivery.send_started_notice(email_addr, package_id, eta_minutes)
    except Exception:
        pass  # best-effort; the job still runs and the report email is what matters most

    _ensure_keepalive_running()
    _executor.submit(_run_async_job, package_id, review_count, email_addr)

    msg = (
        f"Started. You'll get an email at **{email_addr}** in about {eta_minutes} minute(s) "
        f"-- you can close this tab."
    )
    return msg, "", gr.update(visible=False)


def _run_async_job(package_id: str, review_count: int, email_addr: str):
    global _active_jobs
    try:
        data = pipeline.run(package_id, review_count)
        email_delivery.send_report(email_addr, data)
    except Exception as e:
        try:
            email_delivery.send_failure_notice(email_addr, package_id, str(e))
        except Exception:
            pass
    finally:
        with _active_jobs_lock:
            _active_jobs -= 1


# ---------------------------------------------------------------- layout

FORCE_LIGHT_JS = """
() => {
    const strip = () => document.body.classList.remove('dark');
    strip();
    new MutationObserver(strip).observe(document.body, { attributes: true, attributeFilter: ['class'] });
}
"""

HEADER_MARK = """
<svg class="header-mark" width="32" height="32" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="0.5" y="0.5" width="31" height="31" rx="9" style="fill:var(--accent)"/>
  <rect x="8" y="17" width="4" height="8" rx="1.2" style="fill:#FFFFFF"/>
  <rect x="14" y="12" width="4" height="13" rx="1.2" style="fill:#FFFFFF"/>
  <rect x="20" y="7" width="4" height="18" rx="1.2" style="fill:#FFFFFF"/>
</svg>
"""

with gr.Blocks(title="Review Diagnostic", css=THEME_CSS, theme=gr.themes.Base()) as demo:
    demo.load(None, None, None, js=FORCE_LIGHT_JS)
    gr.HTML(
        f'<div class="header-row">{HEADER_MARK}<h1 class="header-title">Review Diagnostic</h1></div>'
        "<p>Paste a Play Store app and see what's actually driving negative reviews, and whether "
        "it's gotten better or worse lately.</p>",
        elem_id="app-header",
    )

    package_id_state = gr.State(None)

    gr.HTML('<div class="step-label"><span class="step-num">1</span>Find your app</div>')
    with gr.Row():
        app_input = gr.Textbox(
            label="Play Store URL or package ID", placeholder="com.example.app or a Play Store link",
            lines=1, elem_id="app-input-box", scale=4, container=True,
        )
        lookup_btn = gr.Button("Look up app", elem_id="lookup-btn", scale=1)

    app_card = gr.HTML()

    step2_label = gr.HTML('<div class="step-label"><span class="step-num">2</span>Configure &amp; run</div>', visible=False)
    with gr.Group(visible=False) as run_group:
        review_count = gr.Slider(
            minimum=config.MIN_VIABLE_REVIEWS, maximum=config.ASYNC_MAX_REVIEWS, step=10, value=150,
            label=f"How many reviews to analyze (≤{config.INSTANT_MAX_REVIEWS} = instant, more = emailed)",
        )
        email = gr.Textbox(label="Email (required for more than 200 reviews)", visible=False)
        run_btn = gr.Button("Run diagnostic", variant="primary", elem_id="run-btn")

    status = gr.Markdown(elem_id="status-box")
    download_file = gr.File(label="Ranked categories (CSV)", visible=False, elem_id="download-file")
    report_html = gr.HTML()

    gr.Markdown(
        "Nothing is stored -- each run is stateless. Reviews are read in English, India store only.",
        elem_id="footnote",
    )

    lookup_btn.click(lookup_app, inputs=[app_input], outputs=[app_card, package_id_state, run_group, step2_label])
    review_count.change(on_count_change, inputs=[review_count], outputs=[email])
    run_btn.click(
        run_diagnostic,
        inputs=[package_id_state, review_count, email],
        outputs=[status, report_html, download_file],
    )

def _prewarm_embedding_model():
    """Forces the fastembed model download/cache to happen once at boot,
    with output visible in deploy logs, instead of silently blocking the
    first real user's request (and, if that first download ever stalls,
    failing during startup where it's obvious rather than inside a
    request where it just looks like a hang)."""
    from fastembed import TextEmbedding

    print(f"Pre-warming embedding model {config.EMBED_MODEL_NAME}...", flush=True)
    TextEmbedding(model_name=config.EMBED_MODEL_NAME, providers=config.EMBED_PROVIDERS, threads=1)
    print("Embedding model ready.", flush=True)


if __name__ == "__main__":
    _prewarm_embedding_model()
    # 0.0.0.0 (not Gradio's 127.0.0.1 default) so the container's external
    # load balancer -- Render's, or a similar host's -- can actually reach
    # it; PORT is Render's own env var for which port it expects the app to
    # listen on, defaulting to Gradio's usual 7860 for local dev / HF.
    demo.queue().launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", 7860)),
    )
