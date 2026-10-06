"""
Phase 1E: Read-only live Google Sheets reconciliation service.

Safely audits incoming processed records against production sheets without mutations.
Zero writes, zero appends, zero formula modifications.
"""

from datetime import date, datetime
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from app.core.exceptions import GoogleSheetsError
from app.core.models import CleanedRecord
from app.google_sheets.client import GoogleSheetsService

logger = logging.getLogger("pothys_reporting")

# Standard month abbreviation convention used in workbook (e.g. 'Sept' for September)
MONTH_TAB_NAMES = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Aug",
    9: "Sept",
    10: "Oct",
    11: "Nov",
    12: "Dec",
}


def derive_monthly_tab_name(tab_prefix: str, report_date: date) -> str:
    """Derive month tab name matching workbook convention, e.g. 'SS - Oct' or 'SS - Sept'."""
    month_name = MONTH_TAB_NAMES.get(report_date.month, report_date.strftime("%b"))
    return f"{tab_prefix} - {month_name}"


def normalize_text(val: Any) -> str:
    """Normalize text by collapsing whitespace and replacing NBSP (\xa0) and encoding artifacts."""
    if val is None:
        return ""
    s = str(val).replace("\xa0", " ").replace("\xc2", "").replace("\ufeff", "").strip()
    return " ".join(s.split())


def normalize_amount(val: Any) -> float:
    """Safely convert cell value to float rounded to 2 decimal places."""
    if val is None:
        return 0.0
    s = str(val).replace(",", "").replace("\xa0", "").strip()
    try:
        return round(float(s), 2)
    except (ValueError, TypeError):
        return 0.0


def normalize_date_str(val: Any, target_month: Optional[int] = None) -> str:
    """Normalize date strings in various formats to ISO YYYY-MM-DD."""
    if not val:
        return ""
    s = str(val).strip()
    parsed_dates = []
    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%m-%d-%Y",
        "%d/%m/%y",
        "%m/%d/%y",
        "%d-%m-%y",
        "%m-%d-%y",
        "%d.%m.%Y",
    ):
        try:
            d = datetime.strptime(s, fmt).date()
            parsed_dates.append(d)
        except ValueError:
            continue

    if not parsed_dates:
        return s

    # Prefer candidate whose month matches target_month when disambiguation is required
    if target_month:
        for d in parsed_dates:
            if d.month == target_month:
                return d.isoformat()

    return parsed_dates[0].isoformat()


# Canonical 11 business fields for semantic comparison (omitting formula columns H, N and manual col G)
BUSINESS_FIELDS_TO_COMPARE = [
    "COSTNAME",
    "CLIENTID",
    "GROUPCODE",
    "MSNO",
    "NAME",
    "SCHDATE",
    "RECAMOUNT",
    "MOBILENO",
    "SCHEME",
    "COMMNAME",
    "COMMCODE",
]


class RecordReconciliationCategory(str, Enum):
    """Classification of an incoming record against the sheet index."""
    ALREADY_PRESENT = "ALREADY_PRESENT"
    NEW = "NEW"
    CONFLICT = "CONFLICT"


class RecordReconciliationDetail(BaseModel):
    """Detailed comparison outcome for an individual record."""
    msno: str
    category: RecordReconciliationCategory
    conflict_reasons: List[str] = Field(default_factory=list)
    sheet_row_index: Optional[int] = None
    incoming_amount: Optional[float] = None
    sheet_amount: Optional[float] = None


class SheetReconciliationResult(BaseModel):
    """Deterministic summary of live sheet reconciliation."""
    report_date: date
    target_tab: str
    incoming_record_count: int
    existing_sheet_msno_count: int
    already_present_count: int
    new_record_count: int
    conflict_count: int
    incoming_amount_total: float = 0.0
    matching_sheet_amount_total: float = 0.0
    live_writes: int = 0
    status: str = "SAFE / NO ACTION"
    details: List[RecordReconciliationDetail] = Field(default_factory=list)
    error_message: Optional[str] = None


