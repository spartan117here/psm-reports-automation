"""Aggregation and business metric calculation engine."""

from collections import defaultdict
from typing import Dict, List, Optional
from app.core.config import TargetsConfig
from app.core.models import AggregationMetric, CleanedRecord


class LocationAggregator:
    """Aggregates cleaned and enriched records by location and calculates target metrics."""

    def __init__(self, targets_config: TargetsConfig):
        self.targets = targets_config

    def aggregate_by_location(
        self,
        records: List[CleanedRecord],
        target_branch_map: Optional[Dict[str, float]] = None,
        day_of_month: int = 1,
    ) -> Dict[str, AggregationMetric]:
        """
        Aggregate records by LOCATION_2, calculating counts, sums, and target metrics.

        Args:
            records: Enriched CleanedRecord items.
            target_branch_map: Dictionary of branch code/name to target amount (e.g. Q2 targets).
            day_of_month: Current day of month for daily average calculation.

        Returns:
            Dictionary of location_label -> AggregationMetric.
        """
        counts: Dict[str, int] = defaultdict(int)
        amounts: Dict[str, float] = defaultdict(float)

        for r in records:
            loc = r.LOCATION_2 or r.LOCATION or "UNKNOWN"
            counts[loc] += 1
            amounts[loc] += float(r.RECAMOUNT or 0.0)

        results: Dict[str, AggregationMetric] = {}
        for loc, count in counts.items():
            tot_amt = amounts[loc]
            target_val = target_branch_map.get(loc) if target_branch_map else None

            yet_to_achieve = None
            achieved_pct = None
            if target_val and target_val > 0:
                yet_to_achieve = tot_amt - target_val
                achieved_pct = (tot_amt / target_val) * 100.0

            daily_avg = (tot_amt / day_of_month) if day_of_month > 0 else tot_amt

            results[loc] = AggregationMetric(
                location=loc,
                new_enrollment_count=count,
                total_amount=tot_amt,
                target_amount=target_val,
                yet_to_achieve=yet_to_achieve,
                achieved_percentage=achieved_pct,
                daily_average=daily_avg,
            )

        return results
