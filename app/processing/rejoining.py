"""
Closed Member & Rejoining Report processing module and point-in-time matching engine.

Features:
1. Strict separation of run date and report cutoff date.
2. Enrollment cutoff enforcement: only enrollments with date <= report_date can match.
3. Preservation of established Google Sheets many-to-one matching policy for shared mobile numbers,
   with explicit detection and flagging of ambiguous shared-phone matches for audit review.
4. Robust date parsing across all formats (%Y-%m-%d, %d/%m/%Y, %d-%m-%Y, %d/%m/%y, %d-%m-%y),
   rejecting invalid or future-dated records so they cannot silently produce incorrect matches.
5. Generation of all three report tables:
   - Table 1: Main Rejoining Table (Closed Count, Rejoin SS, Rejoin SV, Total Rejoin, Rejoin %)
   - Table 2: Closed Weight Table (Closed Weight, Rejoin SS Weight, Rejoin SV Weight)
   - Table 3: Closed Count Table by Scheme
"""

from collections import Counter, defaultdict
from datetime import date, datetime
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger("pothys_reporting")

# Canonical showroom mappings
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
    "NGL": "NGL",
    "NAGERCOIL": "NGL",
    "NAGARKOVIL": "NGL",
}

DEFAULT_PHYSICAL_SHOWROOMS: List[str] = [
    "TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KPM"
]


def normalize_mobile(val: Any) -> str:
    """Normalize mobile number by extracting the last 10 digits."""
    if not val:
        return ""
    s = str(val).strip()
    digits = re.sub(r"\D", "", s)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def parse_date_safe(val: Any) -> Optional[date]:
    """
    Safely parse date values across supported representations.
    Returns None if date is invalid or unparseable.
    """
    if val is None or val == "":
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()

    s = str(val).strip()
    date_part = s.split("T")[0].split(" ")[0].strip()

    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d/%m/%y",
        "%d-%m-%y",
        "%d.%m.%Y",
        "%d.%m.%y",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%m/%d/%y",
    ):
        try:
            return datetime.strptime(date_part, fmt).date()
        except ValueError:
            continue
    return None


def map_showroom_label(val: Any) -> str:
    """Map raw showroom code or name to canonical summary table label."""
    if not val:
        return ""
    code = str(val).strip().upper()
    return SHOWROOM_MAPPING.get(code, code)


class RejoiningRow(BaseModel):
    """Calculated rejoining metrics for a showroom."""
    showroom: str
    closed_count: int = Field(ge=0, description="Total closed accounts")
    rejoin_ss: int = Field(ge=0, description="Rejoined under Subhiksham")
    rejoin_sv: int = Field(ge=0, description="Rejoined under Viruksham")
    total_rejoin: int = Field(default=0, description="Calculated: rejoin_ss + rejoin_sv")
    rejoin_percentage: float = Field(default=0.0, description="Calculated: total_rejoin / closed_count * 100")


class RejoiningWeightRow(BaseModel):
    """Calculated weight rejoining metrics for a showroom."""
    showroom: str
    closed_weight: float = Field(default=0.0, description="Total closed weight in grams")
    rejoin_ss_weight: float = Field(default=0.0, description="Subhiksham rejoining weight")
    rejoin_sv_weight: float = Field(default=0.0, description="Viruksham rejoining weight")
    total_rejoin_weight: float = Field(default=0.0, description="Total rejoining weight")
    rejoin_weight_percentage: float = Field(default=0.0, description="Rejoining weight %")


class RejoiningSchemeRow(BaseModel):
    """Closed count breakdown by scheme for a showroom."""
    showroom: str
    scheme_counts: Dict[str, int] = Field(default_factory=dict)
    total_count: int = 0


class RejoiningMatchAudit(BaseModel):
    """Audit record for an individual rejoining match."""
    closed_row_index: int
    showroom: str
    closed_date: date
    customer_name: str
    passbook: str
    mobile: str
    matched_scheme: str
    enrollment_date: date
    enrollment_branch: str
    enrollment_amount: float = 0.0
    msno: str = ""
    is_ambiguous_shared_phone: bool = False
    matching_rule: str = "PHONE_EXACT_LOOKUP"


