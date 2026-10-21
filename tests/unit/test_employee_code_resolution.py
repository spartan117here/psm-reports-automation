"""Unit tests for employee-code-to-branch resolution and online fallback rules."""

import pytest
from app.core.config import AppConfigBundle
from app.core.exceptions import UnresolvedEmployeeCodeError
from app.processing.location import LocationResolver


def test_numeric_employee_code_resolution_ss(config_bundle: AppConfigBundle):
    """Verify numeric employee codes are resolved to '<BRANCH> ECOMM' via verified registry for SS."""
    resolver = LocationResolver(config_bundle.mappings)

    # HAPP commission agent 1676 -> TIRUNELVELI ECOMM
    attr_1676 = resolver.resolve(cost_name="HAPP", comm_code="1676", scheme_type="ss")
    assert attr_1676.branch == "TIRUNELVELI"
    assert attr_1676.location == "TIRUNELVELI ECOMM"
    assert attr_1676.location_2 == "TIRUNELVELI ECOMM"
    assert attr_1676.is_ecomm is True
    assert attr_1676.unresolved_code is None

    # Employee 415 -> CHROMEPET ECOMM
    attr_415 = resolver.resolve(cost_name="APP", comm_code="415", scheme_type="ss")
    assert attr_415.branch == "CHROMEPET"
    assert attr_415.location == "CHROMEPET ECOMM"
    assert attr_415.location_2 == "CHROMEPET ECOMM"
    assert attr_415.is_ecomm is True

    # Employee 2426 -> COIMBATORE ECOMM
    attr_2426 = resolver.resolve(cost_name="APP", comm_code="2426", scheme_type="ss")
    assert attr_2426.branch == "COIMBATORE"
    assert attr_2426.location == "COIMBATORE ECOMM"

    # Employee 3188 -> CORPORATE OFFICE
    attr_3188 = resolver.resolve(cost_name="APP", comm_code="3188", scheme_type="ss")
    assert attr_3188.branch is None
    assert attr_3188.location == "CORPORATE OFFICE"
    assert attr_3188.location_2 == "CORPORATE OFFICE"
    assert attr_3188.is_special is True

    # Employee 1 -> CORPORATE OFFICE
    attr_1 = resolver.resolve(cost_name="APP", comm_code="1", scheme_type="ss")
    assert attr_1.branch is None
    assert attr_1.location == "CORPORATE OFFICE"
    assert attr_1.is_special is True


def test_numeric_employee_code_resolution_sv(config_bundle: AppConfigBundle):
    """Verify numeric employee codes are resolved to '<BRANCH> SV ECOMM' via verified registry for SV."""
    resolver = LocationResolver(config_bundle.mappings)

    # HAPP commission agent 1676 in SV
    attr_1676_sv = resolver.resolve(cost_name="HAPP", comm_code="1676", scheme_type="sv")
    assert attr_1676_sv.branch == "TIRUNELVELI"
    assert attr_1676_sv.location == "TIRUNELVELI SV ECOMM"
    assert attr_1676_sv.location_2 == "TIRUNELVELI SV ECOMM"
    assert attr_1676_sv.is_ecomm is True

    # Employee 415 in SV
    attr_415_sv = resolver.resolve(cost_name="APP", comm_code="415", scheme_type="sv")
    assert attr_415_sv.branch == "CHROMEPET"
    assert attr_415_sv.location == "CHROMEPET SV ECOMM"

    # Employee 1 in SV
    attr_1_sv = resolver.resolve(cost_name="APP", comm_code="1", scheme_type="sv")
    assert attr_1_sv.branch is None
    assert attr_1_sv.location == "CORPORATE OFFICE SV"
    assert attr_1_sv.is_special is True


