"""Unit tests for branch and ECOMM location attribution."""

import pytest
from app.core.config import AppConfigBundle
from app.core.exceptions import UnresolvedEmployeeCodeError
from app.processing.location import LocationResolver


def test_standard_branch_resolution(config_bundle: AppConfigBundle):
    """Verify standard employee code prefix to branch mapping."""
    resolver = LocationResolver(config_bundle.mappings)

    # Subhiksham mappings
    attr_cpt = resolver.resolve(cost_name="", comm_code="CPT014", scheme_type="ss")
    assert attr_cpt.branch == "CHROMEPET"
    assert attr_cpt.location == "CHROMEPET"
    assert attr_cpt.location_2 == "CHROMEPET"

    attr_tvl = resolver.resolve(cost_name="", comm_code="TVL099", scheme_type="ss")
    assert attr_tvl.branch == "TIRUNELVELI"
    assert attr_tvl.location == "TIRUNELVELI"

    attr_tpj = resolver.resolve(cost_name="", comm_code="TPJ005", scheme_type="ss")
    assert attr_tpj.branch == "TRICHY"

    attr_tvc = resolver.resolve(cost_name="", comm_code="TVC001", scheme_type="ss")
    assert attr_tvc.branch == "TRIVANDRUM"


def test_ecomm_branch_resolution(config_bundle: AppConfigBundle):
    """Verify ECOMM employee codes produce '<BRANCH> ECOMM' labels."""
    resolver = LocationResolver(config_bundle.mappings)

    attr = resolver.resolve(cost_name="CPT ECOMM", comm_code="CPT_ECOMM_01", scheme_type="ss")
    assert attr.branch == "CHROMEPET"
    assert attr.is_ecomm is True
    assert attr.location == "CHROMEPET ECOMM"
    assert attr.location_2 == "CHROMEPET ECOMM"


def test_viruksham_location_suffixes(config_bundle: AppConfigBundle):
    """Verify Viruksham scheme generates '<BRANCH> SV' and '<BRANCH> SV ECOMM' labels."""
    resolver = LocationResolver(config_bundle.mappings)

    # Regular SV
    attr_sv = resolver.resolve(cost_name="", comm_code="CPT010", scheme_type="sv")
    assert attr_sv.location == "CHROMEPET SV"

    # ECOMM SV
    attr_sv_ecomm = resolver.resolve(cost_name="ECOMM", comm_code="CPT010", scheme_type="sv")
    assert attr_sv_ecomm.location == "CHROMEPET SV ECOMM"


def test_special_locations_online_and_corporate(config_bundle: AppConfigBundle):
    """Verify ONLINE and CORPORATE OFFICE special location cases."""
    resolver = LocationResolver(config_bundle.mappings)

    attr_online = resolver.resolve(cost_name="ONLINE STORE", comm_code="ONLINE_APP", scheme_type="ss")
    assert attr_online.location == "ONLINE"
    assert attr_online.is_special is True

    attr_corp = resolver.resolve(cost_name="HEAD OFFICE", comm_code="CORPORATE OFFICE", scheme_type="ss")
    assert attr_corp.location == "CORPORATE OFFICE"
    assert attr_corp.is_special is True


def test_unresolved_employee_code_logged_and_flagged(config_bundle: AppConfigBundle):
    """Verify unknown codes are NOT silently assigned and are recorded in unresolved set."""
    resolver = LocationResolver(config_bundle.mappings, strict_mode=False)

    attr = resolver.resolve(cost_name="UNKNOWN_STORE", comm_code="XYZ_999", scheme_type="ss")
    assert attr.branch is None
    assert "UNRESOLVED" in attr.location
    assert "XYZ_999" in resolver.get_all_unresolved_codes()


def test_unresolved_employee_code_strict_mode(config_bundle: AppConfigBundle):
    """Verify strict mode raises UnresolvedEmployeeCodeError on unknown code."""
    resolver = LocationResolver(config_bundle.mappings, strict_mode=True)

    with pytest.raises(UnresolvedEmployeeCodeError):
        resolver.resolve(cost_name="UNKNOWN", comm_code="INVALID_CODE", scheme_type="ss")
