"""Integration tests for five-day reproducibility (16-20 October 2026) across 1,243 raw records."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest

from app.core.config import AppConfigBundle
from app.processing.cleaners import SchemeReportCleaner
from app.processing.location import LocationResolver
from app.processing.transformers import RecordTransformer
from app.repositories.filesystem import FileSystemReportRepository
from app.reporting.closing import ClosingReportCleaner

DATES_5_DAYS = [
    date(2026, 10, 16),
    date(2026, 10, 17),
    date(2026, 10, 18),
    date(2026, 10, 19),
    date(2026, 10, 20),
]

EXPECTED_METRICS = {
    date(2026, 10, 16): {
        "ss_count": 123,
        "ss_amount": Decimal("671500"),
        "sv_count": 40,
        "sv_amount": Decimal("12829898"),
        "closing_count": 146,
    },
    date(2026, 10, 17): {
        "ss_count": 226,
        "ss_amount": Decimal("1868000"),
        "sv_count": 49,
        "sv_amount": Decimal("18187211"),
        "closing_count": 228,
    },
    date(2026, 10, 18): {
        "ss_count": 237,
        "ss_amount": Decimal("1557500"),
        "sv_count": 46,
        "sv_amount": Decimal("15816307"),
        "closing_count": 286,
    },
    date(2026, 10, 19): {
        "ss_count": 187,
        "ss_amount": Decimal("1494000"),
        "sv_count": 31,
        "sv_amount": Decimal("10560258"),
        "closing_count": 258,
    },
    date(2026, 10, 20): {
        "ss_count": 261,
        "ss_amount": Decimal("1894500"),
        "sv_count": 43,
        "sv_amount": Decimal("13142440"),
        "closing_count": 103,
    },
}


@pytest.mark.parametrize("target_date", DATES_5_DAYS, ids=[d.strftime("%Y-%m-%d") for d in DATES_5_DAYS])
def test_daily_reproducibility_and_zero_unresolved(config_bundle: AppConfigBundle, target_date: date):
    """
    Verify for each of the 5 audit dates:
    - Raw files exist and clean with 100% expected record counts.
    - Financial amounts match exactly to the rupee.
    - Strict LocationResolver resolves every record with zero UnresolvedEmployeeCodeError.
    - Zero records contain 'UNRESOLVED' in LOCATION or LOCATION_2.
    """
    data_dir = Path(__file__).parent.parent.parent / config_bundle.app.storage.data_dir
    repo = FileSystemReportRepository(data_dir)

    cleaner_ss = SchemeReportCleaner(config_bundle.get_report("subhiksham"))
    cleaner_sv = SchemeReportCleaner(config_bundle.get_report("viruksham"))
    resolver = LocationResolver(config_bundle.mappings, strict_mode=True)
    transformer = RecordTransformer(resolver)

    expected = EXPECTED_METRICS[target_date]

    # 1. Subhiksham pipeline
    raw_ss = repo.get_raw_report("subhiksham", target_date)
    assert raw_ss is not None, f"Missing raw Subhiksham report for {target_date}"
    clean_ss = cleaner_ss.clean_and_validate(raw_ss.data, target_date)
    assert len(clean_ss.records) == expected["ss_count"]
    assert clean_ss.total_recamount == expected["ss_amount"]

    enriched_ss = transformer.enrich_records(clean_ss.records, scheme_type="ss")
    assert len(enriched_ss) == expected["ss_count"]
    for r in enriched_ss:
        assert "UNRESOLVED" not in (r.LOCATION or "")
        assert "UNRESOLVED" not in (r.LOCATION_2 or "")

    # 2. Viruksham pipeline
    raw_sv = repo.get_raw_report("viruksham", target_date)
    assert raw_sv is not None, f"Missing raw Viruksham report for {target_date}"
    clean_sv = cleaner_sv.clean_and_validate(raw_sv.data, target_date)
    assert len(clean_sv.records) == expected["sv_count"]
    assert clean_sv.total_recamount == expected["sv_amount"]

    enriched_sv = transformer.enrich_records(clean_sv.records, scheme_type="sv")
    assert len(enriched_sv) == expected["sv_count"]
    for r in enriched_sv:
        assert "UNRESOLVED" not in (r.LOCATION or "")
        assert "UNRESOLVED" not in (r.LOCATION_2 or "")

    # 3. No unresolved codes recorded in resolver
    assert resolver.get_all_unresolved_codes() == []

    # 4. Closing report pipeline
    raw_dir = data_dir / "raw" / target_date.strftime("%Y/%m/%d")
    csv_files = sorted(raw_dir.glob("SCHEME CLOSING REPORT_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    assert len(csv_files) > 0, f"Missing closing report CSV for {target_date}"
    close_cleaner = ClosingReportCleaner()
    raw_csv = csv_files[0].read_text(encoding="utf-8")
    parsed_closing = close_cleaner.parse_raw_csv(raw_csv)
    cleaned_closing = close_cleaner.clean_records(parsed_closing, target_date)
    assert cleaned_closing.cleaned_row_count == expected["closing_count"]
    assert len(cleaned_closing.rejected_rows) == 0


def test_five_day_cumulative_totals(config_bundle: AppConfigBundle):
    """Verify 5-day cumulative totals match 1,034 SS and 209 SV records with 0 unresolved."""
    data_dir = Path(__file__).parent.parent.parent / config_bundle.app.storage.data_dir
    repo = FileSystemReportRepository(data_dir)

    cleaner_ss = SchemeReportCleaner(config_bundle.get_report("subhiksham"))
    cleaner_sv = SchemeReportCleaner(config_bundle.get_report("viruksham"))
    resolver = LocationResolver(config_bundle.mappings, strict_mode=True)
    transformer = RecordTransformer(resolver)

    total_ss_count = 0
    total_ss_amount = 0.0
    total_sv_count = 0
    total_sv_amount = 0.0

    for target_date in DATES_5_DAYS:
        raw_ss = repo.get_raw_report("subhiksham", target_date)
        clean_ss = cleaner_ss.clean_and_validate(raw_ss.data, target_date)
        total_ss_count += len(clean_ss.records)
        total_ss_amount += float(clean_ss.total_recamount or 0)
        transformer.enrich_records(clean_ss.records, scheme_type="ss")

        raw_sv = repo.get_raw_report("viruksham", target_date)
        clean_sv = cleaner_sv.clean_and_validate(raw_sv.data, target_date)
        total_sv_count += len(clean_sv.records)
        total_sv_amount += float(clean_sv.total_recamount or 0)
        transformer.enrich_records(clean_sv.records, scheme_type="sv")

    assert total_ss_count == 1034
    assert abs(total_ss_amount - 7485500.0) < 0.01
    assert total_sv_count == 209
    assert abs(total_sv_amount - 70536114.0) < 0.01
    assert resolver.get_all_unresolved_codes() == []
