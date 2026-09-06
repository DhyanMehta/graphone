"""
Google Sheets export module using gspread.

Exports SQLite records from data/records.db into a 6-tab Google Spreadsheet:
1. Startups
2. Products
3. Research Papers
4. Jobs (Placeholder until pre-submission freshness run)
5. News (Placeholder until pre-submission freshness run)
6. Entity Mapping Log

Adheres to schema formatting rules:
- Flattened nested keys (e.g. content.entityName, source.name)
- schemaVersion and recordType on all entity tabs
- content.authors flattened as '; ' delimited string
- Public view-only access
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional
import sqlite3
from dotenv import load_dotenv

load_dotenv()

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

# Standard OAuth scopes required for Google Sheets and Google Drive
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

DEFAULT_SHEET_TITLE = "FrontierAtlas — Data Ingestion & Entity Resolution"


def get_gspread_client(credentials_path: Optional[str] = None) -> gspread.Client:
    """
    Authenticate and return a gspread Client instance.

    Looks in order:
    1. credentials_path argument (if provided)
    2. ./service_account.json (in project root)
    3. GOOGLE_APPLICATION_CREDENTIALS environment variable
    4. GOOGLE_SERVICE_ACCOUNT_JSON environment variable (inline JSON string)
    """
    proj_root = Path(__file__).resolve().parent.parent.parent
    local_creds = proj_root / "service_account.json"

    creds_file = None
    if credentials_path and os.path.exists(credentials_path):
        creds_file = credentials_path
    elif local_creds.exists():
        creds_file = str(local_creds)
    elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and os.path.exists(
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    ):
        creds_file = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]

    if creds_file:
        logger.info("Authenticating with service account file: %s", creds_file)
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        return gspread.authorize(creds)

    json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_str:
        logger.info("Authenticating with inline GOOGLE_SERVICE_ACCOUNT_JSON")
        creds_dict = json.loads(json_str)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        return gspread.authorize(creds)

    raise FileNotFoundError(
        "Google service account credentials not found! Looked in:\n"
        f"1. {credentials_path}\n"
        f"2. {local_creds}\n"
        "3. GOOGLE_APPLICATION_CREDENTIALS env var\n"
        "4. GOOGLE_SERVICE_ACCOUNT_JSON env var"
    )


def format_authors(raw_authors: Optional[str]) -> str:
    """
    Format authors field into '; ' delimited string.
    Handles JSON array, comma-separated strings, or None.
    """
    if not raw_authors:
        return ""
    try:
        parsed = json.loads(raw_authors)
        if isinstance(parsed, list):
            return "; ".join(str(a).strip() for a in parsed if str(a).strip())
        return str(parsed).strip()
    except (json.JSONDecodeError, TypeError):
        return str(raw_authors).strip()


def export_database_to_sheets(
    db_path: str = "data/records.db",
    spreadsheet_id_or_url: Optional[str] = None,
    spreadsheet_title: str = DEFAULT_SHEET_TITLE,
    credentials_path: Optional[str] = None,
    share_public: bool = True,
) -> str:
    """
    Export all 6 tabs from SQLite database to Google Sheets.

    Args:
        db_path: Path to SQLite database.
        spreadsheet_id_or_url: Optional existing Google Sheet ID or full URL.
            If provided (or set in SPREADSHEET_URL / SPREADSHEET_ID env var),
            opens the sheet shared with the service account.
        spreadsheet_title: Title to search for or create if no ID/URL is given.
        credentials_path: Path to service_account.json.
        share_public: Whether to set public read permissions ('anyone', 'reader').

    Returns:
        The public spreadsheet URL.
    """
    client = get_gspread_client(credentials_path)

    # Check env vars if not passed explicitly
    target_id_or_url = (
        spreadsheet_id_or_url
        or os.environ.get("SPREADSHEET_URL")
        or os.environ.get("SPREADSHEET_ID")
    )

    sheet = None
    if target_id_or_url:
        target_str = target_id_or_url.strip()
        if target_str.startswith("http://") or target_str.startswith("https://"):
            logger.info("Opening spreadsheet by URL: %s", target_str)
            sheet = client.open_by_url(target_str)
        else:
            logger.info("Opening spreadsheet by ID/Key: %s", target_str)
            sheet = client.open_by_key(target_str)
    else:
        try:
            sheet = client.open(spreadsheet_title)
            logger.info("Opened existing spreadsheet: '%s' (ID: %s)", spreadsheet_title, sheet.id)
        except gspread.SpreadsheetNotFound:
            try:
                sheet = client.create(spreadsheet_title)
                logger.info("Created new spreadsheet: '%s' (ID: %s)", spreadsheet_title, sheet.id)
            except gspread.exceptions.APIError as exc:
                if "storage quota" in str(exc).lower():
                    sa_email = "graphone-sheets-writer@graphone-507713.iam.gserviceaccount.com"
                    raise PermissionError(
                        "Google Service Accounts do not have Drive storage quota to create files from scratch in GCP.\n"
                        "Please create a blank Google Sheet in your personal Google Drive and share it with the service account:\n"
                        f"  1. Go to https://sheets.new\n"
                        f"  2. Click 'Share' -> Add '{sa_email}' as 'Editor'\n"
                        "  3. Pass the sheet URL to the export script (via --url or SPREADSHEET_URL in .env)\n"
                    ) from exc
                raise exc

    # Share with user's personal Google account as Editor
    try:
        sheet.share("dhyanm2701@gmail.com", perm_type="user", role="writer", notify=False)
        logger.info("Shared spreadsheet with dhyanm2701@gmail.com as Editor")
    except Exception as exc:
        logger.warning("Could not share with dhyanm2701@gmail.com: %s", exc)

    # Share as public view-only
    if share_public:
        try:
            sheet.share(None, perm_type="anyone", role="reader")
            logger.info("Set public read permissions on spreadsheet")
        except Exception as exc:
            logger.warning("Could not set public permissions: %s", exc)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    # Define the 6 tabs and their schemas
    tabs_data = {}

    # 1. Startups
    startups_headers = [
        "schemaVersion",
        "recordType",
        "source.name",
        "source.url",
        "content.entityName",
        "content.data.employeeCount",
        "collectedAt",
    ]
    cur = con.execute(
        """
        SELECT schema_version, record_type, source_name, source_url,
               entity_name, employee_count, collected_at
        FROM startups
        ORDER BY id ASC
        """
    )
    startups_rows = []
    for r in cur.fetchall():
        startups_rows.append([
            r["schema_version"] or "1.0",
            r["record_type"] or "STARTUP",
            r["source_name"] or "",
            r["source_url"] or "",
            r["entity_name"] or "",
            r["employee_count"] if r["employee_count"] is not None else "",
            r["collected_at"] or "",
        ])
    tabs_data["Startups"] = (startups_headers, startups_rows)

    # 2. Products
    products_headers = [
        "schemaVersion",
        "recordType",
        "source.name",
        "source.url",
        "content.startupName",
        "content.pricingModel",
        "collectedAt",
    ]
    cur = con.execute(
        """
        SELECT schema_version, record_type, source_name, source_url,
               startup_name, pricing_model, collected_at
        FROM products
        ORDER BY id ASC
        """
    )
    products_rows = []
    for r in cur.fetchall():
        products_rows.append([
            r["schema_version"] or "1.0",
            r["record_type"] or "PRODUCT",
            r["source_name"] or "",
            r["source_url"] or "",
            r["startup_name"] or "",
            r["pricing_model"] or "",
            r["collected_at"] or "",
        ])
    tabs_data["Products"] = (products_headers, products_rows)

    # 3. Research Papers
    papers_headers = [
        "schemaVersion",
        "recordType",
        "source.name",
        "source.url",
        "content.title",
        "content.authors",
        "content.paper_url",
        "content.github_url",
        "content.github_stars",
        "content.published_date",
        "collectedAt",
    ]
    cur = con.execute(
        """
        SELECT schema_version, record_type, source_name, paper_url,
               title, authors, github_url, github_stars, published_date,
               collected_at
        FROM research_papers
        ORDER BY id ASC
        """
    )
    papers_rows = []
    for r in cur.fetchall():
        papers_rows.append([
            r["schema_version"] or "1.0",
            r["record_type"] or "RESEARCH_PAPER",
            r["source_name"] or "",
            r["paper_url"] or "",
            r["title"] or "",
            format_authors(r["authors"]),
            r["paper_url"] or "",
            r["github_url"] or "",
            r["github_stars"] if r["github_stars"] is not None else "",
            r["published_date"] or "",
            r["collected_at"] or "",
        ])
    tabs_data["Research Papers"] = (papers_headers, papers_rows)

    # 4. Jobs
    jobs_headers = [
        "schemaVersion",
        "recordType",
        "source.name",
        "source.url",
        "content.title",
        "content.company",
        "content.date",
        "content.is_remote",
        "content.role_family",
        "content.job_url",
        "content.description",
        "collectedAt",
    ]
    cur = con.execute(
        """
        SELECT schema_version, record_type, source_name, source_url,
               title, company, published_date, is_remote, role_family,
               job_url, description, collected_at
        FROM jobs
        ORDER BY id ASC
        """
    )
    jobs_rows = []
    for r in cur.fetchall():
        desc = r["description"] or ""
        if len(desc) > 500:
            desc = desc[:500] + "..."
        jobs_rows.append([
            r["schema_version"] or "1.0",
            r["record_type"] or "JOB",
            r["source_name"] or "",
            r["source_url"] or "",
            r["title"] or "",
            r["company"] or "",
            r["published_date"] or "",
            bool(r["is_remote"]),
            r["role_family"] or "",
            r["job_url"] or "",
            desc,
            r["collected_at"] or "",
        ])
    tabs_data["Jobs"] = (jobs_headers, jobs_rows)

    # 5. News
    news_headers = [
        "schemaVersion",
        "recordType",
        "source.name",
        "source.url",
        "content.title",
        "content.url",
        "content.published_date",
        "content.author",
        "content.summary",
        "content.full_text",
        "content.content_type",
        "collectedAt",
    ]
    cur = con.execute(
        """
        SELECT schema_version, record_type, source_name, source_url,
               title, url, published_date, author, summary, full_text,
               content_type, collected_at
        FROM news
        ORDER BY id ASC
        """
    )
    news_rows = []
    for r in cur.fetchall():
        full_text_val = r["full_text"] or ""
        if len(full_text_val) > 1000:
            full_text_val = full_text_val[:1000] + "..."
        news_rows.append([
            r["schema_version"] or "1.0",
            r["record_type"] or "NEWS",
            r["source_name"] or "",
            r["source_url"] or "",
            r["title"] or "",
            r["url"] or "",
            r["published_date"] or "",
            r["author"] or "",
            r["summary"] or "",
            full_text_val,
            r["content_type"] or "FULL_TEXT",
            r["collected_at"] or "",
        ])
    tabs_data["News"] = (news_headers, news_rows)

    # 6. Entity Mapping Log
    mapping_headers = [
        "raw_name",
        "canonical_name",
        "entity_type",
        "match_method",
        "confidence",
        "source_name",
        "source_url",
        "explanation",
        "resolved_at",
    ]
    try:
        cur = con.execute(
            """
            SELECT raw_name, canonical_name, entity_type, match_method,
                   confidence, source_name, source_url, explanation,
                   resolved_at
            FROM entity_mapping_log
            ORDER BY id ASC
            """
        )
        mapping_rows = []
        for r in cur.fetchall():
            mapping_rows.append([
                r["raw_name"] or "",
                r["canonical_name"] or "",
                r["entity_type"] or "",
                r["match_method"] or "",
                round(float(r["confidence"]), 4) if r["confidence"] is not None else "",
                r["source_name"] or "",
                r["source_url"] or "",
                r["explanation"] or "",
                r["resolved_at"] or "",
            ])
    except Exception as exc:
        logger.warning("Could not load entity_mapping_log: %s", exc)
        mapping_rows = []

    tabs_data["Entity Mapping Log"] = (mapping_headers, mapping_rows)

    con.close()

    # Get existing worksheets to avoid duplicates
    existing_worksheets = {ws.title: ws for ws in sheet.worksheets()}

    # Populate each tab
    for tab_title, (headers, rows) in tabs_data.items():
        logger.info("Updating tab '%s' with %d rows...", tab_title, len(rows))
        all_values = [headers] + rows

        if tab_title in existing_worksheets:
            ws = existing_worksheets[tab_title]
            ws.clear()
        else:
            # Create new worksheet with sufficient rows and cols
            ws = sheet.add_worksheet(
                title=tab_title,
                rows=max(len(all_values) + 10, 100),
                cols=max(len(headers) + 2, 10),
            )
            existing_worksheets[tab_title] = ws

        # Batch update values
        ws.update(all_values, value_input_option="RAW")

        # Format header: freeze 1st row and bold headers
        try:
            ws.freeze(rows=1)
            ws.format("1:1", {"textFormat": {"bold": True}})
        except Exception as exc:
            logger.debug("Formatting header on '%s' skipped: %s", tab_title, exc)

    # If default "Sheet1" exists and wasn't renamed, remove it if we have our 6 tabs
    if "Sheet1" in existing_worksheets and len(sheet.worksheets()) > 1:
        try:
            sheet.del_worksheet(existing_worksheets["Sheet1"])
        except Exception:
            pass

    spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{sheet.id}"
    logger.info("Successfully exported database to Google Sheets: %s", spreadsheet_url)
    return spreadsheet_url


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Export FrontierAtlas database to Google Sheets")
    parser.add_argument("--url", help="URL of existing Google Sheet shared with service account")
    parser.add_argument("--id", help="ID/Key of existing Google Sheet shared with service account")
    parser.add_argument("--title", default=DEFAULT_SHEET_TITLE, help="Sheet title to open/create")
    parser.add_argument("--db", default="data/records.db", help="Path to SQLite database")
    args = parser.parse_args()

    try:
        url = export_database_to_sheets(
            db_path=args.db,
            spreadsheet_id_or_url=args.url or args.id,
            spreadsheet_title=args.title,
        )
        print(f"\n=======================================================")
        print(f"SUCCESS: Export completed!")
        print(f"Public Google Sheet URL: {url}")
        print(f"=======================================================")
    except Exception as e:
        print(f"\nERROR: Export failed: {e}", file=sys.stderr)
        sys.exit(1)
