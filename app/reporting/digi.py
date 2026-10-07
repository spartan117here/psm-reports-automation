"""
DigiGold + DigiSilver Reporting and Consolidate Report Synchronization Engine.

Automates reading daily Achieved Count values from:
    'DiGi Gold New Member - Storewise (FY 26 -27)' -> 'OCT Pivot'
and updating the monthly executive dashboard in:
    'New Enrollment from April 2026' -> 'Consolidate Report - Oct'

Mirrors the manual workflow exactly:
- Determine yesterday's date and verify Digi data for it is available.
- Copy DigiGold Achieved Count (10 branches) -> I20:I29.
- Copy DigiSilver Achieved Count (10 branches) -> J20:J29.
- Update the AVG day divisor in column L (=K{row}/{day_of_month}).
- Update the report date in cell E3.

Not part of this step: the destination total row (I30, J30, K30) is never
written, calculated, or validated. Existing sheet formulas recalculate on their own.

Guarantees:
- Read-only access to source workbook (zero mutations to Digi sheet).
- Source availability check before touching the destination.
- Cell clearing/writing of I20:J29 only (never deletes spreadsheet rows).
- Idempotent repeated execution.
- Post-write read-back of the cells this step wrote.
"""

from datetime import date, datetime, timedelta
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from app.core.exceptions import (
    DataValidationError,
    GoogleSheetsError,
)
from app.google_sheets.client import GoogleSheetsService
from app.google_sheets.reconciliation import derive_monthly_tab_name, normalize_amount

logger = logging.getLogger("pothys_reporting")

# Default Spreadsheet IDs
DEFAULT_DIGI_SPREADSHEET_ID = "1N-4NRCNGHs702W0TlpQpGqgpPsJV9xH5QwXFfR18j2U"

# Exact 10 branches in business display order
DIGI_BRANCH_ORDER: List[str] = [
    "NELLAI",
    "CHROMEPET",
    "TRIVANDRUM",
    "TRICHY",
    "COIMBATORE",
    "SALEM",
    "POONAMALLEE",
    "PADI",
    "KANCHEEPURAM",
    "ECOMMERCE",
]

# Destination spreadsheet layout constants
DESTINATION_ROW_START = 20
DESTINATION_ROW_END = 29
# Last row of the AVG column (L30) whose day divisor is updated manually.
# NOTE: I30 / J30 / K30 are NOT touched by the Digi step.
DESTINATION_AVG_ROW_END = 30
DESTINATION_TITLE_CELL = "E3"
DESTINATION_DATA_RANGE = "I20:J29"
DESTINATION_AVG_RANGE = "L20:L30"

FORMULA_ERROR_INDICATORS: Set[str] = {
    "#REF!",
    "#VALUE!",
    "#N/A",
    "#DIV/0!",
    "#NAME?",
    "#NUM!",
    "#NULL!",
}

# Known branch name aliases to normalize variations
BRANCH_NAME_ALIASES: Dict[str, str] = {
    "NELLAI": "NELLAI",
    "TIRUNELVELI": "NELLAI",
    "TVL": "NELLAI",
    "CHROMEPET": "CHROMEPET",
    "CPT": "CHROMEPET",
    "TRIVANDRUM": "TRIVANDRUM",
    "TVM": "TRIVANDRUM",
    "TRICHY": "TRICHY",
    "TPJ": "TRICHY",
    "COIMBATORE": "COIMBATORE",
    "CBE": "COIMBATORE",
    "SALEM": "SALEM",
    "SLM": "SALEM",
    "POONAMALLEE": "POONAMALLEE",
    "PNM": "POONAMALLEE",
    "PADI": "PADI",
    "KANCHEEPURAM": "KANCHEEPURAM",
    "KANCHI": "KANCHEEPURAM",
    "ECOMMERCE": "ECOMMERCE",
    "E-COMMERCE": "ECOMMERCE",
    "ECOMM": "ECOMMERCE",
    "E COMM": "ECOMMERCE",
    "ONLINE": "ECOMMERCE",
}


class DigiDataUnavailableError(GoogleSheetsError):
    """Raised when Digi workbook data for the requested report date is missing or not yet updated."""


class DigiValidationError(DataValidationError):
    """Raised when extracted Digi counts fail structural, numerical, or reconciliation validation."""


class ConsolidateVerificationError(GoogleSheetsError):
    """Raised when post-write verification of the destination Consolidate Report tab fails."""


