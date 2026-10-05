"""Report extraction logic and payload builders for Innervex reports."""

from datetime import date
import logging
from typing import Any, Dict, List, Optional

from app.core.config import ReportItemConfig
from app.core.models import RawReportPayload
from app.innervex.client import InnervexClient
from app.innervex.schemas import InnervexReportRequestPayload

logger = logging.getLogger("pothys_reporting")


class SchemeReportFetcher:
    """Fetcher for Scheme New Memberlist reports (Subhiksham & Viruksham)."""

    def __init__(self, client: InnervexClient):
        self.client = client

    @staticmethod
    def build_payload(
        config: ReportItemConfig,
        report_date: date,
        showroom_codes: Optional[List[str]] = None,
    ) -> InnervexReportRequestPayload:
        """
        Build the typed request payload for the scheme new customer report.

        Formats dates to the expected date string and maps sub-schemes/showrooms.
        """
        date_str = report_date.strftime("%Y-%m-%d")

        # In Innervex, multi-select parameters are formatted as comma-separated single-quoted strings:
        # e.g., "'CPT','PAD','TVL'"
        if showroom_codes:
            quoted_showrooms = [
                s if (s.startswith("'") and s.endswith("'")) else f"'{s}'"
                for s in showroom_codes
            ]
            showroom_param = ",".join(quoted_showrooms)
        else:
            showroom_param = "ALL"

        if config.subschemes:
            quoted_subschemes = [
                s if (s.startswith("'") and s.endswith("'")) else f"'{s}'"
                for s in config.subschemes
            ]
            subscheme_param = ",".join(quoted_subschemes)
        else:
            subscheme_param = "ALL"

        return InnervexReportRequestPayload(
            Action=config.innervex_action,
            fromDate=date_str,
            toDate=date_str,
            Showroom=showroom_param,
            Scheme=config.scheme_name,
            SubScheme=subscheme_param,
            EmpCode=config.emp_code_filter,
            PromoCode=config.promo_code_filter,
            Export=config.export_mode,
        )

    def fetch_report(
        self,
        config: ReportItemConfig,
        report_date: date,
        showroom_codes: Optional[List[str]] = None,
    ) -> RawReportPayload:
        """
        Request report from Innervex and package it into a RawReportPayload.
        """
        payload = self.build_payload(config, report_date, showroom_codes)
        endpoint = self.client.config.endpoints.get(
            config.endpoint_key, "/schemeNewCustRep"
        )

        logger.info(
            f"Fetching report '{config.display_name}' for date {report_date} from {endpoint}"
        )
        response = self.client.post_report(endpoint, payload.to_form_dict())

        # Extract column headers if records exist
        headers: List[str] = []
        if response.data:
            headers = list(response.data[0].keys())

        return RawReportPayload(
            report_id=config.report_id,
            report_date=report_date,
            row_count=len(response.data),
            data=response.data,
            raw_headers=headers,
            raw_response=response.model_dump(),
        )
