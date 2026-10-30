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

def test_dates():
    config_bundle = load_config()
    session = requests.Session()
    session.headers.update(config_bundle.innervex.default_headers)
    authenticator = InnervexAuthenticator(config_bundle.innervex)
    authenticator.login(session=session)
    client = InnervexClient(config_bundle.innervex, auth_strategy=authenticator)
    client.session = session
    fetcher = SchemeReportFetcher(client)
    ss_cfg = config_bundle.get_report("subhiksham")
    
    # Test 28 Oct
    d28 = date(2026, 10, 28)
    res28 = fetcher.fetch_report(config=ss_cfg, report_date=d28)
    print(f"2026-10-28 with default builder: row_count={res28.row_count}")
    
    # Test manual payload date formats on 28 Oct
    formats = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"]
    endpoint = client.config.endpoints.get(ss_cfg.endpoint_key, "/schemeNewCustRep")
    
    for fmt in formats:
        d_str = d28.strftime(fmt)
        p = fetcher.build_payload(ss_cfg, d28)
        d_dict = p.to_form_dict()
        d_dict["fromDate"] = d_str
        d_dict["toDate"] = d_str
        resp = client.post_report(endpoint, d_dict)
        print(f"Format {fmt} ('{d_str}'): row_count={len(resp.data)}")

if __name__ == "__main__":
    test_dates()

