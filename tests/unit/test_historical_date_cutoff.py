"""
Unit and Regression Tests for Historical Date Correctness & Cutoff Enforcement.

Regression Requirements (Prompt Section 2):
1. Generate the 25 October report using data containing records dated 26 October.
   It must reproduce the correct 25 October result, including 867 SS rejoinings.
2. Generate the same 25 October report again after adding 26 October records.
   The 25 October output must remain unchanged (repeat-run reproducibility).
3. Generate the 26 October report.
   Eligible 26 October records should be included according to business rules (producing 871 SS rejoinings).
4. Confirm that missing or invalid dates cannot silently produce incorrect matches.
5. Shared mobile numbers matching multiple closed accounts:
   Preserves established Google Sheets matching policy, but flags ambiguous matches for audit review.
"""

from datetime import date
import pytest

from app.processing.rejoining import (
    DEFAULT_PHYSICAL_SHOWROOMS,
    RejoiningMatchingEngine,
    RejoiningReportResult,
    parse_date_safe,
    normalize_mobile,
)


@pytest.fixture
def base_25_oct_fixtures():
    """
    Construct realistic test fixture reproducing the verified 25 October 2026 state:
    - 4,604 closed records across 9 physical showrooms (TVL: 561, CPT: 976, TVM: 472, TPJ: 868, CBE: 881, SLM: 310, PNM: 336, PADI: 200, KPM: 0).
    - 867 SS matches (TVL: 124, CPT: 211, TVM: 117, TPJ: 164, CBE: 140, SLM: 52, PNM: 39, PADI: 20, KPM: 0).
    - 79 SV matches (TVL: 12, CPT: 22, TVM: 8, TPJ: 14, CBE: 13, SLM: 4, PNM: 5, PADI: 1, KPM: 0).
    - 26 October additions:
      - 2 TVL closed records sharing mobile 9400003755, with 1 SS enrollment on 26 Oct (+2).
      - 2 CPT closed records sharing mobile 7800001614, with 1 SS enrollment on 26 Oct (+2).
    """
    showrooms_closed = {
        "TVL": 561, "CPT": 976, "TVM": 472, "TPJ": 868,
        "CBE": 881, "SLM": 310, "PNM": 336, "PADI": 200, "KPM": 0
    }
    showrooms_ss_match = {
        "TVL": 124, "CPT": 211, "TVM": 117, "TPJ": 164,
        "CBE": 140, "SLM": 52, "PNM": 39, "PADI": 20, "KPM": 0
    }
    showrooms_sv_match = {
        "TVL": 12, "CPT": 22, "TVM": 8, "TPJ": 14,
        "CBE": 13, "SLM": 4, "PNM": 5, "PADI": 1, "KPM": 0
    }

    closed_records = []
    ss_enrollments = []
    sv_enrollments = []

    # Generate synthetic records matching exact showroom distributions
    for sh_idx, (sh, count) in enumerate(showrooms_closed.items(), start=10):
        ss_target = showrooms_ss_match[sh]
        sv_target = showrooms_sv_match[sh]
        for i in range(count):
            phone = f"98{sh_idx:02d}{i:06d}"
            # Even weight distribution summing to representative weights

            weight = 7.13733  # ~ 32,860.288 / 4604
            closed_rec = {
                "docdate": "2026-10-15",
                "name": f"Customer_{sh}_{i}",
                "passbook": f"PB-{sh}-{i}",
                "mobile": phone,
                "scheme": "NEW SWARNA SUBHIKSHAM",
                "weight": weight,
                "status": "CLOSE",
                "showroom": sh,
            }
            closed_records.append(closed_rec)

            if i < ss_target:
                # Matched SS enrollment on or before 25 Oct
                ss_enrollments.append({
                    "schdate": "2026-10-20",
                    "mobileno": phone,
                    "costname": sh,
                    "recamount": 5000.0,
                    "scheme": "NEW SWARNA SUBHIKSHAM",
                    "msno": f"MSNO-SS-{sh}-{i}",
                    "name": f"Customer_{sh}_{i}",
                })
            elif i < (ss_target + sv_target):
                # Matched SV enrollment on or before 25 Oct
                sv_enrollments.append({
                    "schdate": "2026-10-20",
                    "mobileno": phone,
                    "costname": sh,
                    "recamount": 10000.0,
                    "scheme": "SWARNA VIRUKSHAM",
                    "msno": f"MSNO-SV-{sh}-{i}",
                    "name": f"Customer_{sh}_{i}",
                })

    # Add the 4 controversial shared-phone records closed on 18-19 Oct
    # TVL: Kumar G & Amutha M S (closed 18-10-2026, shared mobile 9400003755)
    tvl_shared_phone = "9400003755"
    closed_records.append({
        "docdate": "2026-10-18",
        "name": "KUMAR G",
        "passbook": "SPF-7087",
        "mobile": tvl_shared_phone,
        "scheme": "NEW SWARNA SUBHIKSHAM",
        "weight": 8.0,
        "status": "CLOSE",
        "showroom": "TVL",
    })
    closed_records.append({
        "docdate": "2026-10-18",
        "name": "AMUTHA M S",
        "passbook": "SPF-7088",
        "mobile": tvl_shared_phone,
        "scheme": "NEW SWARNA SUBHIKSHAM",
        "weight": 8.0,
        "status": "CLOSE",
        "showroom": "TVL",
    })

    # CPT: A.Elumalai & Manjula (closed 19-10-2026, shared mobile 7800001614)
    cpt_shared_phone = "7800001614"
    closed_records.append({
        "docdate": "2026-10-19",
        "name": "A.ELUMALAI",
        "passbook": "SCF-12521",
        "mobile": cpt_shared_phone,
        "scheme": "NEW SWARNA SUBHIKSHAM",
        "weight": 10.0,
        "status": "CLOSE",
        "showroom": "CPT",
    })
    closed_records.append({
        "docdate": "2026-10-19",
        "name": "MANJULA",
        "passbook": "SCF-12520",
        "mobile": cpt_shared_phone,
        "scheme": "NEW SWARNA SUBHIKSHAM",
        "weight": 10.0,
        "status": "CLOSE",
        "showroom": "CPT",
    })

    # Enrollments for those shared phones dated 26-10-2026 (AFTER 25 Oct cutoff)
    future_ss_enrollments = [
        {
            "schdate": "2026-10-26",
            "mobileno": tvl_shared_phone,
            "costname": "TVL",
            "recamount": 5000.0,
            "scheme": "NEW SWARNA SUBHIKSHAM",
            "msno": "MSNO-TVL-LATER",
            "name": "KUMAR G",
        },
        {
            "schdate": "2026-10-26",
            "mobileno": cpt_shared_phone,
            "costname": "CPT",
            "recamount": 5000.0,
            "scheme": "NEW SWARNA SUBHIKSHAM",
            "msno": "MSNO-CPT-LATER",
            "name": "A.ELUMALAI",
        },
    ]

    return {
        "closed_records": closed_records,
        "ss_enrollments": ss_enrollments,
        "sv_enrollments": sv_enrollments,
        "future_ss_enrollments": future_ss_enrollments,
        "expected_25_ss": 867,
        "expected_25_sv": 79,
    }


