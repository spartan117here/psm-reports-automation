"""Unit tests for report column cleaners and transformation."""

import pandas as pd
import pytest

from app.core.config import AppConfigBundle
from app.core.exceptions import TransformationError
from app.core.models import RawReportPayload
from app.processing.cleaners import SchemeReportCleaner


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


def test_clean_dataframe_column_order_and_filtering(config_bundle: AppConfigBundle):
    """Verify DataFrame cleaner extracts exactly 11 columns in specified order."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    # Create dummy DataFrame with all 21 columns
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

    # Must contain exactly 11 columns in correct order
    assert list(cleaned_df.columns) == report_cfg.output_columns
    assert cleaned_df["RECAMOUNT"].iloc[0] == 2500.0


def test_clean_dataframe_missing_column_raises_error(config_bundle: AppConfigBundle):
    """Verify that if a required column is missing, TransformationError is raised."""
    report_cfg = config_bundle.get_report("subhiksham")
    cleaner = SchemeReportCleaner(report_cfg)

    # Missing RECAMOUNT column
    df = pd.DataFrame({
        "COSTNAME": ["CHROMEPET"],
        "CLIENTID": ["C100"],
        "GROUPCODE": ["G100"],
        "MSNO": ["M100"],
    })

    with pytest.raises(TransformationError) as exc_info:
        cleaner.clean_dataframe(df)
    assert "Missing required columns" in str(exc_info.value)
