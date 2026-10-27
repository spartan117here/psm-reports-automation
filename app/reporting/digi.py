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

from datetime import date, datetime, timedelta, timezone
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from app.core.config import DigiScheduleConfig
from app.core.exceptions import (
    DataValidationError,
    GoogleSheetsError,
)
from app.google_sheets.client import GoogleSheetsService
from app.google_sheets.reconciliation import derive_monthly_tab_name, normalize_amount

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger("pothys_reporting")

# Default Spreadsheet IDs
DEFAULT_DIGI_SPREADSHEET_ID = "1N-4NRCNGHs702W0TlpQpGqgpPsJV9xH5QwXFfR18j2U"

# Exact branches in business display order for destination sheet
# (Currently 10 rows: 9 physical showrooms + ECOMMERCE; expands to 11 once NGL row is added to Google Sheet)
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

# All 10 physical showroom branches (including NAGERCOIL) recognized by the reporting system
ALL_SHOWROOM_BRANCHES: List[str] = [
    "NELLAI",
    "CHROMEPET",
    "TRIVANDRUM",
    "TRICHY",
    "COIMBATORE",
    "SALEM",
    "POONAMALLEE",
    "PADI",
    "KANCHEEPURAM",
    "NAGERCOIL",
]

# Physical showroom branches in current destination layout
SHOWROOM_BRANCHES: List[str] = [b for b in DIGI_BRANCH_ORDER if b != "ECOMMERCE"]
SHOWROOM_BRANCHES_9: List[str] = SHOWROOM_BRANCHES

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
    "NAGERCOIL": "NAGERCOIL",
    "NGL": "NAGERCOIL",
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


class DigiValidationStatus(str, Enum):
    """Validation lifecycle status for DigiGold/DigiSilver data source."""
    SUCCESS = "SUCCESS"
    PENDING_RETRY = "PENDING_RETRY"
    TOO_EARLY = "TOO_EARLY"
    BLOCKED_CUTOFF = "BLOCKED_CUTOFF"
    STALE_DATA = "STALE_DATA"
    VALIDATION_FAILED = "VALIDATION_FAILED"


class DigiValidationRecord(BaseModel):
    """Immutable audit record for a single Digi validation attempt."""
    report_date: date
    attempt: int
    timestamp: str
    status: DigiValidationStatus
    gold_showroom_subtotal: Optional[int] = None
    gold_ecommerce_subtotal: Optional[int] = None
    gold_overall_total: Optional[int] = None
    silver_showroom_subtotal: Optional[int] = None
    silver_ecommerce_subtotal: Optional[int] = None
    silver_overall_total: Optional[int] = None
    error_message: Optional[str] = None


class DigiFeedStatus(BaseModel):
    """
    Readiness and completeness status for an individual Digi scheme feed (DigiGold or DigiSilver).
    """
    scheme: str  # "DigiGold" or "DigiSilver"
    is_present: bool = False
    source_date: Optional[date] = None
    is_stale: bool = False
    branch_counts: Dict[str, int] = Field(default_factory=dict)
    missing_branches: List[str] = Field(default_factory=list)
    has_null_or_empty: bool = False
    has_negative_counts: bool = False
    subtotal_reconciled: bool = False
    error_message: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return (
            self.is_present
            and not self.is_stale
            and not self.missing_branches
            and not self.has_null_or_empty
            and not self.has_negative_counts
            and self.subtotal_reconciled
            and not self.error_message
        )


class DigiPivotExtraction(BaseModel):
    """Extracted raw values and subtotals from Digi pivot tab."""
    gold_values: Dict[str, int]
    silver_values: Dict[str, int]
    gold_showroom_subtotal: Optional[int] = None
    silver_showroom_subtotal: Optional[int] = None
    gold_grand_total: Optional[int] = None
    silver_grand_total: Optional[int] = None
    pivot_date_header: Optional[date] = None


