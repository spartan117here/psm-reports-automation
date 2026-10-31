"""
Immutable source data snapshot manager for daily reporting historical reproducibility.

Guarantees:
1. Every report execution can record and persist an immutable snapshot of raw source records.
2. Historical reruns automatically load and use their original source snapshot, never mutating earlier outputs.
3. Snapshots verify cryptographic integrity via SHA-256 checksums on load.
4. Overwriting earlier snapshots is strictly prohibited to preserve audit trail integrity.
"""

from datetime import date, datetime
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("pothys_reporting")

DEFAULT_SNAPSHOTS_DIR = Path("data/snapshots")


def compute_records_sha256(records: List[Any]) -> str:
    """Compute a deterministic SHA-256 hash across record rows."""
    canonical_bytes = json.dumps(records, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


class SourceSnapshot(BaseModel):
    """Immutable record-level source snapshot for a given reporting date."""
    snapshot_id: str
    category: str
    report_date: date
    created_at: datetime = Field(default_factory=datetime.now)
    source_identifier: str = ""
    row_count: int
    sha256: str
    records: List[Any]


class SnapshotManager:
    """Manages persistent, immutable source data snapshots partitioned by category and date."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir or DEFAULT_SNAPSHOTS_DIR).resolve()

    def _get_snapshot_path(self, category: str, report_date: date) -> Path:
        cat_dir = self.base_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        date_str = report_date.strftime("%Y%m%d")
        return cat_dir / f"{category}_snapshot_{date_str}.json"

    def save_snapshot(self, snapshot: SourceSnapshot, overwrite: bool = False) -> Path:
        """
        Persist an immutable source snapshot.
        Fails if snapshot already exists unless overwrite is explicitly requested.
        """
        path = self._get_snapshot_path(snapshot.category, snapshot.report_date)
        if path.is_file() and not overwrite:
            raise FileExistsError(
                f"Source snapshot already exists at '{path}'. "
                "Silent replacement of historical snapshots is prohibited to guarantee audit reproducibility."
            )

        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot.model_dump(mode="json"), f, indent=2)

        logger.info(
            f"Saved immutable {snapshot.category} snapshot for {snapshot.report_date} "
            f"(ID: {snapshot.snapshot_id}, SHA-256: {snapshot.sha256[:12]}..., Rows: {snapshot.row_count}) to {path}"
        )
        return path

    def load_snapshot(self, category: str, report_date: date) -> Optional[SourceSnapshot]:
        """
        Load an existing source snapshot and verify its cryptographic integrity.
        Raises ValueError if SHA-256 checksum fails.
        """
        path = self._get_snapshot_path(category, report_date)
        if not path.is_file():
            return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        snapshot = SourceSnapshot(**data)
        # Verify SHA-256 integrity
        current_hash = compute_records_sha256(snapshot.records)
        if current_hash != snapshot.sha256:
            raise ValueError(
                f"Snapshot integrity violation in '{path}': stored SHA-256 {snapshot.sha256} "
                f"does not match computed SHA-256 {current_hash}."
            )

        logger.info(
            f"Loaded existing immutable {category} snapshot for {report_date} "
            f"(ID: {snapshot.snapshot_id}, SHA-256: {snapshot.sha256[:12]}..., Rows: {snapshot.row_count})"
        )
        return snapshot

    def get_or_create_snapshot(
        self,
        category: str,
        report_date: date,
        records: List[Any],
        source_identifier: str = "",
    ) -> SourceSnapshot:
        """
        Idempotent retrieval or creation of a source snapshot.
        If an immutable snapshot exists from a previous run for this report_date, it is loaded.
        Otherwise, a new snapshot is created and persisted.
        """
        existing = self.load_snapshot(category, report_date)
        if existing is not None:
            return existing

        now = datetime.now()
        sha256 = compute_records_sha256(records)
        date_str = report_date.strftime("%Y%m%d")
        ts_str = now.strftime("%Y%m%dT%H%M%S")
        snap_id = f"snap_{category}_{date_str}_{ts_str}_{sha256[:8]}"

        new_snapshot = SourceSnapshot(
            snapshot_id=snap_id,
            category=category,
            report_date=report_date,
            created_at=now,
            source_identifier=source_identifier,
            row_count=len(records),
            sha256=sha256,
            records=records,
        )

        self.save_snapshot(new_snapshot, overwrite=False)
        return new_snapshot

