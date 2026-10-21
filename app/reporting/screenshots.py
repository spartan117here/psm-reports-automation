"""
Google Sheets Report Screenshot Engine.

Captures presentation-ready screenshot cards of executive tables from Google Sheets:
1. Enrollment Report: 'Consolidate Report - Oct' (New Enrollment and Achieved Count tables together)
2. Closing Report: 'October - 2026' (Scheme Rejoining Summary table)

Dynamic boundary validation verifies table existence, headers, total rows, and dates
before authenticated PDF range export and high-resolution PNG rendering.
"""

from datetime import date
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from app.core.config import AppConfigBundle, load_config
from app.core.exceptions import GoogleSheetsError, ScreenshotError, TableValidationError
from app.google_sheets.client import GoogleSheetsService, MockGoogleSheetsService
from app.reporting.closing import derive_closing_tab_name, normalize_sheet_date
from app.reporting.digi import derive_consolidation_tab_name
from app.reporting.images import pdf_to_cropped_png

logger = logging.getLogger("pothys_reporting")


class TableBoundaryValidation(BaseModel):
    """Validation details and resolved cell range coordinates for a report table."""

    target: str
    workbook_name: str
    worksheet_name: str
    gid: int
    detected_range: str
    report_title: str
    report_date: Optional[date] = None
    is_valid: bool = True
    table_headers: List[str] = Field(default_factory=list)
    row_count: int = 0
    total_row_found: bool = False
    validation_notes: List[str] = Field(default_factory=list)


class ScreenshotResult(BaseModel):
    """Output metadata for a captured report screenshot."""

    target: str
    workbook_name: str
    worksheet_name: str
    gid: int
    range_used: str
    output_path: Path
    dimensions: Tuple[int, int]
    file_size_bytes: int
    report_date: Optional[date] = None
    report_title: str


def _find_sheet_gid(metadata: Dict[str, Any], worksheet_name: str) -> int:
    """Find sheetId (gid) from spreadsheet metadata or raise TableValidationError."""
    sheets = metadata.get("sheets", [])
    sheet_titles = []
    for s in sheets:
        props = s.get("properties", {})
        title = props.get("title", "")
        sheet_titles.append(title)
        if title == worksheet_name:
            return int(props.get("sheetId", 0))

    raise TableValidationError(
        f"Worksheet '{worksheet_name}' not found. Available sheets: {', '.join(sheet_titles)}"
    )


def extract_date_from_text(text: str) -> Optional[date]:
    """Extract date (e.g. DD/MM/YYYY or DD/MM/YY) from title string."""
    if not text:
        return None
    # Look for DD/MM/YYYY or DD/MM/YY pattern
    match = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text)
    if match:
        return normalize_sheet_date(match.group(1))
    return None


