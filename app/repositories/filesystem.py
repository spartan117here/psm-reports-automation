"""Filesystem-backed repository implementation for raw data, processed data, and run metadata."""

from datetime import date, datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.models import CleanedRecord, RawReportPayload, RunMetadata, RunStatus
from app.repositories.base import ReportRepository, RunRepository

logger = logging.getLogger("pothys_reporting")


class FileSystemReportRepository(ReportRepository):
    """
    Stores raw and processed reports in partitioned date folders:
    data/raw/YYYY/MM/DD/<report_id>_<timestamp>.json
    data/processed/YYYY/MM/DD/<report_id>_<timestamp>.json
    """

    def __init__(self, data_root: Path):
        self.raw_root = data_root / "raw"
        self.processed_root = data_root / "processed"

    def _get_partition_dir(self, base_dir: Path, target_date: date) -> Path:
        year_str = target_date.strftime("%Y")
        month_str = target_date.strftime("%m")
        day_str = target_date.strftime("%d")
        partition_dir = base_dir / year_str / month_str / day_str
        partition_dir.mkdir(parents=True, exist_ok=True)
        return partition_dir

    def save_raw_report(self, payload: RawReportPayload) -> Path:
        folder = self._get_partition_dir(self.raw_root, payload.report_date)
        timestamp_str = datetime.now().strftime("%H%M%S")
        filename = f"{payload.report_id}_{timestamp_str}.json"
        target_path = folder / filename

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(payload.model_dump(mode="json"), f, indent=2)

        logger.info(f"Saved raw report ({payload.row_count} rows) to: {target_path}")
        return target_path

    def get_raw_report(self, report_id: str, report_date: date) -> Optional[RawReportPayload]:
        folder = self._get_partition_dir(self.raw_root, report_date)
        matches = sorted(folder.glob(f"{report_id}_*.json"), reverse=True)
        if not matches:
            return None

        # Return latest payload for the day
        with open(matches[0], "r", encoding="utf-8") as f:
            data = json.load(f)
            return RawReportPayload(**data)

    def save_cleaned_records(
        self, report_id: str, report_date: date, records: List[CleanedRecord]
    ) -> Path:
        folder = self._get_partition_dir(self.processed_root, report_date)
        timestamp_str = datetime.now().strftime("%H%M%S")
        filename = f"{report_id}_cleaned_{timestamp_str}.json"
        target_path = folder / filename

        dumpable = [r.model_dump(mode="json") for r in records]
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(dumpable, f, indent=2)

        logger.info(f"Saved {len(records)} cleaned records to: {target_path}")
        return target_path

    def get_cleaned_records(
        self, report_id: str, report_date: date
    ) -> Optional[List[CleanedRecord]]:
        folder = self._get_partition_dir(self.processed_root, report_date)
        matches = sorted(folder.glob(f"{report_id}_cleaned_*.json"), reverse=True)
        if not matches:
            return None

        with open(matches[0], "r", encoding="utf-8") as f:
            data = json.load(f)
            return [CleanedRecord(**item) for item in data]


class FileSystemRunRepository(RunRepository):
    """Stores run metadata as JSON in data/runs/<run_id>.json."""

    def __init__(self, data_root: Path):
        self.runs_root = data_root / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def save_run(self, metadata: RunMetadata) -> None:
        target_path = self.runs_root / f"{metadata.run_id}.json"
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(metadata.model_dump(mode="json"), f, indent=2)
        logger.info(f"Saved run metadata to: {target_path}")

    def get_run(self, run_id: str) -> Optional[RunMetadata]:
        target_path = self.runs_root / f"{run_id}.json"
        if not target_path.is_file():
            return None
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return RunMetadata(**data)

    def is_already_processed(self, report_date: date, report_id: str) -> bool:
        """Scan previous runs to see if a SUCCESS run exists for this report date & report_id."""
        for run_file in self.runs_root.glob("*.json"):
            try:
                with open(run_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if (
                        data.get("report_date") == report_date.isoformat()
                        and data.get("status") == RunStatus.SUCCESS.value
                        and report_id in data.get("source", "")
                    ):
                        return True
            except Exception:
                continue
        return False
