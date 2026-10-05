"""Pytest configuration and shared test fixtures."""

from datetime import date
import json
from pathlib import Path
import pytest

from app.core.config import AppConfigBundle, load_config
from app.core.models import RawReportPayload
from app.google_sheets.client import MockGoogleSheetsService


@pytest.fixture
def project_root() -> Path:
    """Return the absolute path to the project root directory."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def config_bundle(project_root: Path) -> AppConfigBundle:
    """Return the loaded application configuration bundle."""
    return load_config(project_root)


@pytest.fixture
def raw_scheme_json(project_root: Path) -> dict:
    """Load mock Innervex raw response JSON fixture."""
    fixture_path = project_root / "tests" / "fixtures" / "raw_scheme_response.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sample_raw_payload(raw_scheme_json: dict) -> RawReportPayload:
    """Return a typed RawReportPayload instance for testing."""
    records = raw_scheme_json["data"]
    headers = list(records[0].keys()) if records else []
    return RawReportPayload(
        report_id="subhiksham",
        report_date=date(2026, 10, 4),
        row_count=len(records),
        data=records,
        raw_headers=headers,
    )


@pytest.fixture
def mock_google_service() -> MockGoogleSheetsService:
    """Return an in-memory mock Google Sheets service."""
    return MockGoogleSheetsService()