class RejoiningCalculator:
    """Calculates Total Rejoin and Rejoin % for closed member reports."""

    @staticmethod
    def calculate_row(showroom: str, closed: int, rejoin_ss: int, rejoin_sv: int) -> RejoiningRow:
        """
        Compute rejoining metrics for a given showroom.

        Formulas:
            TOTAL REJOIN = REJOIN SS + REJOIN SV
            REJOIN % = TOTAL REJOIN / CLOSED
        """
        total = rejoin_ss + rejoin_sv
        pct = (total / closed * 100.0) if closed > 0 else 0.0

        return RejoiningRow(
            showroom=showroom,
            closed_count=closed,
            rejoin_ss=rejoin_ss,
            rejoin_sv=rejoin_sv,
            total_rejoin=total,
            rejoin_percentage=round(pct, 2),
        )

    @staticmethod
    def compute_summary(rows: List[RejoiningRow]) -> RejoiningRow:
        """Compute grand total summary row across all showrooms."""
        tot_closed = sum(r.closed_count for r in rows)
        tot_ss = sum(r.rejoin_ss for r in rows)
        tot_sv = sum(r.rejoin_sv for r in rows)
        tot_rejoin = tot_ss + tot_sv
        rejoin_pct = (tot_rejoin / tot_closed * 100.0) if tot_closed > 0 else 0.0

        return RejoiningRow(
            showroom="TOTAL",
            closed_count=tot_closed,
            rejoin_ss=tot_ss,
            rejoin_sv=tot_sv,
            total_rejoin=tot_rejoin,
            rejoin_percentage=round(rejoin_pct, 2),
        )


class RejoiningReportResult(BaseModel):
    """Consolidated outcome of the Rejoining calculation pipeline."""
    report_date: date
    mode: str = "daily"
    snapshot_id: Optional[str] = None
    snapshot_hash: Optional[str] = None
    table1_rows: List[RejoiningRow]
    table1_summary: RejoiningRow
    table2_weight_rows: List[RejoiningWeightRow]
    table2_summary: RejoiningWeightRow
    table3_scheme_rows: List[RejoiningSchemeRow]
    table3_summary: Dict[str, int]
    matches: List[RejoiningMatchAudit] = Field(default_factory=list)
    ambiguous_shared_phone_matches: List[RejoiningMatchAudit] = Field(default_factory=list)
    total_closed_count: int
    total_closed_weight: float
    total_rejoin_ss: int
    total_rejoin_sv: int
    total_rejoin_count: int
    overall_rejoin_percentage: float
    unresolved_showrooms: List[str] = Field(default_factory=list)
    invalid_date_records_count: int = 0
    excluded_future_records_count: int = 0


