"""
Dynamic Monthly Source Discovery Engine for Google Sheets Workbooks.

Automatically resolves, validates, and binds month-specific worksheets
(Subhiksham, Viruksham, Scheme Closing, DigiGold/DigiSilver raw & pivot tabs)
based on the target reporting date.

Enforces:
- Dynamic metadata inspection (zero hardcoding of month names).
- Exact month boundary isolation (never silently mixes or falls back to older months).
- Header structure validation.
- Clear error reporting when target tabs do not exist.
"""

from datetime import date
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from app.core.exceptions import GoogleSheetsError
from app.google_sheets.client import GoogleSheetsService

logger = logging.getLogger("pothys_reporting")


class MonthlySourceNotFoundError(GoogleSheetsError):
    """Raised when a required monthly worksheet is missing from a workbook."""
    pass


class MonthlySourceHeaderValidationError(GoogleSheetsError):
    """Raised when a discovered monthly worksheet has unexpected or invalid columns."""
    pass


# Canonical month representations
MONTH_SHORT_NAMES = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sept", 10: "Oct", 11: "Nov", 12: "Dec"
}

# Alternate short names observed in historical sheets (e.g. "Sep" vs "Sept")
MONTH_SHORT_ALIASES = {
    9: ["Sept", "Sep"],
}

MONTH_FULL_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"
}

MONTH_UPPER_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"
}


class MonthlySourceInfo(BaseModel):
    """Structured container holding all resolved monthly source worksheets."""
    report_date: date
    year: int
    month: int
    month_name_short: str
    month_name_full: str
    month_abbr_upper: str

    enrollment_sheet_id: str
    ss_tab_name: str
    sv_tab_name: str
    consolidation_tab_name: Optional[str] = None

    closing_sheet_id: str
    closing_tab_name: str

    digi_sheet_id: str
    digi_raw_tab_name: str
    digi_pivot_tab_name: Optional[str] = None

    discovered_at: str = Field(default_factory=lambda: date.today().isoformat())


