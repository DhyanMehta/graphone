# FrontierAtlas / GraphOne — System Runbook

This document contains every command required to configure, run, and verify the FrontierAtlas data pipeline from scratch. Every command in this runbook has been verified to execute without error in the current environment.

---

## 1. Environment Setup

### 1.1 Python Virtual Environment
This project requires **Python 3.11**. Always execute pipeline commands from within the project virtual environment.

```powershell
# Windows (PowerShell)
python -m venv venv
.\venv\Scripts\Activate.ps1

# Linux / macOS (bash)
python3 -m venv venv
source venv/bin/activate
```

### 1.2 Dependency Installation
Install all required production dependencies from `requirements.txt`:

```bash
pip install -r requirements.txt
```

Verified dependency package list:
- `aiohttp` — High-throughput async HTTP client
- `aiosqlite` — Async interface for SQLite storage
- `beautifulsoup4` & `lxml` — HTML and XML parsing
- `playwright` — Headless browser for JavaScript rendering and anti-bot bypass
- `pydantic` — Schema validation and typed Literal enums
- `python-dotenv` — Environment variable loader
- `pyyaml` — Scraper configuration parsing
- `requests` — Synchronous HTTP utility for testing
- `tenacity` — Retries with exponential backoff & jitter
- `dateparser` — Flexible date parsing
- `rapidfuzz` — C++ string distance engine for token sort ratio and fuzzy matching
- `gspread` & `google-auth` — Google Sheets API client and service account authorization
- `openai` — Client for OpenAI-compatible endpoints across Groq, Gemini, and OpenRouter

### 1.3 Playwright Browser Installation
Install the Chromium browser engine used by `BrowserClient`:

```bash
playwright install chromium
```

### 1.4 Environment Configuration (`.env`)
Create a `.env` file at the project root based on `.env.example`:

```bash
cp .env.example .env
```

