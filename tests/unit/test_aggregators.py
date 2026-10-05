"""Unit tests for business metrics and target backlog aggregations."""

from app.core.config import AppConfigBundle
from app.core.models import CleanedRecord
from app.processing.aggregators import LocationAggregator


def test_location_aggregation_and_targets(config_bundle: AppConfigBundle):
    """Verify counts, sums, and target backlog metrics are accurately calculated."""
    aggregator = LocationAggregator(config_bundle.targets)

    records = [
        CleanedRecord(MSNO="M1", LOCATION_2="CHROMEPET", RECAMOUNT=20000000.0),
        CleanedRecord(MSNO="M2", LOCATION_2="CHROMEPET", RECAMOUNT=30000000.0),
        CleanedRecord(MSNO="M3", LOCATION_2="TIRUNELVELI", RECAMOUNT=10000000.0),
    ]

    # Target branch map (e.g. Q2 target for CHROMEPET = 216,000,000)
    targets_map = {"CHROMEPET": 216000000.0, "TIRUNELVELI": 120000000.0}

    # Run for day 10 of month
    results = aggregator.aggregate_by_location(
        records=records, target_branch_map=targets_map, day_of_month=10
    )

    cpt_metric = results["CHROMEPET"]
    assert cpt_metric.new_enrollment_count == 2
    assert cpt_metric.total_amount == 50000000.0
    assert cpt_metric.target_amount == 216000000.0

    # Yet To Achieve: Total - Target (50,000,000 - 216,000,000 = -166,000,000)
    assert cpt_metric.yet_to_achieve == -166000000.0

    # Achieved %: (50,000,000 / 216,000,000) * 100
    expected_pct = (50000000.0 / 216000000.0) * 100.0
    assert round(cpt_metric.achieved_percentage, 4) == round(expected_pct, 4)

    # Daily Average: 50,000,000 / 10 = 5,000,000
    assert cpt_metric.daily_average == 5000000.0
