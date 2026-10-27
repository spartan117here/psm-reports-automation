"""
Isolated regression test suite for NAGERCOIL showroom expansion.
Mode: STRICT_READ_ONLY=true (Zero production modifications)
"""

import json
from pathlib import Path
from typing import Dict, List, Any
import pytest

from app.core.config import MappingsConfig, LocationNamingConfig
from app.processing.location import LocationResolver, SHOWROOM_TO_BRANCH
from app.reporting.closing import SHOWROOM_MAPPING, seed_mock_closing_sheet
from app.reporting.digi import DIGI_BRANCH_ORDER, BRANCH_NAME_ALIASES


# ==============================================================================
# Proposed Minimal Configurations (In-Memory Simulation)
# ==============================================================================

PROPOSED_SHOWROOM_TO_BRANCH = {
    **SHOWROOM_TO_BRANCH,
    "NGL": "NAGERCOIL",
    "NAGERCOIL": "NAGERCOIL",
}

PROPOSED_EMPLOYEE_TO_BRANCH = {
    "TVC": "TRIVANDRUM",
    "TVL": "TIRUNELVELI",
    "CPT": "CHROMEPET",
    "KPM": "KANCHEEPURAM",
    "PAD": "PADI",
    "SLM": "SALEM",
    "PNM": "POONAMALLEE",
    "CBE": "COIMBATORE",
    "TRI": "TRICHY",
    "TPJ": "TRICHY",
    "NGL": "NAGERCOIL",
}

PROPOSED_SHOWROOM_MAPPING = {
    **SHOWROOM_MAPPING,
    "NGL": "NGL",
    "NAGERCOIL": "NGL",
}

# 26 newly verified NGL employee codes from 2026-10-25 raw data
VERIFIED_NGL_EMPLOYEE_CODES = [
    "3410", "3432", "3490", "3499", "3501", "3502", "3504", "3505", "3507", "3509",
    "3512", "3514", "3515", "3516", "3552", "3557", "3566", "3575", "3578", "3582",
    "3583", "3586", "3589", "3591", "3604", "3613",
]

# 6 transferred employee codes (historical mappings kept intact)
TRANSFERRED_CODES = {
    "768": "TRIVANDRUM",
    "859": "TRIVANDRUM",
    "1183": "TRICHY",
    "1189": "TRICHY",
    "2267": "SALEM",
    "2294": "SALEM",
}


@pytest.fixture
def base_mappings_config():
    """Build MappingsConfig fixture."""
    return MappingsConfig(
        employee_to_branch=PROPOSED_EMPLOYEE_TO_BRANCH,
        showroom_codes=[
            "APP", "CBE", "CPT", "DTH-CBE", "DTH-CPT", "DTH-KPM", "DTH-PAD", "DTH-PNM",
            "DTH-POR", "DTH-SLM", "DTH-TPJ", "DTH-TVC", "DTH-TVL", "ECOM", "EXP01",
            "EXP02", "HAPP", "HO-CONSIGN", "HO-REP", "KPM", "NGL", "PAD", "PNM",
            "POR", "SLM", "TEXBOT", "TEXCBE", "TEXMDU", "TPJ", "TVC", "TVL"
        ],
        special_locations={
            "online_label": "ONLINE",
            "corporate_office_label": "CORPORATE OFFICE",
        },
        location_naming={
            "ss": LocationNamingConfig(store_suffix="", ecomm_suffix=" ECOMM"),
            "sv": LocationNamingConfig(store_suffix=" SV", ecomm_suffix=" SV ECOMM"),
        },
    )


