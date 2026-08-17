"""FastAPI UI. Replaces the earlier Gradio Blocks app: Gradio's SSE-based
queue mechanism didn't reliably deliver results back through Render's
proxy -- live testing proved the backend consistently completed in
well under a second, but the browser's queue/data stream got aborted
before the result rendered, reproducibly across fresh tabs and sessions.
Plain JSON request/response over normal HTTP sidesteps that layer
entirely. Routes <=INSTANT_MAX_REVIEWS reviews to a synchronous response;
more to a background job delivered by email. Owns the single-worker job
queue and a keep-alive self-ping, running for the life of the process
(not just during a job), that stops a free host from spinning the app
down on idle -- trades a small steady trickle of self-traffic for no
cold-start wait on the next real visitor."""

import html
import os
import re
import tempfile
import threading
import time
import uuid
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

import requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from review_diagnostics import config, email_delivery, pipeline, scrape

MAX_QUEUE_DEPTH = 3
RATE_LIMIT_WINDOW_SECONDS = 3600
RATE_LIMIT_MAX_PER_EMAIL = 3
KEEPALIVE_INTERVAL_SECONDS = 240
# Downloads are served from memory, not disk -- consistent with "nothing is
# stored": this is a short-lived handle for the one download click a user
# actually makes, not persistence, and it's gone on redeploy/restart either way.
DOWNLOAD_TTL_SECONDS = 600

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_executor = ThreadPoolExecutor(max_workers=1)
_active_jobs = 0
_active_jobs_lock = threading.Lock()
_email_submissions = defaultdict(list)
_email_lock = threading.Lock()
_keepalive_thread: Optional[threading.Thread] = None
_keepalive_lock = threading.Lock()
_downloads: dict[str, tuple[bytes, str, float]] = {}
_downloads_lock = threading.Lock()


def _space_url() -> Optional[str]:
    host = os.environ.get("SPACE_HOST")
    if host:
        return f"https://{host}"
    return os.environ.get("RENDER_EXTERNAL_URL")


def _keepalive_loop():
    """Runs for the life of the process, not just while a job is active --
    self-pings on a fixed interval so the free-tier host never sees the app
    go idle long enough to spin it down, trading a small steady trickle of
    self-traffic for no cold-start wait on the next real visitor."""
    url = _space_url()
    if not url:
        return
    while True:
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


def _prune_downloads():
    now = time.time()
    with _downloads_lock:
        expired = [k for k, (_, _, ts) in _downloads.items() if now - ts > DOWNLOAD_TTL_SECONDS]
        for k in expired:
            del _downloads[k]


def _stash_csv(data, package_id: str) -> str:
    _prune_downloads()
    token = uuid.uuid4().hex
    filename = f"{package_id.replace('.', '_')}_ranked_categories.csv"
    with _downloads_lock:
        _downloads[token] = (data.to_csv_bytes(), filename, time.time())
    return token


# ---------------------------------------------------------------- API models

class LookupRequest(BaseModel):
    app_input: str


class RunRequest(BaseModel):
    package_id: str
    review_count: int
    email: str = ""


# ---------------------------------------------------------------- routes

@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC_DIR / "index.html").read_text()


@app.post("/api/lookup")
def api_lookup(req: LookupRequest):
    app_input = req.app_input.strip()
    if not app_input:
        return JSONResponse({"ok": False})
    package_id = scrape.resolve_package_id(app_input)
    try:
        info = scrape.scrape_app_info(package_id)
    except Exception:
        return JSONResponse({
            "ok": False,
            "error": f"Couldn't find an app at “{html.escape(app_input)}”. Check the URL or package ID.",
        })

    score = info.get("score")
    return JSONResponse({
        "ok": True,
        "package_id": package_id,
        "title": info.get("title", package_id),
        "icon": info.get("icon", ""),
        "score": f"{score:.1f}" if isinstance(score, (int, float)) else "?",
        "installs": str(info.get("installs", "?")),
        "instant_max_reviews": config.INSTANT_MAX_REVIEWS,
        "min_viable_reviews": config.MIN_VIABLE_REVIEWS,
        "async_max_reviews": config.ASYNC_MAX_REVIEWS,
    })