class MonthlySourceDiscovery:
    """
    Metadata-driven discovery engine that identifies and validates monthly
    Google Sheets tabs dynamically based on report_date.
    """

    def __init__(self, sheets_service: GoogleSheetsService):
        self.service = sheets_service

    def get_workbook_tab_names(self, spreadsheet_id: str) -> List[str]:
        """Fetch all worksheet tab titles for a given spreadsheet."""
        metadata = self.service.get_spreadsheet_metadata(spreadsheet_id)
        sheets_raw = metadata.get("sheets", [])
        return [
            s.get("properties", {}).get("title", "").strip()
            for s in sheets_raw
            if s.get("properties", {}).get("title")
        ]

    def discover_sources(
        self,
        report_date: date,
        enrollment_sheet_id: str,
        closing_sheet_id: str,
        digi_sheet_id: str,
        validate_headers: bool = True,
    ) -> MonthlySourceInfo:
        """
        Dynamically discover and validate all required monthly worksheets.

        Raises:
            MonthlySourceNotFoundError: If any required tab is missing.
            MonthlySourceHeaderValidationError: If discovered tabs fail schema validation.
        """
        logger.info(f"Discovering monthly worksheets for target date: {report_date.isoformat()}")

        year = report_date.year
        month = report_date.month
        month_short = MONTH_SHORT_NAMES.get(month, report_date.strftime("%b"))
        month_full = MONTH_FULL_NAMES.get(month, report_date.strftime("%B"))
        month_upper = MONTH_UPPER_ABBR.get(month, report_date.strftime("%b").upper())

        # ---------------------------------------------------------------------
        # 1. New Enrollment Workbook Discovery (SS, SV, Consolidate)
        # ---------------------------------------------------------------------
        enr_tabs = self.get_workbook_tab_names(enrollment_sheet_id)
        ss_tab = self._find_enrollment_tab(enr_tabs, "SS", month, year)
        if not ss_tab:
            raise MonthlySourceNotFoundError(
                f"Required Subhiksham monthly tab for {month_short} {year} not found in Enrollment workbook "
                f"({enrollment_sheet_id}). Available tabs: {', '.join(enr_tabs[:10])}..."
            )

        sv_tab = self._find_enrollment_tab(enr_tabs, "SV", month, year)
        if not sv_tab:
            raise MonthlySourceNotFoundError(
                f"Required Viruksham monthly tab for {month_short} {year} not found in Enrollment workbook "
                f"({enrollment_sheet_id}). Available tabs: {', '.join(enr_tabs[:10])}..."
            )

        cons_tab = self._find_consolidation_tab(enr_tabs, month, year)

        # ---------------------------------------------------------------------
        # 2. Scheme Closing Workbook Discovery
        # ---------------------------------------------------------------------
        close_tabs = self.get_workbook_tab_names(closing_sheet_id)
        closing_tab = self._find_closing_tab(close_tabs, month, year)
        if not closing_tab:
            raise MonthlySourceNotFoundError(
                f"Required Closing tab for {month_full} {year} not found in Closing workbook "
                f"({closing_sheet_id}). Available tabs: {', '.join(close_tabs[:10])}..."
            )

        # ---------------------------------------------------------------------
        # 3. Digi Workbook Discovery (Raw Tab & Pivot Tab)
        # ---------------------------------------------------------------------
        digi_tabs = self.get_workbook_tab_names(digi_sheet_id)
        digi_raw_tab = self._find_digi_raw_tab(digi_tabs, month, year)
        if not digi_raw_tab:
            raise MonthlySourceNotFoundError(
                f"Required DigiGold/DigiSilver raw tab '{month_upper}' not found in Digi workbook "
                f"({digi_sheet_id}). Available tabs: {', '.join(digi_tabs[:10])}..."
            )

        digi_pivot_tab = self._find_digi_pivot_tab(digi_tabs, month, year)

        # ---------------------------------------------------------------------
        # 4. Header & Schema Structure Validation
        # ---------------------------------------------------------------------
        if validate_headers:
            self._validate_sheet_headers(
                enrollment_sheet_id=enrollment_sheet_id,
                ss_tab=ss_tab,
                sv_tab=sv_tab,
                closing_sheet_id=closing_sheet_id,
                closing_tab=closing_tab,
                digi_sheet_id=digi_sheet_id,
                digi_raw_tab=digi_raw_tab,
            )

        info = MonthlySourceInfo(
            report_date=report_date,
            year=year,
            month=month,
            month_name_short=month_short,
            month_name_full=month_full,
            month_abbr_upper=month_upper,
            enrollment_sheet_id=enrollment_sheet_id,
            ss_tab_name=ss_tab,
            sv_tab_name=sv_tab,
            consolidation_tab_name=cons_tab,
            closing_sheet_id=closing_sheet_id,
            closing_tab_name=closing_tab,
            digi_sheet_id=digi_sheet_id,
            digi_raw_tab_name=digi_raw_tab,
            digi_pivot_tab_name=digi_pivot_tab,
        )

        logger.info(
            f"Successfully resolved monthly sources for {report_date.isoformat()}: "
            f"SS='{ss_tab}', SV='{sv_tab}', Closing='{closing_tab}', "
            f"DigiRaw='{digi_raw_tab}', DigiPivot='{digi_pivot_tab}'"
        )
        return info

    def _find_enrollment_tab(self, tabs: List[str], prefix: str, month: int, year: int) -> Optional[str]:
        """Match enrollment tab, e.g. 'SS - Nov' or 'SV - Nov'."""
        short_names = [MONTH_SHORT_NAMES.get(month, "")] + MONTH_SHORT_ALIASES.get(month, [])
        full_name = MONTH_FULL_NAMES.get(month, "")

        for s_name in short_names:
            candidate = f"{prefix} - {s_name}"
            for t in tabs:
                if t.strip().upper() == candidate.upper():
                    return t

        candidate_full = f"{prefix} - {full_name}"
        for t in tabs:
            if t.strip().upper() == candidate_full.upper():
                return t

        return None

    def _find_consolidation_tab(self, tabs: List[str], month: int, year: int) -> Optional[str]:
        """Match consolidation report tab, e.g. 'Consolidate Report - Nov'."""
        short_names = [MONTH_SHORT_NAMES.get(month, "")] + MONTH_SHORT_ALIASES.get(month, [])
        for s_name in short_names:
            cand = f"Consolidate Report - {s_name}"
            for t in tabs:
                if t.strip().upper() == cand.upper():
                    return t
        return None

    def _find_closing_tab(self, tabs: List[str], month: int, year: int) -> Optional[str]:
        """
        Match monthly closing tab, e.g. 'November - 2026', 'October - 2026', or 'April 2026'.
        """
        full_name = MONTH_FULL_NAMES.get(month, "")
        short_names = [MONTH_SHORT_NAMES.get(month, "")] + MONTH_SHORT_ALIASES.get(month, [])

        # Priority 1: 'Month - YYYY' (e.g. 'November - 2026')
        cand1 = f"{full_name} - {year}".upper()
        for t in tabs:
            if t.strip().upper() == cand1:
                return t

        # Priority 2: 'Month YYYY' (e.g. 'April 2026')
        cand2 = f"{full_name} {year}".upper()
        for t in tabs:
            if t.strip().upper() == cand2:
                return t

        # Priority 3: Short name 'Nov - 2026' or 'Nov 2026'
        for s_name in short_names:
            c_short1 = f"{s_name} - {year}".upper()
            c_short2 = f"{s_name} {year}".upper()
            for t in tabs:
                if t.strip().upper() in (c_short1, c_short2):
                    return t

        return None

    def _find_digi_raw_tab(self, tabs: List[str], month: int, year: int) -> Optional[str]:
        """Match DigiGold/DigiSilver raw data tab, e.g. 'NOV' or 'OCT'."""
        month_upper = MONTH_UPPER_ABBR.get(month, "")
        for t in tabs:
            if t.strip().upper() == month_upper:
                return t
        return None

    def _find_digi_pivot_tab(self, tabs: List[str], month: int, year: int) -> Optional[str]:
        """Match DigiGold/DigiSilver pivot tab, e.g. 'NOV Pivot' or 'OCT Pivot'."""
        month_upper = MONTH_UPPER_ABBR.get(month, "")
        cand = f"{month_upper} PIVOT"
        for t in tabs:
            if t.strip().upper() == cand:
                return t
        return None

    def _validate_sheet_headers(
        self,
        enrollment_sheet_id: str,
        ss_tab: str,
        sv_tab: str,
        closing_sheet_id: str,
        closing_tab: str,
        digi_sheet_id: str,
        digi_raw_tab: str,
    ) -> None:
        """Validate critical columns in discovered monthly worksheets."""
        # 1. Subhiksham header check
        ss_hdr = self.service.read_range(enrollment_sheet_id, f"'{ss_tab}'!A1:N1")
        if not ss_hdr or not ss_hdr[0]:
            raise MonthlySourceHeaderValidationError(f"Header row missing in '{ss_tab}'.")
        ss_cols = [str(c).strip().upper() for c in ss_hdr[0]]
        if "SCHDATE" not in ss_cols or "MOBILENO" not in ss_cols:
            raise MonthlySourceHeaderValidationError(
                f"Missing required columns in '{ss_tab}'. Found: {ss_cols}"
            )

        # 2. Viruksham header check
        sv_hdr = self.service.read_range(enrollment_sheet_id, f"'{sv_tab}'!A1:N1")
        if not sv_hdr or not sv_hdr[0]:
            raise MonthlySourceHeaderValidationError(f"Header row missing in '{sv_tab}'.")
        sv_cols = [str(c).strip().upper() for c in sv_hdr[0]]
        if "SCHDATE" not in sv_cols or "MOBILENO" not in sv_cols:
            raise MonthlySourceHeaderValidationError(
                f"Missing required columns in '{sv_tab}'. Found: {sv_cols}"
            )

        # 3. Closing header check
        close_hdr = self.service.read_range(closing_sheet_id, f"'{closing_tab}'!A1:H1")
        if not close_hdr or not close_hdr[0]:
            raise MonthlySourceHeaderValidationError(f"Header row missing in '{closing_tab}'.")
        close_cols = [str(c).strip().upper() for c in close_hdr[0]]
        if "DOCDATE" not in close_cols or "PASSBOOK" not in close_cols or "SHOWROOM" not in close_cols:
            raise MonthlySourceHeaderValidationError(
                f"Missing required columns in '{closing_tab}'. Found: {close_cols}"
            )

        # 4. Digi raw header check
        digi_hdr = self.service.read_range(digi_sheet_id, f"'{digi_raw_tab}'!A1:R1")
        if not digi_hdr or not digi_hdr[0]:
            raise MonthlySourceHeaderValidationError(f"Header row missing in '{digi_raw_tab}'.")
        digi_cols = [str(c).strip().upper() for c in digi_hdr[0]]
        if "SCHDATE" not in digi_cols or "SCHEME" not in digi_cols:
            raise MonthlySourceHeaderValidationError(
                f"Missing required columns in '{digi_raw_tab}'. Found: {digi_cols}"
            )