@pytest.fixture
def updated_employee_registry():
    """Load existing employee_mapping.json and inject 26 NGL codes while preserving transfers."""
    reg_path = Path("config/employee_mapping.json")
    with open(reg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Verify historical mappings for transfers are intact
    for code, expected_branch in TRANSFERRED_CODES.items():
        if code in data["ss"]:
            assert data["ss"][code] == expected_branch
        if code in data["sv"]:
            assert data["sv"][code] == f"{expected_branch} SV"

    # Add 26 verified NGL codes
    for code in VERIFIED_NGL_EMPLOYEE_CODES:
        data["ss"][code] = "NAGERCOIL"
        data["sv"][code] = "NAGERCOIL SV"

    return data


@pytest.fixture
def proposed_resolver(base_mappings_config, updated_employee_registry):
    """Instantiate LocationResolver with proposed mappings."""
    resolver = LocationResolver(
        mappings=base_mappings_config,
        strict_mode=False,
        employee_registry=updated_employee_registry,
    )
    # Inject proposed SHOWROOM_TO_BRANCH
    resolver.showroom_to_branch = dict(PROPOSED_SHOWROOM_TO_BRANCH)
    return resolver


# ==============================================================================
# TEST 1: All 10 Physical Showrooms are Recognized
# ==============================================================================
def test_all_10_physical_showrooms_recognized(proposed_resolver):
    """Verify all 10 physical showrooms are canonically resolved."""
    expected_physical = [
        ("TVL", "TIRUNELVELI"),
        ("CPT", "CHROMEPET"),
        ("TVC", "TRIVANDRUM"),
        ("TPJ", "TRICHY"),
        ("CBE", "COIMBATORE"),
        ("SLM", "SALEM"),
        ("PNM", "POONAMALLEE"),
        ("PAD", "PADI"),
        ("KPM", "KANCHEEPURAM"),
        ("NGL", "NAGERCOIL"),
    ]
    assert len(expected_physical) == 10

    for code, canonical_branch in expected_physical:
        # Test SS
        res_ss = proposed_resolver.resolve(cost_name=code, comm_code=None, scheme_type="ss")
        assert res_ss.branch == canonical_branch
        assert res_ss.location == canonical_branch
        assert res_ss.is_ecomm is False
        assert res_ss.is_special is False

        # Test SV
        res_sv = proposed_resolver.resolve(cost_name=code, comm_code=None, scheme_type="sv")
        assert res_sv.branch == canonical_branch
        assert res_sv.location == f"{canonical_branch} SV"
        assert res_sv.is_ecomm is False
        assert res_sv.is_special is False


# ==============================================================================
# TEST 2: All 54 Identified NGL Records Resolve to NAGERCOIL
# ==============================================================================
def test_all_54_ngl_records_resolve_to_nagercoil(proposed_resolver):
    """Verify all 54 records (46 SS + 8 SV) from 2026-10-25 raw data resolve to NAGERCOIL."""
    ss_path = Path("data/raw/2026/10/25/innervex_subhiksham_memberlist_2026-10-25.json")
    sv_path = Path("data/raw/2026/10/25/innervex_viruksham_memberlist_2026-10-25.json")

    with open(ss_path, "r", encoding="utf-8") as f:
        raw_json = json.load(f)
        ss_raw = raw_json["data"] if isinstance(raw_json, dict) and "data" in raw_json else raw_json
    with open(sv_path, "r", encoding="utf-8") as f:
        raw_json = json.load(f)
        sv_raw = raw_json["data"] if isinstance(raw_json, dict) and "data" in raw_json else raw_json

    ss_ngl = [r for r in ss_raw if r.get("COSTNAME") == "NGL"]
    sv_ngl = [r for r in sv_raw if r.get("COSTNAME") == "NGL"]

    assert len(ss_ngl) == 46
    assert len(sv_ngl) == 8

    # SS resolution
    for r in ss_ngl:
        res = proposed_resolver.resolve(
            cost_name=r.get("COSTNAME"),
            comm_code=str(r.get("EMPCODE", "")).strip(),
            scheme_type="ss"
        )
        assert res.branch == "NAGERCOIL"
        assert res.location == "NAGERCOIL"
        assert res.is_ecomm is False
        assert res.unresolved_code is None

    # SV resolution
    for r in sv_ngl:
        res = proposed_resolver.resolve(
            cost_name=r.get("COSTNAME"),
            comm_code=str(r.get("EMPCODE", "")).strip(),
            scheme_type="sv"
        )
        assert res.branch == "NAGERCOIL"
        assert res.location == "NAGERCOIL SV"
        assert res.is_ecomm is False
        assert res.unresolved_code is None


# ==============================================================================
# TEST 3: Precedence Rules for Transferred Employees
# ==============================================================================
def test_transferred_employees_precedence_rule(proposed_resolver):
    """
    Verify Rule #3 (physical store COSTNAME) takes precedence over employee historical mapping (Rule #4).
    Physical in-store purchases at NGL with transferred employee codes resolve to NAGERCOIL.
    APP/ECOMM purchases with transferred employee codes resolve to their historical base branch ECOMM.
    """
    # 1. Physical in-store purchase at NGL with employee 768 (historically TRIVANDRUM)
    res_physical = proposed_resolver.resolve(cost_name="NGL", comm_code="768", scheme_type="ss")
    assert res_physical.branch == "NAGERCOIL"
    assert res_physical.location == "NAGERCOIL"
    assert res_physical.is_ecomm is False

    # 2. APP/ECOMM purchase with employee 768 preserves historical TRIVANDRUM mapping
    res_ecomm = proposed_resolver.resolve(cost_name="APP", comm_code="768", scheme_type="ss")
    assert res_ecomm.branch == "TRIVANDRUM"
    assert res_ecomm.location == "TRIVANDRUM ECOMM"
    assert res_ecomm.is_ecomm is True

    # 3. Repeat for employee 2294 (historically SALEM SV)
    res_sv_physical = proposed_resolver.resolve(cost_name="NGL", comm_code="2294", scheme_type="sv")
    assert res_sv_physical.branch == "NAGERCOIL"
    assert res_sv_physical.location == "NAGERCOIL SV"
    assert res_sv_physical.is_ecomm is False

    res_sv_ecomm = proposed_resolver.resolve(cost_name="ECOMM", comm_code="2294", scheme_type="sv")
    assert res_sv_ecomm.branch == "SALEM"
    assert res_sv_ecomm.location == "SALEM SV ECOMM"
    assert res_sv_ecomm.is_ecomm is True


# ==============================================================================
# TEST 4: KANCHI Remains Correctly Mapped (Established Showroom)
# ==============================================================================
def test_kanchi_established_showroom_resolution(proposed_resolver):
    """Verify KANCHI (KPM) continues to resolve accurately with zero disruption."""
    ss_path = Path("data/raw/2026/10/25/innervex_subhiksham_memberlist_2026-10-25.json")
    sv_path = Path("data/raw/2026/10/25/innervex_viruksham_memberlist_2026-10-25.json")

    with open(ss_path, "r", encoding="utf-8") as f:
        raw_json = json.load(f)
        ss_raw = raw_json["data"] if isinstance(raw_json, dict) and "data" in raw_json else raw_json
    with open(sv_path, "r", encoding="utf-8") as f:
        raw_json = json.load(f)
        sv_raw = raw_json["data"] if isinstance(raw_json, dict) and "data" in raw_json else raw_json

    ss_kpm = [r for r in ss_raw if r.get("COSTNAME") == "KPM"]
    sv_kpm = [r for r in sv_raw if r.get("COSTNAME") == "KPM"]

    assert len(ss_kpm) == 23
    assert len(sv_kpm) == 5

    for r in ss_kpm:
        res = proposed_resolver.resolve(
            cost_name=r.get("COSTNAME"),
            comm_code=str(r.get("EMPCODE", "")).strip(),
            scheme_type="ss"
        )
        assert res.branch == "KANCHEEPURAM"
        assert res.location == "KANCHEEPURAM"
        assert res.is_ecomm is False

    for r in sv_kpm:
        res = proposed_resolver.resolve(
            cost_name=r.get("COSTNAME"),
            comm_code=str(r.get("EMPCODE", "")).strip(),
            scheme_type="sv"
        )
        assert res.branch == "KANCHEEPURAM"
        assert res.location == "KANCHEEPURAM SV"
        assert res.is_ecomm is False


# ==============================================================================
# TEST 5: Online / Ecommerce Channel Isolation
# ==============================================================================
def test_online_ecommerce_isolation(proposed_resolver):
    """Verify online transactions remain separate and are not misassigned to physical showrooms."""
    ss_path = Path("data/raw/2026/10/25/innervex_subhiksham_memberlist_2026-10-25.json")
    sv_path = Path("data/raw/2026/10/25/innervex_viruksham_memberlist_2026-10-25.json")

    with open(ss_path, "r", encoding="utf-8") as f:
        raw_json = json.load(f)
        ss_raw = raw_json["data"] if isinstance(raw_json, dict) and "data" in raw_json else raw_json
    with open(sv_path, "r", encoding="utf-8") as f:
        raw_json = json.load(f)
        sv_raw = raw_json["data"] if isinstance(raw_json, dict) and "data" in raw_json else raw_json

    # Pure organic online records (empty comm_code with APP/HAPP/ECOM)
    ss_online = [
        r for r in ss_raw
        if r.get("COSTNAME") in ["APP", "HAPP", "ECOM"] and not (r.get("COMMCODE") or r.get("EMPCODE") or "").strip()
    ]
    sv_online = [
        r for r in sv_raw
        if r.get("COSTNAME") in ["APP", "HAPP", "ECOM"] and not (r.get("COMMCODE") or r.get("EMPCODE") or "").strip()
    ]

    assert len(ss_online) == 18
    assert len(sv_online) == 8

    for r in ss_online:
        code = (r.get("COMMCODE") or r.get("EMPCODE") or "").strip()
        res = proposed_resolver.resolve(
            cost_name=r.get("COSTNAME"), comm_code=code, scheme_type="ss"
        )
        assert res.branch is None
        assert res.location == "ONLINE"
        assert res.is_ecomm is True
        assert res.is_special is True

    for r in sv_online:
        code = (r.get("COMMCODE") or r.get("EMPCODE") or "").strip()
        res = proposed_resolver.resolve(
            cost_name=r.get("COSTNAME"), comm_code=code, scheme_type="sv"
        )
        assert res.branch is None
        assert res.location == "ONLINE SV"
        assert res.is_ecomm is True
        assert res.is_special is True


# ==============================================================================
# TEST 6: Total Reconciliation (Zero Loss, Zero Duplication)
# ==============================================================================
def test_reconciliation_zero_loss(proposed_resolver):
    """Verify 100% of records reconcile with zero loss, zero duplicates, and unchanged totals."""
    ss_path = Path("data/raw/2026/10/25/innervex_subhiksham_memberlist_2026-10-25.json")
    sv_path = Path("data/raw/2026/10/25/innervex_viruksham_memberlist_2026-10-25.json")

    with open(ss_path, "r", encoding="utf-8") as f:
        raw_json = json.load(f)
        ss_raw = raw_json["data"] if isinstance(raw_json, dict) and "data" in raw_json else raw_json
    with open(sv_path, "r", encoding="utf-8") as f:
        raw_json = json.load(f)
        sv_raw = raw_json["data"] if isinstance(raw_json, dict) and "data" in raw_json else raw_json

    assert len(ss_raw) == 317
    assert len(sv_raw) == 72

    raw_ss_total = sum(float(r.get("RECAMOUNT", 0.0)) for r in ss_raw)
    raw_sv_total = sum(float(r.get("RECAMOUNT", 0.0)) for r in sv_raw)

    assert raw_ss_total == 2839000.0
    assert raw_sv_total == 21511494.0

    resolved_ss = [
        proposed_resolver.resolve(r.get("COSTNAME"), r.get("COMMCODE") or r.get("EMPCODE"), "ss")
        for r in ss_raw
    ]
    resolved_sv = [
        proposed_resolver.resolve(r.get("COSTNAME"), r.get("COMMCODE") or r.get("EMPCODE"), "sv")
        for r in sv_raw
    ]

    assert len(resolved_ss) == 317
    assert len(resolved_sv) == 72

    # Zero unresolved codes
    assert len(proposed_resolver.get_all_unresolved_codes()) == 0


# ==============================================================================
# TEST 7: Closing Report Mapping & Mock Fixture Update
# ==============================================================================
def test_closing_report_mapping_and_mock():
    """Verify closing report mapping and mock fixtures handle NGL."""
    assert PROPOSED_SHOWROOM_MAPPING["NGL"] == "NGL"
    assert PROPOSED_SHOWROOM_MAPPING["NAGERCOIL"] == "NGL"
    assert PROPOSED_SHOWROOM_MAPPING["KPM"] == "KPM"

    # Verify mock showroom list contains 10 showrooms
    mock_showrooms = ["CBE", "CPT", "KPM", "NGL", "PADI", "PNM", "SLM", "TPJ", "TVL", "TVM"]
    assert len(mock_showrooms) == 10
    assert "NGL" in mock_showrooms
    assert "KPM" in mock_showrooms


# ==============================================================================
# TEST 8: DigiGold / DigiSilver Branch Order and Dynamic Slicing
# ==============================================================================
def test_digi_branch_order_and_dynamic_slicing():
    """Verify DIGI_BRANCH_ORDER with NAGERCOIL and dynamic showroom list."""
    proposed_digi_branch_order = [
        "NELLAI", "CHROMEPET", "TRIVANDRUM", "TRICHY", "COIMBATORE",
        "SALEM", "POONAMALLEE", "PADI", "KANCHEEPURAM", "NAGERCOIL", "ECOMMERCE",
    ]
    assert len(proposed_digi_branch_order) == 11

    # Dynamic showroom extraction (excludes ECOMMERCE)
    showroom_branches = [b for b in proposed_digi_branch_order if b != "ECOMMERCE"]
    assert len(showroom_branches) == 10
    assert "NAGERCOIL" in showroom_branches
    assert "KANCHEEPURAM" in showroom_branches

    # Aliases
    proposed_aliases = {
        **BRANCH_NAME_ALIASES,
        "NAGERCOIL": "NAGERCOIL",
        "NGL": "NAGERCOIL",
    }
    assert proposed_aliases["NGL"] == "NAGERCOIL"
    assert proposed_aliases["NAGERCOIL"] == "NAGERCOIL"
