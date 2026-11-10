"""
Regression Test Suite: Canonical 10-Branch Roster and Showroom Mapping Integrity.

Verifies:
1. NGL records are never dropped or ignored.
2. KPM records are never dropped or ignored.
3. Both KPM and NGL can have active closures in the same reporting period independently.
4. Zero-activity physical branches still appear in all executive tables with 0 values.
5. Unknown physical showroom codes are visibly detected in unresolved_physical_showrooms,
   while known digital channels (APP, ECOM, HAPP) are excluded as non-physical channels.
"""

from datetime import date
import pytest

from app.processing.rejoining import (
    DEFAULT_PHYSICAL_SHOWROOMS,
    SHOWROOM_MAPPING,
    SHOWROOM_CANONICAL,
    KNOWN_DIGITAL_SHOWROOMS,
    RejoiningMatchingEngine,
    map_showroom_label,
)


def test_canonical_ten_showrooms_constants():
    """Verify that DEFAULT_PHYSICAL_SHOWROOMS contains all 10 canonical branches in expected order."""
    expected = ["TVL", "CPT", "TVM", "TPJ", "CBE", "SLM", "PNM", "PADI", "KPM", "NGL"]
    assert DEFAULT_PHYSICAL_SHOWROOMS == expected
    assert len(DEFAULT_PHYSICAL_SHOWROOMS) == 10

    # Verify alias mappings
    assert SHOWROOM_MAPPING["KANCHI"] == "KPM"
    assert SHOWROOM_MAPPING["KANCHEEPURAM"] == "KPM"
    assert SHOWROOM_MAPPING["KPM"] == "KPM"

    assert SHOWROOM_MAPPING["NGL"] == "NGL"
    assert SHOWROOM_MAPPING["NAGERCOIL"] == "NGL"
    assert SHOWROOM_MAPPING["NAGARKOVIL"] == "NGL"

    assert SHOWROOM_CANONICAL is SHOWROOM_MAPPING


def test_ngl_records_not_dropped():
    """Verify that NGL records (and aliases) are properly processed and not dropped."""
    cutoff = date(2026, 11, 9)
    closed = [
        {"docdate": "05-11-2026", "name": "NGL Customer 1", "passbook": "PB-NGL-1", "mobile": "9800000001", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 2.40352, "status": "TERMINATE", "showroom": "NGL"},
        {"docdate": "06-11-2026", "name": "NGL Customer 2", "passbook": "PB-NGL-2", "mobile": "9800000002", "scheme": "SWARNA VIRUKSHAM", "weight": 5.10, "status": "TERMINATE", "showroom": "NAGERCOIL"},
        {"docdate": "07-11-2026", "name": "NGL Customer 3", "passbook": "PB-NGL-3", "mobile": "9800000003", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 3.20, "status": "TERMINATE", "showroom": "NAGARKOVIL"},
    ]
    ss_enr = [
        {"schdate": date(2026, 11, 6), "mobile": "9800000001", "showroom": "NGL", "scheme": "SS", "amount": 1000},
    ]

    engine = RejoiningMatchingEngine(cutoff_date=cutoff, mode="month_to_date")
    res = engine.process(closed, ss_enr, [])

    # Check NGL metrics in Table 1
    ngl_row = next(r for r in res.table1_rows if r.showroom == "NGL")
    assert ngl_row.closed_count == 3
    assert ngl_row.rejoin_ss == 1
    assert ngl_row.rejoin_sv == 0
    assert ngl_row.total_rejoin == 1
    assert pytest.approx(ngl_row.rejoin_percentage, 0.01) == 33.33

    # Check NGL weight in Table 2
    ngl_weight_row = next(r for r in res.table2_weight_rows if r.showroom == "NGL")
    assert pytest.approx(ngl_weight_row.closed_weight, 0.001) == 10.704

    # Check NGL scheme breakdown in Table 3
    ngl_scheme_row = next(r for r in res.table3_scheme_rows if r.showroom == "NGL")
    assert ngl_scheme_row.scheme_counts["NEW SWARNA SUBHIKSHAM"] == 2
    assert ngl_scheme_row.scheme_counts["SWARNA VIRUKSHAM"] == 1

    # Check zero unresolved physical showrooms
    assert res.unresolved_physical_showrooms == []
    assert res.total_closed_count == 3


def test_kpm_records_not_dropped():
    """Verify that KPM records (and aliases) are properly processed and not dropped."""
    cutoff = date(2026, 10, 30)
    closed = [
        {"docdate": "10-10-2026", "name": "KPM Customer 1", "passbook": "PB-KPM-1", "mobile": "9700000001", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 1.50, "status": "TERMINATE", "showroom": "KPM"},
        {"docdate": "12-10-2026", "name": "KPM Customer 2", "passbook": "PB-KPM-2", "mobile": "9700000002", "scheme": "SWARNA VIRUKSHAM", "weight": 2.80, "status": "TERMINATE", "showroom": "KANCHI"},
        {"docdate": "14-10-2026", "name": "KPM Customer 3", "passbook": "PB-KPM-3", "mobile": "9700000003", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 4.00, "status": "TERMINATE", "showroom": "KANCHEEPURAM"},
    ]

    engine = RejoiningMatchingEngine(cutoff_date=cutoff, mode="month_to_date")
    res = engine.process(closed, [], [])

    kpm_row = next(r for r in res.table1_rows if r.showroom == "KPM")
    assert kpm_row.closed_count == 3
    assert pytest.approx(res.table2_weight_rows[DEFAULT_PHYSICAL_SHOWROOMS.index("KPM")].closed_weight, 0.01) == 8.30
    assert res.unresolved_physical_showrooms == []
    assert res.total_closed_count == 3