class LiveSheetReconciler:
    """
    Read-only reconciler comparing processed records against live Google Sheets.
    Guarantees 0 write/append/update operations.
    """

    def __init__(self, service: GoogleSheetsService):
        self.service = service

    def reconcile_records(
        self,
        spreadsheet_id: str,
        target_tab: str,
        report_date: date,
        records: List[CleanedRecord],
    ) -> SheetReconciliationResult:
        """
        Execute read-only reconciliation against the target sheet tab.

        1. Validates that target_tab exists in spreadsheet metadata.
        2. Reads existing rows (A1:M) without write access.
        3. Validates that header row contains required columns (specifically Column D = MSNO).
        4. Indexes existing rows by normalized MSNO and captures available business fields.
        5. Categorizes incoming records into ALREADY_PRESENT, NEW, or CONFLICT.
        6. Ensures live_writes = 0 always.
        """
        total_incoming = len(records)
        incoming_amt_total = sum(float(r.RECAMOUNT or 0.0) for r in records)

        # 1. Validate target tab existence
        try:
            metadata = self.service.get_spreadsheet_metadata(spreadsheet_id)
        except Exception as e:
            msg = f"Failed to fetch metadata for spreadsheet '{spreadsheet_id}': {e}"
            logger.error(msg)
            return SheetReconciliationResult(
                report_date=report_date,
                target_tab=target_tab,
                incoming_record_count=total_incoming,
                existing_sheet_msno_count=0,
                already_present_count=0,
                new_record_count=total_incoming,
                conflict_count=0,
                live_writes=0,
                status="FAILED: API_READ_ERROR",
                error_message=msg,
            )

        sheet_titles = [
            s.get("properties", {}).get("title", "")
            for s in metadata.get("sheets", [])
        ]
        if target_tab not in sheet_titles:
            msg = f"Target worksheet '{target_tab}' does not exist in workbook. Found tabs: {sheet_titles[:10]}"
            logger.error(msg)
            return SheetReconciliationResult(
                report_date=report_date,
                target_tab=target_tab,
                incoming_record_count=total_incoming,
                existing_sheet_msno_count=0,
                already_present_count=0,
                new_record_count=total_incoming,
                conflict_count=0,
                live_writes=0,
                status=f"FAILED: TAB_NOT_FOUND ({target_tab})",
                error_message=msg,
            )

        # 2. Read existing sheet rows (A1:M)
        try:
            sheet_rows = self.service.read_range(spreadsheet_id, f"{target_tab}!A1:M")
        except Exception as e:
            msg = f"Failed to read range from '{target_tab}': {e}"
            logger.error(msg)
            return SheetReconciliationResult(
                report_date=report_date,
                target_tab=target_tab,
                incoming_record_count=total_incoming,
                existing_sheet_msno_count=0,
                already_present_count=0,
                new_record_count=total_incoming,
                conflict_count=0,
                live_writes=0,
                status="FAILED: API_READ_ERROR",
                error_message=msg,
            )

        if not sheet_rows:
            msg = f"Target worksheet '{target_tab}' contains 0 rows."
            logger.error(msg)
            return SheetReconciliationResult(
                report_date=report_date,
                target_tab=target_tab,
                incoming_record_count=total_incoming,
                existing_sheet_msno_count=0,
                already_present_count=0,
                new_record_count=total_incoming,
                conflict_count=0,
                live_writes=0,
                status="FAILED: SHEET_EMPTY",
                error_message=msg,
            )

        # 3. Validate header row
        header_row = [str(c).strip().upper() for c in sheet_rows[0]]
        col_map = {col: idx for idx, col in enumerate(header_row) if col}

        # Handle workbook schema variations for SV and historical tabs:
        # In SV - Oct, Column A has a blank/space header cell while Column B is CLIENTID
        if "COSTNAME" not in col_map and col_map.get("CLIENTID") == 1:
            col_map["COSTNAME"] = 0
        # In earlier SV tabs (e.g. SV - Apr), SCHEME NAME was used instead of SCHEME
        if "SCHEME" not in col_map and "SCHEME NAME" in col_map:
            col_map["SCHEME"] = col_map["SCHEME NAME"]

        if "MSNO" not in col_map:
            msg = f"Header mismatch in '{target_tab}': 'MSNO' column not found in headers: {header_row}"
            logger.error(msg)
            return SheetReconciliationResult(
                report_date=report_date,
                target_tab=target_tab,
                incoming_record_count=total_incoming,
                existing_sheet_msno_count=0,
                already_present_count=0,
                new_record_count=total_incoming,
                conflict_count=0,
                live_writes=0,
                status="FAILED: HEADER_MISMATCH",
                error_message=msg,
            )

        msno_idx = col_map["MSNO"]

        # 4. Index existing rows
        existing_sheet_data: Dict[str, Dict[str, Any]] = {}
        existing_msno_list: List[str] = []

        for row_idx, row in enumerate(sheet_rows[1:], start=2):
            if len(row) > msno_idx and row[msno_idx] is not None:
                raw_msno = str(row[msno_idx]).strip()
                if raw_msno:
                    norm_msno = normalize_text(raw_msno).upper()
                    existing_msno_list.append(norm_msno)

                    # Extract available business field values
                    row_values = {}
                    for field in BUSINESS_FIELDS_TO_COMPARE:
                        f_idx = col_map.get(field)
                        if f_idx is not None and f_idx < len(row):
                            row_values[field] = row[f_idx]

                    existing_sheet_data[norm_msno] = {
                        "row_index": row_idx,
                        "msno": norm_msno,
                        "row_values": row_values,
                        "recamount": normalize_amount(row_values.get("RECAMOUNT")),
                    }

        total_existing_msnos = len(existing_msno_list)
        logger.info(
            f"Read {total_existing_msnos} existing MSNO entries from '{target_tab}'."
        )

        # 5. Reconcile incoming records against sheet index
        already_present = 0
        new_records = 0
        conflicts = 0
        matching_sheet_amt = 0.0
        details: List[RecordReconciliationDetail] = []
        seen_incoming_msnos: Set[str] = set()

        for record in records:
            r_msno = normalize_text(record.MSNO).upper()
            if not r_msno:
                conflicts += 1
                details.append(
                    RecordReconciliationDetail(
                        msno="EMPTY_MSNO",
                        category=RecordReconciliationCategory.CONFLICT,
                        conflict_reasons=["Incoming record has blank MSNO"],
                        incoming_amount=record.RECAMOUNT,
                    )
                )
                continue

            if r_msno in seen_incoming_msnos:
                conflicts += 1
                details.append(
                    RecordReconciliationDetail(
                        msno=r_msno,
                        category=RecordReconciliationCategory.CONFLICT,
                        conflict_reasons=["Duplicate MSNO within incoming dataset"],
                        incoming_amount=record.RECAMOUNT,
                    )
                )
                continue
            seen_incoming_msnos.add(r_msno)

            if r_msno not in existing_sheet_data:
                new_records += 1
                details.append(
                    RecordReconciliationDetail(
                        msno=r_msno,
                        category=RecordReconciliationCategory.NEW,
                        incoming_amount=record.RECAMOUNT,
                    )
                )
                continue

            # Record exists in sheet - compare all available business fields
            s_row = existing_sheet_data[r_msno]
            s_values = s_row["row_values"]
            r_dict = record.to_11_dict()
            conflict_reasons: List[str] = []

            for field in BUSINESS_FIELDS_TO_COMPARE:
                if field not in s_values:
                    continue  # Field not present in sheet columns

                r_raw = r_dict.get(field)
                s_raw = s_values[field]

                if field == "RECAMOUNT":
                    r_amt = round(float(r_raw or 0.0), 2)
                    s_amt = normalize_amount(s_raw)
                    if round(abs(r_amt - s_amt), 2) > 0.01:
                        conflict_reasons.append(
                            f"RECAMOUNT mismatch: incoming={r_amt:,.2f}, sheet={s_amt:,.2f}"
                        )
                elif field == "SCHDATE":
                    r_date = normalize_date_str(r_raw, report_date.month)
                    s_date = normalize_date_str(s_raw, report_date.month)
                    if r_date and s_date and r_date != s_date:
                        conflict_reasons.append(
                            f"SCHDATE mismatch: incoming={r_date}, sheet={s_date}"
                        )
                else:
                    r_text = normalize_text(r_raw).upper()
                    s_text = normalize_text(s_raw).upper()
                    if r_text and s_text and r_text != s_text:
                        conflict_reasons.append(
                            f"{field} mismatch: incoming='{r_text}', sheet='{s_text}'"
                        )

            r_amt_val = round(float(record.RECAMOUNT or 0.0), 2)
            s_amt_val = s_row["recamount"]

            if conflict_reasons:
                conflicts += 1
                details.append(
                    RecordReconciliationDetail(
                        msno=r_msno,
                        category=RecordReconciliationCategory.CONFLICT,
                        conflict_reasons=conflict_reasons,
                        sheet_row_index=s_row["row_index"],
                        incoming_amount=r_amt_val,
                        sheet_amount=s_amt_val,
                    )
                )
            else:
                already_present += 1
                matching_sheet_amt += s_amt_val
                details.append(
                    RecordReconciliationDetail(
                        msno=r_msno,
                        category=RecordReconciliationCategory.ALREADY_PRESENT,
                        sheet_row_index=s_row["row_index"],
                        incoming_amount=r_amt_val,
                        sheet_amount=s_amt_val,
                    )
                )

        # 6. Determine final status
        if conflicts > 0:
            status = "CONFLICT DETECTED"
        elif new_records > 0:
            status = "READY TO APPEND (Simulation / No writes performed)"
        else:
            status = "SAFE / NO ACTION"

        return SheetReconciliationResult(
            report_date=report_date,
            target_tab=target_tab,
            incoming_record_count=total_incoming,
            existing_sheet_msno_count=total_existing_msnos,
            already_present_count=already_present,
            new_record_count=new_records,
            conflict_count=conflicts,
            incoming_amount_total=incoming_amt_total,
            matching_sheet_amount_total=matching_sheet_amt,
            live_writes=0,
            status=status,
            details=details,
        )


