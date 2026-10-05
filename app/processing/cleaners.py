"""Column cleaning and normalization for scheme memberlist reports."""

import logging
from typing import Any, Dict, List, Optional
import pandas as pd

from app.core.config import ReportItemConfig
from app.core.exceptions import TransformationError
from app.core.models import CleanedRecord

logger = logging.getLogger("pothys_reporting")


class SchemeReportCleaner:
    """
    Standard cleaner for Subhiksham and Viruksham memberlist reports.

    Transforms raw records (from JSON or parsed CSV) into normalized
    11-column CleanedRecord models using column header names.
    """

    def __init__(self, config: ReportItemConfig):
        self.config = config
        self.output_columns = config.output_columns

    def clean_records(self, raw_data: List[Dict[str, Any]]) -> List[CleanedRecord]:
        """
        Clean and order a list of raw record dictionaries according to the
        configured 11-column specification.
        """
        if not raw_data:
            logger.warning("Empty raw data received by cleaner.")
            return []

        cleaned: List[CleanedRecord] = []
        for idx, row in enumerate(raw_data):
            # Header name matching (case-insensitive fallback)
            row_normalized = {k.strip().upper(): v for k, v in row.items()}

            # Extract mandatory deduplication key
            msno = str(row_normalized.get("MSNO", "")).strip()
            if not msno:
                logger.warning(f"Row {idx} is missing mandatory MSNO. Raw row: {row}")

            # Safe amount parsing
            raw_recamount = row_normalized.get("RECAMOUNT", 0.0)
            try:
                recamount = float(str(raw_recamount).replace(",", "").strip() or 0.0)
            except (ValueError, TypeError):
                logger.warning(f"Invalid RECAMOUNT value '{raw_recamount}' at row {idx}. Defaulting to 0.0.")
                recamount = 0.0

            record = CleanedRecord(
                COSTNAME=str(row_normalized.get("COSTNAME", "")).strip() or None,
                CLIENTID=str(row_normalized.get("CLIENTID", "")).strip() or None,
                GROUPCODE=str(row_normalized.get("GROUPCODE", "")).strip() or None,
                MSNO=msno,
                NAME=str(row_normalized.get("NAME", "")).strip() or None,
                SCHDATE=str(row_normalized.get("SCHDATE", "")).strip() or None,
                RECAMOUNT=recamount,
                MOBILENO=str(row_normalized.get("MOBILENO", "")).strip() or None,
                SCHEME=str(row_normalized.get("SCHEME", "")).strip() or None,
                COMMNAME=str(row_normalized.get("COMMNAME", "")).strip() or None,
                COMMCODE=str(row_normalized.get("COMMCODE", "")).strip() or None,
            )
            cleaned.append(record)

        logger.info(f"Cleaned {len(cleaned)} records for report '{self.config.report_id}'")
        return cleaned

    def clean_dataframe(self, df: pd.DataFrame, is_csv_with_preamble: bool = False) -> pd.DataFrame:
        """
        Clean pandas DataFrame representation of the report.

        Handles the 3 preamble rows if parsing from raw CSV.
        Selects and orders the 11 required columns by header name.
        """
        if is_csv_with_preamble and len(df) > self.config.preamble_rows:
            # Promote header row after preamble
            df.columns = df.iloc[self.config.preamble_rows - 1]
            df = df.iloc[self.config.preamble_rows:].reset_index(drop=True)

        # Normalize column names
        df.columns = [str(c).strip().upper() for c in df.columns]

        # Verify presence of expected output columns
        missing_cols = [c for c in self.output_columns if c not in df.columns]
        if missing_cols:
            raise TransformationError(
                f"Missing required columns in dataset: {missing_cols}",
                details={"available_columns": list(df.columns)},
            )

        # Select exactly the 11 columns in required order
        ordered_df = df[self.output_columns].copy()

        # Clean amount column
        if "RECAMOUNT" in ordered_df.columns:
            ordered_df["RECAMOUNT"] = (
                ordered_df["RECAMOUNT"]
                .astype(str)
                .str.replace(",", "", regex=False)
                .str.strip()
            )
            ordered_df["RECAMOUNT"] = pd.to_numeric(ordered_df["RECAMOUNT"], errors="coerce").fillna(0.0)

        return ordered_df
