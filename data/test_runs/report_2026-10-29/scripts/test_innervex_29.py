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
from app.innervex.reports import SchemeReportFetcher
from app.reporting.closing import ClosingReportProcessor

def test_innervex_29():
    config_bundle = load_config()
    target_date = date(2026, 10, 29)
    print(f"Testing Innervex download for date: {target_date}")
    
    session = requests.Session()
    session.headers.update(config_bundle.innervex.default_headers)
    authenticator = InnervexAuthenticator(config_bundle.innervex)
    
    auth_res = authenticator.login(session=session)
    print(f"Auth result: authenticated={auth_res.authenticated}, user={auth_res.username}")
    
    if not auth_res.authenticated:
        print("Authentication failed!")
        return
        
    client = InnervexClient(config_bundle.innervex, auth_strategy=authenticator)
    client.session = session
    fetcher = SchemeReportFetcher(client)
    
    # 1. Fetch Subhiksham
    ss_cfg = config_bundle.get_report("subhiksham")
    print(f"\nFetching Subhiksham for {target_date}...")
    try:
        ss_res = fetcher.fetch_report(config=ss_cfg, report_date=target_date)
        print(f"SS Fetch success: row_count={ss_res.row_count}")
        if ss_res.row_count > 0:
            print("SS Sample row:", ss_res.data[0])
    except Exception as e:
        print(f"SS Fetch error: {type(e).__name__}: {e}")
        
    # 2. Fetch Viruksham
    sv_cfg = config_bundle.get_report("viruksham")
    print(f"\nFetching Viruksham for {target_date}...")
    try:
        sv_res = fetcher.fetch_report(config=sv_cfg, report_date=target_date)
        print(f"SV Fetch success: row_count={sv_res.row_count}")
        if sv_res.row_count > 0:
            print("SV Sample row:", sv_res.data[0])
    except Exception as e:
        print(f"SV Fetch error: {type(e).__name__}: {e}")

    # 3. Fetch Scheme Closing Report
    print(f"\nFetching Closing Report for {target_date}...")
    try:
        from app.reporting.closing import ClosingReportProcessor
        processor = ClosingReportProcessor(
            innervex_client=client,
            sheets_service=None,
            spreadsheet_id="dummy",
        )
        closing_rows = processor.fetch_closing_report(target_date)
        print(f"Closing Fetch success: row_count={len(closing_rows)}")
        if len(closing_rows) > 0:
            print("Closing Sample row:", closing_rows[0])
    except Exception as e:
        print(f"Closing Fetch error: {type(e).__name__}: {e}")

if __name__ == "__main__":
    test_innervex_29()
