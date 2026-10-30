import os
import sys
from pathlib import Path
from datetime import date
import requests

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.core.config import load_config
from app.innervex.auth import InnervexAuthenticator
from app.innervex.client import InnervexClient
from app.reporting.closing import ClosingReportFetcher

def test_fetch_closing():
    config_bundle = load_config()
    session = requests.Session()
    session.headers.update(config_bundle.innervex.default_headers)
    authenticator = InnervexAuthenticator(config_bundle.innervex)
    authenticator.login(session=session)
    client = InnervexClient(config_bundle.innervex, auth_strategy=authenticator)
    client.session = session
    
    closing_fetcher = ClosingReportFetcher(client)
    d_target = date(2026, 10, 29)
    print(f"Fetching Closing Report from Innervex for {d_target}...")
    try:
        raw_rows = closing_fetcher.fetch_closing_report(d_target)
        print(f"Closing row count: {len(raw_rows)}")
        if len(raw_rows) > 0:
            print("Closing sample:", raw_rows[0])
    except Exception as e:
        print(f"Closing fetch error: {type(e).__name__}: {e}")

if __name__ == "__main__":
    test_fetch_closing()