def detect_and_validate_enrollment_range(
    sheets_service: GoogleSheetsService,
    spreadsheet_id: str,
    worksheet_name: str = "Consolidate Report - Oct",
    expected_date: Optional[date] = None,
) -> TableBoundaryValidation:
    """
    Dynamically discover and validate table boundaries for the Enrollment Report.

    Expected structure in 'Consolidate Report - Oct':
    - Table 1 Title: 'New Enrollment as on DD/MM/YYYY' (Row 3, Col E)
    - Table 1 Headers: 'Branch', 'Target Value', 'Subhiksham', 'Viruksham', 'Total', 'Yet to Achieve', 'Achieved %'
    - Table 1 Total Row: 'Total', ..., 41% (Row 16)
    - Table 2 Headers: 'Branch', 'Count Target', 'Achieved Count', ..., 'SS + SV + DG + DS', 'AVG' (Row 18)
    - Table 2 Sub-headers: 'SS + SV + DG + DS', 'Subhiksham', 'Viruksham', 'DigiGold', 'DigiSilver' (Row 19)
    - Table 2 Total Row: 'Total', ..., 1247 (Row 30)

    Encompasses Rows 3 to 30, Columns E to L -> Range 'E3:L30'.
    """
    metadata = sheets_service.get_spreadsheet_metadata(spreadsheet_id)
    gid = _find_sheet_gid(metadata, worksheet_name)

    # Read grid slice encompassing potential candidate table coordinates (E1:M35)
    fetch_range = f"'{worksheet_name}'!E1:M35"
    raw_rows = sheets_service.read_range(spreadsheet_id, fetch_range)
    if not raw_rows:
        raise TableValidationError(
            f"No data returned for range '{fetch_range}' in sheet '{worksheet_name}'."
        )

    title_row_idx: Optional[int] = None
    report_title = ""
    report_date: Optional[date] = None

    table1_header_idx: Optional[int] = None
    table1_total_idx: Optional[int] = None

    table2_header_idx: Optional[int] = None
    table2_total_idx: Optional[int] = None
    avg_col_letter = "L"

    for idx, row in enumerate(raw_rows, start=1):
        padded = [str(c).strip() for c in row] + [""] * (9 - len(row))
        col_e = padded[0]  # Column E is index 0 of slice E..M

        # 1. Search for title row
        if not title_row_idx:
            for cell in padded:
                if "new enrollment as on" in cell.lower():
                    title_row_idx = idx
                    report_title = cell
                    report_date = extract_date_from_text(cell)
                    break

        # 2. Search for Table 1 headers (e.g. 'Branch', 'Target Value')
        if title_row_idx and not table1_header_idx and idx > title_row_idx:
            if "branch" in col_e.lower() and any("target" in c.lower() for c in padded):
                table1_header_idx = idx

        # 3. Search for Table 1 Total row
        if table1_header_idx and not table1_total_idx and idx > table1_header_idx:
            if col_e.lower() == "total":
                table1_total_idx = idx

        # 4. Search for Table 2 headers (e.g. 'Achieved Count' or 'Count Target')
        if table1_total_idx and not table2_header_idx and idx > table1_total_idx:
            if "branch" in col_e.lower() or any("achieved count" in c.lower() for c in padded):
                table2_header_idx = idx

        # 5. Search for Table 2 Total row
        if table2_header_idx and not table2_total_idx and idx > table2_header_idx:
            if col_e.lower() == "total":
                table2_total_idx = idx

    # Validation checks
    errors = []
    if not title_row_idx:
        errors.append("Could not locate 'New Enrollment as on' title row.")
    if not table1_header_idx:
        errors.append("Could not locate Table 1 'Branch' / 'Target Value' header row.")
    if not table1_total_idx:
        errors.append("Could not locate Table 1 'Total' row.")
    if not table2_header_idx:
        errors.append("Could not locate Table 2 'Achieved Count' header row.")
    if not table2_total_idx:
        errors.append("Could not locate Table 2 'Total' row.")

    if errors:
        raise TableValidationError(
            f"Enrollment table boundary validation failed in '{worksheet_name}': {'; '.join(errors)}"
        )

    # Date freshness validation
    if expected_date is not None:
        if not report_date:
            raise TableValidationError(
                f"Enrollment report in '{worksheet_name}' has title '{report_title}' but no date could be parsed. "
                f"Expected target date: {expected_date.strftime('%d/%m/%Y')}."
            )
        if report_date != expected_date:
            raise TableValidationError(
                f"Stale enrollment data detected in '{worksheet_name}': expected {expected_date.strftime('%d/%m/%Y')} "
                f"({expected_date.isoformat()}), but sheet displays {report_date.strftime('%d/%m/%Y')}. "
                "Screenshot capture halted to prevent distributing desynchronized reports."
            )

    start_row = title_row_idx
    end_row = table2_total_idx
    detected_range = f"E{start_row}:{avg_col_letter}{end_row}"

    validation = TableBoundaryValidation(
        target="enrollment",
        workbook_name="New Enrollment from April 2026",
        worksheet_name=worksheet_name,
        gid=gid,
        detected_range=detected_range,
        report_title=report_title,
        report_date=report_date,
        is_valid=True,
        table_headers=["Branch", "Target Value", "Subhiksham", "Viruksham", "Total", "Achieved %", "Achieved Count", "AVG"],
        row_count=(end_row - start_row + 1),
        total_row_found=True,
        validation_notes=[
            f"Table 1: rows {start_row}-{table1_total_idx}",
            f"Table 2: rows {table2_header_idx}-{end_row}",
            f"Columns: E to {avg_col_letter}",
            f"Report date parsed: {report_date}",
        ],
    )
    logger.info(f"Enrollment table boundaries validated successfully: {detected_range} (date: {report_date})")
    return validation


