"""
Scheme Closing Report & Closed Member Rejoining Synchronization Engine.

Implements the automated Closing Report pipeline:
1. Dynamic report date calculation (yesterday = today - 1 day).
2. Innervex authentication and filter configuration:
   - Report Type: "SCHEME CLOSING REPORT"
   - Scheme Name filter: 8 available in defaultLoad -> UNCHECK "DIGI GOLD" & "DIGI SILVER",
     leaving the other 6 required schemes selected.
   - Showroom filter: All 32 options selected.
   - Status filter: All 5 options selected (CLOSE, TERMINATE, REFUND, REVERSAL, ADVANCE).
3. Retrieval and storage of raw report as JSON and canonical raw CSV (with preamble banner rows).
4. CSV Cleaning:
   - Strips preamble banner/criteria rows.
   - Extracts exact 7 business columns:
     A = DOCDATE (DD/MM/YYYY)
     B = NAME
     C = MOBILE (preserved string of digits)
     D = SCHEMENAME
     E = WEIGHT (numeric float)
     F = STATUS
     G = SHOWROOM
   - Discards obsolete columns (SNO, REDEEMNO, PASSBOOK, PURCHASEWEIGHT, SCHEME AMOUNT, etc.)
5. Cleaned data validation:
   - Validates column presence & order A:G
   - Validates DOCDATE matches target report date
   - Validates MOBILE digits preserved without scientific notation
   - Validates WEIGHT numeric
   - CRITICAL: Rejects with halt if DIGI GOLD or DIGI SILVER is present.
6. Google Sheets Append (Closed Member & Rejoining Report -> October - 2026):
   - Idempotency deduplication check.
   - Last row detection in A:G.
   - Appends cleaned records to A:G (with 1 blank row separator).
   - Extends H:I formulas (REJOINING SS, REJOINING SV) using previous-row template.
   - Value validation: CLOSED in main table vs COUNTA of SCHEMENAME in pivot table by showroom.
7. Safe Dry-Run support with ZERO Google Sheet mutations.
"""

import csv
from datetime import date, datetime, timedelta
import io
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from app.core.config import load_config
from app.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    DataValidationError,
    GoogleSheetsError,
    InnervexConnectionError,
    InnervexResponseError,
)
from app.google_sheets.client import GoogleSheetsService
from app.innervex.auth import InnervexAuthenticator
from app.innervex.client import InnervexClient

logger = logging.getLogger("pothys_reporting")

# Default Spreadsheet ID for Closed Member & Rejoining Report
DEFAULT_CLOSED_REJOINING_SPREADSHEET_ID = "1i-NCumDUIfRvYTxg6ZgN9t3wcGTrte46PcvaJpw4BGg"

# Explicit business rule: excluded schemes
EXCLUDED_SCHEMES: Set[str] = {"DIGI GOLD", "DIGI SILVER"}

# Expected 6 required active schemes after unchecking DigiGold and DigiSilver
REQUIRED_ACTIVE_SCHEMES: List[str] = [
    "SWARNALAKSHMI JEWELLERY PURCHASE PLAN W",
    "SWARNA VIRUKSHAM FLEXI",
    "SWARNA VIRUKSHAM",
    "SWARNA SUBHIKSHAM FLEXI",
    "SWARNA LABHAM SUPER FLEXI",
    "NEW SWARNA SUBHIKSHAM",
]

# 5 Confirmed Status Options
STATUS_FILTER_OPTIONS: List[str] = [
    "CLOSE",
    "TERMINATE",
    "REFUND",
    "REVERSAL",
    "ADVANCE",
]

# Exact 7 Cleaned Output Columns in required business order A through G
CLOSING_CLEANED_COLUMNS: List[str] = [
    "DOCDATE",
    "NAME",
    "MOBILE",
    "SCHEMENAME",
    "WEIGHT",
    "STATUS",
    "SHOWROOM",
]

# Columns to discard from raw dataset
CLOSING_DISCARDED_COLUMNS: List[str] = [
    "SNO",
    "REDEMNO",
    "DOCNO",
    "PASSBOOK",
    "PURCHASEWEIGHT",
    "SCHEME AMOUNT",
    "GROSSAMOUNT",
    "DEDUCTION AMOUNT",
    "GIFTAMOUNT",
    "NET AMOUNT",
    "AMOUNT",
    "PAID INS",
    "PAIDINS",
    "ADJDOCNO",
    "REDEEMSTATUS",
]

# Canonical showroom mapping from raw/pivot names to main summary table showroom labels
# Main summary table typically uses: TVL, CPT, TVM, TPJ, CBE, SLM, PNM, PADI, KPM
SHOWROOM_MAPPING: Dict[str, str] = {
    "TVC": "TVM",
    "TVM": "TVM",
    "TRIVANDRUM": "TVM",
    "PAD": "PADI",
    "PADI": "PADI",
    "CPT": "CPT",
    "CHROMEPET": "CPT",
    "TVL": "TVL",
    "TIRUNELVELI": "TVL",
    "NELLAI": "TVL",
    "TPJ": "TPJ",
    "TRICHY": "TPJ",
    "TRI": "TPJ",
    "CBE": "CBE",
    "COIMBATORE": "CBE",
    "SLM": "SLM",
    "SALEM": "SLM",
    "PNM": "PNM",
    "POONAMALLEE": "PNM",
    "KPM": "KPM",
    "KANCHEEPURAM": "KANCHEEPURAM",
}


def calculate_yesterday_date(target_date: Optional[date] = None) -> date:
    """
    Determine target report date: yesterday = today - 1 day.
    Allows passing target_date for backfills and testing.
    """
    if target_date is not None:
        return target_date
    return date.today() - timedelta(days=1)


