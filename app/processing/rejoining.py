"""
Closed Member & Rejoining Report processing module.

IMPORTANT ARCHITECTURAL NOTE:
The matching logic used to attribute closed members to REJOIN SS and REJOIN SV
is currently unconfirmed and marked as a pending business-rule dependency.
This module encapsulates the known mathematical formulas and provides
the abstract interface for the matching engine to be finalized in Phase 7.
"""

from typing import List, NamedTuple, Optional
from pydantic import BaseModel, Field


class RejoiningRow(BaseModel):
    """Calculated rejoining metrics for a showroom."""
    showroom: str
    closed_count: int = Field(ge=0, description="Total closed accounts")
    rejoin_ss: int = Field(ge=0, description="Rejoined under Subhiksham")
    rejoin_sv: int = Field(ge=0, description="Rejoined under Viruksham")
    total_rejoin: int = Field(default=0, description="Calculated: rejoin_ss + rejoin_sv")
    rejoin_percentage: float = Field(default=0.0, description="Calculated: total_rejoin / closed_count * 100")


class RejoiningCalculator:
    """Calculates Total Rejoin and Rejoin % for closed member reports."""

    @staticmethod
    def calculate_row(showroom: str, closed: int, rejoin_ss: int, rejoin_sv: int) -> RejoiningRow:
        """
        Compute rejoining metrics for a given showroom.

        Formulas:
            TOTAL REJOIN = REJOIN SS + REJOIN SV
            REJOIN % = TOTAL REJOIN / CLOSED
        """
        total = rejoin_ss + rejoin_sv
        pct = (total / closed * 100.0) if closed > 0 else 0.0

        return RejoiningRow(
            showroom=showroom,
            closed_count=closed,
            rejoin_ss=rejoin_ss,
            rejoin_sv=rejoin_sv,
            total_rejoin=total,
            rejoin_percentage=round(pct, 2),
        )

    @staticmethod
    def compute_summary(rows: List[RejoiningRow]) -> RejoiningRow:
        """Compute grand total summary row across all showrooms."""
        tot_closed = sum(r.closed_count for r in rows)
        tot_ss = sum(r.rejoin_ss for r in rows)
        tot_sv = sum(r.rejoin_sv for r in rows)
        tot_rejoin = tot_ss + tot_sv
        rejoin_pct = (tot_rejoin / tot_closed * 100.0) if tot_closed > 0 else 0.0

        return RejoiningRow(
            showroom="TOTAL",
            closed_count=tot_closed,
            rejoin_ss=tot_ss,
            rejoin_sv=tot_sv,
            total_rejoin=tot_rejoin,
            rejoin_percentage=round(rejoin_pct, 2),
        )
