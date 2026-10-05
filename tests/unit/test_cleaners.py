"""Unit tests for Phase 1C: Subhiksham report cleaning, normalization, validation, and auditing."""

from copy import deepcopy
from datetime import date
import pandas as pd
import pytest

from app.core.config import AppConfigBundle
from app.core.exceptions import TransformationError
from app.core.models import CleanedRecord, RawReportPayload
from app.processing.cleaners import SchemeReportCleaner, normalize_amount, normalize_date_str, normalize_identifier


def test_clean_records_11_columns(config_bundle: AppConfigBundle, sample_raw_payload: RawReportPayload):
    """Verify raw 21-column records are correctly reduced to 11 clean columns."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    cleaned = cleaner.clean_records(sample_raw_payload.data)

    assert len(cleaned) == len(sample_raw_payload.data)
    first = cleaned[0]
    assert first.MSNO == "MSNO-90001"
    assert first.COSTNAME == "CHROMEPET"
    assert first.RECAMOUNT == 5000.0
    assert first.COMMCODE == "CPT001"
    assert first.SCHEME == "NEW SWARNA SUBHIKSHAM"


def test_exact_11_column_output_and_ordering(config_bundle: AppConfigBundle, sample_raw_payload: RawReportPayload):
    """Verify that the cleaned output contains exactly the 11 columns in exact business order."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    result = cleaner.clean_and_validate(sample_raw_payload.data)
    assert result.cleaned_count == len(sample_raw_payload.data)

    expected_cols = [
        "COSTNAME", "CLIENTID", "GROUPCODE", "MSNO", "NAME",
        "SCHDATE", "RECAMOUNT", "MOBILENO", "SCHEME", "COMMNAME", "COMMCODE"
    ]

    for record in result.records:
        rec_dict = record.to_11_dict()
        assert list(rec_dict.keys()) == expected_cols


def test_clean_dataframe_column_order_and_filtering(config_bundle: AppConfigBundle):
    """Verify DataFrame cleaner extracts exactly 11 columns in specified order."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    data = {
        "SNO": [1],
        "COSTNAME": ["CHROMEPET"],
        "CLIENTID": ["C100"],
        "GROUPCODE": ["G100"],
        "MSNO": ["M100"],
        "NAME": ["RAMESH"],
        "DATEOFBIRTH": ["1990-01-01"],
        "SCHDATE": ["2026-10-04"],
        "RECAMOUNT": ["2,500.00"],
        "MOBILENO": ["9876543210"],
        "SCHEME": ["NEW SWARNA SUBHIKSHAM"],
        "COMMNAME": ["AGENT"],
        "COMMCODE": ["CPT01"],
        "ADDRESS": ["ADDR"],
        "PHONE": ["123"],
        "EMPNAME": ["EMP"],
        "EMPCODE": ["EMP01"],
        "IS TELECALLER": ["NO"],
        "IS HM": ["NO"],
        "PROMOCODE": ["PROMO"],
        "BRANCHNAME": ["CHROMEPET"],
    }
    df = pd.DataFrame(data)

    cleaned_df = cleaner.clean_dataframe(df, is_csv_with_preamble=False)

    assert list(cleaned_df.columns) == report_cfg.output_columns
    assert cleaned_df["RECAMOUNT"].iloc[0] == 2500.0


def test_missing_column_handling(config_bundle: AppConfigBundle):
    """Verify that if a required column is missing from the raw dataset, TransformationError is raised."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    # Missing RECAMOUNT and SCHDATE
    incomplete_raw = [{
        "COSTNAME": "CHROMEPET",
        "CLIENTID": "CLI-100",
        "GROUPCODE": "GRP-01",
        "MSNO": "M-100",
        "NAME": "TEST",
    }]

    with pytest.raises(TransformationError) as exc_info:
        cleaner.clean_and_validate(incomplete_raw)
    assert "Missing required columns" in str(exc_info.value)


