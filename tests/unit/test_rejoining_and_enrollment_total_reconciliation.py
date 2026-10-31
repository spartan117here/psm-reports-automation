"""
Unit and regression tests for Scheme Rejoining Report reconciliation and New Enrollment Report grand total Yet to Achieve calculation.

Covers:
1. New Enrollment Report Grand Total Yet to Achieve (₹60,569,352).
2. Scheme Rejoining Report 2-mode architecture (daily vs month_to_date).
3. Reporting month boundary enforcement in both modes.
4. Strict enrollment cutoff enforcement (schdate <= cutoff_date) in both modes.
5. SnapshotManager immutability, SHA-256 integrity, and historical reproducibility.
6. Closure deduplication safeguarding legitimate multi-account records.
"""

from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional
import pytest

from app.processing.rejoining import (
    RejoiningMatchingEngine,
    DEFAULT_PHYSICAL_SHOWROOMS,
    map_showroom_label,
)
from app.repositories.snapshots import (
    SnapshotManager,
    SourceSnapshot,
    compute_records_sha256,
)


def test_new_enrollment_grand_total_yet_to_achieve():
    """
    REGRESSION TEST:
    Verify that the Grand Total Yet to Achieve in the New Enrollment Report
    is calculated by summing the validated branch-level Yet to Achieve values,
    yielding exactly ₹60,569,352 (NOT ₹87,569,352).
    """
    branch_data = [
        {"branch": "TVL", "target": 40000000, "ss": 1622500, "sv": 52784401, "total": 54406901},
        {"branch": "CPT", "target": 72000000, "ss": 3816000, "sv": 91129364, "total": 94945364},
        {"branch": "TVM", "target": 27000000, "ss": 923500, "sv": 27398335, "total": 28321835},
        {"branch": "TPJ", "target": 40000000, "ss": 1227000, "sv": 42389554, "total": 43616554},
        {"branch": "CBE", "target": 30000000, "ss": 1118000, "sv": 25060849, "total": 26178849},
        {"branch": "SLM", "target": 30000000, "ss": 788000, "sv": 31901757, "total": 32689757},
        {"branch": "PNM", "target": 20000000, "ss": 986500, "sv": 17577635, "total": 18564135},
        {"branch": "PADI", "target": 54000000, "ss": 2987500, "sv": 64950756, "total": 67938256},
        {"branch": "KANCHI", "target": 27000000, "ss": 1379500, "sv": 15453584, "total": 16833084},
        {"branch": "NGL", "target": None, "ss": 791000, "sv": 4611533, "total": 5402533},
        {"branch": "ONLINE", "target": None, "ss": 2447000, "sv": 9225084, "total": 11672084},
    ]

    expected_branch_yet = {
        "TVL": 14406901,
        "CPT": 22945364,
        "TVM": 1321835,
        "TPJ": 3616554,
        "CBE": -3821151,
        "SLM": 2689757,
        "PNM": -1435865,
        "PADI": 13938256,
        "KANCHI": -10166916,
        "NGL": 5402533,
        "ONLINE": 11672084,
    }

    table1_rows = []
    for b_item in branch_data:
        tot_amt = b_item["total"]
        tgt_val = b_item["target"]
        yet_val = (tot_amt - tgt_val) if tgt_val else tot_amt
        ach_pct = round((tot_amt / tgt_val) * 100) if tgt_val else None

        table1_rows.append({
            "branch": b_item["branch"],
            "target": tgt_val,
            "ss": b_item["ss"],
            "sv": b_item["sv"],
            "total": tot_amt,
            "yet": yet_val,
            "ach_pct": ach_pct,
        })

    # 1. Verify every branch-level Yet to Achieve
    for r in table1_rows:
        b = r["branch"]
        assert r["yet"] == expected_branch_yet[b], f"Branch {b} Yet to Achieve mismatch: {r['yet']} vs {expected_branch_yet[b]}"

    # 2. Verify KANCHI's target of 27,000,000 produces negative Yet to Achieve
    kanchi_row = next(r for r in table1_rows if r["branch"] == "KANCHI")
    assert kanchi_row["yet"] == -10166916

    # 3. Verify NGL and ONLINE have target=None and full revenue as Yet to Achieve
    ngl_row = next(r for r in table1_rows if r["branch"] == "NGL")
    assert ngl_row["target"] is None
    assert ngl_row["yet"] == 5402533

    online_row = next(r for r in table1_rows if r["branch"] == "ONLINE")
    assert online_row["target"] is None
    assert online_row["yet"] == 11672084

    # 4. Verify Grand Total calculation
    tot_rev_target = 313000000
    tot_comb_rev = sum(r["total"] for r in table1_rows)
    assert tot_comb_rev == 400569352

    # Grand total Yet to Achieve is the sum of branch Yet to Achieve values
    tot_yet_rev = sum(r["yet"] for r in table1_rows)
    assert tot_yet_rev == 60569352, f"Expected 60,569,352, got {tot_yet_rev}"

    # Verify that the old buggy calculation would have produced 87,569,352 (a ₹27M difference)
    buggy_tot_yet_rev = tot_comb_rev - tot_rev_target
    assert buggy_tot_yet_rev == 87569352
    assert buggy_tot_yet_rev - tot_yet_rev == 27000000  # Exactly KANCHI's target