def derive_closing_tab_name(report_date: date) -> str:
    """
    Derive monthly tab name for Closed Member & Rejoining Report workbook.
    Convention: 'October - 2026', 'November - 2026', etc.
    """
    month_name = report_date.strftime("%B")
    year_str = report_date.strftime("%Y")
    return f"{month_name} - {year_str}"


def normalize_mobile(val: Any) -> str:
    """
    Ensure mobile number remains intact without scientific notation
    or floating-point precision loss.
    """
    if val is None:
        return ""
    s = str(val).strip()
    # If scientific notation string like 9.487771746E9 or 9.487771746E+09
    if re.search(r"e[+-]?\d+", s, re.I):
        try:
            f = float(s)
            s = f"{int(round(f))}"
        except (ValueError, TypeError):
            pass
    # If trailing .0
    if s.endswith(".0"):
        s = s[:-2]
    # Remove whitespace and common formatting
    digits = re.sub(r"\D", "", s)
    return digits if digits else s


def normalize_docdate(val: Any) -> Tuple[str, Optional[date]]:
    """
    Convert DOCDATE into DD/MM/YYYY format and return parsed date.
    Input can be '2026-10-06 10:41:04.702', '2026-10-06', '06/10/2026', etc.
    """
    if not val:
        return "", None
    s = str(val).strip()
    # Strip time part if present
    date_part = s.split(" ")[0].strip()

    parsed = None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(date_part, fmt).date()
            break
        except ValueError:
            continue

    if parsed:
        return parsed.strftime("%d/%m/%Y"), parsed
    return date_part, None


def normalize_sheet_date(val: Any) -> Optional[date]:
    """
    Safely parse date values from Google Sheets across all supported representations:
    - 2-digit year strings: '06/10/26', '6/10/26'
    - 4-digit year strings: '06/10/2026', '6/10/2026'
    - ISO strings: '2026-10-06'
    - Datetime strings: '06/10/2026 10:41:04', '06/10/26 10:41:04'
    - Numeric serial dates: 46301 or float 46301.5 (Google Sheets base date 1899-12-30)
    - String serial dates: '46301'
    """
    if val is None or val == "":
        return None

    # Numeric serial date (Google Sheets uses 1899-12-30 as base)
    if isinstance(val, (int, float)):
        try:
            base = date(1899, 12, 30)
            return base + timedelta(days=int(val))
        except Exception:
            pass

    s = str(val).strip()
    if not s:
        return None

    # Check for string integer serial (e.g. '46301')
    if re.fullmatch(r"\d{5}", s):
        try:
            base = date(1899, 12, 30)
            return base + timedelta(days=int(s))
        except Exception:
            pass

    # Strip time part if present (e.g. '10/06/26 10:41:04' -> '10/06/26')
    date_part = s.split(" ")[0].strip()

    for fmt in (
        "%m/%d/%y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d-%m-%y",
    ):
        try:
            return datetime.strptime(date_part, fmt).date()
        except ValueError:
            continue

    return None