def test_numeric_recamount_conversion(config_bundle: AppConfigBundle):
    """Verify numeric conversion of RECAMOUNT for integers, floats, and strings with commas."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    raw_data = [
        {"COSTNAME": "CPT", "CLIENTID": "C1", "GROUPCODE": "G1", "MSNO": "M1", "NAME": "N1",
         "SCHDATE": "04-10-2026", "RECAMOUNT": "15,000.50", "MOBILENO": "11", "SCHEME": "SS", "COMMNAME": "", "COMMCODE": ""},
        {"COSTNAME": "CPT", "CLIENTID": "C2", "GROUPCODE": "G1", "MSNO": "M2", "NAME": "N2",
         "SCHDATE": "2026-10-04", "RECAMOUNT": 25000, "MOBILENO": "22", "SCHEME": "SS", "COMMNAME": "", "COMMCODE": ""},
    ]

    result = cleaner.clean_and_validate(raw_data)
    assert result.records[0].RECAMOUNT == 15000.50
    assert result.records[1].RECAMOUNT == 25000.0
    assert result.total_recamount == 40000.50
    assert result.invalid_amount_count == 0


def test_invalid_recamount_not_silently_zeroed(config_bundle: AppConfigBundle):
    """Verify invalid non-numeric RECAMOUNT is flagged and recorded as an error, not silently zeroed."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    raw_data = [
        {"COSTNAME": "CPT", "CLIENTID": "C1", "GROUPCODE": "G1", "MSNO": "M1", "NAME": "N1",
         "SCHDATE": "2026-10-04", "RECAMOUNT": "INVALID_AMT", "MOBILENO": "11", "SCHEME": "SS", "COMMNAME": "", "COMMCODE": ""},
    ]

    result = cleaner.clean_and_validate(raw_data)
    assert result.cleaned_count == 1
    assert result.invalid_amount_count == 1
    assert result.invalid_count == 1
    assert result.records[0].is_valid is False
    assert any("Invalid numeric RECAMOUNT" in err for err in result.records[0].validation_errors)


def test_identifier_string_preservation(config_bundle: AppConfigBundle):
    """Verify that identifiers with leading zeros are preserved as strings and not coerced to numbers."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    raw_data = [
        {
            "COSTNAME": "CPT",
            "CLIENTID": "001234",
            "GROUPCODE": "001",
            "MSNO": "0009988",
            "NAME": "TEST",
            "SCHDATE": "2026-10-04",
            "RECAMOUNT": 1000,
            "MOBILENO": "0987654321",
            "SCHEME": "NEW SWARNA SUBHIKSHAM",
            "COMMNAME": "STAFF",
            "COMMCODE": "0045",
        }
    ]

    result = cleaner.clean_and_validate(raw_data)
    rec = result.records[0]

    assert rec.CLIENTID == "001234"
    assert rec.GROUPCODE == "001"
    assert rec.MSNO == "0009988"
    assert rec.MOBILENO == "0987654321"
    assert rec.COMMCODE == "0045"


def test_duplicate_msno_detection(config_bundle: AppConfigBundle):
    """Verify duplicate MSNO detection without dropping records."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    raw_data = [
        {"COSTNAME": "CPT", "CLIENTID": "C1", "GROUPCODE": "G1", "MSNO": "DUP-MSNO", "NAME": "N1",
         "SCHDATE": "2026-10-04", "RECAMOUNT": 1000, "MOBILENO": "11", "SCHEME": "SS", "COMMNAME": "", "COMMCODE": ""},
        {"COSTNAME": "CPT", "CLIENTID": "C2", "GROUPCODE": "G1", "MSNO": "DUP-MSNO", "NAME": "N2",
         "SCHDATE": "2026-10-04", "RECAMOUNT": 2000, "MOBILENO": "22", "SCHEME": "SS", "COMMNAME": "", "COMMCODE": ""},
        {"COSTNAME": "CPT", "CLIENTID": "C3", "GROUPCODE": "G1", "MSNO": "UNIQUE-MSNO", "NAME": "N3",
         "SCHDATE": "2026-10-04", "RECAMOUNT": 3000, "MOBILENO": "33", "SCHEME": "SS", "COMMNAME": "", "COMMCODE": ""},
    ]

    result = cleaner.clean_and_validate(raw_data)
    assert result.raw_count == 3
    assert result.cleaned_count == 3
    assert result.duplicate_msno_count == 1
    assert result.duplicate_msno_affected_count == 2


