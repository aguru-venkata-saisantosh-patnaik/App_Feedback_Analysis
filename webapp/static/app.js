const appInput = document.getElementById('app-input');
const lookupBtn = document.getElementById('lookup-btn');
const appCard = document.getElementById('app-card');
const step2Label = document.getElementById('step2-label');
const runGroup = document.getElementById('run-group');
const reviewCount = document.getElementById('review-count');
const reviewCountNum = document.getElementById('review-count-num');
const reviewCountLabel = document.getElementById('review-count-label');
const emailWrap = document.getElementById('email-wrap');
const email = document.getElementById('email');
const runBtn = document.getElementById('run-btn');
const statusBox = document.getElementById('status-box');
const downloadFile = document.getElementById('download-file');
const reportHtml = document.getElementById('report-html');

let packageId = null;
let instantMax = 120;
let minViable = 100;
let asyncMax = 1000;

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

function updateReviewCountLabel() {
  reviewCountLabel.textContent = `How many reviews to analyze (≤${instantMax} = instant, more = emailed)`;
  emailWrap.classList.toggle('hidden', Number(reviewCount.value) <= instantMax);
}

reviewCount.addEventListener('input', () => {
  reviewCountNum.value = reviewCount.value;
  updateReviewCountLabel();
});
reviewCountNum.addEventListener('input', () => {
  let v = Number(reviewCountNum.value);
  if (Number.isNaN(v)) return;
  v = Math.max(minViable, Math.min(asyncMax, v));
  reviewCount.value = v;
  updateReviewCountLabel();
});

appInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    lookupBtn.click();
  }
});

lookupBtn.addEventListener('click', async () => {
  const val = appInput.value.trim();
  if (!val) return;
  appCard.innerHTML = '';
  lookupBtn.disabled = true;
  try {
    const res = await fetch('/api/lookup', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ app_input: val }),
    });
    const data = await res.json();
    if (!data.ok) {
      appCard.innerHTML = `<div class="app-card app-card-error">${escapeHtml(data.error || "Couldn't find that app.")}</div>`;
      step2Label.classList.add('hidden');
      runGroup.classList.add('hidden');
      return;
    }
    packageId = data.package_id;
    instantMax = data.instant_max_reviews;
    minViable = data.min_viable_reviews;
    asyncMax = data.async_max_reviews;
    reviewCount.min = minViable;
    reviewCount.max = asyncMax;
    reviewCountNum.min = minViable;
    reviewCountNum.max = asyncMax;
    appCard.innerHTML = `
      <div class="app-card">
        <img class="app-icon" src="${escapeHtml(data.icon)}" alt="" onerror="this.style.visibility='hidden'" />
        <div class="app-meta">
          <div class="app-name">${escapeHtml(data.title)}</div>
          <div class="app-stats">${escapeHtml(data.score)} rating &nbsp;&middot;&nbsp; ${escapeHtml(data.installs)} installs</div>
          <code class="app-pkg">${escapeHtml(data.package_id)}</code>
        </div>
      </div>`;
    step2Label.classList.remove('hidden');
    runGroup.classList.remove('hidden');
    updateReviewCountLabel();
  } catch (e) {
    appCard.innerHTML = `<div class="app-card app-card-error">Something went wrong looking that up. Try again.</div>`;
  } finally {
    lookupBtn.disabled = false;
  }
});

runBtn.addEventListener('click', async () => {
  if (!packageId) {
    statusBox.textContent = 'Look up an app first.';
    statusBox.classList.add('error');
    return;
  }
  statusBox.classList.remove('error');
  statusBox.innerHTML = '<span class="spinner"></span>Running… this can take a minute or two.';
  downloadFile.classList.add('hidden');
  downloadFile.innerHTML = '';
  reportHtml.innerHTML = '';
  runBtn.disabled = true;
  try {
    const res = await fetch('/api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        package_id: packageId,
        review_count: Number(reviewCount.value),
        email: email.value.trim(),
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      statusBox.textContent = data.message || 'Run failed.';
      statusBox.classList.add('error');
      return;
    }
    if (data.instant) {
      statusBox.innerHTML = '';
      reportHtml.innerHTML = data.html;
      if (data.download_token) {
        downloadFile.classList.remove('hidden');
        downloadFile.innerHTML = `<a class="download-link" href="/api/download/${data.download_token}" download>↓ Ranked categories (CSV)</a>`;
      }
    } else {
      statusBox.innerHTML = (data.message || '').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    }
  } catch (e) {
    statusBox.textContent = 'Something went wrong running the diagnostic. Try again.';
    statusBox.classList.add('error');
  } finally {
    runBtn.disabled = false;
  }
});