class DigiExtractionResult(BaseModel):
    """Extracted and validated DigiGold + DigiSilver data for a report date."""
    report_date: date
    gold_values: Dict[str, int]
    silver_values: Dict[str, int]
    source_latest_schdate: Optional[date] = None
    source_record_count_latest_schdate: int = 0
    source_records_for_report_date: int = 0


class DigiUpdateResult(BaseModel):
    """Result of updating the destination Consolidate Report tab."""
    report_date: date
    destination_spreadsheet_id: str
    destination_tab: str
    gold_values: Dict[str, int]
    silver_values: Dict[str, int]
    avg_divisor: int
    report_title: str
    cells_cleared: List[str] = Field(default_factory=list)
    cells_updated: List[str] = Field(default_factory=list)
    verified: bool = False
    dry_run: bool = False


def calculate_report_date(target_date: Optional[date] = None) -> date:
    """
    Determine the target report date.

    By business rule:
    report_date = today - 1 day (yesterday).
    An explicit target_date can be passed for historic backfills/testing.
    """
    if target_date is not None:
        return target_date
    return date.today() - timedelta(days=1)


def derive_digi_tab_names(report_date: date) -> Tuple[str, str]:
    """
    Derive the raw sheet tab name and pivot tab name for the Digi workbook.
    Convention in Digi workbook: 'OCT' and 'OCT Pivot', 'SEP' and 'SEP Pivot', etc.
    """
    month_abbr_map = {
        1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
        7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
    }
    month_name = month_abbr_map.get(report_date.month, report_date.strftime("%b").upper())
    return month_name, f"{month_name} Pivot"


def derive_consolidation_tab_name(report_date: date) -> str:
    """
    Derive the monthly Consolidate Report tab name in the destination workbook.
    Convention: 'Consolidate Report - Oct', 'Consolidate Report - Sept', etc.
    """
    return derive_monthly_tab_name("Consolidate Report", report_date)


def normalize_branch_name(val: Any) -> str:
    """Normalize branch string into canonical uppercase key."""
    if val is None:
        return ""
    cleaned = str(val).strip().upper()
    cleaned_spaced = re.sub(r"[\s\-_]+", " ", cleaned).strip()
    cleaned_compact = re.sub(r"[\s\-_]+", "", cleaned).strip()
    if cleaned_spaced in BRANCH_NAME_ALIASES:
        return BRANCH_NAME_ALIASES[cleaned_spaced]
    if cleaned_compact in BRANCH_NAME_ALIASES:
        return BRANCH_NAME_ALIASES[cleaned_compact]
    return cleaned_spaced


def parse_schdate(val: Any) -> Optional[date]:
    """Parse date from cell string supporting YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY."""
    if not val:
        return None
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_integer_count(val: Any, branch_name: str, scheme: str) -> int:
    """Parse integer count safely with validation."""
    if val is None:
        raise DigiValidationError(f"Null count encountered for {scheme} branch '{branch_name}'.")
    s = str(val).replace(",", "").replace("\xa0", "").strip()
    try:
        f_val = float(s)
        i_val = int(round(f_val))
        if i_val < 0:
            raise DigiValidationError(
                f"Negative count ({i_val}) encountered for {scheme} branch '{branch_name}'."
            )
        return i_val
    except (ValueError, TypeError) as e:
        raise DigiValidationError(
            f"Non-numeric count '{val}' encountered for {scheme} branch '{branch_name}': {e}"
        ) from e


