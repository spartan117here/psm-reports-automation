"""Read-only discovery engine for Google Sheets workbook inspection."""

from collections import defaultdict
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.exceptions import GoogleSheetsError
from app.google_sheets.client import GoogleSheetsService

logger = logging.getLogger("pothys_reporting")

# Expected 14-column layout for monthly Subhiksham sheets (A through N)
EXPECTED_MONTHLY_COLUMNS = [
    "COSTNAME",      # A
    "CLIENTID",      # B
    "GROUPCODE",     # C
    "MSNO",          # D
    "NAME",          # E
    "SCHDATE",       # F
    "LOCATION 2",    # G (or LOCATION_2)
    "LOCATION",      # H
    "RECAMOUNT",     # I
    "MOBILENO",      # J
    "SCHEME",        # K
    "COMMNAME",      # L
    "COMMCODE",      # M
    "CODE - NAME",   # N (or CODE_NAME)
]

CLEANED_11_COLUMNS = [
    "COSTNAME", "CLIENTID", "GROUPCODE", "MSNO", "NAME",
    "SCHDATE", "RECAMOUNT", "MOBILENO", "SCHEME", "COMMNAME", "COMMCODE"
]


def sanitize_value(col_name: str, val: Any) -> Any:
    """Mask sensitive customer personal information for safe logging and reporting."""
    if val is None or val == "":
        return ""
    col_upper = col_name.upper().strip()
    s = str(val).strip()

    if any(k in col_upper for k in ["NAME", "CUST"]) and "COMM" not in col_upper and "COST" not in col_upper:
        if len(s) > 2:
            return s[0] + "*" * (len(s) - 2) + s[-1]
        return "***"

    if any(k in col_upper for k in ["MOBILE", "PHONE"]):
        if len(s) >= 4:
            return "*" * (len(s) - 4) + s[-4:]
        return "******"

    if any(k in col_upper for k in ["ADDRESS", "IDPROOF"]):
        return "[REDACTED_PII]"

    return s


def redact_spreadsheet_id(spreadsheet_id: str) -> str:
    """Safely redact Google Spreadsheet ID for display/logging."""
    if not spreadsheet_id:
        return ""
    if len(spreadsheet_id) <= 8:
        return "****"
    return spreadsheet_id[:4] + "..." + spreadsheet_id[-4:]


