"""Data transformation and Google Sheet row layout generators."""

import logging
from typing import Any, List, Optional
from app.core.models import CleanedRecord
from app.processing.location import LocationResolver

logger = logging.getLogger("pothys_reporting")

# Standard column order for SS/SV monthly tabs in Google Sheets (A through N)
GOOGLE_SHEET_ROW_ORDER = [
    "COSTNAME",      # A
    "CLIENTID",      # B
    "GROUPCODE",     # C
    "MSNO",          # D
    "NAME",          # E
    "SCHDATE",       # F
    "LOCATION_2",    # G
    "LOCATION",      # H
    "RECAMOUNT",     # I
    "MOBILENO",      # J
    "SCHEME",        # K
    "COMMNAME",      # L
    "COMMCODE",      # M
    "CODE_NAME",     # N
]


class RecordTransformer:
    """Enriches cleaned records with location metadata and formats for sheet export."""

    def __init__(self, location_resolver: LocationResolver):
        self.location_resolver = location_resolver

    def enrich_records(
        self, records: List[CleanedRecord], scheme_type: str = "ss"
    ) -> List[CleanedRecord]:
        """
        Enrich CleanedRecord instances with LOCATION, LOCATION_2, and CODE_NAME helper column.
        """
        enriched_list: List[CleanedRecord] = []
        for record in records:
            attribution = self.location_resolver.resolve(
                cost_name=record.COSTNAME,
                comm_code=record.COMMCODE,
                scheme_type=scheme_type,
            )

            # Generate helper column: "code - name"
            code = (record.COMMCODE or "").strip()
            name = (record.COMMNAME or "").strip()
            code_name = f"{code} - {name}" if (code and name) else (code or name or "")

            enriched_record = record.model_copy(
                update={
                    "LOCATION_2": attribution.location_2,
                    "LOCATION": attribution.location,
                    "CODE_NAME": code_name,
                }
            )
            enriched_list.append(enriched_record)

        return enriched_list

    @staticmethod
    def to_sheet_row_values(record: CleanedRecord) -> List[Any]:
        """
        Convert an enriched CleanedRecord to a list of row values matching Google Sheet columns A-N.
        """
        return [
            record.COSTNAME or "",
            record.CLIENTID or "",
            record.GROUPCODE or "",
            record.MSNO or "",
            record.NAME or "",
            record.SCHDATE or "",
            record.LOCATION_2 or "",
            record.LOCATION or "",
            record.RECAMOUNT,
            record.MOBILENO or "",
            record.SCHEME or "",
            record.COMMNAME or "",
            record.COMMCODE or "",
            record.CODE_NAME or "",
        ]
