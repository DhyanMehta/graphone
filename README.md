# FrontierAtlas / GraphOne
## High-Throughput Autonomous Data Pipeline & Entity Resolution Engine

[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/release/python-3110/)
[![Architecture PDF](https://img.shields.io/badge/deliverable-architecture.pdf-indigo.svg)](architecture.pdf)
[![Runbook](https://img.shields.io/badge/guide-RUNBOOK.md-green.svg)](RUNBOOK.md)
[![Live Google Sheet](https://img.shields.io/badge/Google%20Sheets-Live%20Sync-emerald.svg)](https://docs.google.com/spreadsheets/d/17gTaiwBVaW8mLKDEtulJSkUClU1JAfL_KB4kHze0Jj4)

FrontierAtlas is an autonomous, fault-tolerant web data ingestion pipeline and entity resolution engine built for the AI Engineer assessment. It continuously crawls, structures, normalizes, deduplicates, and resolves AI entities across 14 diverse data sources into a unified knowledge graph exported directly to a 6-tab Google Spreadsheet.

---

### Key Deliverables

1. **Live Google Sheet (All 6 Tabs Populated)**:
   - **Public Read-Only Link**: [https://docs.google.com/spreadsheets/d/17gTaiwBVaW8mLKDEtulJSkUClU1JAfL_KB4kHze0Jj4](https://docs.google.com/spreadsheets/d/17gTaiwBVaW8mLKDEtulJSkUClU1JAfL_KB4kHze0Jj4)
   - `Startups` (1,427 active YC startups)
   - `Products` (1,050 SaaSHub software products with pricing tiers)
   - `Research Papers` (1,149 Arxiv & Papers with Code papers with GitHub star enrichment)
   - `Jobs` (201 fresh jobs within rolling 24h UTC window across 5 job boards)
   - `News` (5 fresh AI articles within rolling 24h UTC window across 5 news feeds)
   - `Entity Mapping Log` (2,523 raw-to-canonical entity resolution audit trail)
2. **Architecture Deliverable (`architecture.pdf`)**:
   - Master 3-page technical specification covering Scale Strategy (500k+), 413/429 Handling, Freshness Tracking across distributed nodes, and Storage Strategy (Postgres, `pgvector`, Graph DB).
3. **Master Runbook (`RUNBOOK.md`)**:
   - Single reference guide with every command verified to run without error from a clean environment.

---

### Architecture Overview

```
Source Sites (14 Disparate Domains: YC, SaaSHub, Arxiv, PwC, 5x Jobs, 5x News)
      │
      ▼
Crawler Layer (Protocol Router via config.yaml: aiohttp + Playwright Chromium)
      │
      ▼
LLM Extraction & Fallback Chain (Groq Qwen 3.8 → Gemini 3.6 Flash → OpenRouter DeepSeek V3)
      │
      ▼
Entity Resolution Engine (Inverted Token Index Blocking → 4-Tier Matcher → Canonical Election)
      │
      ▼
Storage & Export (SQLite data/records.db with Compound UNIQUE Constraints → gspread Google Sheets Sync)
```

1. **Phase I: Core Ingestion**: Asynchronous multi-source crawling of startups (Y Combinator Algolia directory), software products (SaaSHub), and research papers (Arxiv official Atom XML API + Papers with Code with authenticated GitHub API star enrichment).
2. **Phase II: Signal Ingestion**: Strict rolling 24-hour UTC freshness filters across 5 AI job boards (Jobicy, Arbeitnow, HN Hiring, RemoteOK, Remotive) and 5 AI news feeds (TechCrunch, The Verge, MIT News via Playwright, Ars Technica, Wired).
3. **Phase III: LLM Extraction & Fallback Chain**: Semantic payload chunking (`chunking.py`), Tenacity exponential backoff with additive jitter, strict Pydantic typed schema validation, and multi-tier LLM failover.
4. **Phase IV: Entity Resolution**: Inverted token index candidate blocking (99.8% reduction in pairwise comparisons), 4-tier matching (Seed List $\rightarrow$ Exact Normalized $\rightarrow$ RapidFuzz token sort $\ge 98\% \rightarrow$ LLM Arbitration), and transitive cluster closure.
5. **Phase V: Storage & Google Sheets Sync**: Embedded SQLite with compound `UNIQUE` constraints and atomic transactions, serialized to Google Sheets with flattened schemas and public read-only sharing.

---

### Quickstart

For complete step-by-step setup instructions and verified phase execution commands, refer to **[RUNBOOK.md](RUNBOOK.md)**.

```bash
# 1. Activate Python 3.11 virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1    # Windows
source venv/bin/activate       # Linux/macOS

# 2. Install dependencies & browser engine
pip install -r requirements.txt
playwright install chromium

# 3. Configure environment
cp .env.example .env

# 4. Verify database state
python scratch/verify_counts.py

# 5. Export to Google Sheets
python -m src.export.sheets_writer
```