"""
Consolidated Daily Report Model and Builder Interface.

Generates the programmatic data structure corresponding to the
monthly 'Consolidate Report - <Mon>' Google Sheet tab.
"""

from datetime import date
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.models import AggregationMetric


class ConsolidatedReportData(BaseModel):
    """Aggregate business data structure across all schemes and stores."""
    report_date: date
    subhiksham_metrics: Dict[str, AggregationMetric] = Field(default_factory=dict)
    viruksham_metrics: Dict[str, AggregationMetric] = Field(default_factory=dict)
    digi_gold_metrics: Dict[str, AggregationMetric] = Field(default_factory=dict)
    digi_silver_metrics: Dict[str, AggregationMetric] = Field(default_factory=dict)

    # Grand Totals
    total_enrollment_count: int = 0
    total_enrollment_amount: float = 0.0
    total_target_amount: float = 0.0
    total_achieved_percentage: float = 0.0


class ConsolidationEngine:
    """Computes consolidated store-wise and scheme-wise tables."""

    @staticmethod
    def build_summary(
        report_date: date,
        ss_metrics: Dict[str, AggregationMetric],
        sv_metrics: Dict[str, AggregationMetric],
    ) -> ConsolidatedReportData:
        """Merge SS and SV metrics and calculate grand totals."""
        tot_count = sum(m.new_enrollment_count for m in ss_metrics.values()) + sum(
            m.new_enrollment_count for m in sv_metrics.values()
        )
        tot_amt = sum(m.total_amount for m in ss_metrics.values()) + sum(
            m.total_amount for m in sv_metrics.values()
        )

        return ConsolidatedReportData(
            report_date=report_date,
            subhiksham_metrics=ss_metrics,
            viruksham_metrics=sv_metrics,
            total_enrollment_count=tot_count,
            total_enrollment_amount=tot_amt,
        )