def format_reconciliation_report(result: SheetReconciliationResult) -> str:
    """Format reconciliation outcome matching exact specification."""
    lines = [
        "",
        "=" * 60,
        "PSM LIVE GOOGLE SHEETS RECONCILIATION",
        "READ ONLY",
        "=" * 60,
        f"{'Report date:':<24}{result.report_date.isoformat()}",
        f"{'Target tab:':<24}{result.target_tab}",
        f"{'Incoming records:':<24}{result.incoming_record_count:>8}",
        f"{'Existing records:':<24}{result.existing_sheet_msno_count:>8}",
        f"{'Already present:':<24}{result.already_present_count:>8}",
        f"{'New:':<24}{result.new_record_count:>8}",
        f"{'Conflicts:':<24}{result.conflict_count:>8}",
        f"{'Live writes:':<24}{result.live_writes:>8}",
        f"{'Status:':<24}{result.status}",
        "=" * 60,
    ]
    if result.conflict_count > 0:
        lines.append("\nIdentified Conflicts:")
        lines.append("-" * 60)
        for d in result.details:
            if d.category == RecordReconciliationCategory.CONFLICT:
                lines.append(f"  MSNO: {d.msno} (Sheet Row: {d.sheet_row_index or 'N/A'})")
                for reason in d.conflict_reasons:
                    lines.append(f"    - {reason}")
        lines.append("-" * 60)
    if result.new_record_count > 0:
        lines.append(f"\nNew Records Details ({result.new_record_count}):")
        lines.append("-" * 60)
        new_samples = [d for d in result.details if d.category == RecordReconciliationCategory.NEW]
        for d in new_samples[:5]:
            lines.append(f"  MSNO: {d.msno} (Amount: INR {d.incoming_amount or 0.0:,.2f})")
        if len(new_samples) > 5:
            lines.append(f"  ... and {len(new_samples) - 5} more records")
        lines.append("-" * 60)
    if result.error_message:
        lines.append(f"\nError Details: {result.error_message}")
    lines.append("")
    return "\n".join(lines)
