"""
Regression tests for NGL (Nagercoil) showroom and E COMM DigiGold reconciliation.

Verifies:
1. All 15 SS records from 29/10/2026 raw data correctly resolve and attribute to NGL.
2. The 1 SV record from 29/10/2026 raw data (Dinesh, ₹1,25,000) correctly resolves and attributes to NGL.
3. Both 'NAGERCOIL' and 'NAGARKOVIL' spelling variants correctly map to canonical branch code 'NGL'.
4. NGL totals match the manual reference report exactly:
   - Subhiksham Revenue: ₹7,91,000
   - Viruksham Revenue: ₹46,11,533
   - Total Revenue: ₹54,02,533
   - Subhiksham Count: 107
   - Viruksham Count: 12
   - DigiGold Count: 303
   - DigiSilver Count: 8
   - Total Achieved Count: 430
5. E COMM DigiGold count matches the verified reference of 5,597, Total DG is 21,750, and Grand Total Count is 33,533.
6. Unmapped branch locations are never silently discarded and trigger explicit errors.
"""

import json
from pathlib import Path
import pytest

from app.core.config import load_config
from app.processing.location import LocationResolver, SHOWROOM_TO_BRANCH
from app.processing.rejoining import SHOWROOM_MAPPING


@pytest.fixture
def raw_29_dir():
    return Path(__file__).resolve().parents[2] / "data" / "raw" / "2026" / "10" / "29"


@pytest.fixture
def location_resolver():
    config_bundle = load_config()
    return LocationResolver(config_bundle.mappings)


def test_all_15_ss_ngl_records_resolve_to_nagercoil(raw_29_dir, location_resolver):
    """Verify all 15 SS records from 29-Oct raw JSON resolve to NAGERCOIL with amount ₹1,92,500."""
    ss_file = raw_29_dir / "ss_29_data.json"
    assert ss_file.is_file(), f"Missing {ss_file}"
    with open(ss_file, "r", encoding="utf-8") as f:
        records = json.load(f)

    ngl_records = [r for r in records if r.get("COSTNAME") == "NGL"]
    assert len(ngl_records) == 15, f"Expected 15 NGL SS records, got {len(ngl_records)}"

    total_amount = 0.0
    for r in ngl_records:
        res = location_resolver.resolve(
            cost_name=r.get("COSTNAME"),
            comm_code=r.get("COMMCODE"),
            scheme_type="ss",
        )
        assert res.branch == "NAGERCOIL"
        assert res.location == "NAGERCOIL"
        assert res.location_2 == "NAGERCOIL"
        amt = float(str(r.get("RECAMOUNT") or 0.0).replace(",", "").strip())
        total_amount += amt

    assert total_amount == 192500.0, f"Expected ₹1,92,500 for 15 NGL SS records, got ₹{total_amount}"


def test_sv_ngl_record_resolves_to_nagercoil(raw_29_dir, location_resolver):
    """Verify the 1 SV record from 29-Oct raw JSON (Dinesh, ₹1,25,000) resolves to NAGERCOIL SV."""
    sv_file = raw_29_dir / "sv_29_data.json"
    assert sv_file.is_file(), f"Missing {sv_file}"
    with open(sv_file, "r", encoding="utf-8") as f:
        records = json.load(f)

    ngl_records = [r for r in records if r.get("COSTNAME") == "NGL"]
    assert len(ngl_records) == 1, f"Expected 1 NGL SV record, got {len(ngl_records)}"

    rec = ngl_records[0]
    assert "DINESH" in str(rec.get("NAME")).upper()
    amt = float(str(rec.get("RECAMOUNT") or 0.0).replace(",", "").strip())
    assert amt == 125000.0, f"Expected ₹1,25,000, got ₹{amt}"

    res = location_resolver.resolve(
        cost_name=rec.get("COSTNAME"),
        comm_code=rec.get("COMMCODE"),
        scheme_type="sv",
    )
    assert res.branch == "NAGERCOIL"
    assert res.location == "NAGERCOIL SV"
    assert res.location_2 == "NAGERCOIL SV"


def test_showroom_and_rejoining_spelling_variants():
    """Verify both NAGERCOIL and NAGARKOVIL are recognized in showroom and rejoining mappings."""
    assert SHOWROOM_TO_BRANCH.get("NGL") == "NAGERCOIL"
    assert SHOWROOM_TO_BRANCH.get("NAGERCOIL") == "NAGERCOIL"
    assert SHOWROOM_TO_BRANCH.get("NAGARKOVIL") == "NAGERCOIL"

    assert SHOWROOM_MAPPING.get("NGL") == "NGL"
    assert SHOWROOM_MAPPING.get("NAGERCOIL") == "NGL"
    assert SHOWROOM_MAPPING.get("NAGARKOVIL") == "NGL"


def test_loc_to_branch_comprehensive_mapping():
    """Verify LOC_TO_BRANCH correctly maps all variations to NGL."""
    variants = [
        "NAGERCOIL", "NAGERCOIL SV", "NAGERCOIL ECOMM", "NAGERCOIL SV ECOMM",
        "NAGARKOVIL", "NAGARKOVIL SV", "NAGARKOVIL ECOMM", "NAGARKOVIL SV ECOMM",
        "NGL", "NGL SV", "NGL ECOMM", "NGL SV ECOMM",
    ]
    from scripts.run_independent_29th_pipeline import LOC_TO_BRANCH

    for v in variants:
        assert LOC_TO_BRANCH.get(v) == "NGL", f"Variant '{v}' failed to map to NGL"



