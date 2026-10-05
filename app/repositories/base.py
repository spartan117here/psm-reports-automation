"""Abstract repository interfaces for run metadata and report storage."""

from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import List, Optional

from app.core.models import CleanedRecord, RawReportPayload, RunMetadata


class RunRepository(ABC):
    """Abstract repository for persisting and querying execution run records."""

    @abstractmethod
    def save_run(self, metadata: RunMetadata) -> None:
        """Persist or update execution run metadata."""
        pass

    @abstractmethod
    def get_run(self, run_id: str) -> Optional[RunMetadata]:
        """Fetch metadata for a specific run ID."""
        pass

    @abstractmethod
    def is_already_processed(self, report_date: date, report_id: str) -> bool:
        """Idempotency check: returns True if this report date was already successfully processed."""
        pass


class ReportRepository(ABC):
    """Abstract repository for storing and loading raw and cleaned report datasets."""

    @abstractmethod
    def save_raw_report(self, payload: RawReportPayload) -> Path:
        """Persist raw Innervex response payload into immutable storage."""
        pass

    @abstractmethod
    def get_raw_report(self, report_id: str, report_date: date) -> Optional[RawReportPayload]:
        """Retrieve stored raw report payload for a given date."""
        pass

    @abstractmethod
    def save_cleaned_records(
        self, report_id: str, report_date: date, records: List[CleanedRecord]
    ) -> Path:
        """Persist cleaned records to processed storage."""
        pass

    @abstractmethod
    def get_cleaned_records(
        self, report_id: str, report_date: date
    ) -> Optional[List[CleanedRecord]]:
        """Retrieve stored cleaned records for a given date."""
        pass