def detect_and_validate_closing_range(
    sheets_service: GoogleSheetsService,
    spreadsheet_id: str,
    worksheet_name: str = "October - 2026",
    expected_date: Optional[date] = None,
) -> TableBoundaryValidation:
    """
    Dynamically discover and validate table boundaries for the complete Closing Report.

    Expected structure in 'October - 2026':
    1. Scheme Rejoining Report Summary:
       - Title Row: 'SCHEME REJOINING REPORT AS ON DD/MM/YYYY' (Row 3, Col L to Q)
       - Header Row: 'SHOWROOM', 'CLOSED', 'REJOIN SS', 'REJOIN SV', 'TOTAL REJOIN', 'REJOIN %' (Row 4)
       - Showroom Rows: TVL, CPT, TVM, TPJ, CBE, SLM, PNM, PADI, KPM (Rows 5 to 13)
       - Total Row 1: 'TOTAL', 1605, 290, 36, 326, 20.31% (Row 14)
    2. Closed Weight Table (Cols L to N):
       - Section Header: 'CLOSED WEIGHT' (Row 15)
       - Column Headers: 'SHOWROOM', 'COUNT', 'WEIGHT' (Row 16)
       - Showroom Rows: Rows 17 to 25
       - Total Row 2: 'TOTAL', 1605, 10881.76 (Row 26)
    3. Closed Count Table (Cols O to Q):
       - Section Header: 'CLOSED COUNT' (Row 15)
       - Column Headers: 'SHOWROOM', 'SS', 'SV' (Row 16)
       - Showroom Rows: Rows 17 to 25
       - Total Row 3: 'TOTAL', 1351, 254 (Row 26)

    Encompasses Rows 3 to 26, Columns L to Q -> Range 'L3:Q26'.
    """
    metadata = sheets_service.get_spreadsheet_metadata(spreadsheet_id)
    gid = _find_sheet_gid(metadata, worksheet_name)

    # Read grid slice encompassing full closing table coordinates (J1:R30)
    fetch_range = f"'{worksheet_name}'!J1:R30"
    raw_rows = sheets_service.read_range(spreadsheet_id, fetch_range)
    if not raw_rows:
        raise TableValidationError(
            f"No data returned for range '{fetch_range}' in sheet '{worksheet_name}'."
        )

    title_row_idx: Optional[int] = None
    title_col_idx: Optional[int] = None  # 0-indexed relative to J (index 2 = L)
    report_title = ""
    report_date: Optional[date] = None

    table1_header_idx: Optional[int] = None
    table1_headers: List[str] = []
    table1_total_idx: Optional[int] = None
    section1_showroom_count = 0

    sub_title_row_idx: Optional[int] = None
    sub_header_row_idx: Optional[int] = None
    sub_headers: List[str] = []
    bottom_total_row_idx: Optional[int] = None
    section2_showroom_count = 0

    col_letters = ["J", "K", "L", "M", "N", "O", "P", "Q", "R"]

    for idx, row in enumerate(raw_rows, start=1):
        padded = [str(c).strip() for c in row] + [""] * (9 - len(row))

        # 1. Search for title row
        if not title_row_idx:
            for c_idx, cell in enumerate(padded):
                if "scheme rejoining report as on" in cell.lower():
                    title_row_idx = idx
                    title_col_idx = c_idx  # typically 2 (Col L)
                    report_title = cell
                    # Search entire row for date string (e.g. Col Q)
                    for text_cell in padded:
                        d = extract_date_from_text(text_cell)
                        if d:
                            report_date = d
                            break
                    break

        # 2. Search for Table 1 header row ('SHOWROOM', 'CLOSED', etc.)
        if title_row_idx and not table1_header_idx and idx > title_row_idx:
            if title_col_idx is not None and "showroom" in padded[title_col_idx].lower():
                table1_header_idx = idx
                for c_idx in range(title_col_idx, min(len(padded), title_col_idx + 6)):
                    if padded[c_idx]:
                        table1_headers.append(padded[c_idx])
                continue

        # 3. Search for Table 1 showroom rows and Table 1 Total row
        if table1_header_idx and not table1_total_idx and idx > table1_header_idx:
            cell_val = padded[title_col_idx] if title_col_idx is not None else ""
            if cell_val.upper() == "TOTAL":
                table1_total_idx = idx
            elif cell_val and cell_val.upper() not in ("SHOWROOM", "CLOSED WEIGHT", ""):
                section1_showroom_count += 1
            continue

        # 4. Search for Section 2/3 subtitle row ('CLOSED WEIGHT', 'CLOSED COUNT')
        if table1_total_idx and not sub_title_row_idx and idx > table1_total_idx:
            row_str = " ".join(padded).lower()
            if "closed weight" in row_str and "closed count" in row_str:
                sub_title_row_idx = idx
            continue

        # 5. Search for Section 2/3 sub-header row ('SHOWROOM', 'COUNT', 'WEIGHT', ...)
        if sub_title_row_idx and not sub_header_row_idx and idx > sub_title_row_idx:
            row_str = " ".join(padded).lower()
            if "showroom" in row_str and "weight" in row_str and "ss" in row_str:
                sub_header_row_idx = idx
                for c_idx in range(title_col_idx or 2, min(len(padded), (title_col_idx or 2) + 6)):
                    if padded[c_idx]:
                        sub_headers.append(padded[c_idx])
            continue

        # 6. Search for Section 2/3 showroom rows and Bottom Total row
        if sub_header_row_idx and not bottom_total_row_idx and idx > sub_header_row_idx:
            cell_val = padded[title_col_idx] if title_col_idx is not None else ""
            if cell_val.upper() == "TOTAL":
                bottom_total_row_idx = idx
            elif cell_val and cell_val.upper() not in ("SHOWROOM", ""):
                section2_showroom_count += 1
            continue

    # Validation checks
    errors = []
    if not title_row_idx:
        errors.append("Could not locate 'SCHEME REJOINING REPORT AS ON' title row.")
    if not table1_header_idx:
        errors.append("Could not locate summary table header row ('SHOWROOM', 'CLOSED', etc.).")
    if not table1_total_idx:
        errors.append("Could not locate Section 1 'TOTAL' row in summary table.")
    if section1_showroom_count < 5:
        errors.append(f"Insufficient showroom rows found in Section 1 ({section1_showroom_count} found; expected at least 5).")

    if not sub_title_row_idx:
        errors.append("Could not locate Section 2/3 'CLOSED WEIGHT' / 'CLOSED COUNT' subtitle row.")
    if not sub_header_row_idx:
        errors.append("Could not locate Section 2/3 headers row ('SHOWROOM', 'COUNT', 'WEIGHT', etc.).")
    if not bottom_total_row_idx:
        errors.append("Could not locate bottom 'TOTAL' row in Closed Weight / Closed Count tables.")
    if section2_showroom_count < 5:
        errors.append(f"Insufficient showroom rows found in Section 2/3 ({section2_showroom_count} found; expected at least 5).")

    if errors:
        raise TableValidationError(
            f"Closing table boundary validation failed in '{worksheet_name}': {'; '.join(errors)}"
        )

    # Date freshness validation
    if expected_date is not None:
        if not report_date:
            raise TableValidationError(
                f"Closing report in '{worksheet_name}' has title '{report_title}' but no date could be parsed. "
                f"Expected target date: {expected_date.strftime('%d/%m/%Y')}."
            )
        if report_date != expected_date:
            raise TableValidationError(
                f"Stale closing data detected in '{worksheet_name}': expected {expected_date.strftime('%d/%m/%Y')} "
                f"({expected_date.isoformat()}), but sheet displays {report_date.strftime('%d/%m/%Y')}. "
                "Screenshot capture halted to prevent distributing desynchronized reports."
            )

    start_col_letter = col_letters[title_col_idx or 2]  # "L"
    end_col_letter = "Q"  # Covers SV in Closed Count and REJOIN % in Summary
    start_row = title_row_idx
    end_row = bottom_total_row_idx
    detected_range = f"{start_col_letter}{start_row}:{end_col_letter}{end_row}"

    validation = TableBoundaryValidation(
        target="closing",
        workbook_name="Closed Member & Rejoining Report",
        worksheet_name=worksheet_name,
        gid=gid,
        detected_range=detected_range,
        report_title=report_title,
        report_date=report_date,
        is_valid=True,
        table_headers=table1_headers + sub_headers,
        row_count=(end_row - start_row + 1),
        total_row_found=True,
        validation_notes=[
            f"Range: {detected_range}",
            f"Section 1 (Summary): rows {start_row}-{table1_total_idx} ({section1_showroom_count} showrooms)",
            f"Section 2/3 (Closed Weight & Count): rows {sub_title_row_idx}-{end_row} ({section2_showroom_count} showrooms)",
            f"Report date parsed: {report_date}",
        ],
    )
    logger.info(f"Closing table boundaries validated successfully: {detected_range} (date: {report_date})")
    return validation


