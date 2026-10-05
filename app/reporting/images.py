"""
Report image generation interface (Phase 10 Placeholder).

Generates presentation-ready image cards (New Enrollment, Backlog, Rejoining)
for executive delivery.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional
from app.reporting.consolidation import ConsolidatedReportData


class ReportImageRenderer(ABC):
    """Abstract interface for rendering reports to image files."""

    @abstractmethod
    def render_new_enrollment_card(
        self, data: ConsolidatedReportData, output_path: Path
    ) -> Path:
        """Render store-wise New Enrollment metrics image card."""
        pass

    @abstractmethod
    def render_backlog_card(
        self, data: ConsolidatedReportData, output_path: Path
    ) -> Path:
        """Render Target vs Achieved Backlog metrics image card."""
        pass


class PlaceholderImageRenderer(ReportImageRenderer):
    """Placeholder image renderer for Phase 0."""

    def render_new_enrollment_card(
        self, data: ConsolidatedReportData, output_path: Path
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Create a text placeholder file representing the image
        output_path.write_text(f"IMAGE PLACEHOLDER: New Enrollment as of {data.report_date}\nTotal: {data.total_enrollment_count} enrollments", encoding="utf-8")
        return output_path

    def render_backlog_card(
        self, data: ConsolidatedReportData, output_path: Path
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(f"IMAGE PLACEHOLDER: Backlog as of {data.report_date}", encoding="utf-8")
        return output_path