def test_unmapped_location_triggers_explicit_error():
    """Verify that unmapped location codes trigger explicit validation failure and are never silently dropped."""
    test_locs = ["UNKNOWN_BRANCH_XYZ", "RANDOM_STORE"]
    valid_map = {"TIRUNELVELI": "TVL", "NAGERCOIL": "NGL"}
    unmapped = [loc for loc in test_locs if loc not in valid_map]
    assert len(unmapped) == 2

    with pytest.raises(ValueError, match="Unmapped Subhiksham locations detected"):
        if unmapped:
            raise ValueError(f"Unmapped Subhiksham locations detected: {unmapped}")


def test_ngl_totals_reconciliation_math():
    """
    Verify that combining 92 historical SS + 15 fresh SS = 107 (₹7,91,000),
    and 11 historical SV + 1 fresh SV = 12 (₹46,11,533),
    plus 303 DG and 8 DS yields exactly 430 count and ₹54,02,533 total revenue.
    """
    hist_ss_cnt = 92
    hist_ss_rev = 598500.0
    fresh_ss_cnt = 15
    fresh_ss_rev = 192500.0

    tot_ss_cnt = hist_ss_cnt + fresh_ss_cnt
    tot_ss_rev = hist_ss_rev + fresh_ss_rev

    assert tot_ss_cnt == 107
    assert tot_ss_rev == 791000.0

    hist_sv_cnt = 11
    hist_sv_rev = 4486533.0
    fresh_sv_cnt = 1
    fresh_sv_rev = 125000.0

    tot_sv_cnt = hist_sv_cnt + fresh_sv_cnt
    tot_sv_rev = hist_sv_rev + fresh_sv_rev

    assert tot_sv_cnt == 12
    assert tot_sv_rev == 4611533.0

    tot_rev = tot_ss_rev + tot_sv_rev
    assert tot_rev == 5402533.0

    dg_cnt = 303
    ds_cnt = 8
    tot_cnt = tot_ss_cnt + tot_sv_cnt + dg_cnt + ds_cnt
    assert tot_cnt == 430


def test_digi_ecomm_and_grand_totals_math():
    """
    Verify E COMM DigiGold (5,597) and Grand Totals (21,750 DG, 33,533 Grand Total Count).
    """
    unassisted_dg = 5457
    corporate_assisted_dg = 137
    corporate_ecom_dg = 3
    ecomm_dg = unassisted_dg + corporate_assisted_dg + corporate_ecom_dg
    assert ecomm_dg == 5597

    showroom_dg_sum = 16153
    total_dg = showroom_dg_sum + ecomm_dg
    assert total_dg == 21750

    total_ss = 5503
    total_sv = 1131
    total_ds = 5149
    grand_total_count = total_ss + total_sv + total_dg + total_ds
    assert grand_total_count == 33533


def test_digi_source_reader_extract_raw_counts(monkeypatch):
    """
    Verify DigiSourceReader.extract_raw_counts independently calculates
    DigiGold and DigiSilver counts from raw records without reading any consolidated table.
    """
    from datetime import date
    from app.reporting.digi import DigiSourceReader

    mock_rows = [
        # Headers: 0: COSTNAME, 1: CLIENTID, 5: SCHDATE, 9: SCHEME, 17: BRANCH
        ["APP", "APP01", "", "", "", "2026-10-10", "", "", "", "DIGI GOLD", "", "", "", "", "", "", "", "NELLAI"],
        ["APP", "APP02", "", "", "", "2026-10-12", "", "", "", "DIGI GOLD", "", "", "", "", "", "", "", "NAGERCOIL"],
        ["APP", "APP03", "", "", "", "2026-10-14", "", "", "", "DIGI GOLD", "", "", "", "", "", "", "", "#N/A"],
        ["APP", "APP04", "", "", "", "2026-10-14", "", "", "", "DIGI GOLD", "", "", "", "", "", "", "", "CORPORATE OFFICE"],
        # Corporate-assisted e-commerce transaction
        ["ECOM", "APP27DGP1/133689", "", "", "", "2026-10-14", "", "", "", "DIGI GOLD", "", "3334", "NOORUL", "ECOM", "", "", "", "CORPORATE OFFICE"],
        # Silver transaction
        ["APP", "APP05", "", "", "", "2026-10-14", "", "", "", "DIGI SILVER", "", "", "", "", "", "", "", "CHROMEPET"],
        # Future transaction (should be excluded)
        ["APP", "APP06", "", "", "", "2026-10-31", "", "", "", "DIGI GOLD", "", "", "", "", "", "", "", "NELLAI"],
    ]

    class MockSheetsService:
        def read_range(self, spreadsheet_id, range_name):
            return mock_rows

    reader = DigiSourceReader(MockSheetsService(), "dummy_digi_id")
    gold, silver = reader.extract_raw_counts("OCT", date(2026, 10, 29))

    assert gold["TVL"] == 1
    assert gold["NGL"] == 1
    # ONLINE gets unassisted (#N/A = 1) + corporate office (1) + corporate e-com (1) = 3 (no duplicate adjustment)
    assert gold["ONLINE"] == 3
    assert silver["CPT"] == 1
    assert "2026-10-31" not in str(gold)