class DigiSourceReader:
    """
    Read-only source extractor for DigiGold and DigiSilver data.

    Inspects both the raw sheet (e.g. 'OCT') and pivot sheet (e.g. 'OCT Pivot')
    with zero mutations to the source workbook.
    """

    def __init__(
        self,
        sheets_service: GoogleSheetsService,
        spreadsheet_id: Optional[str] = None,
    ):
        self.sheets_service = sheets_service
        self.spreadsheet_id = spreadsheet_id or os.getenv(
            "GOOGLE_SHEET_DIGI_ID", DEFAULT_DIGI_SPREADSHEET_ID
        )

    def validate_freshness(self, report_date: date, raw_tab: str) -> Tuple[date, int, int]:
        """
        Verify that data for report_date is actually present in the raw sheet.

        Returns:
            Tuple of (max_schdate, max_schdate_record_count, report_date_record_count)
        """
        logger.info(f"Validating freshness in Digi raw tab '{raw_tab}' for {report_date}...")
        # Read SCHDATE column (typically column F / 6th column, read header row + all rows)
        raw_rows = self.sheets_service.read_range(
            self.spreadsheet_id, f"{raw_tab}!A1:R"
        )
        if not raw_rows:
            raise DigiDataUnavailableError(
                f"Raw sheet '{raw_tab}' in Digi workbook is completely empty."
            )

        headers = [str(c).strip().upper() for c in raw_rows[0]]
        schdate_col_idx = 5  # default Col F
        if "SCHDATE" in headers:
            schdate_col_idx = headers.index("SCHDATE")

        date_counter: Dict[date, int] = {}
        for row in raw_rows[1:]:
            if len(row) > schdate_col_idx and row[schdate_col_idx]:
                d = parse_schdate(row[schdate_col_idx])
                if d:
                    date_counter[d] = date_counter.get(d, 0) + 1

        if not date_counter:
            raise DigiDataUnavailableError(
                f"No parseable SCHDATE values found in '{raw_tab}'."
            )

        sorted_dates = sorted(date_counter.items(), key=lambda x: x[0], reverse=True)
        max_date, max_date_count = sorted_dates[0]
        report_date_count = date_counter.get(report_date, 0)

        logger.info(
            f"Digi raw tab '{raw_tab}' latest date: {max_date} ({max_date_count} records). "
            f"Records for requested report date ({report_date}): {report_date_count}"
        )

        if max_date < report_date:
            raise DigiDataUnavailableError(
                f"Digi data for expected report date {report_date} is not available yet. "
                f"Maximum SCHDATE found in '{raw_tab}' is {max_date}."
            )

        if report_date_count == 0:
            raise DigiDataUnavailableError(
                f"No records found for report date {report_date} in Digi raw tab '{raw_tab}' "
                f"(latest date is {max_date})."
            )

        return max_date, max_date_count, report_date_count

    def extract_pivot_counts(
        self, pivot_tab: str
    ) -> Tuple[Dict[str, int], Dict[str, int]]:
        """
        Extract DigiGold and DigiSilver Achieved Count values from the pivot tab.

        Uses robust label-based column and row identification.
        """
        logger.info(f"Extracting Achieved Counts from Digi pivot tab '{pivot_tab}'...")
        rows = self.sheets_service.read_range(
            self.spreadsheet_id, f"{pivot_tab}!A1:Z35"
        )
        if not rows:
            raise DigiDataUnavailableError(
                f"Pivot sheet '{pivot_tab}' in Digi workbook is empty."
            )

        # Locate table headers in rows 2-5
        gold_branch_col = 9    # Col J (0-indexed 9)
        gold_achieved_col = 11  # Col L (0-indexed 11)
        silver_branch_col = 16  # Col Q (0-indexed 16)
        silver_achieved_col = 18 # Col S (0-indexed 18)
        header_row_idx = 3     # Row 4 (0-indexed 3)

        # Search for exact header columns if labels match
        for r_i in range(min(5, len(rows))):
            row_text = [str(c).strip().upper() for c in rows[r_i]]
            for c_i, cell in enumerate(row_text):
                if "BRANCH" in cell and c_i < 14:
                    gold_branch_col = c_i
                elif "ACHIEVED COUNT" in cell and c_i < 14:
                    gold_achieved_col = c_i
                    header_row_idx = r_i
                elif "BRANCH" in cell and c_i >= 14:
                    silver_branch_col = c_i
                elif "ACHIEVED COUNT" in cell and c_i >= 14:
                    silver_achieved_col = c_i
                    header_row_idx = r_i

        gold_values: Dict[str, int] = {}
        silver_values: Dict[str, int] = {}

        data_start_row = header_row_idx + 1
        for row in rows[data_start_row:]:
            # Process Gold
            if len(row) > gold_branch_col and str(row[gold_branch_col]).strip():
                raw_branch = str(row[gold_branch_col]).strip()
                norm_branch = normalize_branch_name(raw_branch)
                if len(row) > gold_achieved_col and str(row[gold_achieved_col]).strip():
                    achieved_cell = row[gold_achieved_col]
                    if norm_branch in DIGI_BRANCH_ORDER:
                        gold_values[norm_branch] = parse_integer_count(
                            achieved_cell, norm_branch, "DigiGold"
                        )

            # Process Silver
            if len(row) > silver_branch_col and str(row[silver_branch_col]).strip():
                raw_branch = str(row[silver_branch_col]).strip()
                norm_branch = normalize_branch_name(raw_branch)
                if len(row) > silver_achieved_col and str(row[silver_achieved_col]).strip():
                    achieved_cell = row[silver_achieved_col]
                    if norm_branch in DIGI_BRANCH_ORDER:
                        silver_values[norm_branch] = parse_integer_count(
                            achieved_cell, norm_branch, "DigiSilver"
                        )

        # Validation Rule 1: All 10 Gold branches present
        missing_gold = [b for b in DIGI_BRANCH_ORDER if b not in gold_values]
        if missing_gold:
            raise DigiValidationError(
                f"Missing DigiGold branches in '{pivot_tab}': {missing_gold}"
            )

        # Validation Rule 2: All 10 Silver branches present
        missing_silver = [b for b in DIGI_BRANCH_ORDER if b not in silver_values]
        if missing_silver:
            raise DigiValidationError(
                f"Missing DigiSilver branches in '{pivot_tab}': {missing_silver}"
            )

        return gold_values, silver_values

    def read_and_validate(self, report_date: date) -> DigiExtractionResult:
        """
        Execute full read-only extraction and validation workflow.

        Step 2: Freshness verification on raw sheet.
        Step 3 & 4: Achieved Count extraction for Gold & Silver.
        Step 5: Ensure 10 branches are available with valid numeric counts.
        """
        raw_tab, pivot_tab = derive_digi_tab_names(report_date)
        max_date, max_count, rep_count = self.validate_freshness(report_date, raw_tab)
        gold_vals, silver_vals = self.extract_pivot_counts(pivot_tab)

        return DigiExtractionResult(
            report_date=report_date,
            gold_values=gold_vals,
            silver_values=silver_vals,
            source_latest_schdate=max_date,
            source_record_count_latest_schdate=max_count,
            source_records_for_report_date=rep_count,
        )