class SheetDiscoveryEngine:
    """
    Read-only discovery service that inspects live Google Sheets workbooks
    without performing any write, append, update, or clear operations.
    """

    def __init__(self, service: GoogleSheetsService):
        self.service = service

    def inspect_workbook(self, spreadsheet_id: str) -> Dict[str, Any]:
        """
        Execute comprehensive read-only discovery of the workbook.

        Returns structured discovery report containing:
        - metadata (title, total sheets)
        - discovered worksheets (titles, ids, dimensions)
        - relevant tabs categorized (Employees, Monthly, Consolidation)
        - detailed monthly sheet structure (headers, row count, formulas, sample rows)
        - October data presence check
        - 11-column mapping analysis
        """
        logger.info(f"Starting read-only discovery on spreadsheet: {spreadsheet_id}")
        metadata = self.service.get_spreadsheet_metadata(spreadsheet_id)

        title = metadata.get("properties", {}).get("title", "Unknown Workbook")
        sheets_raw = metadata.get("sheets", [])

        discovered_tabs: List[Dict[str, Any]] = []
        tab_names: List[str] = []

        for s in sheets_raw:
            props = s.get("properties", {})
            t_name = props.get("title", "")
            t_id = props.get("sheetId", 0)
            grid = props.get("gridProperties", {})
            tab_names.append(t_name)
            discovered_tabs.append({
                "title": t_name,
                "sheet_id": t_id,
                "row_count": grid.get("rowCount", 0),
                "column_count": grid.get("columnCount", 0),
            })

        # Categorize relevant tabs
        employees_tabs = [t for t in tab_names if "EMPLOYEE" in t.upper()]
        subhiksham_monthly = [t for t in tab_names if t.startswith("SS -") or "SUBHIKSHAM" in t.upper()]
        viruksham_monthly = [t for t in tab_names if t.startswith("SV -") or "VIRUKSHAM" in t.upper()]
        consolidation_tabs = [t for t in tab_names if "CONSOLIDAT" in t.upper()]

        report: Dict[str, Any] = {
            "spreadsheet_title": title,
            "spreadsheet_id": spreadsheet_id,
            "redacted_spreadsheet_id": redact_spreadsheet_id(spreadsheet_id),
            "total_sheets": len(sheets_raw),
            "all_tabs": discovered_tabs,
            "categorized_tabs": {
                "employees": employees_tabs,
                "subhiksham_monthly": subhiksham_monthly,
                "viruksham_monthly": viruksham_monthly,
                "consolidation": consolidation_tabs,
            },
            "subhiksham_inspection": {},
            "viruksham_inspection": {},
            "consolidation_inspection": {},
            "employees_inspection": {},
            "column_mapping_analysis": {},
            "october_data_status": {},
        }

        # Inspect relevant Subhiksham monthly sheet (prioritize latest, e.g. SS - Oct or SS - Sept)
        target_ss_tab = None
        for cand in ["SS - Oct", "SS - Sept", "SS - Aug"]:
            if cand in tab_names:
                target_ss_tab = cand
                break
        if not target_ss_tab and subhiksham_monthly:
            target_ss_tab = subhiksham_monthly[-1]

        if target_ss_tab:
            report["subhiksham_inspection"] = self._inspect_monthly_tab(spreadsheet_id, target_ss_tab)
            report["column_mapping_analysis"] = self._analyze_column_mapping(
                report["subhiksham_inspection"].get("headers", [])
            )
            report["october_data_status"] = self._check_october_data(spreadsheet_id, tab_names)

        # Inspect Viruksham monthly sheet if available
        target_sv_tab = None
        for cand in ["SV - Oct", "SV - Sept", "SV - Aug"]:
            if cand in tab_names:
                target_sv_tab = cand
                break
        if not target_sv_tab and viruksham_monthly:
            target_sv_tab = viruksham_monthly[-1]

        if target_sv_tab:
            report["viruksham_inspection"] = self._inspect_monthly_tab(spreadsheet_id, target_sv_tab)

        # Inspect Consolidation Report tab if available
        target_cons_tab = None
        for cand in ["Consolidate Report - Sept", "Consolidate Report - Aug", "Consolidate Report - Jul"]:
            if cand in tab_names:
                target_cons_tab = cand
                break
        if not target_cons_tab and consolidation_tabs:
            target_cons_tab = consolidation_tabs[-1]

        if target_cons_tab:
            report["consolidation_inspection"] = self._inspect_consolidation_tab(spreadsheet_id, target_cons_tab)

        # Inspect Employees tab if available
        if employees_tabs:
            report["employees_inspection"] = self._inspect_employees_tab(spreadsheet_id, employees_tabs[0])

        return report

    def _inspect_consolidation_tab(self, spreadsheet_id: str, tab_name: str) -> Dict[str, Any]:
        """Inspect structure, headers, and formulas of a consolidation report tab."""
        header_rows = self.service.read_range(spreadsheet_id, f"{tab_name}!A1:Z3")
        headers = [str(c).strip() for c in header_rows[0]] if header_rows else []

        sample_rows_raw = self.service.read_range(spreadsheet_id, f"{tab_name}!A1:Z10")
        sample_rows: List[List[Any]] = []
        if sample_rows_raw and len(sample_rows_raw) > 1:
            for r in sample_rows_raw[1:10]:
                sample_rows.append([str(c).strip() for c in r])

        formula_rows = self.service.read_formulas(spreadsheet_id, f"{tab_name}!A1:Z15")
        detected_formulas: Dict[str, str] = {}
        if formula_rows:
            for r_idx, r in enumerate(formula_rows):
                for c_idx, val in enumerate(r):
                    if isinstance(val, str) and val.startswith("="):
                        col_letter = chr(ord("A") + c_idx)
                        detected_formulas[f"{col_letter}{r_idx + 1}"] = val

        return {
            "tab_name": tab_name,
            "headers": headers,
            "sample_rows": sample_rows,
            "sample_formulas": detected_formulas,
        }

    def _inspect_monthly_tab(self, spreadsheet_id: str, tab_name: str) -> Dict[str, Any]:
        """Inspect structure, headers, formulas, and dimensions of a monthly scheme tab."""
        # Read header row
        header_rows = self.service.read_range(spreadsheet_id, f"{tab_name}!A1:Z1")
        headers = [str(c).strip() for c in header_rows[0]] if header_rows else []

        # Read first 10 rows of formatted values
        sample_rows_raw = self.service.read_range(spreadsheet_id, f"{tab_name}!A1:Z10")
        sanitized_samples: List[List[Any]] = []
        if sample_rows_raw and len(sample_rows_raw) > 1:
            for r in sample_rows_raw[1:10]:
                sanitized_r = [
                    sanitize_value(headers[idx] if idx < len(headers) else "", val)
                    for idx, val in enumerate(r)
                ]
                sanitized_samples.append(sanitized_r)

        # Read formulas for first 10 rows
        formula_rows = self.service.read_formulas(spreadsheet_id, f"{tab_name}!A1:Z10")
        detected_formulas: Dict[str, str] = {}
        if formula_rows:
            for r_idx, r in enumerate(formula_rows):
                for c_idx, val in enumerate(r):
                    if isinstance(val, str) and val.startswith("="):
                        col_letter = chr(ord("A") + c_idx)
                        detected_formulas[f"{col_letter}{r_idx + 1}"] = val

        # Estimate row count with data
        key_column_values = self.service.read_range(spreadsheet_id, f"{tab_name}!D:D")
        data_rows_count = max(0, len(key_column_values) - 1) if key_column_values else 0

        return {
            "tab_name": tab_name,
            "headers": headers,
            "detected_column_count": len(headers),
            "estimated_data_row_count": data_rows_count,
            "sample_rows_sanitized": sanitized_samples,
            "sample_formulas": detected_formulas,
        }

    def _inspect_employees_tab(self, spreadsheet_id: str, tab_name: str) -> Dict[str, Any]:
        """Inspect the Employees mapping directory tab."""
        header_rows = self.service.read_range(spreadsheet_id, f"{tab_name}!A1:Z1")
        headers = [str(c).strip() for c in header_rows[0]] if header_rows else []

        sample_rows = self.service.read_range(spreadsheet_id, f"{tab_name}!A2:Z6")
        all_rows = self.service.read_range(spreadsheet_id, f"{tab_name}!A:A")
        total_employees = max(0, len(all_rows) - 1) if all_rows else 0

        return {
            "tab_name": tab_name,
            "headers": headers,
            "total_records": total_employees,
            "sample_rows": sample_rows[:5] if sample_rows else [],
        }

    def _check_october_data(self, spreadsheet_id: str, tab_names: List[str]) -> Dict[str, Any]:
        """Determine whether October data is present or already has its own tab."""
        has_oct_tab = "SS - Oct" in tab_names
        oct_dates_found_in_sept = 0

        # Check if October rows exist in SS - Sept by accident
        if "SS - Sept" in tab_names:
            date_values = self.service.read_range(spreadsheet_id, "SS - Sept!F:F")
            for r in date_values:
                if r and any(d in str(r[0]) for d in ["-10-", "/10/", "10-2026", "2026-10"]):
                    oct_dates_found_in_sept += 1

        return {
            "has_october_tab": has_oct_tab,
            "october_tab_name": "SS - Oct" if has_oct_tab else None,
            "october_records_in_september_tab": oct_dates_found_in_sept,
            "status": "TAB_EXISTS" if has_oct_tab else "TAB_NOT_CREATED_YET",
        }

    @staticmethod
    def _analyze_column_mapping(sheet_headers: List[str]) -> Dict[str, Any]:
        """
        Compare sheet columns against Phase 1C cleaned 11 columns
        and generate detailed field-to-column mapping specification.
        """
        mapping_table: List[Dict[str, str]] = []

        # If sheet headers match observed standard A-N
        col_letters = [chr(ord("A") + i) for i in range(len(sheet_headers or EXPECTED_MONTHLY_COLUMNS))]
        headers_to_check = sheet_headers if sheet_headers else EXPECTED_MONTHLY_COLUMNS

        for idx, col_name in enumerate(headers_to_check):
            letter = col_letters[idx] if idx < len(col_letters) else f"Col{idx+1}"
            norm = col_name.strip().upper().replace("_", " ")

            if norm in [c.replace("_", " ") for c in CLEANED_11_COLUMNS]:
                orig_cleaned = [c for c in CLEANED_11_COLUMNS if c.replace("_", " ") == norm][0]
                transformation = "Direct 1:1 mapping"
                status = "DIRECT"
            elif "LOCATION 2" in norm:
                orig_cleaned = "Enriched (LocationResolver)"
                transformation = "Derived from COMMCODE / COSTNAME via LocationResolver"
                status = "ENRICHED"
            elif "LOCATION" in norm:
                orig_cleaned = "Enriched (LocationResolver)"
                transformation = "Derived from COMMCODE / COSTNAME via LocationResolver"
                status = "ENRICHED"
            elif "CODE - NAME" in norm or "CODE NAME" in norm:
                orig_cleaned = "Enriched (Helper)"
                transformation = "Concatenation: COMMCODE & ' - ' & COMMNAME"
                status = "HELPER_CONCAT"
            else:
                orig_cleaned = "Unknown / Custom"
                transformation = "Unmapped column"
                status = "UNMAPPED"

            mapping_table.append({
                "column_letter": letter,
                "sheet_column_name": col_name,
                "source_field": orig_cleaned,
                "transformation": transformation,
                "status": status,
            })

        return {
            "total_sheet_columns": len(headers_to_check),
            "expected_columns": EXPECTED_MONTHLY_COLUMNS,
            "mappings": mapping_table,
        }