def test_duplicate_clientid_detection(config_bundle: AppConfigBundle):
    """Verify duplicate CLIENTID detection without dropping records."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    raw_data = [
        {"COSTNAME": "CPT", "CLIENTID": "DUP-CLI", "GROUPCODE": "G1", "MSNO": "M1", "NAME": "N1",
         "SCHDATE": "2026-10-04", "RECAMOUNT": 1000, "MOBILENO": "11", "SCHEME": "SS", "COMMNAME": "", "COMMCODE": ""},
        {"COSTNAME": "CPT", "CLIENTID": "DUP-CLI", "GROUPCODE": "G1", "MSNO": "M2", "NAME": "N2",
         "SCHDATE": "2026-10-04", "RECAMOUNT": 2000, "MOBILENO": "22", "SCHEME": "SS", "COMMNAME": "", "COMMCODE": ""},
    ]

    result = cleaner.clean_and_validate(raw_data)
    assert result.duplicate_clientid_count == 1
    assert result.duplicate_clientid_affected_count == 2


def test_null_blank_required_fields(config_bundle: AppConfigBundle):
    """Verify that records with blank required fields are flagged and counted."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    raw_data = [
        {"COSTNAME": "CPT", "CLIENTID": "", "GROUPCODE": "G1", "MSNO": "M1", "NAME": "N1",
         "SCHDATE": "2026-10-04", "RECAMOUNT": 1000, "MOBILENO": "11", "SCHEME": "SS", "COMMNAME": "", "COMMCODE": ""},
    ]

    result = cleaner.clean_and_validate(raw_data)
    assert result.blank_required_field_count == 1
    assert result.invalid_count == 1
    assert result.records[0].is_valid is False
    assert any("Missing required field: CLIENTID" in err for err in result.records[0].validation_errors)


def test_raw_data_preservation(config_bundle: AppConfigBundle):
    """Verify that cleaning operations do not alter or mutate the raw source data in-place."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    raw_data = [
        {"COSTNAME": "  CHROMEPET  ", "CLIENTID": "C100", "GROUPCODE": "GRP1", "MSNO": "M100", "NAME": "RAMESH",
         "SCHDATE": "04-10-2026", "RECAMOUNT": "1,000", "MOBILENO": "999", "SCHEME": "SS", "COMMNAME": "A", "COMMCODE": "01"}
    ]
    raw_clone = deepcopy(raw_data)

    cleaner.clean_and_validate(raw_data)

    # Input dictionary should remain exactly untouched
    assert raw_data == raw_clone


def test_row_count_reconciliation_and_costname_summary(config_bundle: AppConfigBundle):
    """Verify that row count matches raw count, total amounts reconcile, and COSTNAME summary is accurate."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    raw_data = [
        {"COSTNAME": "APP", "CLIENTID": "C1", "GROUPCODE": "G1", "MSNO": "M1", "NAME": "N1",
         "SCHDATE": "2026-10-04", "RECAMOUNT": 5000, "MOBILENO": "11", "SCHEME": "SS", "COMMNAME": "", "COMMCODE": ""},
        {"COSTNAME": "APP", "CLIENTID": "C2", "GROUPCODE": "G1", "MSNO": "M2", "NAME": "N2",
         "SCHDATE": "2026-10-04", "RECAMOUNT": 10000, "MOBILENO": "22", "SCHEME": "SS", "COMMNAME": "", "COMMCODE": ""},
        {"COSTNAME": "CBE", "CLIENTID": "C3", "GROUPCODE": "G1", "MSNO": "M3", "NAME": "N3",
         "SCHDATE": "2026-10-04", "RECAMOUNT": 15000, "MOBILENO": "33", "SCHEME": "SS", "COMMNAME": "", "COMMCODE": ""},
    ]

    result = cleaner.clean_and_validate(raw_data)
    assert result.raw_count == 3
    assert result.cleaned_count == 3
    assert result.total_recamount == 30000.0
    assert result.costname_summary["APP"] == {"count": 2, "total_amount": 15000.0}
    assert result.costname_summary["CBE"] == {"count": 1, "total_amount": 15000.0}