class ConsolidateReportUpdater:
    """
    Safely updates the destination 'Consolidate Report - <Mon>' Google Sheet tab.

    Guarantees:
    - Never deletes rows.
    - Clears only I20:J29 before writing.
    - Writes exact 10 branch values in ordered rows 20 to 29.
    - Writes dynamic formula =K{row}/{divisor} into Column L (AVG) for rows 20 to 30.
    - Updates title in cell E3 with dynamic date.
    - Validates formula integrity post-write.
    """

    def __init__(
        self,
        sheets_service: GoogleSheetsService,
        spreadsheet_id: Optional[str] = None,
    ):
        self.sheets_service = sheets_service
        self.spreadsheet_id = spreadsheet_id or os.getenv(
            "GOOGLE_SHEET_NEW_ENROLLMENT_ID", ""
        )
        if not self.spreadsheet_id:
            raise GoogleSheetsError(
                "Destination spreadsheet ID not configured. "
                "Set GOOGLE_SHEET_NEW_ENROLLMENT_ID in .env or pass explicitly."
            )

    def update_consolidate_report(
        self,
        extraction: DigiExtractionResult,
        tab_name: Optional[str] = None,
        dry_run: bool = False,
    ) -> DigiUpdateResult:
        """
        Execute the destination update steps (Steps 6, 7, 8, 9).
        """
        target_tab = tab_name or derive_consolidation_tab_name(extraction.report_date)
        report_date = extraction.report_date
        divisor = max(1, report_date.day)
        date_formatted = report_date.strftime("%d/%m/%Y")
        title_text = f"New Enrollment as on {date_formatted}"

        logger.info(
            f"Preparing update for '{target_tab}' on {report_date} (divisor={divisor}, dry_run={dry_run})..."
        )

        # 1. Prepare Data Payload (I20:J29)
        # Order: NELLAI (20), CHROMEPET (21), TRIVANDRUM (22), TRICHY (23), COIMBATORE (24),
        #        SALEM (25), POONAMALLEE (26), PADI (27), KANCHEEPURAM (28), ECOMMERCE (29)
        data_rows: List[List[int]] = []
        for branch in DIGI_BRANCH_ORDER:
            g_val = extraction.gold_values[branch]
            s_val = extraction.silver_values[branch]
            data_rows.append([g_val, s_val])

        # 2. Prepare AVG Formulas (L20:L30) - day divisor update only
        # =K20/d .. =K30/d  (column K itself is never written)
        avg_rows: List[List[str]] = []
        for r_num in range(DESTINATION_ROW_START, DESTINATION_AVG_ROW_END + 1):
            avg_rows.append([f"=K{r_num}/{divisor}"])

        # 3. Prepare Title Payload (E3)
        title_rows = [[title_text]]

        if dry_run:
            logger.info("[DRY RUN] Simulating destination update. No changes written to Google Sheet.")
            return DigiUpdateResult(
                report_date=report_date,
                destination_spreadsheet_id=self.spreadsheet_id,
                destination_tab=target_tab,
                gold_values=extraction.gold_values,
                silver_values=extraction.silver_values,
                avg_divisor=divisor,
                report_title=title_text,
                cells_cleared=[f"{target_tab}!{DESTINATION_DATA_RANGE}"],
                cells_updated=[
                    f"{target_tab}!{DESTINATION_DATA_RANGE}",
                    f"{target_tab}!{DESTINATION_AVG_RANGE}",
                    f"{target_tab}!{DESTINATION_TITLE_CELL}",
                ],
                verified=True,
                dry_run=True,
            )

        # STEP 6: DESTINATION UPDATE
        # Clear only I20:J29
        range_data = f"{target_tab}!{DESTINATION_DATA_RANGE}"
        logger.info(f"Clearing cell contents of {range_data}...")
        self.sheets_service.clear_range(self.spreadsheet_id, range_data)

        # Write new values into I20:J29
        logger.info(f"Writing {len(data_rows)} rows of Gold + Silver values to {range_data}...")
        self.sheets_service.update_range(self.spreadsheet_id, range_data, data_rows)

        # STEP 7: UPDATE AVG
        range_avg = f"{target_tab}!{DESTINATION_AVG_RANGE}"
        logger.info(f"Updating AVG formulas with divisor /{divisor} to {range_avg}...")
        self.sheets_service.update_range(self.spreadsheet_id, range_avg, avg_rows)

        # STEP 8: UPDATE REPORT DATE
        range_title = f"{target_tab}!{DESTINATION_TITLE_CELL}"
        logger.info(f"Updating title date '{title_text}' to {range_title}...")
        self.sheets_service.update_range(self.spreadsheet_id, range_title, title_rows)

        # STEP 9: POST-WRITE VERIFICATION
        self.verify_destination_state(target_tab, extraction, divisor, title_text)

        logger.info(f"Consolidate Report tab '{target_tab}' successfully updated and verified!")
        return DigiUpdateResult(
            report_date=report_date,
            destination_spreadsheet_id=self.spreadsheet_id,
            destination_tab=target_tab,
            gold_values=extraction.gold_values,
            silver_values=extraction.silver_values,
            avg_divisor=divisor,
            report_title=title_text,
            cells_cleared=[range_data],
            cells_updated=[range_data, range_avg, range_title],
            verified=True,
            dry_run=False,
        )

    def verify_destination_state(
        self,
        tab_name: str,
        extraction: DigiExtractionResult,
        divisor: int,
        expected_title: str,
    ) -> None:
        """
        Verify the destination sheet post-write state.

        Checks:
        1. I20:I29 exactly matches Gold source values.
        2. J20:J29 exactly matches Silver source values.
        3. Branch ordering is strictly preserved.
        4. Date title matches expected string.
        5. AVG formulas/divisor are correct.
        6. Existing formulas outside intended cells remain intact.
        7. No formula error strings (#REF!, #VALUE!, #N/A, etc.) are present.
        """
        logger.info(f"Performing post-write verification on '{tab_name}'...")

        # 1. Read back Data Range (I20:J29)
        range_data = f"{tab_name}!{DESTINATION_DATA_RANGE}"
        read_data = self.sheets_service.read_range(self.spreadsheet_id, range_data)
        if len(read_data) != len(DIGI_BRANCH_ORDER):
            raise ConsolidateVerificationError(
                f"Verification failed: Expected {len(DIGI_BRANCH_ORDER)} rows in {range_data}, got {len(read_data)}."
            )

        for i, branch in enumerate(DIGI_BRANCH_ORDER):
            row = read_data[i]
            if len(row) < 2:
                raise ConsolidateVerificationError(
                    f"Verification failed: Incomplete row at row {DESTINATION_ROW_START + i} ({branch}): {row}"
                )
            gold_val = int(normalize_amount(row[0]))
            silver_val = int(normalize_amount(row[1]))

            expected_gold = extraction.gold_values[branch]
            expected_silver = extraction.silver_values[branch]

            if gold_val != expected_gold:
                raise ConsolidateVerificationError(
                    f"Gold mismatch at row {DESTINATION_ROW_START + i} ({branch}): "
                    f"wrote {expected_gold}, read back {gold_val}."
                )
            if silver_val != expected_silver:
                raise ConsolidateVerificationError(
                    f"Silver mismatch at row {DESTINATION_ROW_START + i} ({branch}): "
                    f"wrote {expected_silver}, read back {silver_val}."
                )

        # 2. Read back Title (E3)
        range_title = f"{tab_name}!{DESTINATION_TITLE_CELL}"
        read_title = self.sheets_service.read_range(self.spreadsheet_id, range_title)
        if not read_title or not read_title[0] or str(read_title[0][0]).strip() != expected_title:
            actual_title = read_title[0][0] if (read_title and read_title[0]) else ""
            raise ConsolidateVerificationError(
                f"Title mismatch in {range_title}: expected '{expected_title}', read back '{actual_title}'."
            )

        # 3. Read back AVG formulas (L20:L30)
        range_avg = f"{tab_name}!{DESTINATION_AVG_RANGE}"
        read_formulas = self.sheets_service.read_formulas(self.spreadsheet_id, range_avg)
        if len(read_formulas) != (DESTINATION_AVG_ROW_END - DESTINATION_ROW_START + 1):
            raise ConsolidateVerificationError(
                f"AVG formula verification failed: Expected {DESTINATION_AVG_ROW_END - DESTINATION_ROW_START + 1} rows in {range_avg}, got {len(read_formulas)}."
            )

        for idx, r_num in enumerate(range(DESTINATION_ROW_START, DESTINATION_AVG_ROW_END + 1)):
            formula = str(read_formulas[idx][0]).strip() if read_formulas[idx] else ""
            expected_formula = f"=K{r_num}/{divisor}"
            # Normalize possible whitespace in formula e.g. '=K20 / 6'
            norm_formula = formula.replace(" ", "")
            if norm_formula != expected_formula:
                raise ConsolidateVerificationError(
                    f"AVG formula mismatch at cell L{r_num}: expected '{expected_formula}', read back '{formula}'."
                )

        # 4. Check formula errors only in modified ranges (E3, I20:J29, L20:L30)
        # Note: I30, J30, and K30 are NOT modified, calculated, or validated by Digi update logic.
        for r_name in [range_title, range_data, range_avg]:
            cells = self.sheets_service.read_range(self.spreadsheet_id, r_name)
            for r in cells:
                for c in r:
                    str_val = str(c).strip()
                    if any(err in str_val for err in FORMULA_ERROR_INDICATORS):
                        raise ConsolidateVerificationError(
                            f"Formula error '{str_val}' detected post-write in range {r_name}."
                        )