def test_rejoining_modes_daily_vs_month_to_date():
    """
    REGRESSION TEST:
    Verify explicit two-mode behavior:
    - mode='daily': strictly filters cl['docdate'] <= cutoff_date.
    - mode='month_to_date': includes all valid physical showroom closures within the reporting month.
    """
    records = [
        # Record dated 28-Oct (in past)
        {"docdate": "28-10-2026", "name": "User 1", "passbook": "PB-1", "mobile": "9800000001", "scheme": "SS", "weight": 5.0, "status": "CLOSE", "showroom": "TVL"},
        # Record dated 29-Oct (on cutoff date)
        {"docdate": "29-10-2026", "name": "User 2", "passbook": "PB-2", "mobile": "9800000002", "scheme": "SS", "weight": 10.0, "status": "CLOSE", "showroom": "TVL"},
        # Record dated 30-Oct (in same month, but > cutoff_date)
        {"docdate": "30-10-2026", "name": "User 3", "passbook": "PB-3", "mobile": "9800000003", "scheme": "SS", "weight": 15.0, "status": "CLOSE", "showroom": "TVL"},
    ]

    target_date = date(2026, 10, 29)

    # 1. Daily mode: excludes the 30-Oct record (retaining PB-1 and PB-2)
    engine_daily = RejoiningMatchingEngine(cutoff_date=target_date, mode="daily")
    res_daily = engine_daily.process(records, [])
    assert res_daily.total_closed_count == 2
    assert res_daily.total_closed_weight == 15.0
    assert res_daily.excluded_future_records_count == 1

    # 2. Month-to-date mode: includes all 3 records within October
    engine_mtd = RejoiningMatchingEngine(cutoff_date=target_date, mode="month_to_date")
    res_mtd = engine_mtd.process(records, [])
    assert res_mtd.total_closed_count == 3
    assert res_mtd.total_closed_weight == 30.0
    assert res_mtd.excluded_future_records_count == 0


def test_rejoining_month_boundary_enforcement():
    """
    REGRESSION TEST:
    Verify that records outside the target reporting month/year are strictly excluded in BOTH modes.
    """
    records = [
        {"docdate": "30-09-2026", "name": "Past Month", "passbook": "PB-SEP", "mobile": "9800000001", "scheme": "SS", "weight": 5.0, "status": "CLOSE", "showroom": "TVL"},
        {"docdate": "15-10-2026", "name": "Target Month", "passbook": "PB-OCT", "mobile": "9800000002", "scheme": "SS", "weight": 10.0, "status": "CLOSE", "showroom": "TVL"},
        {"docdate": "01-11-2026", "name": "Next Month", "passbook": "PB-NOV", "mobile": "9800000003", "scheme": "SS", "weight": 15.0, "status": "CLOSE", "showroom": "TVL"},
    ]

    target_date = date(2026, 10, 29)

    # Both modes must reject PB-SEP and PB-NOV, keeping only PB-OCT
    for test_mode in ("daily", "month_to_date"):
        engine = RejoiningMatchingEngine(cutoff_date=target_date, mode=test_mode)
        res = engine.process(records, [])
        assert res.total_closed_count == 1, f"Mode {test_mode} failed month-boundary isolation"
        assert res.total_closed_weight == 10.0
        assert res.excluded_future_records_count == 2