def test_both_kpm_and_ngl_active_concurrently():
    """Verify that both KPM and NGL can have active closures in the same period without merging."""
    cutoff = date(2026, 11, 9)
    closed = [
        {"docdate": "02-11-2026", "name": "KPM User", "passbook": "PB-K-1", "mobile": "9100000001", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 3.0, "status": "TERMINATE", "showroom": "KPM"},
        {"docdate": "03-11-2026", "name": "NGL User", "passbook": "PB-N-1", "mobile": "9200000001", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 2.40352, "status": "TERMINATE", "showroom": "NGL"},
        {"docdate": "04-11-2026", "name": "TVL User", "passbook": "PB-T-1", "mobile": "9300000001", "scheme": "SWARNA VIRUKSHAM", "weight": 1.0, "status": "TERMINATE", "showroom": "TVL"},
    ]

    engine = RejoiningMatchingEngine(cutoff_date=cutoff, mode="month_to_date")
    res = engine.process(closed, [], [])

    kpm_row = next(r for r in res.table1_rows if r.showroom == "KPM")
    ngl_row = next(r for r in res.table1_rows if r.showroom == "NGL")
    tvl_row = next(r for r in res.table1_rows if r.showroom == "TVL")

    assert kpm_row.closed_count == 1
    assert ngl_row.closed_count == 1
    assert tvl_row.closed_count == 1
    assert res.total_closed_count == 3
    assert pytest.approx(res.total_closed_weight, 0.001) == 6.40352


def test_zero_activity_branch_appears_with_zeroes():
    """Verify that a branch with zero activity (e.g. KPM in November) still appears with 0 values."""
    cutoff = date(2026, 11, 9)
    # Only NGL has activity
    closed = [
        {"docdate": "05-11-2026", "name": "JACOB CHANDRAN", "passbook": "NGL27SSP1/9", "mobile": "9751995118", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 2.40352, "status": "TERMINATE", "showroom": "NGL"},
    ]

    engine = RejoiningMatchingEngine(cutoff_date=cutoff, mode="month_to_date")
    res = engine.process(closed, [], [])

    # All 10 branches must be present
    assert len(res.table1_rows) == 10
    assert len(res.table2_weight_rows) == 10
    assert len(res.table3_scheme_rows) == 10

    # Branch order matches DEFAULT_PHYSICAL_SHOWROOMS
    assert [r.showroom for r in res.table1_rows] == DEFAULT_PHYSICAL_SHOWROOMS

    # KPM has 0 activity
    kpm_row = next(r for r in res.table1_rows if r.showroom == "KPM")
    assert kpm_row.closed_count == 0
    assert kpm_row.rejoin_ss == 0
    assert kpm_row.rejoin_sv == 0
    assert kpm_row.total_rejoin == 0
    assert kpm_row.rejoin_percentage == 0.0

    kpm_w = next(r for r in res.table2_weight_rows if r.showroom == "KPM")
    assert kpm_w.closed_weight == 0.0

    # NGL has 1 activity
    ngl_row = next(r for r in res.table1_rows if r.showroom == "NGL")
    assert ngl_row.closed_count == 1
    ngl_w = next(r for r in res.table2_weight_rows if r.showroom == "NGL")
    assert pytest.approx(ngl_w.closed_weight, 0.001) == 2.40352

    assert res.total_closed_count == 1
    assert pytest.approx(res.total_closed_weight, 0.001) == 2.40352


def test_unknown_physical_showroom_detected():
    """Verify that unknown physical showroom codes are tracked in unresolved_physical_showrooms."""
    cutoff = date(2026, 11, 9)
    closed = [
        # Known physical
        {"docdate": "05-11-2026", "name": "NGL User", "passbook": "PB-NGL", "mobile": "9800000001", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 2.40, "status": "TERMINATE", "showroom": "NGL"},
        # Known digital
        {"docdate": "05-11-2026", "name": "App User", "passbook": "PB-APP", "mobile": "9800000002", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 1.00, "status": "TERMINATE", "showroom": "APP"},
        {"docdate": "05-11-2026", "name": "Ecom User", "passbook": "PB-ECOM", "mobile": "9800000003", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 1.00, "status": "TERMINATE", "showroom": "ECOM"},
        {"docdate": "05-11-2026", "name": "Happ User", "passbook": "PB-HAPP", "mobile": "9800000004", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 0.15, "status": "TERMINATE", "showroom": "HAPP"},
        # UNKNOWN physical showrooms
        {"docdate": "05-11-2026", "name": "Madurai User", "passbook": "PB-MDU", "mobile": "9800000005", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 3.00, "status": "TERMINATE", "showroom": "MADURAI"},
        {"docdate": "05-11-2026", "name": "Unknown Store", "passbook": "PB-UNK", "mobile": "9800000006", "scheme": "NEW SWARNA SUBHIKSHAM", "weight": 2.00, "status": "TERMINATE", "showroom": "XYZ_STORE"},
    ]

    engine = RejoiningMatchingEngine(cutoff_date=cutoff, mode="month_to_date")
    res = engine.process(closed, [], [])

    # Only 1 physical record (NGL) is eligible
    assert res.total_closed_count == 1
    assert pytest.approx(res.total_closed_weight, 0.01) == 2.40

    # Digital channels are in unresolved_showrooms but NOT in unresolved_physical_showrooms
    assert "APP" in res.unresolved_showrooms
    assert "ECOM" in res.unresolved_showrooms
    assert "HAPP" in res.unresolved_showrooms

    # Unknown physical showrooms MUST be in unresolved_physical_showrooms
    assert "MADURAI" in res.unresolved_physical_showrooms
    assert "XYZ_STORE" in res.unresolved_physical_showrooms
    assert len(res.unresolved_physical_showrooms) == 2