class GoogleSheetsScreenshotEngine:
    """
    High-level engine for capturing, validating, and rendering report screenshots.
    """

    def __init__(
        self,
        sheets_service: GoogleSheetsService,
        config_bundle: Optional[AppConfigBundle] = None,
        output_dir: Optional[Path] = None,
        dpi: int = 200,
        padding: int = 20,
    ):
        self.sheets_service = sheets_service
        self.config_bundle = config_bundle or load_config()

        screenshots_cfg = getattr(self.config_bundle.reports, "screenshots", None)
        default_dir = (
            Path(screenshots_cfg.output_dir) if screenshots_cfg else Path("data/screenshots")
        )
        self.output_dir = output_dir or (self.config_bundle.project_root / default_dir)
        self.dpi = dpi or (screenshots_cfg.dpi if screenshots_cfg else 200)
        self.padding = padding if padding is not None else (screenshots_cfg.padding if screenshots_cfg else 20)

    def capture_enrollment(
        self,
        spreadsheet_id: Optional[str] = None,
        worksheet_name: Optional[str] = None,
        filename: Optional[str] = None,
        expected_date: Optional[date] = None,
    ) -> ScreenshotResult:
        """
        Capture the Enrollment report table card (New Enrollment & Achieved Count).
        """
        sid = spreadsheet_id or os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
        if not sid:
            raise ScreenshotError(
                "GOOGLE_SHEET_NEW_ENROLLMENT_ID is not configured in environment or arguments."
            )

        ws_name = worksheet_name
        if not ws_name:
            if expected_date:
                ws_name = derive_consolidation_tab_name(expected_date)
            else:
                ws_name = "Consolidate Report - Oct"

        logger.info(f"Starting Enrollment screenshot capture for sheet '{ws_name}' (expected_date={expected_date})...")
        validation = detect_and_validate_enrollment_range(
            self.sheets_service, sid, worksheet_name=ws_name, expected_date=expected_date
        )

        # Export PDF of validated exact range
        try:
            pdf_bytes = self.sheets_service.export_pdf(
                sid, gid=validation.gid, range_name=validation.detected_range
            )
        except Exception as e:
            raise ScreenshotError(
                f"Failed to export Enrollment report PDF (range {validation.detected_range}): {e}"
            ) from e

        # Determine output filename
        date_str = validation.report_date.strftime("%Y%m%d") if validation.report_date else "latest"
        is_mock_run = isinstance(self.sheets_service, MockGoogleSheetsService) or "mock" in str(self.output_dir).lower()
        if filename:
            fn = f"mock_{filename}" if (is_mock_run and not filename.startswith("mock_")) else filename
        else:
            prefix = "mock_" if is_mock_run else ""
            fn = f"{prefix}enrollment_report_{date_str}.png"
        out_path = self.output_dir / fn

        # Render PDF to tightly cropped PNG
        rendered_path, dimensions = pdf_to_cropped_png(
            pdf_source=pdf_bytes,
            output_path=out_path,
            dpi=self.dpi,
            padding=self.padding,
            auto_crop=True,
        )

        return ScreenshotResult(
            target="enrollment",
            workbook_name="New Enrollment from April 2026",
            worksheet_name=ws_name,
            gid=validation.gid,
            range_used=validation.detected_range,
            output_path=rendered_path,
            dimensions=dimensions,
            file_size_bytes=rendered_path.stat().st_size,
            report_date=validation.report_date,
            report_title=validation.report_title,
        )

    def capture_closing(
        self,
        spreadsheet_id: Optional[str] = None,
        worksheet_name: Optional[str] = None,
        filename: Optional[str] = None,
        expected_date: Optional[date] = None,
    ) -> ScreenshotResult:
        """
        Capture the Closing report summary table card (Scheme Rejoining Summary).
        """
        sid = spreadsheet_id or os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")
        if not sid:
            raise ScreenshotError(
                "GOOGLE_SHEET_CLOSED_REJOINING_ID is not configured in environment or arguments."
            )

        ws_name = worksheet_name
        if not ws_name:
            if expected_date:
                ws_name = derive_closing_tab_name(expected_date)
            else:
                ws_name = "October - 2026"

        logger.info(f"Starting Closing screenshot capture for sheet '{ws_name}' (expected_date={expected_date})...")
        validation = detect_and_validate_closing_range(
            self.sheets_service, sid, worksheet_name=ws_name, expected_date=expected_date
        )

        # Export PDF of validated exact range
        try:
            pdf_bytes = self.sheets_service.export_pdf(
                sid, gid=validation.gid, range_name=validation.detected_range
            )
        except Exception as e:
            raise ScreenshotError(
                f"Failed to export Closing report PDF (range {validation.detected_range}): {e}"
            ) from e

        date_str = validation.report_date.strftime("%Y%m%d") if validation.report_date else "latest"
        is_mock_run = isinstance(self.sheets_service, MockGoogleSheetsService) or "mock" in str(self.output_dir).lower()
        if filename:
            fn = f"mock_{filename}" if (is_mock_run and not filename.startswith("mock_")) else filename
        else:
            prefix = "mock_" if is_mock_run else ""
            fn = f"{prefix}closing_rejoining_report_{date_str}.png"
        out_path = self.output_dir / fn

        rendered_path, dimensions = pdf_to_cropped_png(
            pdf_source=pdf_bytes,
            output_path=out_path,
            dpi=self.dpi,
            padding=self.padding,
            auto_crop=True,
        )

        return ScreenshotResult(
            target="closing",
            workbook_name="Closed Member & Rejoining Report",
            worksheet_name=ws_name,
            gid=validation.gid,
            range_used=validation.detected_range,
            output_path=rendered_path,
            dimensions=dimensions,
            file_size_bytes=rendered_path.stat().st_size,
            report_date=validation.report_date,
            report_title=validation.report_title,
        )

    def capture_all(self, expected_date: Optional[date] = None) -> List[ScreenshotResult]:
        """
        Capture screenshots for both Enrollment and Closing reports.
        Enforces that both reports correspond to the exact same expected report date.
        """
        res_enrollment = self.capture_enrollment(expected_date=expected_date)
        res_closing = self.capture_closing(expected_date=expected_date)

        # Cross-validation: Both reports MUST share the exact same date
        if res_enrollment.report_date != res_closing.report_date:
            raise ScreenshotError(
                f"Mismatched report dates detected between captured screenshots: "
                f"Enrollment report date is {res_enrollment.report_date}, "
                f"Closing report date is {res_closing.report_date}. "
                "Screenshot delivery blocked to prevent communicating desynchronized reports."
            )

        return [res_enrollment, res_closing]
