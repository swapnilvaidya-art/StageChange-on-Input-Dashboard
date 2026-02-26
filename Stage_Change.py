import os
import time
import json
import math
import requests
import pandas as pd
import numpy as np
import gspread
from google.oauth2.service_account import Credentials

# -------------------- START TIMER --------------------
start_time = time.time()

# -------------------- ENV VARIABLES --------------------
sec = os.getenv("SWAPNIL_SECRET_KEY")
User_name = os.getenv("USERNAME")
service_account_json = os.getenv("SERVICE_ACCOUNT_JSON")
MB_URL = os.getenv("METABASE_URL")
QUERY_URL = os.getenv("STAGE_CHANGE_QUERY")
SAK = os.getenv("SHEET_ACCESS_KEY")

if not sec or not service_account_json:
    raise ValueError("❌ Missing environment variables. Check GitHub secrets.")

# -------------------- GOOGLE AUTH --------------------
service_info = json.loads(service_account_json)

creds = Credentials.from_service_account_info(
    service_info,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)

gc = gspread.authorize(creds)

# -------------------- METABASE LOGIN --------------------
print("🔐 Creating Metabase session...")

res = requests.post(
    MB_URL,
    headers={"Content-Type": "application/json"},
    json={"username": User_name, "password": sec},
    timeout=60
)

res.raise_for_status()
token = res.json()['id']

METABASE_HEADERS = {
    "Content-Type": "application/json",
    "X-Metabase-Session": token
}

print("✅ Metabase session created")

# -------------------- FETCH WITH RETRY --------------------
def fetch_with_retry(url, headers, retries=5):
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(url, headers=headers, timeout=180)
            response.raise_for_status()
            return response
        except Exception as e:
            wait_time = 10 * attempt
            print(f"[Metabase] Attempt {attempt} failed: {e}")
            if attempt < retries:
                print(f"⏳ Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise

# -------------------- SANITIZE DATAFRAME --------------------
def sanitize_df(df):
    df.replace([np.inf, -np.inf], None, inplace=True)

    def clean_value(x):
        if x is None:
            return ""
        if isinstance(x, float):
            if math.isnan(x) or math.isinf(x):
                return ""
        return x

    df = df.apply(lambda col: col.map(clean_value))
    return df

# -------------------- SAFE SHEET UPDATE --------------------
def safe_update_sheet(worksheet, df, retries=5):
    print(f"🔄 Updating worksheet: {worksheet.title}")

    for attempt in range(1, retries + 1):
        try:
            rows = len(df) + 1
            cols = len(df.columns)

            # Clear only A:U (leaves rest of sheet untouched)
            worksheet.batch_clear(["A:U"])

            # Prepare values
            header = df.columns.tolist()
            data_rows = df.values.tolist()

            # Sanitize after tolist()
            def sanitize_row(row):
                return [
                    None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v
                    for v in row
                ]

            data_rows = [sanitize_row(row) for row in data_rows]
            values = [header] + data_rows

            worksheet.update(
                values,
                f"A1:{chr(64 + cols)}{rows}"
            )

            print(f"✅ Sheet updated successfully: {worksheet.title}")
            return True

        except Exception as e:
            wait_time = 15 * attempt
            print(f"[Sheets] Attempt {attempt} failed: {e}")
            if attempt < retries:
                print(f"⏳ Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise

# -------------------- MAIN EXECUTION --------------------
print("📥 Fetching Stage Change query from Metabase...")

response = fetch_with_retry(QUERY_URL, METABASE_HEADERS)
df = pd.DataFrame(response.json())

if df.empty:
    print("⚠️ WARNING: Query returned empty dataset.")
else:
    print(f"📊 Rows fetched: {len(df)}")

    required_cols = [
        'lead_created_on', 'modified_on', 'prospect_email', 'prospect_id', 'prospect_stage',
        'mx_prospect_status', 'crm_user_role', 'sales_user_email', 'mx_utm_medium',
        'mx_utm_source', 'mx_lead_quality_grade', 'mx_lead_inherent_intent',
        'mx_priority_status', 'mx_organic_inbound', 'lead_last_call_status',
        'mx_city', 'event', 'current_stage', 'previous_stage',
        'mx_identifer', 'mx_phoenix_identifer', 'lead_owner'
    ]

    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"❌ Missing columns from query: {missing_cols}")

    df = df[required_cols]
    df = sanitize_df(df)

    print("🔗 Connecting to Google Sheets...")
    sheet = gc.open_by_key(SAK)
    ws = sheet.worksheet("Helper StageChange Dump")

    print("⬆️ Updating Helper StageChange Dump...")
    safe_update_sheet(ws, df)

# -------------------- TIMER SUMMARY --------------------
end_time = time.time()
elapsed = end_time - start_time
mins, secs = divmod(elapsed, 60)

print(f"⏱ Total execution time: {int(mins)}m {int(secs)}s")
print("🎯 Stage Change Automation Completed Successfully!")
