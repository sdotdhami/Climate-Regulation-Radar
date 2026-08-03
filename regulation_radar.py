"""
regulation_radar.py

Lightweight automation that watches a few free sources for sustainability/
climate regulation news and drops CANDIDATE updates into a "Needs Review"
tab in the tracker Google Sheet -- it does NOT write directly into the
main tracker. A human still reviews and decides what gets added: same
principle as the Streamlit bot -- never let an unverified/scraped fact
land in the sheet without a person checking it first.

Runs on a schedule via GitHub Actions (see .github/workflows/regulation_radar.yml).
"""

import os
import json
from datetime import datetime, timezone

import feedparser
import requests
import gspread
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------------------------
# CONFIG -- edit these to match your sheet
# ---------------------------------------------------------------------------
SHEET_ID = "1sRVlCyzbXiLKTk33dFeqxcz5WRdMNl2Wulo0QXnbNas"
TRACKER_GID = "628203108"          # the main tracker tab (read-only, just for keywords)
REVIEW_TAB_NAME = "Needs Review"   # created automatically on first run if missing

# Framework acronyms worth watching for in news headlines. Add to this list
# any time you notice a relevant framework that isn't catching matches.
FRAMEWORK_KEYWORDS = [
    "CSRD", "ESRS", "CSDDD", "ISSB", "IFRS S1", "IFRS S2", "TCFD", "TNFD",
    "SEC climate", "SB 253", "SB 261", "SSBJ", "BRSR", "JSE", "ASRS",
    "SFDR", "EU Taxonomy", "GHG Protocol", "CBAM", "Omnibus",
]

STATE_FILE = "radar_state.json"    # tracks what's already been flagged, avoids duplicates

MONTHLY_TRACKERS = {
    "S&P Global Sustainable1": "https://www.spglobal.com/sustainable1/en/insights/regulatory-tracker",
    "ESG Book Policy Digest": "https://www.esgbook.com/insights/content-type/regulatory-updates",
}


# ---------------------------------------------------------------------------
# Google Sheets access
# ---------------------------------------------------------------------------
def get_sheet_client():
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]  # set as a GitHub secret, see setup notes
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return gspread.authorize(creds)


def get_tracked_countries():
    """Read country names straight from the live tracker so the keyword
    list always matches what you're actually tracking, without needing
    to hardcode/maintain a separate country list."""
    import pandas as pd
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={TRACKER_GID}"
    df = pd.read_csv(url, skiprows=2)
    df.columns = df.columns.str.strip()
    countries = df["Country"].dropna().unique().tolist()
    return [c for c in countries if str(c).strip().lower() != "any"]


def append_candidate_rows(rows):
    """rows: list of dicts with keys date_found, source, headline, link, matched_on"""
    if not rows:
        return
    client = get_sheet_client()
    sh = client.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(REVIEW_TAB_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=REVIEW_TAB_NAME, rows=1000, cols=6)
        ws.append_row(["Date found", "Source", "Headline", "Link", "Matched on", "Status"])

    for r in rows:
        ws.append_row([
            r["date_found"], r["source"], r["headline"], r["link"], r["matched_on"], "New",
        ])


# ---------------------------------------------------------------------------
# Source 1: ESG Today RSS -- the "radar" layer, catches things early
# ---------------------------------------------------------------------------
def check_esg_today(keywords, seen_links):
    feed = feedparser.parse("https://www.esgtoday.com/feed")
    new_rows = []
    for entry in feed.entries:
        if entry.link in seen_links:
            continue
        text = f"{entry.title} {entry.get('summary', '')}"
        matched = [kw for kw in keywords if kw.lower() in text.lower()]
        if matched:
            new_rows.append({
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "source": "ESG Today",
                "headline": entry.title,
                "link": entry.link,
                "matched_on": ", ".join(matched),
            })
            seen_links.add(entry.link)
    return new_rows


# ---------------------------------------------------------------------------
# Source 2 & 3: S&P Global Sustainable1 + ESG Book monthly digests.
# These are narrative articles -- we deliberately do NOT try to auto-extract
# facts from them (same hallucination risk flagged before). We just check
# "is this month's edition live" and drop a reminder row with the link so
# a person reads it and manually decides what to update.
# ---------------------------------------------------------------------------
def check_monthly_trackers(seen_months):
    new_rows = []
    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    for source_name, url in MONTHLY_TRACKERS.items():
        key = f"{source_name}:{this_month}"
        if key in seen_months:
            continue
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        except requests.RequestException:
            continue  # skip quietly, will retry on the next scheduled run
        new_rows.append({
            "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "source": source_name,
            "headline": f"Monthly update check for {this_month} -- go read and compare to tracker",
            "link": url,
            "matched_on": "monthly check",
        })
        seen_months.add(key)
    return new_rows


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"seen_links": [], "seen_months": []}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def main():
    state = load_state()
    seen_links = set(state["seen_links"])
    seen_months = set(state["seen_months"])

    countries = get_tracked_countries()
    keywords = FRAMEWORK_KEYWORDS + countries

    rows = []
    rows += check_esg_today(keywords, seen_links)
    rows += check_monthly_trackers(seen_months)

    append_candidate_rows(rows)

    state["seen_links"] = list(seen_links)
    state["seen_months"] = list(seen_months)
    save_state(state)

    print(f"Found {len(rows)} new item(s) to review.")


if __name__ == "__main__":
    main()