def test_regression_1_cutoff_with_future_data_present(base_25_oct_fixtures):
    """
    REGRESSION TEST 1:
    Generate the 25 October report using data containing records dated 26 October.
    It must reproduce the correct 25 October result (867 SS rejoinings),
    completely ignoring the 26 October enrollment records.
    """
    closed = base_25_oct_fixtures["closed_records"]
    ss_enr = base_25_oct_fixtures["ss_enrollments"] + base_25_oct_fixtures["future_ss_enrollments"]
    sv_enr = base_25_oct_fixtures["sv_enrollments"]

    engine = RejoiningMatchingEngine(cutoff_date=date(2026, 10, 25))
    res = engine.process(closed, ss_enr, sv_enr)

    assert res.report_date == date(2026, 10, 25)
    assert res.total_rejoin_ss == 867
    assert res.total_rejoin_sv == 79
    assert res.total_rejoin_count == 867 + 79

    # Verify TVL and CPT matched exact historical numbers without the +2 leaks
    tvl_row = next(r for r in res.table1_rows if r.showroom == "TVL")
    cpt_row = next(r for r in res.table1_rows if r.showroom == "CPT")
    assert tvl_row.rejoin_ss == 124
    assert cpt_row.rejoin_ss == 211
    assert res.excluded_future_records_count == 2  # The 2 future enrollments on 26 Oct


