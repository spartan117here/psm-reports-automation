"""Unit tests for configuration loading and validation."""

from pathlib import Path
import pytest

from app.core.config import AppConfigBundle, load_config
from app.core.exceptions import ConfigurationError


def test_load_config_success(config_bundle: AppConfigBundle):
    """Verify that configuration loads and validates all sections."""
    assert config_bundle.app.name == "pothys-reporting-automation"
    assert config_bundle.app.timezone == "Asia/Kolkata"

    # Innervex config
    assert config_bundle.innervex.base_url == "http://192.168.5.213:4499"
    assert config_bundle.innervex.timeout_seconds == 60

    # Reports config
    assert "subhiksham" in config_bundle.reports.reports
    assert "viruksham" in config_bundle.reports.reports
    subhiksham = config_bundle.get_report("subhiksham")
    assert subhiksham.raw_expected_column_count == 21
    assert len(subhiksham.output_columns) == 11

    # Mappings config
    assert config_bundle.mappings.employee_to_branch["CPT"] == "CHROMEPET"
    assert config_bundle.mappings.employee_to_branch["TVL"] == "TIRUNELVELI"

    # Targets config
    assert config_bundle.targets.q2["CPT"] == 216000000.0
    assert config_bundle.targets.h1_multiplier == 2.0


def test_get_unknown_report_raises_error(config_bundle: AppConfigBundle):
    """Verify querying an unconfigured report raises ConfigurationError."""
    with pytest.raises(ConfigurationError):
        config_bundle.get_report("non_existent_report")