def test_physical_showroom_precedence_over_employee_registry(config_bundle: AppConfigBundle):
    """Verify physical store COSTNAME takes precedence so staff transfers do not misattribute physical sales."""
    resolver = LocationResolver(config_bundle.mappings)

    # Employee 1984 in KPM physical store
    attr = resolver.resolve(cost_name="KPM", comm_code="1984", scheme_type="ss")
    assert attr.branch == "KANCHEEPURAM"
    assert attr.location == "KANCHEEPURAM"
    assert attr.location_2 == "KANCHEEPURAM"
    assert attr.is_ecomm is False

    # In-store purchase with no code
    attr_no_code = resolver.resolve(cost_name="TVL", comm_code="", scheme_type="ss")
    assert attr_no_code.branch == "TIRUNELVELI"
    assert attr_no_code.location == "TIRUNELVELI"
    assert attr_no_code.is_ecomm is False


def test_unassisted_online_app_fallback(config_bundle: AppConfigBundle):
    """Verify organic unassisted app enrollments (blank COMMCODE) fall back to ONLINE / ONLINE SV."""
    resolver = LocationResolver(config_bundle.mappings)

    # Subhiksham APP with blank code
    attr_app_ss = resolver.resolve(cost_name="APP", comm_code="", scheme_type="ss")
    assert attr_app_ss.branch is None
    assert attr_app_ss.location == "ONLINE"
    assert attr_app_ss.location_2 == "ONLINE"
    assert attr_app_ss.is_ecomm is True
    assert attr_app_ss.is_special is True

    # Subhiksham HAPP with blank code
    attr_happ_ss = resolver.resolve(cost_name="HAPP", comm_code="", scheme_type="ss")
    assert attr_happ_ss.location == "ONLINE"

    # Viruksham APP with blank code
    attr_app_sv = resolver.resolve(cost_name="APP", comm_code="", scheme_type="sv")
    assert attr_app_sv.branch is None
    assert attr_app_sv.location == "ONLINE SV"
    assert attr_app_sv.location_2 == "ONLINE SV"
    assert attr_app_sv.is_ecomm is True
    assert attr_app_sv.is_special is True

    # Viruksham ECOM with blank code
    attr_ecom_sv = resolver.resolve(cost_name="ECOM", comm_code="", scheme_type="sv")
    assert attr_ecom_sv.location == "ONLINE SV"


def test_unknown_employee_code_strict_mode(config_bundle: AppConfigBundle):
    """Verify strict mode raises UnresolvedEmployeeCodeError when employee code is unknown."""
    resolver = LocationResolver(config_bundle.mappings, strict_mode=True)

    with pytest.raises(UnresolvedEmployeeCodeError) as exc_info:
        resolver.resolve(cost_name="APP", comm_code="999999", scheme_type="ss")
    assert "999999" in str(exc_info.value)
    assert exc_info.value.details.get("code") == "999999"


def test_unknown_employee_code_non_strict_mode(config_bundle: AppConfigBundle):
    """Verify non-strict mode sets 'UNRESOLVED (code)' and records code, never silently guessing."""
    resolver = LocationResolver(config_bundle.mappings, strict_mode=False)

    attr = resolver.resolve(cost_name="APP", comm_code="888888", scheme_type="ss")
    assert attr.branch is None
    assert attr.location == "UNRESOLVED (888888)"
    assert attr.location_2 == "UNRESOLVED (888888)"
    assert attr.unresolved_code == "888888"
    assert "888888" in resolver.get_all_unresolved_codes()


def test_physical_showroom_never_falls_back_to_online(config_bundle: AppConfigBundle):
    """Verify physical store records without employee code are NOT classified as ONLINE."""
    resolver = LocationResolver(config_bundle.mappings)

    for store_code, expected_branch in [
        ("TVL", "TIRUNELVELI"),
        ("CPT", "CHROMEPET"),
        ("KPM", "KANCHEEPURAM"),
        ("PAD", "PADI"),
        ("SLM", "SALEM"),
        ("PNM", "POONAMALLEE"),
        ("CBE", "COIMBATORE"),
        ("TRI", "TRICHY"),
        ("TVC", "TRIVANDRUM"),
    ]:
        res_ss = resolver.resolve(cost_name=store_code, comm_code="", scheme_type="ss")
        assert res_ss.location == expected_branch
        assert res_ss.is_special is False
        assert res_ss.is_ecomm is False

        res_sv = resolver.resolve(cost_name=store_code, comm_code="", scheme_type="sv")
        assert res_sv.location == f"{expected_branch} SV"