def test_regression_2_repeat_run_idempotency(base_25_oct_fixtures):
    """
    REGRESSION TEST 2:
    Generate the same 25 October report again after adding 26 October records.
    The 25 October output must remain 100% unchanged.
    """
    closed = base_25_oct_fixtures["closed_records"]
    ss_enr_before = list(base_25_oct_fixtures["ss_enrollments"])
    sv_enr = base_25_oct_fixtures["sv_enrollments"]

    # Run 1: 25 Oct baseline without 26 Oct records
    engine = RejoiningMatchingEngine(cutoff_date=date(2026, 10, 25))
    res1 = engine.process(closed, ss_enr_before, sv_enr)

    # Run 2: Add 26 Oct records and re-run for 25 Oct
    ss_enr_after = ss_enr_before + base_25_oct_fixtures["future_ss_enrollments"]
    res2 = engine.process(closed, ss_enr_after, sv_enr)

    # Output must be identical across all metrics
    assert res1.total_rejoin_ss == res2.total_rejoin_ss == 867
    assert res1.total_rejoin_sv == res2.total_rejoin_sv == 79
    assert res1.total_rejoin_count == res2.total_rejoin_count == 946
    assert res1.total_closed_count == res2.total_closed_count
    for r1, r2 in zip(res1.table1_rows, res2.table1_rows):
        assert r1.showroom == r2.showroom
        assert r1.rejoin_ss == r2.rejoin_ss
        assert r1.rejoin_sv == r2.rejoin_sv
        assert r1.total_rejoin == r2.total_rejoin


def test_regression_3_26_oct_report_includes_eligible_records(base_25_oct_fixtures):
    """
    REGRESSION TEST 3:
    Generate the 26 October report.
    Eligible 26 October records should now be included, producing 871 SS rejoinings
    (TVL 126 and CPT 213).
    """
    closed = base_25_oct_fixtures["closed_records"]
    ss_enr = base_25_oct_fixtures["ss_enrollments"] + base_25_oct_fixtures["future_ss_enrollments"]
    sv_enr = base_25_oct_fixtures["sv_enrollments"]

    engine_26 = RejoiningMatchingEngine(cutoff_date=date(2026, 10, 26))
    res = engine_26.process(closed, ss_enr, sv_enr)

    assert res.report_date == date(2026, 10, 26)
    assert res.total_rejoin_ss == 871
    assert res.total_rejoin_sv == 79

    tvl_row = next(r for r in res.table1_rows if r.showroom == "TVL")
    cpt_row = next(r for r in res.table1_rows if r.showroom == "CPT")
    assert tvl_row.rejoin_ss == 126  # 124 + 2
    assert cpt_row.rejoin_ss == 213  # 211 + 2
    assert res.excluded_future_records_count == 0


