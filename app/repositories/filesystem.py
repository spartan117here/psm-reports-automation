"""Filesystem-backed repository implementation for raw data, processed data, and run metadata."""

import csv
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
        date_str = payload.report_date.isoformat()
        base_name = f"innervex_{payload.report_id}_memberlist_{date_str}.json"
        target_path = folder / base_name

        if target_path.exists():
            timestamp_str = datetime.now().strftime("%H%M%S")
            target_path = folder / f"innervex_{payload.report_id}_memberlist_{date_str}_{timestamp_str}.json"

        # Preserve raw response structure exactly as received from Innervex
        content = payload.raw_response if payload.raw_response is not None else payload.model_dump(mode="json")
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2)

        logger.info(f"Saved raw report ({payload.row_count} rows) to: {target_path}")
        return target_path

    def get_raw_report(self, report_id: str, report_date: date) -> Optional[RawReportPayload]:
        folder = self._get_partition_dir(self.raw_root, report_date)
        matches = sorted(
            list(folder.glob(f"innervex_{report_id}_memberlist_*.json"))
            + list(folder.glob(f"{report_id}_*.json")),
            reverse=True,
        )
        if not matches:
            return None

        # Return latest payload for the day
        with open(matches[0], "r", encoding="utf-8") as f:
            data = json.load(f)
            if "data" in data and "report_id" not in data:
                return RawReportPayload(
                    report_id=report_id,
                    report_date=report_date,
                    row_count=len(data.get("data", [])),
                    data=data.get("data", []),
                    raw_headers=list(data["data"][0].keys()) if data.get("data") else [],
                    raw_response=data,
                )
            return RawReportPayload(**data)

    def save_cleaned_records(
        self, report_id: str, report_date: date, records: List[CleanedRecord]
    ) -> Path:
        folder = self._get_partition_dir(self.processed_root, report_date)
        date_str = report_date.isoformat()

        # 1. Save canonical 11-column CSV
        csv_filename = f"{report_id}_memberlist_{date_str}.csv"
        csv_path = folder / csv_filename
        columns = [
            "COSTNAME", "CLIENTID", "GROUPCODE", "MSNO", "NAME",
            "SCHDATE", "RECAMOUNT", "MOBILENO", "SCHEME", "COMMNAME", "COMMCODE"
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for r in records:
                writer.writerow(r.to_11_dict())

        # 2. Save JSON representation
        json_filename = f"{report_id}_memberlist_{date_str}.json"
        json_path = folder / json_filename
        dumpable = [r.to_11_dict() for r in records]
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(dumpable, f, indent=2)

        logger.info(f"Saved {len(records)} cleaned records to CSV: {csv_path} and JSON: {json_path}")
        return csv_path

    def get_cleaned_records(
        self, report_id: str, report_date: date
    ) -> Optional[List[CleanedRecord]]:
        folder = self._get_partition_dir(self.processed_root, report_date)
        matches = sorted(
            list(folder.glob(f"{report_id}_memberlist_*.json"))
            + list(folder.glob(f"{report_id}_cleaned_*.json")),
            reverse=True,
        )
        if matches:
            with open(matches[0], "r", encoding="utf-8") as f:
                data = json.load(f)
                return [CleanedRecord(**item) for item in data]

        # CSV fallback
        csv_matches = sorted(folder.glob(f"{report_id}_memberlist_*.csv"), reverse=True)
        if csv_matches:
            with open(csv_matches[0], "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                records = []
                for row in reader:
                    amt_str = row.get("RECAMOUNT", "0")
                    try:
                        amt = float(amt_str) if amt_str else 0.0
                    except ValueError:
                        amt = 0.0
                    row_copy = dict(row)
                    row_copy["RECAMOUNT"] = amt
                    records.append(CleanedRecord(**row_copy))
                return records
        return None


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