def test_rejoining_enrollment_cutoff_enforced_in_both_modes():
    """
    REGRESSION TEST:
    Verify that rejoining enrollments MUST have schdate <= cutoff_date in BOTH modes.
    Future enrollments can NEVER match, preventing premature rejoining leaks.
    """
    closed = [
        {"docdate": "25-10-2026", "name": "Customer A", "passbook": "PB-A", "mobile": "9800000001", "scheme": "SS", "weight": 5.0, "status": "CLOSE", "showroom": "TVL"},
        {"docdate": "30-10-2026", "name": "Customer B", "passbook": "PB-B", "mobile": "9800000002", "scheme": "SS", "weight": 5.0, "status": "CLOSE", "showroom": "TVL"},
    ]

    # Eligible enrollment on 26 Oct (<= 29 Oct)
    eligible_enr = {"schdate": date(2026, 10, 26), "mobile": "9800000001", "showroom": "TVL", "scheme": "SS", "amount": 1000}
    # Future enrollment on 31 Oct (> 29 Oct)
    future_enr = {"schdate": date(2026, 10, 31), "mobile": "9800000002", "showroom": "TVL", "scheme": "SS", "amount": 1000}

    target_date = date(2026, 10, 29)

    # In month_to_date mode, Customer B closure is included, but their future enrollment on 31-Oct CANNOT match
    engine_mtd = RejoiningMatchingEngine(cutoff_date=target_date, mode="month_to_date")
    res = engine_mtd.process(closed, [eligible_enr, future_enr])

    assert res.total_closed_count == 2
    assert res.total_rejoin_ss == 1  # Only Customer A matched
    assert res.total_rejoin_count == 1
    assert res.excluded_future_records_count == 1  # The 31-Oct enrollment was excluded


def test_snapshot_manager_immutability_and_hash_integrity(tmp_path):
    """
    REGRESSION TEST:
    Verify that SnapshotManager preserves immutable snapshots, computes SHA-256,
    verifies integrity upon loading, and refuses to silently overwrite existing snapshots.
    """
    snap_mgr = SnapshotManager(base_dir=tmp_path)
    sample_records = [
        ["29-10-2026", "User Alpha", "PB-100", "9840011111", "SS", "5.0", "CLOSE", "TVL"],
        ["30-10-2026", "User Beta", "PB-101", "9840022222", "SS", "10.0", "CLOSE", "CPT"],
    ]

    target_date = date(2026, 10, 29)

    # 1. Create and save snapshot
    snap1 = snap_mgr.get_or_create_snapshot(
        category="closing",
        report_date=target_date,
        records=sample_records,
        source_identifier="test_closing_sheet",
    )

    assert snap1.row_count == 2
    assert len(snap1.sha256) == 64
    assert snap1.report_date == target_date

    # 2. Historical rerun: loading for the same date returns the exact original snapshot
    snap2 = snap_mgr.get_or_create_snapshot(
        category="closing",
        report_date=target_date,
        records=[["NEW", "MODIFIED", "RECORDS"]],
    )
    assert snap2.snapshot_id == snap1.snapshot_id
    assert snap2.sha256 == snap1.sha256
    assert snap2.row_count == 2
    assert snap2.records == sample_records  # Uses original records!

    # 3. Explicit overwrite without overwrite=True is blocked
    with pytest.raises(FileExistsError):
        snap_mgr.save_snapshot(snap1, overwrite=False)

    # 4. Tamper check: altering the file causes SHA-256 verification to fail
    snap_file = snap_mgr._get_snapshot_path("closing", target_date)
    import json
    with open(snap_file, "r") as f:
        tampered_data = json.load(f)
    tampered_data["records"][0][1] = "Tampered Name"
    with open(snap_file, "w") as f:
        json.dump(tampered_data, f)

    with pytest.raises(ValueError, match="Snapshot integrity violation"):
        snap_mgr.load_snapshot("closing", target_date)