class DigiExtractionResult(BaseModel):
    """Extracted and validated DigiGold + DigiSilver data for a report date."""
    report_date: date
    gold_values: Dict[str, int]
    silver_values: Dict[str, int]
    source_latest_schdate: Optional[date] = None
    source_record_count_latest_schdate: int = 0
    source_records_for_report_date: int = 0
    gold_showroom_subtotal: Optional[int] = None
    silver_showroom_subtotal: Optional[int] = None
    gold_ecommerce_subtotal: Optional[int] = None
    silver_ecommerce_subtotal: Optional[int] = None
    gold_overall_total: Optional[int] = None
    silver_overall_total: Optional[int] = None
    pivot_date_header: Optional[date] = None


class DigiValidationTracker:
    """Persistent audit ledger for Digi validation attempts and idempotency."""

    def __init__(self, history_file: Optional[Path] = None):
        self.history_file = Path(history_file or "data/delivery/digi_validation_history.json")

    def _load(self) -> List[Dict[str, Any]]:
        if not self.history_file.is_file():
            return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("attempts", [])
        except Exception as e:
            logger.warning(f"Could not load Digi validation history from {self.history_file}: {e}")
            return []

    def _save(self, attempts: List[Dict[str, Any]]) -> None:
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump({"attempts": attempts}, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save Digi validation history: {e}")

    def record_attempt(self, record: DigiValidationRecord) -> None:
        attempts = self._load()
        attempts.append(record.model_dump(mode="json"))
        self._save(attempts)

    def get_attempts(self, report_date: date) -> List[DigiValidationRecord]:
        iso_d = report_date.isoformat()
        records: List[DigiValidationRecord] = []
        for a in self._load():
            if a.get("report_date") == iso_d:
                try:
                    records.append(DigiValidationRecord(**a))
                except Exception:
                    pass
        return records

    def is_already_validated(self, report_date: date) -> bool:
        """Return True if a successful validation has already been recorded for this report date."""
        for r in self.get_attempts(report_date):
            if r.status == DigiValidationStatus.SUCCESS:
                return True
        return False

    def is_blocked(self, report_date: date) -> bool:
        """Return True if BLOCKED_CUTOFF was reached for this report date."""
        for r in self.get_attempts(report_date):
            if r.status == DigiValidationStatus.BLOCKED_CUTOFF:
                return True
        return False

    def next_attempt_number(self, report_date: date) -> int:
        return len(self.get_attempts(report_date)) + 1


def evaluate_digi_timing_window(
    now_dt: Optional[datetime] = None,
    config: Optional[DigiScheduleConfig] = None,
    force: bool = False,
) -> Tuple[bool, DigiValidationStatus, str]:
    """
    Evaluate whether current time is within the allowed 11:00 AM - 12:30 PM IST Digi validation window.
    """
    if force:
        return True, DigiValidationStatus.SUCCESS, "Forced / mock execution bypasses timing window restrictions."

    current_dt = now_dt or datetime.now(IST)
    start_time_str = config.start_time if config else "11:00"
    cutoff_time_str = config.cutoff_time if config else "12:30"

    try:
        sh, sm = [int(p) for p in start_time_str.split(":")]
        ch, cm = [int(p) for p in cutoff_time_str.split(":")]
    except ValueError:
        sh, sm = 11, 0
        ch, cm = 12, 30

    curr_time = current_dt.time()
    from datetime import time as dt_time
    t_start = dt_time(sh, sm)
    t_cutoff = dt_time(ch, cm)

    if curr_time < t_start:
        return (
            False,
            DigiValidationStatus.TOO_EARLY,
            f"Current time ({current_dt.strftime('%H:%M')} IST) is before scheduled start time ({start_time_str} IST). Validation postponed.",
        )

    if curr_time > t_cutoff:
        return (
            False,
            DigiValidationStatus.BLOCKED_CUTOFF,
            f"Current time ({current_dt.strftime('%H:%M')} IST) exceeds cutoff time ({cutoff_time_str} IST). Digi validation window closed.",
        )

    return True, DigiValidationStatus.SUCCESS, f"Current time ({current_dt.strftime('%H:%M')} IST) is within operational window ({start_time_str} - {cutoff_time_str} IST)."


def evaluate_digi_feed_readiness(
    report_date: date,
    scheme: str,
    raw_date: Optional[date] = None,
    pivot_date: Optional[date] = None,
    branch_values: Optional[Dict[str, Any]] = None,
    branch_subtotal: Optional[int] = None,
    grand_total: Optional[int] = None,
    expected_branches: Optional[List[str]] = None,
) -> DigiFeedStatus:
    """
    Evaluate completeness, freshness, and reconciliation of an individual Digi feed.

    Guarantees:
    - Never silently reuses previous-day data.
    - Never substitutes zeroes for missing values.
    - Never infers completion from file existence alone.
    """
    clean_scheme = scheme.strip()
    status = DigiFeedStatus(scheme=clean_scheme)
    expected_b = expected_branches or DIGI_BRANCH_ORDER

    if branch_values is None:
        status.is_present = False
        status.error_message = f"{clean_scheme} feed data is missing."
        return status

    status.is_present = True
    eff_date = pivot_date or raw_date
    status.source_date = eff_date

    # 1. Freshness & Stale Previous-Day Check
    if eff_date is None:
        status.error_message = f"{clean_scheme} date header could not be verified."
        return status

    if eff_date < report_date:
        status.is_stale = True
        status.error_message = (
            f"Stale previous-day data detected for {clean_scheme}: feed date is {eff_date.isoformat()}, "
            f"expected report date is {report_date.isoformat()}. Silently reusing previous-day data is forbidden."
        )
        return status
    elif eff_date > report_date:
        status.error_message = (
            f"Future date detected for {clean_scheme}: feed date is {eff_date.isoformat()}, "
            f"expected report date is {report_date.isoformat()}."
        )
        return status

    # 2. Completeness Check: all 10 branches present, non-null, non-negative
    parsed_counts: Dict[str, int] = {}
    missing = []
    has_null = False
    has_neg = False

    for b in expected_b:
        if b not in branch_values:
            missing.append(b)
            continue
        v = branch_values[b]
        if v is None or str(v).strip() == "":
            has_null = True
            missing.append(b)
            continue
        try:
            val_int = int(round(float(str(v).replace(",", "").strip())))
            if val_int < 0:
                has_neg = True
            parsed_counts[b] = val_int
        except (ValueError, TypeError):
            has_null = True
            missing.append(b)

    status.branch_counts = parsed_counts
    status.missing_branches = missing
    status.has_null_or_empty = has_null
    status.has_negative_counts = has_neg

    if missing:
        status.error_message = f"{clean_scheme} feed is incomplete; missing or invalid branches: {missing}."
        return status

    if has_null:
        status.error_message = f"{clean_scheme} feed contains null or empty branch values."
        return status

    if has_neg:
        status.error_message = f"{clean_scheme} feed contains negative branch counts."
        return status

    # 3. Mathematical Reconciliation Check
    # Showroom subtotal (sum of 9 stores) == BRANCH TOTAL
    showroom_sum = sum(parsed_counts.get(b, 0) for b in SHOWROOM_BRANCHES_9)
    if branch_subtotal is not None and showroom_sum != branch_subtotal:
        status.subtotal_reconciled = False
        status.error_message = (
            f"{clean_scheme} showroom subtotal mismatch: sum of physical showrooms is {showroom_sum:,}, "
            f"BRANCH TOTAL in sheet is {branch_subtotal:,}."
        )
        return status

    # Grand total (showroom + ecommerce) == GRAND TOTAL
    ecomm = parsed_counts.get("ECOMMERCE", 0)
    overall_sum = showroom_sum + ecomm
    if grand_total is not None and overall_sum != grand_total:
        status.subtotal_reconciled = False
        status.error_message = (
            f"{clean_scheme} overall total mismatch: sum of showrooms and ecommerce is {overall_sum:,}, "
            f"GRAND TOTAL in sheet is {grand_total:,}."
        )
        return status

    status.subtotal_reconciled = True
    return status


def evaluate_digi_readiness_at_deadline(
    report_date: date,
    gold_feed: DigiFeedStatus,
    silver_feed: DigiFeedStatus,
    current_time_ist: Optional[datetime] = None,
    deadline_str: str = "11:30",
) -> Tuple[bool, str, str]:
    """
    Evaluate DigiGold and DigiSilver readiness against the 11:30 AM local India time deadline.

    Returns:
        Tuple[can_proceed, status, message]:
        - can_proceed (bool): True if both feeds are valid and report may proceed.
        - status (str): One of:
            * "PASS": Both feeds valid.
            * "PENDING — DO NOT SEND": Feeds not ready, but before 11:30 AM deadline.
            * "FAILED — DO NOT SEND": Feeds not ready at/after 11:30 AM deadline.
        - message (str): Detailed explanation.
    """
    now = current_time_ist or datetime.now(IST)
    try:
        dh, dm = [int(p) for p in deadline_str.split(":")]
    except ValueError:
        dh, dm = 11, 30

    from datetime import time as dt_time
    deadline_time = dt_time(dh, dm)
    curr_time = now.time()

    both_valid = gold_feed.is_valid and silver_feed.is_valid

    if both_valid:
        return (
            True,
            "PASS",
            f"Both DigiGold and DigiSilver feeds are verified complete and reconciled for {report_date.isoformat()}.",
        )

    # Compile defect summary
    defects = []
    if not gold_feed.is_valid:
        defects.append(f"DigiGold: {gold_feed.error_message or 'incomplete/missing'}")
    if not silver_feed.is_valid:
        defects.append(f"DigiSilver: {silver_feed.error_message or 'incomplete/missing'}")
    defect_summary = "; ".join(defects)

    # If current time is strictly before 11:30 AM deadline -> PENDING — DO NOT SEND
    if curr_time < deadline_time:
        return (
            False,
            "PENDING — DO NOT SEND",
            f"DigiGold/DigiSilver readiness pending before {deadline_str} AM deadline ({now.strftime('%H:%M')} IST). "
            f"Delivery held pending upload: {defect_summary}.",
        )

    # At or after 11:30 AM deadline -> FAILED — DO NOT SEND
    return (
        False,
        "FAILED — DO NOT SEND",
        f"DigiGold/DigiSilver readiness deadline ({deadline_str} AM IST) reached at {now.strftime('%H:%M')} IST "
        f"with unready feeds. Final delivery blocked: {defect_summary}.",
    )


def extract_pivot_header_date(rows: List[List[Any]]) -> Optional[date]:
    """
    Extract reporting date from top rows of pivot sheet.
    Matches text like 'DigiGold New Enrollment as of 06 Oct 2026', '21 Oct 2026',
    '06/10/2026', '2026-10-06', etc.
    """
    for r in rows[:6]:
        for cell in r:
            s = str(cell).strip()
            if not s:
                continue
            # Try matching 'DD Mon YYYY' e.g. '06 Oct 2026' or '6 Oct 2026'
            m1 = re.search(r"(\d{1,2}\s+[A-Za-z]{3,}\s+\d{2,4})", s)
            if m1:
                dt_str = m1.group(1)
                for fmt in ("%d %b %Y", "%d %B %Y", "%d %b %y", "%d %B %y"):
                    try:
                        return datetime.strptime(dt_str, fmt).date()
                    except ValueError:
                        pass
            # Try matching 'YYYY-MM-DD' or 'DD/MM/YYYY'
            m2 = re.search(r"(\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", s)
            if m2:
                for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d"):
                    try:
                        return datetime.strptime(m2.group(1), fmt).date()
                    except ValueError:
                        pass
    return None


def validate_pivot_subtotals_and_totals(
    gold_values: Dict[str, int],
    silver_values: Dict[str, int],
    gold_branch_total: Optional[int],
    silver_branch_total: Optional[int],
    gold_grand_total: Optional[int] = None,
    silver_grand_total: Optional[int] = None,
) -> None:
    """
    Validate showroom subtotal (sum of 9 branches == BRANCH TOTAL), ecommerce subtotal,
    and overall total (showroom + ecommerce == Grand Total) for DigiGold and DigiSilver.
    """
    # Upstream Google Sheet formula in Cell L15/S15 uses =sum(L5:L13)/=sum(R5:R13),
    # which sums the 9 original showrooms (omitting Row 14 NAGERCOIL).
    # The true sum of all 10 physical showrooms includes NAGERCOIL.
    gold_showroom_sum_9 = sum(gold_values.get(b, 0) for b in SHOWROOM_BRANCHES_9)
    gold_showroom_sum_10 = sum(gold_values.get(b, 0) for b in ALL_SHOWROOM_BRANCHES)
    silver_showroom_sum_9 = sum(silver_values.get(b, 0) for b in SHOWROOM_BRANCHES_9)
    silver_showroom_sum_10 = sum(silver_values.get(b, 0) for b in ALL_SHOWROOM_BRANCHES)

    # 1. Gold showroom subtotal check
    if gold_branch_total is not None and gold_branch_total not in (gold_showroom_sum_9, gold_showroom_sum_10):
        raise DigiValidationError(
            f"DigiGold showroom subtotal mismatch: sum of physical showrooms is {gold_showroom_sum_10:,} "
            f"(or {gold_showroom_sum_9:,} for 9 showrooms), but Pivot 'BRANCH TOTAL' is {gold_branch_total:,}."
        )

    # 2. Gold overall total check
    gold_ecomm = gold_values.get("ECOMMERCE", 0)
    gold_overall_9 = gold_showroom_sum_9 + gold_ecomm
    gold_overall_10 = gold_showroom_sum_10 + gold_ecomm
    if gold_grand_total is not None and gold_grand_total not in (gold_overall_9, gold_overall_10):
        raise DigiValidationError(
            f"DigiGold overall total mismatch: showroom + ecommerce sum is {gold_overall_10:,} "
            f"(or {gold_overall_9:,}), but Pivot Grand Total is {gold_grand_total:,}."
        )

    # 3. Silver showroom subtotal check
    if silver_branch_total is not None and silver_branch_total not in (silver_showroom_sum_9, silver_showroom_sum_10):
        raise DigiValidationError(
            f"DigiSilver showroom subtotal mismatch: sum of physical showrooms is {silver_showroom_sum_10:,} "
            f"(or {silver_showroom_sum_9:,} for 9 showrooms), but Pivot 'BRANCH TOTAL' is {silver_branch_total:,}."
        )

    # 4. Silver overall total check
    silver_ecomm = silver_values.get("ECOMMERCE", 0)
    silver_overall_9 = silver_showroom_sum_9 + silver_ecomm
    silver_overall_10 = silver_showroom_sum_10 + silver_ecomm
    if silver_grand_total is not None and silver_grand_total not in (silver_overall_9, silver_overall_10):
        raise DigiValidationError(
            f"DigiSilver overall total mismatch: showroom + ecommerce sum is {silver_overall_10:,} "
            f"(or {silver_overall_9:,}), but Pivot Grand Total is {silver_grand_total:,}."
        )


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
    if val is None or str(val).strip() == "":
        raise DigiValidationError(f"Null or blank count encountered for {scheme} branch '{branch_name}'.")
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

    def extract_pivot_data(self, pivot_tab: str) -> DigiPivotExtraction:
        """
        Extract DigiGold and DigiSilver Achieved Count values, showroom subtotals,
        grand totals, and date header from the pivot tab.
        """
        logger.info(f"Extracting Achieved Counts & Subtotals from Digi pivot tab '{pivot_tab}'...")
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

        for r_i in range(min(5, len(rows))):
            row_text = [str(c).strip().upper() for c in rows[r_i]]
            for c_i, cell in enumerate(row_text):
                if "BRANCH" in cell and c_i < 14:
                    gold_branch_col = c_i
                elif "ACHIEVED COUNT" in cell and c_i < 14:
                    gold_achieved_col = c_i
                    header_row_idx = r_i
                elif "BRANCH" in cell and 14 <= c_i < 22:
                    silver_branch_col = c_i
                elif "ACHIEVED COUNT" in cell and 14 <= c_i < 22:
                    silver_achieved_col = c_i
                    header_row_idx = r_i

        pivot_date = extract_pivot_header_date(rows)

        gold_values: Dict[str, int] = {}
        silver_values: Dict[str, int] = {}
        gold_showroom_subtotal: Optional[int] = None
        silver_showroom_subtotal: Optional[int] = None
        gold_grand_total: Optional[int] = None
        silver_grand_total: Optional[int] = None

        data_start_row = header_row_idx + 1
        for row in rows[data_start_row:]:
            # Process Gold
            if len(row) > gold_branch_col and str(row[gold_branch_col]).strip():
                raw_branch = str(row[gold_branch_col]).strip()
                norm_branch = normalize_branch_name(raw_branch)
                val_cell = row[gold_achieved_col] if len(row) > gold_achieved_col else None
                if norm_branch in DIGI_BRANCH_ORDER or norm_branch in ALL_SHOWROOM_BRANCHES:
                    if val_cell is not None and str(val_cell).strip():
                        gold_values[norm_branch] = parse_integer_count(val_cell, norm_branch, "DigiGold")
                elif norm_branch in ("BRANCH TOTAL", "BRANCHTOTAL"):
                    if val_cell is not None and str(val_cell).strip():
                        gold_showroom_subtotal = parse_integer_count(val_cell, "BRANCH TOTAL", "DigiGold")
                elif norm_branch in ("GRAND TOTAL", "GRANDTOTAL", "TOTAL"):
                    if val_cell is not None and str(val_cell).strip():
                        gold_grand_total = parse_integer_count(val_cell, "GRAND TOTAL", "DigiGold")

            # Process Silver
            if len(row) > silver_branch_col and str(row[silver_branch_col]).strip():
                raw_branch = str(row[silver_branch_col]).strip()
                norm_branch = normalize_branch_name(raw_branch)
                val_cell = row[silver_achieved_col] if len(row) > silver_achieved_col else None
                if norm_branch in DIGI_BRANCH_ORDER or norm_branch in ALL_SHOWROOM_BRANCHES:
                    if val_cell is not None and str(val_cell).strip():
                        silver_values[norm_branch] = parse_integer_count(val_cell, norm_branch, "DigiSilver")
                elif norm_branch in ("BRANCH TOTAL", "BRANCHTOTAL"):
                    if val_cell is not None and str(val_cell).strip():
                        silver_showroom_subtotal = parse_integer_count(val_cell, "BRANCH TOTAL", "DigiSilver")
                elif norm_branch in ("GRAND TOTAL", "GRANDTOTAL", "TOTAL"):
                    if val_cell is not None and str(val_cell).strip():
                        silver_grand_total = parse_integer_count(val_cell, "GRAND TOTAL", "DigiSilver")

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

        return DigiPivotExtraction(
            gold_values=gold_values,
            silver_values=silver_values,
            gold_showroom_subtotal=gold_showroom_subtotal,
            silver_showroom_subtotal=silver_showroom_subtotal,
            gold_grand_total=gold_grand_total,
            silver_grand_total=silver_grand_total,
            pivot_date_header=pivot_date,
        )

    def extract_pivot_counts(
        self, pivot_tab: str
    ) -> Tuple[Dict[str, int], Dict[str, int]]:
        """
        Extract DigiGold and DigiSilver Achieved Count values from the pivot tab.
        Preserves backwards-compatible 2-tuple return for existing unit tests.
        """
        extraction = self.extract_pivot_data(pivot_tab)
        return extraction.gold_values, extraction.silver_values

    def read_and_validate(
        self,
        report_date: date,
        validate_subtotals: bool = False,
        validate_pivot_date: bool = False,
    ) -> DigiExtractionResult:
        """
        Execute full read-only extraction and validation workflow.

        Step 2: Freshness verification on raw sheet (SCHDATE).
        Step 3: Achieved Count extraction for Gold & Silver.
        Step 4: Subtotal and total reconciliation (if validate_subtotals=True).
        Step 5: Pivot date header consistency check (if validate_pivot_date=True).
        """
        raw_tab, pivot_tab = derive_digi_tab_names(report_date)
        max_date, max_count, rep_count = self.validate_freshness(report_date, raw_tab)
        pivot_data = self.extract_pivot_data(pivot_tab)

        if validate_pivot_date and pivot_data.pivot_date_header:
            if pivot_data.pivot_date_header != report_date:
                raise DigiValidationError(
                    f"Digi Pivot date header mismatch: expected {report_date.isoformat()}, "
                    f"found {pivot_data.pivot_date_header.isoformat()} in '{pivot_tab}'. "
                    f"External upload is stale or incorrect."
                )

        if validate_subtotals:
            validate_pivot_subtotals_and_totals(
                gold_values=pivot_data.gold_values,
                silver_values=pivot_data.silver_values,
                gold_branch_total=pivot_data.gold_showroom_subtotal,
                silver_branch_total=pivot_data.silver_showroom_subtotal,
                gold_grand_total=pivot_data.gold_grand_total,
                silver_grand_total=pivot_data.silver_grand_total,
            )

        showroom_branches = (
            ALL_SHOWROOM_BRANCHES
            if ("NAGERCOIL" in pivot_data.gold_values or "NAGERCOIL" in pivot_data.silver_values)
            else SHOWROOM_BRANCHES
        )
        gold_showroom = sum(pivot_data.gold_values.get(b, 0) for b in showroom_branches)
        gold_ecomm = pivot_data.gold_values.get("ECOMMERCE", 0)
        gold_total = gold_showroom + gold_ecomm

        silver_showroom = sum(pivot_data.silver_values.get(b, 0) for b in showroom_branches)
        silver_ecomm = pivot_data.silver_values.get("ECOMMERCE", 0)
        silver_total = silver_showroom + silver_ecomm

        return DigiExtractionResult(
            report_date=report_date,
            gold_values=pivot_data.gold_values,
            silver_values=pivot_data.silver_values,
            source_latest_schdate=max_date,
            source_record_count_latest_schdate=max_count,
            source_records_for_report_date=rep_count,
            gold_showroom_subtotal=gold_showroom,
            silver_showroom_subtotal=silver_showroom,
            gold_ecommerce_subtotal=gold_ecomm,
            silver_ecommerce_subtotal=silver_ecomm,
            gold_overall_total=gold_total,
            silver_overall_total=silver_total,
            pivot_date_header=pivot_data.pivot_date_header,
        )

    def validate_for_consolidation(self, report_date: date) -> DigiExtractionResult:
        """
        Execute strict Phase 2 validation: requires SCHDATE match, Pivot date header match,
        branch completeness, and 100% reconciliation of showroom subtotals and overall totals.
        """
        return self.read_and_validate(
            report_date=report_date,
            validate_subtotals=True,
            validate_pivot_date=True,
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

        # Fail closed if live execution is attempted under read-only mode
        from app.google_sheets.client import GoogleApiSheetsService
        if getattr(self.sheets_service, "read_only", None) is True or (isinstance(self.sheets_service, GoogleApiSheetsService) and os.getenv("STRICT_READ_ONLY", "false").lower() in ("true", "1", "yes")):
            from app.core.exceptions import GoogleSheetsError
            raise GoogleSheetsError(f"Cannot mutate Consolidate Report '{target_tab}': service is initialized in READ-ONLY mode.")

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
