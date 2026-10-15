"""Unit tests for the Google Sheets Report Screenshot Engine."""

from datetime import date
from pathlib import Path
import pytest
from PIL import Image

from app.cli import build_parser, cmd_screenshot, seed_mock_screenshot_data
from app.core.config import AppConfigBundle, load_config
from app.core.exceptions import ScreenshotError, TableValidationError
from app.google_sheets.client import MockGoogleSheetsService
from app.reporting.images import crop_white_margins, pdf_to_cropped_png
from app.reporting.screenshots import (
    GoogleSheetsScreenshotEngine,
    detect_and_validate_closing_range,
    detect_and_validate_enrollment_range,
    extract_date_from_text,
)


@pytest.fixture
def seeded_mock_service() -> MockGoogleSheetsService:
    """Return a MockGoogleSheetsService seeded with both report workbooks."""
    service = MockGoogleSheetsService()
    seed_mock_screenshot_data(service)
    return service


# ==============================================================================
# 1. Image Rendering & White Margin Cropping
# ==============================================================================
def test_crop_white_margins():
    """Verify crop_white_margins detects content and trims surrounding white space."""
    # Create 200x200 white image with a 50x50 black rectangle in the center
    img = Image.new("RGB", (200, 200), (255, 255, 255))
    for x in range(75, 125):
        for y in range(75, 125):
            img.putpixel((x, y), (0, 0, 0))

    cropped = crop_white_margins(img, padding=5)
    # Bounding box is (75, 75, 125, 125); with padding 5, expect (70, 70, 130, 130) -> 60x60
    assert cropped.width == 60
    assert cropped.height == 60


def test_crop_white_margins_empty_image():
    """Verify all-white image returns original image without error."""
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    cropped = crop_white_margins(img, padding=10)
    assert cropped.size == img.size