@app.post("/api/run")
def api_run(req: RunRequest):
    global _active_jobs

    package_id = req.package_id.strip()
    if not package_id:
        return JSONResponse({"ok": False, "message": "Look up an app first."})

    review_count = int(req.review_count)

    if review_count <= config.INSTANT_MAX_REVIEWS:
        print(f"[app] run starting for {package_id}, {review_count} reviews", flush=True)
        try:
            data = pipeline.run(package_id, review_count)
        except Exception as e:
            print(f"[app] pipeline.run raised: {e}", flush=True)
            return JSONResponse({"ok": False, "message": f"Run failed: {e}"})
        print("[app] pipeline.run returned, building response", flush=True)
        if data.insufficient_data or data.ranked_table.empty:
            return JSONResponse({"ok": True, "instant": True, "html": data.to_html_report()})
        token = _stash_csv(data, package_id)
        return JSONResponse({
            "ok": True, "instant": True, "html": data.to_html_report(), "download_token": token,
        })

    email_addr = (req.email or "").strip()
    if not _valid_email(email_addr):
        return JSONResponse({"ok": False, "message": "Enter a valid email address to run more than "
                                                       f"{config.INSTANT_MAX_REVIEWS} reviews."})
    if not _rate_limit_ok(email_addr):
        return JSONResponse({"ok": False, "message": "Too many runs requested from this email recently. "
                                                       "Please try again later."})

    with _active_jobs_lock:
        if _active_jobs >= MAX_QUEUE_DEPTH:
            return JSONResponse({"ok": False, "message": "The queue is busy right now. Please try again in a few minutes."})
        _active_jobs += 1

    eta_minutes = max(2, review_count // 150)
    try:
        email_delivery.send_started_notice(email_addr, package_id, eta_minutes)
        print(f"[email] started-notice sent to {email_addr}", flush=True)
    except Exception as e:
        print(f"[email] started-notice FAILED for {email_addr}: {e}", flush=True)
        # best-effort; the job still runs and the report email is what matters most

    _executor.submit(_run_async_job, package_id, review_count, email_addr)

    return JSONResponse({
        "ok": True, "instant": False,
        "message": f"Started. You'll get an email at **{email_addr}** in about {eta_minutes} minute(s) "
                    "-- you can close this tab.",
    })


def _run_async_job(package_id: str, review_count: int, email_addr: str):
    global _active_jobs
    try:
        data = pipeline.run(package_id, review_count)
        email_delivery.send_report(email_addr, data)
        print(f"[email] report sent to {email_addr}", flush=True)
    except Exception as e:
        print(f"[email] job/report FAILED for {email_addr}: {e}", flush=True)
        try:
            email_delivery.send_failure_notice(email_addr, package_id, str(e))
            print(f"[email] failure-notice sent to {email_addr}", flush=True)
        except Exception as e2:
            print(f"[email] failure-notice ALSO FAILED for {email_addr}: {e2}", flush=True)
    finally:
        with _active_jobs_lock:
            _active_jobs -= 1


@app.get("/api/download/{token}")
def api_download(token: str):
    with _downloads_lock:
        entry = _downloads.get(token)
    if entry is None:
        return JSONResponse({"ok": False, "message": "That download has expired."}, status_code=404)
    content, filename, _ = entry
    tmp = Path(tempfile.mkstemp(suffix=".csv")[1])
    tmp.write_bytes(content)
    return FileResponse(tmp, filename=filename, media_type="text/csv")


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


def _prewarm_nltk_data():
    """Same reasoning as _prewarm_embedding_model(): snippets.py lazily
    calls nltk.download() on first use, against yet another external host
    (nltk's own download server, separate from Play Store and Hugging Face)
    -- a live test on this host hung silently for minutes with no crash and
    no error at exactly this step before this fix, the same failure mode as
    the two earlier unbounded-network-call bugs. Downloading at boot makes
    a stall visible in deploy logs instead of hanging a user's request."""
    import nltk

    for pkg in ("punkt", "punkt_tab"):
        try:
            nltk.data.find(f"tokenizers/{pkg}")
        except LookupError:
            print(f"Pre-warming NLTK data: {pkg}...", flush=True)
            nltk.download(pkg)
    print("NLTK data ready.", flush=True)


if __name__ == "__main__":
    _prewarm_embedding_model()
    _prewarm_nltk_data()
    _ensure_keepalive_running()
    # 0.0.0.0 (not localhost) so the container's external load balancer --
    # Render's, or a similar host's -- can actually reach it; PORT is
    # Render's own env var for which port it expects the app to listen on.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 7860)))