Populate the following keys:
| Environment Variable | Role & Purpose | Where to Obtain |
| :--- | :--- | :--- |
| `GITHUB_TOKEN` | Authenticates GitHub API calls to unlock the 5,000 req/hr rate limit (for paper star enrichment) | [GitHub Settings → Developer Settings → Personal Access Tokens (Classic)](https://github.com/settings/tokens) with `public_repo` scope |
| `GROQ_API_KEY` | Primary tier for LLM extraction and arbitration running `qwen/qwen3.8-27b` | Free tier key from [Groq Console](https://console.groq.com/keys) |
| `GEMINI_API_KEY` | Secondary fallback tier for LLM extraction running `gemini-2.5-flash` | Free tier key from [Google AI Studio](https://aistudio.google.com/app/apikey) |
| `OPENROUTER_API_KEY` | Third fallback tier for LLM extraction running `deepseek/deepseek-chat-v3-0324` | API key from [OpenRouter](https://openrouter.ai/keys) |
| `SPREADSHEET_URL` | Full URL of the destination Google Sheet | Create at [https://sheets.new](https://sheets.new) and copy the browser URL |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to the Google Cloud service account JSON file | Default: `service_account.json` in project root |

### 1.5 Service Account Setup (`service_account.json`)
Google Cloud service accounts on free tiers have a **0-byte Google Drive storage quota** and cannot create new files from scratch via the Drive API. The pipeline avoids this by updating an existing sheet created in your personal Google Drive:

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project (e.g. `frontier-atlas`).
2. Enable both the **Google Sheets API** and **Google Drive API**.
3. Navigate to **IAM & Admin → Service Accounts**, create a service account, and download its JSON key.
4. Rename the downloaded file to `service_account.json` and place it in the project root.
5. Create a blank Google Sheet in your personal Google Drive at [https://sheets.new](https://sheets.new).
6. Click **Share**, add your service account email (e.g. `your-sa@your-project.iam.gserviceaccount.com`) as an **Editor**, and paste the sheet URL into `SPREADSHEET_URL` in `.env`.

---

## 2. Running Each Phase Independently

### Phase I: Core Ingestion (Startups, Products, Research Papers)
Ingests Y Combinator active startups, SaaSHub software products, and Arxiv/Papers with Code research papers:

```bash
# Run all Phase I sources with default limits
python -m src.scraper.main --sources arxiv,paperswithcode,ycombinator,saashub

# Run with a limit per source (e.g. for rapid local testing)
python -m src.scraper.main --sources arxiv,paperswithcode,ycombinator,saashub --limit 10

# Scale-up runners (used to achieve 1,000+ records per category)
python scratch/scale_yc_arxiv.py
python scratch/scale_saashub.py
```

### Phase II: Signal Ingestion (Jobs & News — 24-Hour Freshness)
Crawls 5 AI job boards (Jobicy, Arbeitnow, Hacker News Hiring, RemoteOK, Remotive) and 5 AI news feeds (TechCrunch, The Verge, MIT News via Playwright, Ars Technica, Wired) under strict rolling 24-hour UTC freshness filters:

```bash
# Run jobs and news scrapers via unified CLI
python -m src.scraper.main --sources jobs,news

# Run with a per-source record limit
python -m src.scraper.main --sources jobs,news --limit 10

# Fresh pre-submission standalone runner (clears and re-scrapes jobs & news right now)
python scratch/run_fresh_jobs_news.py
```

### Phase III: LLM Extraction & Fallback Chain
Phase III is an integrated library layer (`src/extraction/`) invoked dynamically by the crawlers (for HN job parsing and news summarization) and by entity resolution (for Tier 3 arbitration). Run the standalone test suite to verify the multi-provider failover chain and Pydantic validation:

```bash
# Verify multi-provider failover (Groq -> Gemini -> OpenRouter)
python scratch/test_provider_failover.py

# Verify deterministic parser fallback enrichment (Strategy A)
python scratch/test_fallback_parsers.py

# Verify Hacker News structured job extraction (Strategy B.1)
python scratch/test_hn_extraction.py

# Verify news article summarization bounds (Strategy B.2)
python scratch/test_news_summary.py
```

### Phase IV: Entity Resolution
Clusters and deduplicates company and product entities across `startups`, `products`, and `jobs` using inverted token index candidate blocking and 4-tier matching:

```bash
# Run entity resolution across the full database
python -m src.resolver.resolver

# Run comprehensive entity resolution test suite (seed matching, legal suffix normalization, false friends)
python scratch/test_entity_resolution.py
```

### Phase V: Google Sheets Export
Serializes SQLite tables, flattens nested schemas, formats authors, and updates all 6 tabs in the live Google Sheet:

```bash
# Export using SPREADSHEET_URL from .env
python -m src.export.sheets_writer

# Export by explicitly passing sheet URL and database path
python -m src.export.sheets_writer --url "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit" --db data/records.db

# Test unauthenticated public read-only access (incognito check)
python scratch/test_incognito_access.py "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"
```

---

## 3. Running the Full Pipeline End-to-End

### Current Architecture Note
> [!NOTE]
> There is currently no single monolithic wrapper script (e.g. `run_pipeline.py`) that chains all phases together. This design deliberate preserves decoupling between the asynchronous ingestion rate limits, the LLM extraction tier, and the final pre-submission freshness run.

To execute the entire pipeline from an empty database to a fully populated, public Google Sheet, run this 4-step sequence:

```powershell
# Step 1: Ingest Core Entities (Startups, Products, Research Papers)
# For scaled production volume (1,000+ each):
python scratch/scale_yc_arxiv.py
python scratch/scale_saashub.py
# (Or for a quick test run: python -m src.scraper.main --sources arxiv,paperswithcode,ycombinator,saashub --limit 20)

# Step 2: Ingest Fresh Signal Feeds (Jobs & News within last 24h)
python scratch/run_fresh_jobs_news.py

# Step 3: Execute Entity Resolution across all scoped entities
python -m src.resolver.resolver

# Step 4: Export all 6 tabs to Google Sheets
python -m src.export.sheets_writer
```

---

## 4. Verification Commands

Reviewers can verify the database state and Google Sheets deliverable without reading the source code using the following commands.

### 4.1 Check SQLite Record Counts

#### Option A: Universal Cross-Platform Python Runner (Windows PowerShell / CMD / Linux / macOS)
Runs without shell quotation issues or external binary requirements:

```bash
python scratch/verify_counts.py
```

#### Option B: Standard SQLite3 CLI (macOS / Linux / Windows with sqlite3.exe)
```bash
sqlite3 data/records.db "SELECT 'startups', count(*) FROM startups UNION ALL SELECT 'products', count(*) FROM products UNION ALL SELECT 'research_papers', count(*) FROM research_papers UNION ALL SELECT 'jobs', count(*) FROM jobs UNION ALL SELECT 'news', count(*) FROM news UNION ALL SELECT 'entity_mapping_log', count(*) FROM entity_mapping_log;"
```

Or check individual tables:
```bash
sqlite3 data/records.db "SELECT count(*) FROM startups;"
sqlite3 data/records.db "SELECT count(*) FROM products;"
sqlite3 data/records.db "SELECT count(*) FROM research_papers;"
sqlite3 data/records.db "SELECT count(*) FROM jobs;"
sqlite3 data/records.db "SELECT count(*) FROM news;"
sqlite3 data/records.db "SELECT count(*) FROM entity_mapping_log;"
```

**Verified Output**:
```text
=============================================
FRONTIERATLAS — SQLITE DATABASE RECORD COUNTS
=============================================
startups              :   1427 rows
products              :   1050 rows
research_papers       :   1149 rows
jobs                  :    201 rows (24h fresh)
news                  :      5 rows (24h fresh)
entity_mapping_log    :   2523 rows (100% scoped entity audit)
=============================================
```

---

### 4.2 Query Specific Entity Clusters

Inspect real canonical clusters resolved across sources in `entity_mapping_log`:

#### Cross-Platform Python Helper:
```bash
# Check HubSpot cluster (Product extension canonicalization)
python scratch/query_clusters.py HubSpot

# Check Pipeliner vs Pipeline CRM (False-friend separation)
python scratch/query_clusters.py Pipeline
```

#### SQLite3 CLI Equivalent:
```bash
sqlite3 data/records.db "SELECT raw_name, canonical_name, match_method FROM entity_mapping_log WHERE raw_name LIKE '%HubSpot%';"
sqlite3 data/records.db "SELECT raw_name, canonical_name, match_method FROM entity_mapping_log WHERE raw_name LIKE '%Pipeline%';"
```

**Verified Output (HubSpot)**:
```text
Raw Name                       | Canonical Name            | Method
---------------------------------------------------------------------------
HubSpot                        | HubSpot                   | EXACT_NORMALIZED
HubSpot CRM                    | HubSpot                   | FUZZY_CONFIDENT
```

**Verified Output (Pipeline)**:
```text
Raw Name                       | Canonical Name            | Method
---------------------------------------------------------------------------
Pipeline CRM                   | Pipeline CRM              | CANONICAL_SINGLETON
Pipeliner CRM                  | Pipeliner CRM             | CANONICAL_SINGLETON
```

---

### 4.3 Verify Live Google Sheet Accessibility

Verify that the public sheet is live, readable without authentication, and populated with data:

```bash
python scratch/test_incognito_access.py "https://docs.google.com/spreadsheets/d/17gTaiwBVaW8mLKDEtulJSkUClU1JAfL_KB4kHze0Jj4"
```

- **Live Public URL**: [https://docs.google.com/spreadsheets/d/17gTaiwBVaW8mLKDEtulJSkUClU1JAfL_KB4kHze0Jj4](https://docs.google.com/spreadsheets/d/17gTaiwBVaW8mLKDEtulJSkUClU1JAfL_KB4kHze0Jj4)
- **Permissions**: Public view-only (`Anyone on the internet with the link can view`), Editor access shared with user.
- **Tabs Populated**:
  1. `Startups` (1,428 rows including header)
  2. `Products` (1,051 rows including header)
  3. `Research Papers` (1,150 rows including header)
  4. `Jobs` (201 rows including header — fresh 24h)
  5. `News` (6 rows including header — fresh 24h)
  6. `Entity Mapping Log` (2,524 rows including header — 100% audit log)