def test_pdf_to_cropped_png(tmp_path: Path):
    """Verify converting PDF bytes to high-resolution cropped PNG."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((100, 100), "TEST REPORT TABLE")
    pdf_bytes = doc.tobytes()

    out_png = tmp_path / "test_out.png"
    result_path, dimensions = pdf_to_cropped_png(
        pdf_bytes, output_path=out_png, dpi=150, padding=10
    )

    assert result_path.exists()
    assert dimensions[0] > 0
    assert dimensions[1] > 0
    assert result_path == out_png


def test_pdf_to_cropped_png_empty_doc_raises_error(tmp_path: Path):
    """Verify empty PDF bytes raise ValueError."""
    out_png = tmp_path / "empty.png"
    with pytest.raises(ValueError, match="empty PDF"):
        pdf_to_cropped_png(b"", out_png)


# ==============================================================================
# 2. Date Extraction from Report Titles
# ==============================================================================
def test_extract_date_from_text():
    """Verify extract_date_from_text correctly parses Indian DD/MM/YYYY dates."""
    assert extract_date_from_text("New Enrollment as on 13/10/2026") == date(2026, 10, 13)
    assert extract_date_from_text("New Enrollment as on 06/10/26") == date(2026, 10, 6)
    assert extract_date_from_text("SCHEME REJOINING REPORT AS ON 14/10/2026") == date(2026, 10, 14)
    assert extract_date_from_text("SCHEME REJOINING REPORT AS ON ") is None
    assert extract_date_from_text("") is None


# ==============================================================================
# 3. Dynamic Boundary Detection & Validation: Enrollment Report
# ==============================================================================
def test_detect_and_validate_enrollment_range_success(
    seeded_mock_service: MockGoogleSheetsService,
):
    """Verify dynamic discovery and validation of Enrollment Report table range E3:L30."""
    validation = detect_and_validate_enrollment_range(
        seeded_mock_service,
        spreadsheet_id="mock_enrollment_id",
        worksheet_name="Consolidate Report - Oct",
    )

    assert validation.is_valid is True
    assert validation.target == "enrollment"
    assert validation.detected_range == "E3:L30"
    assert validation.report_date == date(2026, 10, 14)
    assert "New Enrollment as on" in validation.report_title
    assert validation.total_row_found is True
    assert validation.gid == 928019529


def test_detect_and_validate_enrollment_range_missing_title(
    seeded_mock_service: MockGoogleSheetsService,
):
    """Verify validation fails when Enrollment title row is missing."""
    # Corrupt data by wiping the title row
    seeded_mock_service.update_range(
        "mock_enrollment_id", "'Consolidate Report - Oct'!E1:M35", [["Invalid", "Data"]]
    )

    with pytest.raises(TableValidationError, match="Could not locate 'New Enrollment as on'"):
        detect_and_validate_enrollment_range(
            seeded_mock_service,
            spreadsheet_id="mock_enrollment_id",
            worksheet_name="Consolidate Report - Oct",
        )


def test_detect_and_validate_enrollment_range_missing_sheet(
    seeded_mock_service: MockGoogleSheetsService,
):
    """Verify validation fails clearly when target sheet does not exist."""
    with pytest.raises(TableValidationError, match="Worksheet 'NonExistentTab' not found"):
        detect_and_validate_enrollment_range(
            seeded_mock_service,
            spreadsheet_id="mock_enrollment_id",
            worksheet_name="NonExistentTab",
        )


# ==============================================================================
# 4. Dynamic Boundary Detection & Validation: Closing Report
# ==============================================================================
def test_detect_and_validate_closing_range_success(
    seeded_mock_service: MockGoogleSheetsService,
):
    """Verify dynamic discovery and validation of complete Closing Report table range L3:Q26."""
    validation = detect_and_validate_closing_range(
        seeded_mock_service,
        spreadsheet_id="mock_closing_id",
        worksheet_name="October - 2026",
    )

    assert validation.is_valid is True
    assert validation.target == "closing"
    assert validation.detected_range == "L3:Q26"
    assert validation.report_date == date(2026, 10, 14)
    assert "SCHEME REJOINING REPORT AS ON" in validation.report_title
    assert validation.total_row_found is True
    assert validation.gid == 2105033369
    assert validation.row_count == 24


def test_detect_and_validate_closing_range_insufficient_showrooms(
    seeded_mock_service: MockGoogleSheetsService,
):
    """Verify validation fails when too few showroom rows are detected."""
    # Seed with only 1 showroom row instead of >= 5
    short_rows = [
        ["", "", "SCHEME REJOINING REPORT AS ON ", "", "", "", "", "14/10/2026"],
        ["", "", "SHOWROOM", "CLOSED", "REJOIN SS", "REJOIN SV", "TOTAL REJOIN", "REJOIN %"],
        ["", "", "TVL", "207", "38", "5", "43", "20.77%"],
        ["", "", "TOTAL", "207", "38", "5", "43", "20.77%"],
    ]
    seeded_mock_service.update_range("mock_closing_id", "'October - 2026'!J1:R30", short_rows)

    with pytest.raises(TableValidationError, match="Insufficient showroom rows found in Section 1"):
        detect_and_validate_closing_range(
            seeded_mock_service,
            spreadsheet_id="mock_closing_id",
            worksheet_name="October - 2026",
        )


def test_detect_and_validate_closing_range_missing_closed_weight_section(
    seeded_mock_service: MockGoogleSheetsService,
):
    """Verify validation fails when Closed Weight / Count section (rows 15-26) is missing."""
    # Seed with only Section 1 (rows 3-14)
    section1_only = [
        ["", "", "SCHEME REJOINING REPORT AS ON ", "", "", "", "", "14/10/2026"],
        ["", "", "SHOWROOM", "CLOSED", "REJOIN SS", "REJOIN SV", "TOTAL REJOIN", "REJOIN %"],
        ["", "", "TVL", "207", "38", "5", "43", "20.77%"],
        ["", "", "CPT", "346", "62", "5", "67", "19.36%"],
        ["", "", "TVM", "194", "38", "4", "42", "21.65%"],
        ["", "", "TPJ", "285", "53", "4", "57", "20.00%"],
        ["", "", "CBE", "243", "48", "2", "50", "20.58%"],
        ["", "", "TOTAL", "1275", "238", "20", "259", "20.31%"],
    ]
    seeded_mock_service.update_range("mock_closing_id", "'October - 2026'!J1:R30", section1_only)

    with pytest.raises(TableValidationError, match="CLOSED WEIGHT"):
        detect_and_validate_closing_range(
            seeded_mock_service,
            spreadsheet_id="mock_closing_id",
            worksheet_name="October - 2026",
        )


def test_detect_and_validate_closing_range_missing_bottom_total(
    seeded_mock_service: MockGoogleSheetsService,
):
    """Verify validation fails when the bottom TOTAL row (row 26) is missing."""
    rows_no_bottom_total = [
        ["", "", "SCHEME REJOINING REPORT AS ON ", "", "", "", "", "14/10/2026"],
        ["", "", "SHOWROOM", "CLOSED", "REJOIN SS", "REJOIN SV", "TOTAL REJOIN", "REJOIN %"],
        ["", "", "TVL", "207", "38", "5", "43", "20.77%"],
        ["", "", "CPT", "346", "62", "5", "67", "19.36%"],
        ["", "", "TVM", "194", "38", "4", "42", "21.65%"],
        ["", "", "TPJ", "285", "53", "4", "57", "20.00%"],
        ["", "", "CBE", "243", "48", "2", "50", "20.58%"],
        ["", "", "TOTAL", "1275", "238", "20", "259", "20.31%"],
        ["", "", "CLOSED WEIGHT", "", "", "CLOSED COUNT", "", ""],
        ["", "", "SHOWROOM", "COUNT", "WEIGHT", "SHOWROOM", "SS", "SV"],
        ["", "", "TVL", "207", "1559.92", "TVL", "187", "20"],
        ["", "", "CPT", "346", "3345.20", "CPT", "269", "77"],
        ["", "", "TVM", "194", "1121.06", "TVM", "170", "24"],
        ["", "", "TPJ", "285", "1692.79", "TPJ", "241", "44"],
        ["", "", "CBE", "243", "964.60", "CBE", "216", "27"],
    ]
    seeded_mock_service.update_range("mock_closing_id", "'October - 2026'!J1:R30", rows_no_bottom_total)

    with pytest.raises(TableValidationError, match="bottom 'TOTAL' row"):
        detect_and_validate_closing_range(
            seeded_mock_service,
            spreadsheet_id="mock_closing_id",
            worksheet_name="October - 2026",
        )


# ==============================================================================
# 5. Screenshot Engine Execution in Mock Mode
# ==============================================================================
def test_screenshot_engine_capture_all(
    seeded_mock_service: MockGoogleSheetsService, tmp_path: Path
):
    """Verify engine captures both Enrollment and Closing screenshots in mock mode."""
    engine = GoogleSheetsScreenshotEngine(
        sheets_service=seeded_mock_service,
        output_dir=tmp_path,
        dpi=150,
    )

    results = engine.capture_all()
    assert len(results) == 2

    res_enrollment, res_closing = results[0], results[1]

    # Check Enrollment Result
    assert res_enrollment.target == "enrollment"
    assert res_enrollment.range_used == "E3:L30"
    assert res_enrollment.output_path.exists()
    assert res_enrollment.dimensions[0] > 0
    assert res_enrollment.dimensions[1] > 0

    # Check Closing Result
    assert res_closing.target == "closing"
    assert res_closing.range_used == "L3:Q26"
    assert res_closing.output_path.exists()
    assert res_closing.dimensions[0] > 0
    assert res_closing.dimensions[1] > 0


# ==============================================================================
# 6. CLI Command Integration Tests
# ==============================================================================
def test_cli_screenshot_mock_all(tmp_path: Path):
    """Verify python -m app screenshot --target all --mock runs successfully."""
    parser = build_parser()
    args = parser.parse_args(
        ["screenshot", "--target", "all", "--mock", "--output-dir", str(tmp_path)]
    )

    exit_code = cmd_screenshot(args)
    assert exit_code == 0

    # Verify 2 PNG files were created
    pngs = list(tmp_path.glob("*.png"))
    assert len(pngs) == 2
    filenames = [p.name for p in pngs]
    assert any("enrollment_report" in fn for fn in filenames)
    assert any("closing_rejoining_report" in fn for fn in filenames)


def test_cli_screenshot_mock_enrollment_only(tmp_path: Path):
    """Verify python -m app screenshot --target enrollment --mock creates single enrollment PNG."""
    parser = build_parser()
    args = parser.parse_args(
        ["screenshot", "--target", "enrollment", "--mock", "--output-dir", str(tmp_path)]
    )

    exit_code = cmd_screenshot(args)
    assert exit_code == 0

    pngs = list(tmp_path.glob("*.png"))
    assert len(pngs) == 1
    assert "enrollment_report" in pngs[0].name


def test_cli_screenshot_mock_closing_only(tmp_path: Path):
    """Verify python -m app screenshot --target closing --mock creates single closing PNG."""
    parser = build_parser()
    args = parser.parse_args(
        ["screenshot", "--target", "closing", "--mock", "--output-dir", str(tmp_path)]
    )

    exit_code = cmd_screenshot(args)
    assert exit_code == 0

    pngs = list(tmp_path.glob("*.png"))
    assert len(pngs) == 1
    assert "closing_rejoining_report" in pngs[0].name
