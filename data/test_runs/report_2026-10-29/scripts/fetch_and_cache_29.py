import os
import sys
import json
import time
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
from app.reporting.closing import ClosingReportFetcher

def fetch_and_cache():
    target_date = date(2026, 10, 29)
    cache_dir = project_root / "data" / "raw" / "2026" / "10" / "29"
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    ss_cache = cache_dir / "ss_29_data.json"
    sv_cache = cache_dir / "sv_29_data.json"
    close_cache = cache_dir / "close_29_data.json"
    
    config_bundle = load_config()
    session = requests.Session()
    session.headers.update(config_bundle.innervex.default_headers)
    authenticator = InnervexAuthenticator(config_bundle.innervex)
    authenticator.login(session=session)
    config_bundle.innervex.timeout_seconds = 180
    client = InnervexClient(config_bundle.innervex, auth_strategy=authenticator)
    client.session = session
    
    fetcher = SchemeReportFetcher(client)
    showrooms = config_bundle.mappings.showroom_codes
    ss_cfg = config_bundle.get_report("subhiksham")
    sv_cfg = config_bundle.get_report("viruksham")
    
    # 1. SS
    if not ss_cache.exists():
        print(f"Fetching Subhiksham for {target_date}...")
        res_ss = fetcher.fetch_report(config=ss_cfg, report_date=target_date, showroom_codes=showrooms)
        with open(ss_cache, "w", encoding="utf-8") as f:
            json.dump(res_ss.data, f, ensure_ascii=False, indent=2)
        print(f"Cached SS: {len(res_ss.data)} records")
    else:
        print(f"SS already cached: {ss_cache}")
        
    time.sleep(2)
    # 2. SV
    if not sv_cache.exists():
        print(f"Fetching Viruksham for {target_date}...")
        res_sv = fetcher.fetch_report(config=sv_cfg, report_date=target_date, showroom_codes=showrooms)
        with open(sv_cache, "w", encoding="utf-8") as f:
            json.dump(res_sv.data, f, ensure_ascii=False, indent=2)
        print(f"Cached SV: {len(res_sv.data)} records")
    else:
        print(f"SV already cached: {sv_cache}")
        
    time.sleep(2)
    # 3. Closing
    if not close_cache.exists():
        print(f"Fetching Closing for {target_date}...")
        closing_fetcher = ClosingReportFetcher(client)
        close_rows = closing_fetcher.fetch_closing_report(target_date)
        with open(close_cache, "w", encoding="utf-8") as f:
            json.dump(close_rows, f, ensure_ascii=False, indent=2)
        print(f"Cached Closing: {len(close_rows)} records")
    else:
        print(f"Closing already cached: {close_cache}")

    print("ALL CACHES READY!")

if __name__ == "__main__":
    fetch_and_cache()