class RejoiningMatchingEngine:
    """
    Point-in-time closed member rejoining attribution engine.

    Guarantees:
    - Pure historical isolation: only enrollments with date <= cutoff_date are considered.
    - Strict month-boundary enforcement: only records within the reporting month are evaluated.
    - Two explicit modes:
      * 'daily' (default): strictly enforces cl["docdate"] <= cutoff_date for historical backfills.
      * 'month_to_date': evaluates all verified closures within the target month tab,
        accurately reproducing the Google Sheets monthly Pivot Table for daily operational runs.
    - Zero retroactive mutation: appending future data never affects earlier snapshots.
    - Shared phone transparency: flags ambiguous many-to-one matches for audit.
    - Full three-table reconciliation.
    """

    def __init__(
        self,
        cutoff_date: date,
        showrooms: Optional[List[str]] = None,
        require_enrollment_after_closure: bool = False,
        mode: str = "daily",
        filter_closed_by_cutoff: Optional[bool] = None,
        snapshot_id: Optional[str] = None,
        snapshot_hash: Optional[str] = None,
    ):
        self.cutoff_date = cutoff_date
        self.showrooms = list(showrooms or DEFAULT_PHYSICAL_SHOWROOMS)
        self.require_enrollment_after_closure = require_enrollment_after_closure
        if filter_closed_by_cutoff is not None:
            self.mode = "daily" if filter_closed_by_cutoff else "month_to_date"
        else:
            norm_mode = mode.lower().strip()
            if norm_mode not in ("daily", "month_to_date"):
                raise ValueError(f"Invalid mode '{mode}': must be 'daily' or 'month_to_date'")
            self.mode = norm_mode
        self.snapshot_id = snapshot_id
        self.snapshot_hash = snapshot_hash

    def _extract_closed_record(self, raw: Any, index: int) -> Optional[Dict[str, Any]]:
        """Normalize dict or list into standardized closed record dict."""
        if isinstance(raw, dict):
            raw_date = raw.get("DOCDATE") or raw.get("docdate") or raw.get("date")
            name = str(raw.get("NAME") or raw.get("name") or "").strip()
            passbook = str(raw.get("PASSBOOK") or raw.get("passbook") or "").strip()
            mobile = str(raw.get("MOBILE") or raw.get("mobile") or "").strip()
            scheme = str(raw.get("SCHEMENAME") or raw.get("schemename") or raw.get("scheme") or "").strip()
            raw_weight = raw.get("WEIGHT") or raw.get("weight") or 0.0
            status = str(raw.get("STATUS") or raw.get("status") or "").strip()
            sh = str(raw.get("SHOWROOM") or raw.get("showroom") or raw.get("costname") or "").strip()
        elif isinstance(raw, (list, tuple)):
            # Fallback for Google Sheet row format
            # Row format A:J -> 0:DOCDATE, 1:NAME, 2:PASSBOOK, 3:MOBILE, 4:SCHEME, 5:WEIGHT, 6:STATUS, 7:SHOWROOM
            raw_date = raw[0] if len(raw) > 0 else None
            name = str(raw[1]).strip() if len(raw) > 1 else ""
            passbook = str(raw[2]).strip() if len(raw) > 2 else ""
            mobile = str(raw[3]).strip() if len(raw) > 3 else ""
            scheme = str(raw[4]).strip() if len(raw) > 4 else ""
            raw_weight = raw[5] if len(raw) > 5 else 0.0
            status = str(raw[6]).strip() if len(raw) > 6 else ""
            sh = str(raw[7]).strip() if len(raw) > 7 else ""
        else:
            return None

        dt = parse_date_safe(raw_date)
        if not dt:
            return None

        try:
            weight = float(str(raw_weight).replace(",", "").strip() or 0.0)
        except (ValueError, TypeError):
            weight = 0.0

        canon_sh = map_showroom_label(sh)

        return {
            "index": index,
            "docdate": dt,
            "name": name,
            "passbook": passbook,
            "mobile": normalize_mobile(mobile),
            "scheme": scheme,
            "weight": weight,
            "status": status,
            "raw_showroom": sh,
            "showroom": canon_sh,
        }

    def _extract_enrollment_record(self, raw: Any, default_scheme: str = "SS") -> Optional[Dict[str, Any]]:
        """Normalize enrollment record from dict, CleanedRecord, or list."""
        if hasattr(raw, "SCHDATE"):  # CleanedRecord model
            dt = parse_date_safe(raw.SCHDATE)
            mob = normalize_mobile(raw.MOBILENO)
            amt = float(raw.RECAMOUNT or 0.0)
            sh = map_showroom_label(raw.LOCATION_2 or raw.COSTNAME or "")
            sch = str(raw.SCHEME or default_scheme)
            msno = str(raw.MSNO or "")
            name = str(raw.NAME or "")
        elif isinstance(raw, dict):
            dt = parse_date_safe(raw.get("SCHDATE") or raw.get("schdate") or raw.get("date"))
            mob = normalize_mobile(raw.get("MOBILENO") or raw.get("mobileno") or raw.get("mobile"))
            amt = float(raw.get("RECAMOUNT") or raw.get("recamount") or raw.get("amount") or 0.0)
            sh = map_showroom_label(raw.get("LOCATION_2") or raw.get("COSTNAME") or raw.get("showroom") or "")
            sch = str(raw.get("SCHEME") or raw.get("scheme") or default_scheme)
            msno = str(raw.get("MSNO") or raw.get("msno") or "")
            name = str(raw.get("NAME") or raw.get("name") or "")
        elif isinstance(raw, (list, tuple)):
            # Google Sheet row format for SS / SV tab
            # e.g. SS - Oct: col 3: MSNO, col 4: NAME, col 5: SCHDATE, col 6: LOC2, col 8: AMT, col 9: MOBILENO, col 10: SCHEME
            mob = normalize_mobile(raw[9]) if len(raw) > 9 else ""
            dt = parse_date_safe(raw[5]) if len(raw) > 5 else None
            sh = map_showroom_label(raw[6]) if len(raw) > 6 else ""
            amt = float(str(raw[8]).replace(",", "").strip() or 0.0) if len(raw) > 8 else 0.0
            sch = str(raw[10]).strip() if len(raw) > 10 else default_scheme
            msno = str(raw[3]).strip() if len(raw) > 3 else ""
            name = str(raw[4]).strip() if len(raw) > 4 else ""
        else:
            return None

        if not dt or not mob:
            return None

        return {
            "schdate": dt,
            "mobile": mob,
            "showroom": sh,
            "amount": amt,
            "scheme": sch,
            "msno": msno,
            "name": name,
        }

    def process(
        self,
        closed_records: List[Any],
        ss_enrollments: List[Any],
        sv_enrollments: Optional[List[Any]] = None,
    ) -> RejoiningReportResult:
        """
        Execute point-in-time matching and compute all three report tables.
        """
        sv_enrollments = sv_enrollments or []

        # 1. Index eligible enrollments strictly <= cutoff_date
        eligible_ss_by_mobile = defaultdict(list)
        eligible_sv_by_mobile = defaultdict(list)
        excluded_future_count = 0

        for r in ss_enrollments:
            enr = self._extract_enrollment_record(r, default_scheme="SS")
            if not enr:
                continue
            if enr["schdate"] <= self.cutoff_date:
                eligible_ss_by_mobile[enr["mobile"]].append(enr)
            else:
                excluded_future_count += 1

        for r in sv_enrollments:
            enr = self._extract_enrollment_record(r, default_scheme="SV")
            if not enr:
                continue
            if enr["schdate"] <= self.cutoff_date:
                eligible_sv_by_mobile[enr["mobile"]].append(enr)
            else:
                excluded_future_count += 1

        # 2. Extract and filter closed records strictly <= cutoff_date
        eligible_closed: List[Dict[str, Any]] = []
        invalid_date_count = 0
        unresolved_showrooms_set: Set[str] = set()

        # Track mobile occurrences among closed records to detect shared phone numbers
        phone_closed_counter = Counter()

        for idx, r in enumerate(closed_records, start=2):
            cl = self._extract_closed_record(r, index=idx)
            if not cl:
                invalid_date_count += 1
                continue

            # 1. Enforce reporting-month boundary in both modes
            if (cl["docdate"].year != self.cutoff_date.year or 
                cl["docdate"].month != self.cutoff_date.month):
                excluded_future_count += 1
                continue

            # 2. In mode='daily', strictly enforce cl['docdate'] <= cutoff_date
            if self.mode == "daily" and cl["docdate"] > self.cutoff_date:
                excluded_future_count += 1
                continue

            if cl["showroom"] not in self.showrooms:
                unresolved_showrooms_set.add(cl["raw_showroom"] or "EMPTY")
                continue

            eligible_closed.append(cl)
            if cl["mobile"]:
                phone_closed_counter[cl["mobile"]] += 1

        # 3. Perform matching and aggregate metrics
        counts_by_sh = {sh: 0 for sh in self.showrooms}
        ss_by_sh = {sh: 0 for sh in self.showrooms}
        sv_by_sh = {sh: 0 for sh in self.showrooms}

        weight_by_sh = {sh: 0.0 for sh in self.showrooms}
        ss_weight_by_sh = {sh: 0.0 for sh in self.showrooms}
        sv_weight_by_sh = {sh: 0.0 for sh in self.showrooms}

        schemes_by_sh: Dict[str, Dict[str, int]] = {sh: defaultdict(int) for sh in self.showrooms}
        all_schemes: Set[str] = set()

        matches_list: List[RejoiningMatchAudit] = []
        ambiguous_matches: List[RejoiningMatchAudit] = []

        for cl in eligible_closed:
            sh = cl["showroom"]
            mob = cl["mobile"]
            docdate = cl["docdate"]
            weight = cl["weight"]
            sch_name = cl["scheme"] or "UNKNOWN"

            counts_by_sh[sh] += 1
            weight_by_sh[sh] += weight
            schemes_by_sh[sh][sch_name] += 1
            all_schemes.add(sch_name)

            # Check Subhiksham match
            ss_candidates = eligible_ss_by_mobile.get(mob, [])
            if self.require_enrollment_after_closure:
                ss_candidates = [e for e in ss_candidates if e["schdate"] >= docdate]

            matched_ss = len(ss_candidates) > 0
            if matched_ss:
                ss_by_sh[sh] += 1
                ss_weight_by_sh[sh] += weight
                best_ss = ss_candidates[0]
                is_ambiguous = phone_closed_counter[mob] > 1

                audit_rec = RejoiningMatchAudit(
                    closed_row_index=cl["index"],
                    showroom=sh,
                    closed_date=docdate,
                    customer_name=cl["name"],
                    passbook=cl["passbook"],
                    mobile=mob,
                    matched_scheme="SS",
                    enrollment_date=best_ss["schdate"],
                    enrollment_branch=best_ss["showroom"],
                    enrollment_amount=best_ss["amount"],
                    msno=best_ss["msno"],
                    is_ambiguous_shared_phone=is_ambiguous,
                )
                matches_list.append(audit_rec)
                if is_ambiguous:
                    ambiguous_matches.append(audit_rec)

            # Check Viruksham match (only if not already matched to SS or if independent)
            sv_candidates = eligible_sv_by_mobile.get(mob, [])
            if self.require_enrollment_after_closure:
                sv_candidates = [e for e in sv_candidates if e["schdate"] >= docdate]

            matched_sv = len(sv_candidates) > 0
            if matched_sv:
                sv_by_sh[sh] += 1
                sv_weight_by_sh[sh] += weight
                best_sv = sv_candidates[0]
                is_ambiguous = phone_closed_counter[mob] > 1

                audit_rec = RejoiningMatchAudit(
                    closed_row_index=cl["index"],
                    showroom=sh,
                    closed_date=docdate,
                    customer_name=cl["name"],
                    passbook=cl["passbook"],
                    mobile=mob,
                    matched_scheme="SV",
                    enrollment_date=best_sv["schdate"],
                    enrollment_branch=best_sv["showroom"],
                    enrollment_amount=best_sv["amount"],
                    msno=best_sv["msno"],
                    is_ambiguous_shared_phone=is_ambiguous,
                )
                matches_list.append(audit_rec)
                if is_ambiguous and not matched_ss:
                    ambiguous_matches.append(audit_rec)

        # 4. Build Table 1 Rows & Summary
        table1_rows = [
            RejoiningCalculator.calculate_row(
                showroom=sh,
                closed=counts_by_sh[sh],
                rejoin_ss=ss_by_sh[sh],
                rejoin_sv=sv_by_sh[sh],
            )
            for sh in self.showrooms
        ]
        table1_summary = RejoiningCalculator.compute_summary(table1_rows)

        # 5. Build Table 2 (Weight) Rows & Summary
        table2_rows: List[RejoiningWeightRow] = []
        for sh in self.showrooms:
            cw = round(weight_by_sh[sh], 3)
            ss_w = round(ss_weight_by_sh[sh], 3)
            sv_w = round(sv_weight_by_sh[sh], 3)
            tot_w = round(ss_w + sv_w, 3)
            pct_w = round((tot_w / cw * 100.0), 2) if cw > 0 else 0.0

            table2_rows.append(
                RejoiningWeightRow(
                    showroom=sh,
                    closed_weight=cw,
                    rejoin_ss_weight=ss_w,
                    rejoin_sv_weight=sv_w,
                    total_rejoin_weight=tot_w,
                    rejoin_weight_percentage=pct_w,
                )
            )

        tot_cw = round(sum(r.closed_weight for r in table2_rows), 3)
        tot_ss_w = round(sum(r.rejoin_ss_weight for r in table2_rows), 3)
        tot_sv_w = round(sum(r.rejoin_sv_weight for r in table2_rows), 3)
        tot_rejoin_w = round(tot_ss_w + tot_sv_w, 3)
        tot_pct_w = round((tot_rejoin_w / tot_cw * 100.0), 2) if tot_cw > 0 else 0.0

        table2_summary = RejoiningWeightRow(
            showroom="TOTAL",
            closed_weight=tot_cw,
            rejoin_ss_weight=tot_ss_w,
            rejoin_sv_weight=tot_sv_w,
            total_rejoin_weight=tot_rejoin_w,
            rejoin_weight_percentage=tot_pct_w,
        )

        # 6. Build Table 3 (Scheme Breakdown) Rows & Summary
        table3_rows: List[RejoiningSchemeRow] = []
        table3_summary: Dict[str, int] = defaultdict(int)

        for sh in self.showrooms:
            sh_schemes = dict(schemes_by_sh[sh])
            sh_total = sum(sh_schemes.values())
            for sch, cnt in sh_schemes.items():
                table3_summary[sch] += cnt

            table3_rows.append(
                RejoiningSchemeRow(
                    showroom=sh,
                    scheme_counts=sh_schemes,
                    total_count=sh_total,
                )
            )

        return RejoiningReportResult(
            report_date=self.cutoff_date,
            mode=self.mode,
            snapshot_id=self.snapshot_id,
            snapshot_hash=self.snapshot_hash,
            table1_rows=table1_rows,
            table1_summary=table1_summary,
            table2_weight_rows=table2_rows,
            table2_summary=table2_summary,
            table3_scheme_rows=table3_rows,
            table3_summary=dict(table3_summary),
            matches=matches_list,
            ambiguous_shared_phone_matches=ambiguous_matches,
            total_closed_count=table1_summary.closed_count,
            total_closed_weight=table2_summary.closed_weight,
            total_rejoin_ss=table1_summary.rejoin_ss,
            total_rejoin_sv=table1_summary.rejoin_sv,
            total_rejoin_count=table1_summary.total_rejoin,
            overall_rejoin_percentage=table1_summary.rejoin_percentage,
            unresolved_showrooms=sorted(list(unresolved_showrooms_set)),
            invalid_date_records_count=invalid_date_count,
            excluded_future_records_count=excluded_future_count,
        )
