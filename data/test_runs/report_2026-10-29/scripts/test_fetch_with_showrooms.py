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

def test_fetch_with_showrooms():
    config_bundle = load_config()
    session = requests.Session()
    session.headers.update(config_bundle.innervex.default_headers)
    authenticator = InnervexAuthenticator(config_bundle.innervex)
    authenticator.login(session=session)
    client = InnervexClient(config_bundle.innervex, auth_strategy=authenticator)
    client.session = session
    fetcher = SchemeReportFetcher(client)
    
    ss_cfg = config_bundle.get_report("subhiksham")
    sv_cfg = config_bundle.get_report("viruksham")
    showrooms = config_bundle.mappings.showroom_codes
    print(f"Showrooms count: {len(showrooms)}, sample: {showrooms[:5]}")
    
    for d_target in [date(2026, 10, 28), date(2026, 10, 29)]:
        print(f"\n--- Testing Date: {d_target} ---")
        res_ss = fetcher.fetch_report(config=ss_cfg, report_date=d_target, showroom_codes=showrooms)
        print(f"  SS row count: {res_ss.row_count}")
        res_sv = fetcher.fetch_report(config=sv_cfg, report_date=d_target, showroom_codes=showrooms)
        print(f"  SV row count: {res_sv.row_count}")

if __name__ == "__main__":
    test_fetch_with_showrooms()