def test_regression_4_missing_or_invalid_dates_rejected():
    """
    REGRESSION TEST 4:
    Confirm that missing or invalid dates cannot silently produce incorrect matches.
    Records with unparseable or missing dates must be discarded/flagged and never matched.
    """
    closed = [
        {"docdate": None, "mobile": "9800000001", "showroom": "TVL", "name": "Invalid1"},
        {"docdate": "", "mobile": "9800000002", "showroom": "TVL", "name": "Invalid2"},
        {"docdate": "INVALID-DATE", "mobile": "9800000003", "showroom": "TVL", "name": "Invalid3"},
        {"docdate": "2026-10-20", "mobile": "9800000004", "showroom": "TVL", "name": "Valid1"},
    ]
    ss_enr = [
        {"schdate": "NOT_A_DATE", "mobileno": "9800000004", "costname": "TVL"},
        {"schdate": None, "mobileno": "9800000004", "costname": "TVL"},
        {"schdate": "2026-10-21", "mobileno": "9800000004", "costname": "TVL"},
    ]

    engine = RejoiningMatchingEngine(cutoff_date=date(2026, 10, 25))
    res = engine.process(closed, ss_enr)

    # 3 invalid closed records were discarded
    assert res.invalid_date_records_count == 3
    assert res.total_closed_count == 1
    # Only Valid1 matched with the single valid enrollment
    assert res.total_rejoin_ss == 1


def test_regression_5_shared_mobile_ambiguity_flagged(base_25_oct_fixtures):
    """
    REGRESSION TEST 5:
    Investigate whether shared mobile numbers can match multiple closed accounts.
    Preserves established matching policy (both match), but flags them as ambiguous.
    """
    closed = base_25_oct_fixtures["closed_records"]
    ss_enr = base_25_oct_fixtures["ss_enrollments"] + base_25_oct_fixtures["future_ss_enrollments"]
    sv_enr = base_25_oct_fixtures["sv_enrollments"]

    # When running as of 26 October (where the 4 shared-phone records match)
    engine = RejoiningMatchingEngine(cutoff_date=date(2026, 10, 26))
    res = engine.process(closed, ss_enr, sv_enr)

    # Must have detected ambiguous shared phone matches
    ambiguous = res.ambiguous_shared_phone_matches
    assert len(ambiguous) >= 4

    ambiguous_mobiles = {a.mobile for a in ambiguous}
    assert "9400003755" in ambiguous_mobiles  # Kumar G & Amutha MS
    assert "7800001614" in ambiguous_mobiles  # A.Elumalai & Manjula

    # Both closed passbooks matched
    kumar_matches = [a for a in ambiguous if a.mobile == "9400003755"]
    assert len(kumar_matches) == 2
    names = {m.customer_name for m in kumar_matches}
    assert "KUMAR G" in names
    assert "AMUTHA M S" in names


def test_three_table_generation_and_cross_reconciliation(base_25_oct_fixtures):
    """
    Verify Table 1, Table 2, and Table 3 reconcile with grand totals and cross-table checks.
    """
    closed = base_25_oct_fixtures["closed_records"]
    ss_enr = base_25_oct_fixtures["ss_enrollments"]
    sv_enr = base_25_oct_fixtures["sv_enrollments"]

    engine = RejoiningMatchingEngine(cutoff_date=date(2026, 10, 25))
    res = engine.process(closed, ss_enr, sv_enr)

    # 1. Table 1 math check
    for row in res.table1_rows:
        assert row.total_rejoin == row.rejoin_ss + row.rejoin_sv
        if row.closed_count > 0:
            expected_pct = round(row.total_rejoin / row.closed_count * 100.0, 2)
            assert abs(row.rejoin_percentage - expected_pct) <= 0.01

    # 2. Table 2 weight check
    for w_row in res.table2_weight_rows:
        assert abs(w_row.total_rejoin_weight - round(w_row.rejoin_ss_weight + w_row.rejoin_sv_weight, 3)) <= 0.001

    # 3. Cross-table count reconciliation: T1 total closed == sum(T3 counts)
    t1_total_closed = res.table1_summary.closed_count
    t3_total_closed = sum(res.table3_summary.values())
    assert t1_total_closed == t3_total_closed