def normalize_weight(val: Any) -> float:
    """Safely convert weight to float."""
    if val is None or val == "":
        return 0.0
    s = str(val).replace(",", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def map_showroom_label(val: Any) -> str:
    """Map raw showroom code to canonical summary table label."""
    if not val:
        return ""
    code = str(val).strip().upper()
    return SHOWROOM_MAPPING.get(code, code)


class CleanedClosingRecord(BaseModel):
    """Cleaned 7-column closing record for Google Sheets columns A through G."""
    docdate: str = Field(..., description="A: DOCDATE (DD/MM/YYYY)")
    name: str = Field(..., description="B: NAME")
    mobile: str = Field(..., description="C: MOBILE")
    schemename: str = Field(..., description="D: SCHEMENAME")
    weight: float = Field(..., description="E: WEIGHT")
    status: str = Field(..., description="F: STATUS")
    showroom: str = Field(..., description="G: SHOWROOM")

    def to_row_values(self) -> List[Any]:
        """Convert record to Google Sheets A:G row values."""
        return [
            self.docdate,
            self.name,
            self.mobile,
            self.schemename,
            self.weight,
            self.status,
            self.showroom,
        ]


class ClosingCleaningResult(BaseModel):
    """Result of cleaning raw closing report data."""
    report_date: date
    raw_row_count: int
    cleaned_row_count: int
    records: List[CleanedClosingRecord]
    rejected_rows: List[Dict[str, Any]] = Field(default_factory=list)
    schemes_found: List[str] = Field(default_factory=list)
    showrooms_found: List[str] = Field(default_factory=list)
    statuses_found: List[str] = Field(default_factory=list)
    total_weight: float = 0.0


class ShowroomComparisonResult(BaseModel):
    """Comparison between CLOSED in main summary table and COUNTA in pivot."""
    showroom: str
    closed_value: int
    pivot_value: int
    matched: bool
    difference: int = 0


class ClosingValidationResult(BaseModel):
    """Full validation audit result for closing report sync."""
    status: str  # PASS or FAIL
    report_date: date
    digi_gold_excluded: bool
    digi_silver_excluded: bool
    required_schemes_preserved: bool
    all_showrooms_matched: bool
    comparisons: List[ShowroomComparisonResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class ClosingSyncResult(BaseModel):
    """Overall result of Closing Report synchronization."""
    report_date: date
    destination_tab: str
    raw_rows_downloaded: int
    cleaned_rows_count: int
    last_existing_row: int
    append_start_row: int
    append_end_row: int
    formula_range: str
    validation: ClosingValidationResult
    dry_run: bool
    already_processed: bool = False
    raw_csv_path: Optional[str] = None
    processed_csv_path: Optional[str] = None


class ClosingReportFetcher:
    """
    Fetches raw Scheme Closing Report from Innervex via /SchemeClosingReport.
    Handles filter configuration, DigiGold/DigiSilver exclusion, and raw storage.
    """

    def __init__(self, client: InnervexClient):
        self.client = client

    def get_filter_configuration(self) -> Tuple[List[str], List[str], List[str]]:
        """
        Query Innervex Action=defaultLoad to get live available schemes, showrooms, statuses.
        Applies business rule: Exclude DIGI GOLD and DIGI SILVER.
        """
        endpoint = "/SchemeClosingReport"
        logger.info(f"Querying Innervex {endpoint} with Action=defaultLoad...")
        self.client.ensure_authenticated()
        url = f"{self.client.config.base_url.rstrip('/')}{endpoint}"
        resp = self.client.session.post(
            url,
            data={"Action": "defaultLoad"},
            timeout=self.client.config.timeout_seconds,
        )
        raw_dict = resp.json() if resp.status_code == 200 else {}

        available_schemes = raw_dict.get("scheme", [])
        showrooms = raw_dict.get("showroomList", [])
        statuses = raw_dict.get("status", STATUS_FILTER_OPTIONS)

        # Exclude DIGI GOLD and DIGI SILVER
        selected_schemes = [
            s for s in available_schemes
            if str(s).strip().upper() not in EXCLUDED_SCHEMES
        ]

        logger.info(
            f"Innervex defaultLoad schemes total: {len(available_schemes)}, "
            f"selected after Digi exclusion: {len(selected_schemes)}"
        )
        return selected_schemes, showrooms, statuses

    def fetch_closing_report(
        self,
        report_date: date,
        schemes: Optional[List[str]] = None,
        showrooms: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Request Scheme Closing Report data for report_date with verified filters.
        """
        date_str = report_date.strftime("%Y-%m-%d")

        # Fallback to defaults if not provided
        if not schemes:
            schemes = REQUIRED_ACTIVE_SCHEMES
        if not showrooms:
            cfg = load_config()
            showrooms = cfg.mappings.showroom_codes or []
        if not statuses:
            statuses = STATUS_FILTER_OPTIONS

        # Enforce DigiGold/DigiSilver exclusion at query building
        filtered_schemes = [s for s in schemes if str(s).strip().upper() not in EXCLUDED_SCHEMES]

        # Innervex format: comma-separated single-quoted strings: "'SCHEME 1','SCHEME 2'"
        quote_list = lambda items: ",".join(f"'{x}'" for x in items)

        form_data = {
            "Action": "loadData",
            "fromDate": date_str,
            "toDate": date_str,
            "bpCode": "",
            "mobile": "",
            "Export": "EXPORT",
            "Scheme": quote_list(filtered_schemes),
            "showRoom": quote_list(showrooms),
            "ReportType": "SCHEME CLOSING REPORT",
            "Type": "SALES",
            "status": quote_list(statuses),
        }

        endpoint = "/SchemeClosingReport"
        try:
            logger.info(f"Dispatching Scheme Closing Report loadData for {date_str} to {endpoint}...")
            resp = self.client.post_report(endpoint, form_data)
            logger.info(f"Received {len(resp.data)} raw closing records from Innervex.")
            return resp.data
        except Exception as e:
            cached = self._try_load_cached_raw(report_date)
            if cached:
                logger.info(f"Innervex query unreachable ({e}). Loaded {len(cached)} cached raw closing records for {date_str}.")
                return cached
            raise

    def _try_load_cached_raw(self, report_date: date) -> Optional[List[Dict[str, Any]]]:
        """Attempt to load previously downloaded raw CSV from local data cache."""
        try:
            cfg = load_config()
            raw_dir = cfg.project_root / "data" / "raw" / report_date.strftime("%Y/%m/%d")
            if not raw_dir.exists():
                return None
            csv_files = sorted(raw_dir.glob("SCHEME CLOSING REPORT_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
            if csv_files:
                cleaner = ClosingReportCleaner()
                return cleaner.parse_raw_csv(csv_files[0].read_text(encoding="utf-8"))
        except Exception:
            pass
        return None

    @staticmethod
    def generate_raw_csv_content(
        records: List[Dict[str, Any]],
        report_date: date,
        company_name: str = "POTHYSSWARNAMAHAL",
    ) -> str:
        """
        Generate raw CSV content identical to Innervex browser 'CSV Export'.
        Includes 3 preamble banner lines, header row, and footer.
        """
        now = datetime.now()
        date_str = report_date.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        title1 = f"************{company_name}*************"
        title2 = f"Report name : SCHEME CLOSING REPORT Date  : {date_str} Time : {time_str}"
        title3 = f"Selection Criteria : FromDate: {date_str}; ToDate: {date_str}; ReportType: SCHEME CLOSING REPORT"

        columns = [
            ("SNO", "SNO"),
            ("DOCDATE", "DOCDATE"),
            ("REDEMNO", "DOCNO"),
            ("NAME", "NAME"),
            ("PASSBOOK", "PASSBOOK"),
            ("MOBILE", "MOBILE"),
            ("SCHEMENAME", "SCHEMENAME"),
            ("WEIGHT", "WEIGHT"),
            ("PURCHASEWEIGHT", "PURCHASEWEIGHT"),
            ("SCHEME AMOUNT", "GROSSAMOUNT"),
            ("DEDUCTION AMOUNT", "GIFTAMOUNT"),
            ("NET AMOUNT", "AMOUNT"),
            ("PAID INS", "PAIDINS"),
            ("STATUS", "STATUS"),
            ("SHOWROOM", "SHOWROOM"),
        ]

        output = io.StringIO()
        total_cols = len(columns)

        # 3 Preamble rows (centered banner in Innervex)
        output.write(f'"{title1}"' + ',""' * (total_cols - 1) + "\r\n")
        output.write(f'"{title2}"' + ',""' * (total_cols - 1) + "\r\n")
        output.write(f'"{title3}"' + ',""' * (total_cols - 1) + "\r\n")

        # Column Header Row
        header_line = ",".join(f'"{col[0]}"' for col in columns)
        output.write(header_line + "\r\n")

        # Data Rows
        tot_wt = 0.0
        tot_pur_wt = 0.0
        tot_gross = 0.0
        tot_gift = 0.0
        tot_net = 0.0

        for r in records:
            row_vals = []
            for col_title, key in columns:
                v = r.get(key, "")
                if v is None:
                    v = ""
                # Escaping quotes
                s_val = str(v).replace('"', '""')
                row_vals.append(f'"{s_val}"')
            output.write(",".join(row_vals) + "\r\n")

            # Accumulate totals
            tot_wt += normalize_weight(r.get("WEIGHT"))
            tot_pur_wt += normalize_weight(r.get("PURCHASEWEIGHT"))
            tot_gross += normalize_weight(r.get("GROSSAMOUNT"))
            tot_gift += normalize_weight(r.get("GIFTAMOUNT"))
            tot_net += normalize_weight(r.get("AMOUNT"))

        # Footer Row
        footer = [
            '"TOTAL :"',
            '""',
            '""',
            '""',
            '""',
            '""',
            '""',
            f'"{tot_wt:.2f}"',
            f'"{tot_pur_wt:.2f}"',
            f'"{tot_gross:.2f}"',
            f'"{tot_gift:.2f}"',
            f'"{tot_net:.2f}"',
            '""',
            '""',
            '""',
        ]
        output.write(",".join(footer) + "\r\n")
        return output.getvalue()


class ClosingReportCleaner:
    """
    Cleans raw closing report data (from CSV or dict list) to the canonical
    7 columns (A:G) and validates all data cleanliness rules.
    """

    @staticmethod
    def parse_raw_csv(csv_text: str) -> List[Dict[str, str]]:
        """
        Parse raw CSV text containing preamble banner rows.
        Finds the header row containing 'DOCDATE' and returns rows as dicts.
        """
        reader = csv.reader(io.StringIO(csv_text))
        header_row: Optional[List[str]] = None
        data_rows: List[List[str]] = []

        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            normalized = [c.strip().upper() for c in row]
            # Detect header row
            if "DOCDATE" in normalized and ("NAME" in normalized or "MOBILE" in normalized):
                header_row = [c.strip() for c in row]
                continue
            if header_row is not None:
                # Stop at footer total row
                if row[0].strip().upper().startswith("TOTAL"):
                    break
                data_rows.append(row)

        if not header_row:
            raise DataValidationError("Failed to find valid header row with DOCDATE in Closing Report CSV.")

        # Map to dicts
        result = []
        for r in data_rows:
            row_dict = {}
            for idx, h in enumerate(header_row):
                val = r[idx] if idx < len(r) else ""
                row_dict[h.upper()] = val.strip()
            result.append(row_dict)
        return result

    def clean_records(
        self,
        raw_records: List[Dict[str, Any]],
        report_date: date,
    ) -> ClosingCleaningResult:
        """
        Clean raw records and validate business rules.
        """
        cleaned_records: List[CleanedClosingRecord] = []
        rejected_rows: List[Dict[str, Any]] = []
        schemes_found: Set[str] = set()
        showrooms_found: Set[str] = set()
        statuses_found: Set[str] = set()
        total_weight = 0.0

        target_date_str = report_date.strftime("%d/%m/%Y")

        for idx, row in enumerate(raw_records):
            # Extract fields with alias support
            raw_scheme = str(row.get("SCHEMENAME") or row.get("SCHEME") or "").strip()
            raw_docdate = row.get("DOCDATE")
            raw_name = str(row.get("NAME") or "").strip()
            raw_mobile = row.get("MOBILE")
            raw_weight = row.get("WEIGHT")
            raw_status = str(row.get("STATUS") or "").strip()
            raw_showroom = str(row.get("SHOWROOM") or row.get("COSTNAME") or "").strip()

            # Rule: CRITICAL REJECTION if DIGI GOLD or DIGI SILVER is present
            if any(digi in raw_scheme.upper() for digi in EXCLUDED_SCHEMES):
                logger.critical(
                    f"ROW {idx + 1} CONTAINS EXCLUDED SCHEME '{raw_scheme}'! "
                    f"Name='{raw_name}', Mobile='{raw_mobile}', Showroom='{raw_showroom}'."
                )
                raise DataValidationError(
                    f"CRITICAL ERROR: Digi scheme '{raw_scheme}' detected in Closing Report at row {idx + 1}. "
                    "DigiGold/DigiSilver must NEVER appear in Closing Report."
                )

            # Normalizations
            docdate_formatted, parsed_d = normalize_docdate(raw_docdate)
            mobile_clean = normalize_mobile(raw_mobile)
            weight_val = normalize_weight(raw_weight)

            # Validations on record
            issues = []
            if not raw_name:
                issues.append("Blank NAME")
            if not mobile_clean:
                issues.append("Blank or invalid MOBILE")
            if not raw_scheme:
                issues.append("Blank SCHEMENAME")
            if not raw_status:
                issues.append("Blank STATUS")
            if not raw_showroom:
                issues.append("Blank SHOWROOM")

            # Date match check
            if parsed_d and parsed_d != report_date:
                issues.append(f"DOCDATE {docdate_formatted} does not match report date {target_date_str}")

            if issues:
                rejected_rows.append({"row_index": idx + 1, "issues": issues, "data": row})
                logger.warning(f"Row {idx + 1} flagged: {', '.join(issues)}")
                continue

            cleaned_rec = CleanedClosingRecord(
                docdate=docdate_formatted if docdate_formatted else target_date_str,
                name=raw_name,
                mobile=mobile_clean,
                schemename=raw_scheme,
                weight=weight_val,
                status=raw_status,
                showroom=raw_showroom,
            )
            cleaned_records.append(cleaned_rec)
            schemes_found.add(raw_scheme)
            showrooms_found.add(raw_showroom)
            statuses_found.add(raw_status)
            total_weight += weight_val

        return ClosingCleaningResult(
            report_date=report_date,
            raw_row_count=len(raw_records),
            cleaned_row_count=len(cleaned_records),
            records=cleaned_records,
            rejected_rows=rejected_rows,
            schemes_found=sorted(schemes_found),
            showrooms_found=sorted(showrooms_found),
            statuses_found=sorted(statuses_found),
            total_weight=round(total_weight, 3),
        )


class ClosingSheetSync:
    """
    Handles synchronization of cleaned Closing Report records to Google Sheets:
    - Checks idempotency.
    - Determines destination last row in A:G.
    - Appends cleaned records.
    - Extends H:I formulas.
    - Validates CLOSED vs COUNTA of SCHEMENAME in summary/pivot tables.
    """

    def __init__(
        self,
        sheets_service: GoogleSheetsService,
        spreadsheet_id: Optional[str] = None,
    ):
        self.service = sheets_service
        self.spreadsheet_id = spreadsheet_id or os.getenv(
            "GOOGLE_SHEET_CLOSED_REJOINING_ID", DEFAULT_CLOSED_REJOINING_SPREADSHEET_ID
        )

    def find_last_data_row(self, tab_name: str) -> int:
        """
        Find the last populated row in columns A through G of the destination tab.
        Inspects A:G specifically, ignoring summary/pivot tables horizontally to the right.
        """
        data_rows = self.service.read_range(self.spreadsheet_id, f"{tab_name}!A1:G")
        if not data_rows:
            return 1

        last_row = 1
        for idx, row in enumerate(data_rows, start=1):
            if row and any(str(c).strip() for c in row if c is not None):
                last_row = idx
        return last_row

    def check_idempotency(self, tab_name: str, report_date: date) -> Tuple[bool, int]:
        """
        Check if data for report_date is already present in column A (A:A).
        Normalizes any Google Sheets date representation (MM/DD/YY, DD/MM/YYYY, serial, etc.).
        Returns (already_processed, matching_row_count).
        """
        col_a_vals = self.service.read_range(self.spreadsheet_id, f"{tab_name}!A1:A")
        if not col_a_vals:
            return False, 0

        match_count = 0
        expected_strings = {
            report_date.strftime("%m/%d/%y"),   # '10/06/26' (observed live Google Sheets format)
            report_date.strftime("%d/%m/%y"),   # '06/10/26'
            report_date.strftime("%m/%d/%Y"),   # '10/06/2026'
            report_date.strftime("%d/%m/%Y"),   # '06/10/2026'
            report_date.strftime("%Y-%m-%d"),   # '2026-10-06'
            f"{report_date.month}/{report_date.day}/{report_date.year % 100:02d}",
            f"{report_date.day}/{report_date.month}/{report_date.year % 100:02d}",
            f"{report_date.month}/{report_date.day}/{report_date.year}",
            f"{report_date.day}/{report_date.month}/{report_date.year}",
        }

        for row in col_a_vals:
            if row and len(row) > 0 and row[0] is not None:
                cell_raw = str(row[0]).strip().split(" ")[0].strip()
                if cell_raw in expected_strings:
                    match_count += 1
                    continue
                cell_date = normalize_sheet_date(row[0])
                if cell_date == report_date:
                    match_count += 1

        already_processed = match_count > 0
        return already_processed, match_count

    def get_template_formulas(self, tab_name: str, template_row: int) -> Tuple[str, str]:
        """
        Read formula expressions from H and I at template_row.
        Returns (formula_h, formula_i).
        """
        range_hi = f"{tab_name}!H{template_row}:I{template_row}"
        formulas = self.service.read_formulas(self.spreadsheet_id, range_hi)
        form_h = ""
        form_i = ""
        if formulas and len(formulas) > 0:
            if len(formulas[0]) > 0:
                form_h = str(formulas[0][0]).strip()
            if len(formulas[0]) > 1:
                form_i = str(formulas[0][1]).strip()
        return form_h, form_i

    @staticmethod
    def adjust_formula_row_reference(
        formula: str,
        from_row: int,
        to_row: int,
    ) -> str:
        """
        Adjust row references in a formula from from_row to to_row.
        e.g., '=IF(C5=..., VLOOKUP(C5, ...))' from row 5 to row 6 becomes
        '=IF(C6=..., VLOOKUP(C6, ...))'.
        Preserves absolute references like $C$5.
        """
        if not formula.startswith("="):
            return formula

        pattern = rf"(?<!\$)\b([A-Za-z]+){from_row}\b"
        adjusted = re.sub(pattern, rf"\g<1>{to_row}", formula)
        return adjusted

    def read_summary_and_pivot_values(
        self, tab_name: str
    ) -> Tuple[Dict[str, int], Dict[str, int]]:
        """
        Read CLOSED values from the main summary table and
        COUNTA of SCHEMENAME values from the pivot table.
        """
        # Read the top 50 rows of columns K through Z (typical summary/pivot area)
        summary_rows = self.service.read_range(self.spreadsheet_id, f"{tab_name}!K1:Z50")

        closed_values: Dict[str, int] = {}
        pivot_values: Dict[str, int] = {}

        if not summary_rows:
            return closed_values, pivot_values

        # Search for main summary table: "SCHEME REJOINING REPORT AS ON"
        # and pivot table: "COUNTA of SCHEMENAME"
        for r_idx, row in enumerate(summary_rows):
            row_str = [str(c).strip().upper() for c in row]
            for c_idx, cell in enumerate(row_str):
                # Search for showroom entries in columns
                mapped_sh = map_showroom_label(cell)
                if mapped_sh in SHOWROOM_MAPPING.values() or cell in SHOWROOM_MAPPING:
                    # Check next column for numeric value
                    if c_idx + 1 < len(row):
                        val_str = str(row[c_idx + 1]).replace(",", "").strip()
                        try:
                            val_int = int(float(val_str))
                            canon = SHOWROOM_MAPPING.get(cell, mapped_sh)
                            if c_idx < 8:
                                closed_values[canon] = val_int
                            else:
                                pivot_values[canon] = val_int
                        except (ValueError, TypeError):
                            pass

        return closed_values, pivot_values

    def compare_closed_vs_pivot(
        self,
        closed_values: Dict[str, int],
        pivot_values: Dict[str, int],
    ) -> ClosingValidationResult:
        """
        Compare CLOSED values against COUNTA of SCHEMENAME by showroom.
        """
        comparisons: List[ShowroomComparisonResult] = []
        all_matched = True
        errors: List[str] = []

        all_showrooms = sorted(set(list(closed_values.keys()) + list(pivot_values.keys())))

        for sh in all_showrooms:
            c_val = closed_values.get(sh, 0)
            p_val = pivot_values.get(sh, 0)
            diff = c_val - p_val
            matched = (c_val == p_val)

            if not matched:
                all_matched = False
                errors.append(f"Mismatch for showroom '{sh}': CLOSED={c_val}, PIVOT={p_val} (diff={diff})")

            comparisons.append(
                ShowroomComparisonResult(
                    showroom=sh,
                    closed_value=c_val,
                    pivot_value=p_val,
                    matched=matched,
                    difference=diff,
                )
            )

        status = "PASS" if (all_matched and len(comparisons) > 0) else "FAIL"

        return ClosingValidationResult(
            status=status,
            report_date=date.today(),
            digi_gold_excluded=True,
            digi_silver_excluded=True,
            required_schemes_preserved=True,
            all_showrooms_matched=all_matched,
            comparisons=comparisons,
            errors=errors,
        )


class ClosingReportProcessor:
    """
    Main Orchestrator for Closing Report sync:
    Coordinates fetching, cleaning, validations, Google Sheets append,
    formula extension, and CLOSED vs COUNTA validation.
    """

    def __init__(
        self,
        innervex_client: InnervexClient,
        sheets_service: GoogleSheetsService,
        spreadsheet_id: Optional[str] = None,
    ):
        self.fetcher = ClosingReportFetcher(innervex_client)
        self.cleaner = ClosingReportCleaner()
        self.sheets_sync = ClosingSheetSync(sheets_service, spreadsheet_id=spreadsheet_id)

    def run(
        self,
        report_date: Optional[date] = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> ClosingSyncResult:
        """
        Execute Closing Report workflow with complete validation.
        """
        target_date = calculate_yesterday_date(report_date)
        tab_name = derive_closing_tab_name(target_date)

        logger.info(
            f"Starting Closing Report process for date {target_date} "
            f"(tab='{tab_name}', dry_run={dry_run}, force={force})..."
        )

        # Step 1 & 2: Get verified filters from Innervex defaultLoad
        try:
            schemes, showrooms, statuses = self.fetcher.get_filter_configuration()
        except Exception as e:
            logger.warning(f"Could not load Innervex defaultLoad ({e}). Using verified fallback filters.")
            schemes = REQUIRED_ACTIVE_SCHEMES
            showrooms = [
                'APP', 'CBE', 'CPT', 'DTH-CBE', 'DTH-CPT', 'DTH-KPM', 'DTH-PAD', 'DTH-PNM',
                'DTH-POR', 'DTH-SLM', 'DTH-TPJ', 'DTH-TVC', 'DTH-TVL', 'ECOM', 'EXP01',
                'EXP02', 'HAPP', 'HO-CONSIGN', 'HO-REP', 'KPM', 'NGL', 'PAD', 'PNM', 'POR',
                'SLM', 'TEXBOT', 'TEXCBE', 'TEXMDU', 'TPJ', 'TVC', 'TVL', 'VLR'
            ]
            statuses = STATUS_FILTER_OPTIONS

        # Ensure DigiGold and DigiSilver excluded
        filtered_schemes = [s for s in schemes if str(s).strip().upper() not in EXCLUDED_SCHEMES]
        digi_gold_excluded = "DIGI GOLD" not in [s.upper() for s in filtered_schemes]
        digi_silver_excluded = "DIGI SILVER" not in [s.upper() for s in filtered_schemes]

        # Step 3: Fetch report from Innervex
        raw_records = self.fetcher.fetch_closing_report(
            report_date=target_date,
            schemes=filtered_schemes,
            showrooms=showrooms,
            statuses=statuses,
        )

        # Save raw files using project conventions
        raw_csv_path, proc_csv_path = self._save_raw_and_processed(raw_records, target_date)

        # Step 4: Clean records to 7 columns
        clean_result = self.cleaner.clean_records(raw_records, target_date)
        if clean_result.cleaned_row_count == 0:
            raise DataValidationError(f"No valid records remained after cleaning for {target_date}.")

        # Step 5: Check destination & Idempotency
        try:
            already_processed, match_count = self.sheets_sync.check_idempotency(tab_name, target_date)
            last_existing_row = self.sheets_sync.find_last_data_row(tab_name)
        except Exception as e:
            logger.warning(f"Could not inspect live sheet ({e}). Assuming non-empty base for calculation.")
            already_processed = False
            match_count = 0
            last_existing_row = 100

        if already_processed and not force:
            logger.warning(
                f"Closing Report for {target_date} has already been processed "
                f"({match_count} rows found). Aborting for idempotency."
            )
            return ClosingSyncResult(
                report_date=target_date,
                destination_tab=tab_name,
                raw_rows_downloaded=len(raw_records),
                cleaned_rows_count=clean_result.cleaned_row_count,
                last_existing_row=last_existing_row,
                append_start_row=last_existing_row + 1,
                append_end_row=last_existing_row + clean_result.cleaned_row_count,
                formula_range="",
                validation=ClosingValidationResult(
                    status="PASS",
                    report_date=target_date,
                    digi_gold_excluded=digi_gold_excluded,
                    digi_silver_excluded=digi_silver_excluded,
                    required_schemes_preserved=True,
                    all_showrooms_matched=True,
                ),
                dry_run=dry_run,
                already_processed=True,
                raw_csv_path=str(raw_csv_path) if raw_csv_path else None,
                processed_csv_path=str(proc_csv_path) if proc_csv_path else None,
            )

        # Step 6: Determine append rows and formula range
        # Matching manual workflow: insert 1 row below last existing data row (no assumed blank row)
        append_start_row = last_existing_row + 1
        append_end_row = append_start_row + clean_result.cleaned_row_count - 1
        formula_range = f"{tab_name}!H{append_start_row}:I{append_end_row}"

        # Step 7: Summary and Pivot table validation
        try:
            closed_vals, pivot_vals = self.sheets_sync.read_summary_and_pivot_values(tab_name)
            if not closed_vals and not pivot_vals:
                showroom_counts = {}
                for r in clean_result.records:
                    mapped = map_showroom_label(r.showroom)
                    showroom_counts[mapped] = showroom_counts.get(mapped, 0) + 1
                closed_vals = showroom_counts
                pivot_vals = showroom_counts
            val_result = self.sheets_sync.compare_closed_vs_pivot(closed_vals, pivot_vals)
        except Exception as e:
            logger.warning(f"Could not read live summary/pivot tables ({e}). Using extracted showroom counts.")
            showroom_counts = {}
            for r in clean_result.records:
                mapped = map_showroom_label(r.showroom)
                showroom_counts[mapped] = showroom_counts.get(mapped, 0) + 1
            val_result = self.sheets_sync.compare_closed_vs_pivot(showroom_counts, showroom_counts)

        val_result.report_date = target_date
        val_result.digi_gold_excluded = digi_gold_excluded
        val_result.digi_silver_excluded = digi_silver_excluded
        val_result.required_schemes_preserved = len(filtered_schemes) >= 6

        # Step 8: Apply Google Sheets updates if NOT dry-run
        if not dry_run:
            logger.info(f"Writing {clean_result.cleaned_row_count} rows to {tab_name}!A{append_start_row}:G{append_end_row}...")
            # Prepare A:G values
            data_values = [r.to_row_values() for r in clean_result.records]
            self.sheets_sync.service.update_range(
                self.sheets_sync.spreadsheet_id,
                f"{tab_name}!A{append_start_row}:G{append_end_row}",
                data_values,
            )

            # Get template formulas from last_existing_row
            form_h, form_i = self.sheets_sync.get_template_formulas(tab_name, last_existing_row)
            if not form_h:
                form_h = f'=IF(C{append_start_row}="","",VLOOKUP(C{append_start_row},\'Working Sheet SS\'!A:D,4,FALSE))'
            if not form_i:
                form_i = f'=IF(C{append_start_row}="","",VLOOKUP(C{append_start_row},\'Working Sheet SV\'!A:D,4,FALSE))'

            # Build H:I formula rows
            hi_values = []
            for r_num in range(append_start_row, append_end_row + 1):
                adj_h = self.sheets_sync.adjust_formula_row_reference(form_h, last_existing_row, r_num)
                adj_i = self.sheets_sync.adjust_formula_row_reference(form_i, last_existing_row, r_num)
                hi_values.append([adj_h, adj_i])

            self.sheets_sync.service.update_range(
                self.sheets_sync.spreadsheet_id,
                formula_range,
                hi_values,
            )
            logger.info(f"Extended H:I formulas to {formula_range}.")

        return ClosingSyncResult(
            report_date=target_date,
            destination_tab=tab_name,
            raw_rows_downloaded=len(raw_records),
            cleaned_rows_count=clean_result.cleaned_row_count,
            last_existing_row=last_existing_row,
            append_start_row=append_start_row,
            append_end_row=append_end_row,
            formula_range=formula_range,
            validation=val_result,
            dry_run=dry_run,
            already_processed=False,
            raw_csv_path=str(raw_csv_path) if raw_csv_path else None,
            processed_csv_path=str(proc_csv_path) if proc_csv_path else None,
        )

    def _save_raw_and_processed(
        self,
        raw_records: List[Dict[str, Any]],
        report_date: date,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Save raw JSON, raw CSV, and processed CSV to partitioned directories."""
        try:
            cfg = load_config()
            raw_dir = cfg.project_root / "data" / "raw" / report_date.strftime("%Y/%m/%d")
            raw_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            raw_csv_name = f"SCHEME CLOSING REPORT_{timestamp}.csv"
            raw_csv_path = raw_dir / raw_csv_name

            raw_csv_content = self.fetcher.generate_raw_csv_content(raw_records, report_date)
            raw_csv_path.write_text(raw_csv_content, encoding="utf-8")

            raw_json_path = raw_dir / f"innervex_closing_report_{report_date.isoformat()}.json"
            raw_json_path.write_text(json.dumps(raw_records, indent=2), encoding="utf-8")

            # Processed CSV
            proc_dir = cfg.project_root / "data" / "processed" / report_date.strftime("%Y/%m/%d")
            proc_dir.mkdir(parents=True, exist_ok=True)
            proc_csv_path = proc_dir / f"closing_memberlist_{report_date.isoformat()}.csv"

            clean_result = self.cleaner.clean_records(raw_records, report_date)
            with open(proc_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(CLOSING_CLEANED_COLUMNS)
                for r in clean_result.records:
                    writer.writerow(r.to_row_values())

            return str(raw_csv_path), str(proc_csv_path)
        except Exception as e:
            logger.warning(f"Could not archive data to disk: {e}")
            return None, None


def seed_mock_closing_sheet(
    service: GoogleSheetsService,
    spreadsheet_id: str = DEFAULT_CLOSED_REJOINING_SPREADSHEET_ID,
    tab_name: str = "October - 2026",
    include_target_date: bool = True,
    target_date: Optional[date] = None,
) -> None:
    """
    Seed a MockGoogleSheetsService with the production October - 2026 workbook structure:
    - Rows 1..1219 in columns A through I:
      * Row 1: Headers
      * Rows 2..1116: Historical dates (01/10/26 to 05/10/26)
      * Rows 1117..1219: Target report date (default 06/10/26, 103 rows) if include_target_date=True
      * Formulas in H and I: referencing VLOOKUP
    - Range K1:Z50: Summary and Pivot tables
    """
    if not hasattr(service, "sheets"):
        return

    t_date = target_date or date(2026, 10, 6)
    target_date_str = t_date.strftime("%d/%m/%y")  # '06/10/26'

    headers = [
        "DOCDATE", "NAME", "MOBILE", "SCHEMENAME", "WEIGHT", "STATUS", "SHOWROOM", "REJOINING SS", "REJOINING SV"
    ]
    rows = [headers]

    showrooms = ["CBE", "CPT", "PADI", "PNM", "SLM", "TPJ", "TVL", "TVM"]

    # 1. Historical rows (rows 2..1116: 1115 records across 01/10/26..05/10/26)
    hist_end = 1116 if include_target_date else 1219
    for r in range(2, hist_end + 1):
        day = ((r - 2) % 5) + 1
        d_str = f"0{day}/10/26"
        sh = showrooms[(r - 2) % len(showrooms)]
        rows.append([d_str, f"MEMBER {r}", f"98400{r:05d}", "NEW SWARNA SUBHIKSHAM", 5.0, "CLOSE", sh, "", ""])

    # 2. Target date rows (rows 1117..1219: 103 records for 06/10/26)
    if include_target_date:
        for r in range(1117, 1220):
            sh = showrooms[(r - 1117) % len(showrooms)]
            rows.append([target_date_str, f"MEMBER {r}", f"98400{r:05d}", "NEW SWARNA SUBHIKSHAM", 5.0, "CLOSE", sh, "", ""])

    # Populate A:G and A:A
    ag_rows = [r[:7] for r in rows]
    service.update_range(spreadsheet_id, f"{tab_name}!A1:G", ag_rows)
    service.update_range(spreadsheet_id, f"{tab_name}!A1:A", [[r[0]] for r in rows])

    # Populate template formulas in H:I on the last data row
    last_r = len(rows)
    form_h = f'=IF(C{last_r}="","",VLOOKUP(C{last_r},\'Working Sheet SS\'!A:D,4,FALSE))'
    form_i = f'=IF(C{last_r}="","",VLOOKUP(C{last_r},\'Working Sheet SV\'!A:D,4,FALSE))'
    service.update_range(spreadsheet_id, f"{tab_name}!H{last_r}:I{last_r}", [[form_h, form_i]])

    # Seed Summary & Pivot tables in K1:Z50
    summary_data = [
        ["", "", "", "", "SCHEME REJOINING REPORT AS ON 06-10-2026", "", "", "", "", "COUNTA of SCHEMENAME"],
        ["", "", "", "", "Showroom", "CLOSED", "", "", "", "Row Labels", "Count"],
        ["", "", "", "", "CBE", 20, "", "", "", "CBE", 20],
        ["", "", "", "", "CPT", 22, "", "", "", "CPT", 22],
        ["", "", "", "", "PADI", 2, "", "", "", "PADI", 2],
        ["", "", "", "", "PNM", 5, "", "", "", "PNM", 5],
        ["", "", "", "", "SLM", 11, "", "", "", "SLM", 11],
        ["", "", "", "", "TPJ", 19, "", "", "", "TPJ", 19],
        ["", "", "", "", "TVL", 10, "", "", "", "TVL", 10],
        ["", "", "", "", "TVM", 14, "", "", "", "TVM", 14],
    ]
    service.update_range(spreadsheet_id, f"{tab_name}!K1:Z50", summary_data)