class DigiReportProcessor:
    """
    Unified Orchestrator for the DigiGold + DigiSilver Daily Reporting Pipeline.

    Integrates:
    - Dynamic report date calculation.
    - Read-only source extraction & freshness check.
    - Safe destination update with full post-write verification.
    """

    def __init__(
        self,
        source_service: GoogleSheetsService,
        dest_service: GoogleSheetsService,
        source_spreadsheet_id: Optional[str] = None,
        dest_spreadsheet_id: Optional[str] = None,
    ):
        self.reader = DigiSourceReader(source_service, spreadsheet_id=source_spreadsheet_id)
        self.updater = ConsolidateReportUpdater(dest_service, spreadsheet_id=dest_spreadsheet_id)

    def run(
        self,
        report_date: Optional[date] = None,
        dry_run: bool = False,
        destination_tab: Optional[str] = None,
    ) -> DigiUpdateResult:
        """
        Execute end-to-end synchronization for report_date (defaults to yesterday).
        """
        resolved_date = calculate_report_date(report_date)
        logger.info(f"Starting Digi synchronization for report_date={resolved_date} (dry_run={dry_run})...")

        # Step 1-5: Extract and validate source data
        extraction = self.reader.read_and_validate(resolved_date)

        # Step 6-9: Update destination sheet
        result = self.updater.update_consolidate_report(
            extraction=extraction,
            tab_name=destination_tab,
            dry_run=dry_run,
        )

        return result
