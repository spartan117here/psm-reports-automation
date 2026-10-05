"""Column cleaning, type normalization, validation, and auditing for scheme reports."""

from collections import Counter, defaultdict
from datetime import date, datetime
import logging
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd

from app.core.config import ReportItemConfig
from app.core.exceptions import TransformationError
from app.core.models import CleanedRecord, CleaningResult

logger = logging.getLogger("pothys_reporting")

# Required retained fields that must not be null or blank for a valid record
REQUIRED_FIELDS = ("COSTNAME", "CLIENTID", "GROUPCODE", "MSNO", "NAME", "SCHDATE", "SCHEME")


def normalize_date_str(val: Any) -> Optional[str]:
    """
    Normalize SCHDATE to ISO YYYY-MM-DD representation while preserving the business date.
    Supports DD-MM-YYYY, YYYY-MM-DD, DD/MM/YYYY, etc.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None

    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    # If parsing as standard date fails, return stripped string
    return s


def normalize_identifier(val: Any) -> Optional[str]:
    """
    Normalize identifiers (CLIENTID, GROUPCODE, MSNO, MOBILENO, COMMCODE) as strings.
    Never converts to float; preserves leading zeros.
    """
    if val is None:
        return None
    s = str(val).strip()
    return s if s != "" else None


def normalize_amount(val: Any) -> Tuple[Optional[float], Optional[str]]:
    """
    Safely convert RECAMOUNT to a numeric float.
    Returns (amount, error_message).
    Does NOT silently convert invalid values to zero.
    """
    if val is None:
        return None, "Missing RECAMOUNT value"
    s = str(val).replace(",", "").strip()
    if not s:
        return None, "Empty RECAMOUNT value"
    try:
        fval = float(s)
        return fval, None
    except (ValueError, TypeError) as e:
        return None, f"Invalid numeric RECAMOUNT: '{val}' ({e})"


class SchemeReportCleaner:
    """
    Cleaner and validator for Subhiksham and Viruksham memberlist reports.

    Transforms raw records into normalized 11-column CleanedRecord models,
    validates data types and required fields, performs duplicate analysis,
    and produces audit metrics.
    """

    def __init__(self, config: ReportItemConfig):
        self.config = config
        self.output_columns = config.output_columns

    def clean_and_validate(
        self,
        raw_data: List[Dict[str, Any]],
        report_date: Optional[date] = None,
    ) -> CleaningResult:
        """
        Execute full cleaning, type normalization, duplicate analysis, and validation.
        """
        raw_count = len(raw_data)
        if not raw_data:
            logger.warning("Empty raw data received by cleaner.")
            return CleaningResult(
                report_id=self.config.report_id,
                report_date=report_date or date.today(),
                raw_count=0,
                cleaned_count=0,
            )

        # 1. Verify that all 11 required output columns exist in the raw dataset
        sample_keys = {k.strip().upper() for k in raw_data[0].keys()}
        missing_columns = [col for col in self.output_columns if col not in sample_keys]
        if missing_columns:
            raise TransformationError(
                f"Missing required columns in raw dataset: {missing_columns}",
                details={"available_columns": list(sample_keys)},
            )

        cleaned_records: List[CleanedRecord] = []
        blank_required_field_count = 0
        invalid_amount_count = 0
        invalid_count = 0
        total_recamount = 0.0

        costname_counts: Dict[str, int] = defaultdict(int)
        costname_amounts: Dict[str, float] = defaultdict(float)

        # 2. Iterate and normalize each row
        for idx, row in enumerate(raw_data):
            row_norm = {k.strip().upper(): v for k, v in row.items()}
            row_errors: List[str] = []
            is_valid = True

            # Identifiers as strings (preserve leading zeros)
            costname = normalize_identifier(row_norm.get("COSTNAME"))
            clientid = normalize_identifier(row_norm.get("CLIENTID"))
            groupcode = normalize_identifier(row_norm.get("GROUPCODE"))
            msno = normalize_identifier(row_norm.get("MSNO")) or ""
            name = normalize_identifier(row_norm.get("NAME"))
            mobileno = normalize_identifier(row_norm.get("MOBILENO"))
            scheme = normalize_identifier(row_norm.get("SCHEME"))
            commname = normalize_identifier(row_norm.get("COMMNAME"))
            commcode = normalize_identifier(row_norm.get("COMMCODE"))

            # Normalized date
            schdate = normalize_date_str(row_norm.get("SCHDATE"))

            # Safe amount parsing
            raw_recamount = row_norm.get("RECAMOUNT")
            recamount_val, amount_err = normalize_amount(raw_recamount)
            if amount_err:
                is_valid = False
                invalid_amount_count += 1
                row_errors.append(amount_err)
                recamount = 0.0  # Safe fallback for storage while flagged
            else:
                recamount = recamount_val if recamount_val is not None else 0.0
                total_recamount += recamount

            # Check required fields
            row_dict_for_check = {
                "COSTNAME": costname,
                "CLIENTID": clientid,
                "GROUPCODE": groupcode,
                "MSNO": msno,
                "NAME": name,
                "SCHDATE": schdate,
                "SCHEME": scheme,
            }
            has_blank_required = False
            for req_field, req_val in row_dict_for_check.items():
                if req_val is None or str(req_val).strip() == "":
                    has_blank_required = True
                    row_errors.append(f"Missing required field: {req_field}")

            if has_blank_required:
                is_valid = False
                blank_required_field_count += 1

            if not is_valid:
                invalid_count += 1

            # Track aggregations by COSTNAME
            c_key = costname or "UNKNOWN"
            costname_counts[c_key] += 1
            costname_amounts[c_key] += recamount

            record = CleanedRecord(
                COSTNAME=costname,
                CLIENTID=clientid,
                GROUPCODE=groupcode,
                MSNO=msno,
                NAME=name,
                SCHDATE=schdate,
                RECAMOUNT=recamount,
                MOBILENO=mobileno,
                SCHEME=scheme,
                COMMNAME=commname,
                COMMCODE=commcode,
                is_valid=is_valid,
                validation_errors=row_errors,
            )
            cleaned_records.append(record)

        # 3. Duplicate analysis for MSNO
        msno_counter = Counter(r.MSNO for r in cleaned_records if r.MSNO)
        dup_msnos = {k: v for k, v in msno_counter.items() if v > 1}
        duplicate_msno_count = len(dup_msnos)
        duplicate_msno_affected_count = sum(dup_msnos.values())

        # 4. Duplicate analysis for CLIENTID
        clientid_counter = Counter(r.CLIENTID for r in cleaned_records if r.CLIENTID)
        dup_clientids = {k: v for k, v in clientid_counter.items() if v > 1}
        duplicate_clientid_count = len(dup_clientids)
        duplicate_clientid_affected_count = sum(dup_clientids.values())

        # 5. Build COSTNAME summary
        costname_summary: Dict[str, Dict[str, Any]] = {}
        for c in sorted(costname_counts.keys()):
            costname_summary[c] = {
                "count": costname_counts[c],
                "total_amount": round(costname_amounts[c], 2),
            }

        effective_date = report_date or (
            datetime.strptime(cleaned_records[0].SCHDATE, "%Y-%m-%d").date()
            if cleaned_records and cleaned_records[0].SCHDATE
            else date.today()
        )

        return CleaningResult(
            report_id=self.config.report_id,
            report_date=effective_date,
            raw_count=raw_count,
            cleaned_count=len(cleaned_records),
            invalid_count=invalid_count,
            duplicate_msno_count=duplicate_msno_count,
            duplicate_msno_affected_count=duplicate_msno_affected_count,
            duplicate_clientid_count=duplicate_clientid_count,
            duplicate_clientid_affected_count=duplicate_clientid_affected_count,
            blank_required_field_count=blank_required_field_count,
            invalid_amount_count=invalid_amount_count,
            total_recamount=round(total_recamount, 2),
            costname_summary=costname_summary,
            records=cleaned_records,
        )

    def clean_records(self, raw_data: List[Dict[str, Any]]) -> List[CleanedRecord]:
        """
        Clean and order raw records into CleanedRecord models.
        Preserves backward compatibility with pipeline stages.
        """
        result = self.clean_and_validate(raw_data)
        return result.records

    def clean_dataframe(self, df: pd.DataFrame, is_csv_with_preamble: bool = False) -> pd.DataFrame:
        """
        Clean pandas DataFrame representation of the report.

        Handles the 3 preamble rows if parsing from raw CSV.
        Selects and orders exactly the 11 required columns by header name.
        """
        if is_csv_with_preamble and len(df) > self.config.preamble_rows:
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

        # Clean SCHDATE
        if "SCHDATE" in ordered_df.columns:
            ordered_df["SCHDATE"] = ordered_df["SCHDATE"].apply(normalize_date_str)

        # Clean amount column
        if "RECAMOUNT" in ordered_df.columns:
            ordered_df["RECAMOUNT"] = (
                ordered_df["RECAMOUNT"]
                .astype(str)
                .str.replace(",", "", regex=False)
                .str.strip()
            )
            ordered_df["RECAMOUNT"] = pd.to_numeric(ordered_df["RECAMOUNT"], errors="coerce").fillna(0.0)

        # Preserve identifiers as strings
        str_cols = ["COSTNAME", "CLIENTID", "GROUPCODE", "MSNO", "NAME", "MOBILENO", "SCHEME", "COMMNAME", "COMMCODE"]
        for col in str_cols:
            if col in ordered_df.columns:
                ordered_df[col] = ordered_df[col].astype(str).str.strip()
                ordered_df[col] = ordered_df[col].replace({"nan": None, "None": None, "": None})

        return ordered_df
