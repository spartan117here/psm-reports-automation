"""Unit tests asserting 100% resolution for all 110 previously unresolved records."""

import json
from pathlib import Path
import pytest
from app.core.config import AppConfigBundle
from app.processing.location import LocationResolver

DATA_FILE = Path(__file__).parent.parent.parent / "data" / "previously_unresolved_110_records.json"

with open(DATA_FILE, "r", encoding="utf-8") as f:
    RECORDS_110 = json.load(f)


def test_previously_unresolved_record_count():
    """Verify that exactly 110 records are tested."""
    assert len(RECORDS_110) == 110


@pytest.mark.parametrize(
    "rec",
    RECORDS_110,
    ids=[f"{r['date']}_{r['scheme']}_{r['msno']}_{r['commcode'] or 'blank'}" for r in RECORDS_110],
)
def test_each_previously_unresolved_record_resolves_correctly(config_bundle: AppConfigBundle, rec: dict):
    """
    Verify each of the 110 previously unresolved records resolves cleanly
    to its verified manual location without UNRESOLVED flags.
    """
    resolver = LocationResolver(config_bundle.mappings, strict_mode=True)

    attr = resolver.resolve(
        cost_name=rec["costname"],
        comm_code=rec["commcode"],
        scheme_type=rec["scheme"],
    )

    # Must resolve with no unresolved exceptions
    assert attr.unresolved_code is None
    assert "UNRESOLVED" not in attr.location
    assert "UNRESOLVED" not in attr.location_2

    # Must match the verified manual finalization in Google Sheets
    assert attr.location_2 == rec["resolved_location_2"]
    if rec.get("resolved_branch"):
        assert attr.branch == rec["resolved_branch"]
