# 📱 App Feedback Analysis — Meesho Play Store NLP Pipeline

**NLP pipeline that scrapes Google Play reviews at scale, runs sentiment analysis and LDA topic modeling, and generates Gemini-powered strategic insights — applied to a Meesho product case study.**

> Sample: Latest 100,000 Google Play Store reviews · Analysed: October 2025 · Output: Sentiment split + 15 LDA topics + AI-generated recommendations

---

## 📌 Table of Contents

- [Project Overview](#-project-overview)
- [Case Study Context](#-case-study-context)
- [Technical Pipeline](#-technical-pipeline)
- [Results & Key Findings](#-results--key-findings)
  - [Sentiment Distribution](#sentiment-distribution)
  - [Positive Themes (LDA)](#positive-themes-lda)
  - [Negative Themes (LDA)](#negative-themes-lda)
- [Strategic Insights Applied](#-strategic-insights-applied)
- [Repository Structure](#-repository-structure)
- [How to Run](#-how-to-run)
- [API Key Setup](#-api-key-setup)
- [Dependencies](#-dependencies)
- [Limitations & Caveats](#-limitations--caveats)

---

## 📖 Project Overview

This project builds an end-to-end NLP pipeline for extracting structured product intelligence from Google Play Store reviews. The pipeline scrapes reviews at scale, runs sentiment classification and topic modeling, and uses the Gemini API to generate actionable product recommendations — all grounded in real user voice data.

**The pipeline was applied to Meesho** (one of India's largest social commerce platforms) as part of a structured product case study on growing Net Merchandise Value (NMV) and Average Order Value (AOV) while retaining its value-focused user base.

---

## 💡 Case Study Context

**Problem Statement:** How can Meesho raise AOV and NMV by expanding into higher-value categories without alienating its price-sensitive Tier-2/3 user base?

**Key Meesho metrics (FY25, Meesho IPO Filing):**

| Metric | Value |
|---|---|
| Net Merchandise Value (NMV) | ₹30,000 Cr |
| Average Order Value (AOV) | ₹315 – ₹370 |
| Annual Transacting Users | 187 million |
| Placed Orders (FY25) | ~1.8 billion |

The NLP pipeline was used to extract the real voice of Meesho's users at scale — identifying what is working and what is broken — to ground the strategic recommendations in data rather than assumption.

---

## ⚙️ Technical Pipeline

The notebook (`AI_App_Review_Insights.ipynb`) implements the following stages end-to-end:

```
STAGE 1 — Data Collection
  └── Scrape latest 100,000 Google Play Store reviews via google-play-scraper

STAGE 2 — Preprocessing
  └── Tokenization → lowercase → punctuation removal → stopword removal (NLTK)
  └── Lemmatization for LDA input

STAGE 3 — Sentiment Analysis
  └── TextBlob polarity scoring → classify each review as Positive / Neutral / Negative
  └── Output: three labelled subsets for topic modelling

STAGE 4 — Topic Modelling (LDA)
  └── gensim LDA applied separately on each sentiment subset
  └── k = 5 topics per subset (15 topics total)
  └── Dictionary filtered for low-frequency and high-frequency terms
  └── Coherence score computed to validate topic quality
  └── Manual inspection of 50-100 representative samples per topic

STAGE 5 — Insight Generation
  └── Top terms and representative reviews per topic → structured prompt
  └── Gemini API generates plain-English product recommendations per theme

STAGE 6 — Visual Analytics
  └── Sentiment distribution bar chart
  └── Word clouds per sentiment class
  └── Topic term frequency plots
  └── Sentiment trend over time (review date)
```

**Model choices:**
- **TextBlob** for sentiment: lightweight, no fine-tuning required, sufficient signal for polarity classification at this scale
- **LDA (k=5 per subset):** balances topic granularity vs. interpretability for a 100k-review corpus; k was validated via coherence scores
- **Gemini API** for insight synthesis: translates raw topic clusters into structured product recommendations that can directly feed a PM or strategy workflow

---

## 📊 Results & Key Findings

### Sentiment Distribution

| Sentiment | Review Count | Share |
|---|---|---|
| **Positive** | 69,274 | **69.3%** |
| Neutral | 18,150 | 18.2% |
| Negative | 12,576 | 12.6% |

Overall sentiment skews strongly positive, but the 12.6% negative cohort (12,576 reviews) represents a concentrated signal — at Meesho's order volumes (~1.8B FY25), even small pain points affect millions of transactions.

### Positive Themes (LDA)

| Topic | Key Signal |
|---|---|
| Product & Value | Users repeatedly praise product selection and perceived value-for-money |
| App Experience | App described as smooth, easy to navigate, and well-designed |
| Service & Order Experience | Positive mentions of delivery execution and customer service responsiveness |
| Trust & Repeat Usage | Many reviewers describe habitual usage and high platform trust |
| Price & Speed | Price competitiveness and fast delivery are the most frequently co-mentioned attributes |

### Negative Themes (LDA)

| Topic | Key Signal |
|---|---|
| Returns & Trust Issues | Wrong items delivered, slow or failed refunds, occasional fraud reports |
| Delivery & Fulfillment | Delivery delays and inconsistent last-mile handling |
| App / Checkout Friction | App bugs, checkout errors, payment failures |
| Counterfeit / Misleading Deals | "Fake" products and misleading promotional claims |
| Regional Language Complaints | Non-English reviews flagging localised friction in language and CS support |

---

## 🧠 Strategic Insights Applied

The NLP output fed directly into a five-theme ICE-prioritised action plan for the Meesho case study:

| Theme | Action | ICE Score | Priority |
|---|---|---|---|
| Returns & Trust Issues | Tighten seller verification, automate refunds, add Verified badge | 320 | High |
| Delivery & Fulfillment | Pilot stricter SLAs with last-mile partners, real-time tracking | 288 | High |
| App / Checkout Friction | Fix high-impact bugs, simplify checkout, 1-tap payment | 252 | Medium |
| Counterfeit / Misleading Deals | Verified-listing program, rapid takedown for repeat offenders | 240 | Medium |
| Regional Language Complaints | Localise UI and help content, route reviews to regional CS teams | 210 | Low |

The case study also covers market sizing (TAM/SAM/SOM), competitive positioning vs. Flipkart/Amazon/reseller apps, user persona mapping (Value-Seeking Shopper, Emerging Urban Shopper, Micro-Entrepreneur Seller), full customer journey analysis, and a pilot → measure → scale roadmap.

---

## 📁 Repository Structure

```
App_Feedback_Analysis/
│
├── AI_App_Review_Insights.ipynb   # Full pipeline: scraping → preprocessing → sentiment → LDA → Gemini insights → visualisations
├── requirements.txt               # Python dependencies
└── README.md
```

---

## 🚀 How to Run

1. Clone the repository:
   ```bash
   git clone https://github.com/aguru-venkata-saisantosh-patnaik/App_Feedback_Analysis.git
   cd App_Feedback_Analysis
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Download required NLTK data (first run only):
   ```python
   import nltk
   nltk.download('stopwords')
   nltk.download('punkt')
   nltk.download('wordnet')
   ```

4. Set your Gemini API key — see [API Key Setup](#-api-key-setup) below.

5. Open and run the notebook:
   ```bash
   jupyter notebook AI_App_Review_Insights.ipynb
   ```

6. Run all cells from top to bottom. The scraper fetches live data from the Play Store at runtime — full execution takes approximately **10–20 minutes** depending on connection speed and review count.

> **Note:** A live internet connection is required for both the Play Store scraper and Gemini API calls.

---

## 🔑 API Key Setup

This project uses the **Gemini API** (Google AI Studio — free tier available) for insight generation.

1. Get a free API key at [aistudio.google.com](https://aistudio.google.com)
2. Create a `.env` file in the project root:
   ```
   GEMINI_API_KEY=your_api_key_here
   ```
3. The notebook loads it via:
   ```python
   import os
   api_key = os.getenv("GEMINI_API_KEY")
   ```

Alternatively, paste your key directly into the config cell at the top of the notebook (not recommended for shared environments).

---

## 📦 Dependencies

```
google-play-scraper   # Play Store review scraping
pandas                # Data manipulation
matplotlib            # Charting
seaborn               # Statistical visualisation
textblob              # Sentiment polarity scoring
nltk                  # Tokenisation, stopwords, lemmatisation
gensim                # LDA topic modelling
wordcloud             # Word cloud visualisation
requests              # Gemini API calls
streamlit             # Optional: interactive dashboard
```

All review data is fetched live at runtime — no local dataset file is required.

---

## ⚠️ Limitations & Caveats

- **No date filter** — the pipeline scrapes the latest 100k reviews, so findings reflect current user voice but are not tied to a fixed time window or product release
- **TextBlob sentiment** — rule-based polarity scoring; may misclassify sarcasm or mixed-sentiment reviews. A fine-tuned model (e.g., IndicBERT) would improve accuracy on Hindi/regional reviews
- **LDA topic stability** — topic assignments can vary slightly between runs due to random initialisation; coherence scores and manual inspection were used to validate stability
- **English-only preprocessing** — stopword removal and lemmatisation are English-only; regional-language reviews are retained but not cleaned optimally
- **Play Store only** — App Store (iOS) reviews are not included; sentiment distribution may differ across platforms
- **Recency bias in scraping** — `google-play-scraper` retrieves the most recent reviews; older reviews and deleted reviews are not captured

---

*Built to demonstrate how large-scale NLP can replace survey-based research for product and strategy teams — grounded in real user voice, not assumption.*
