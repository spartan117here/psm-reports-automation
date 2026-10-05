"""Domain models and data schemas for the reporting automation pipeline."""

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ReportType(str, Enum):
    """Supported report types in the automation system."""
    SUBHIKSHAM = "subhiksham"
    VIRUKSHAM = "viruksham"
    CLOSING = "closing"


class PipelineStage(str, Enum):
    """Stages of the daily automation pipeline execution."""
    INIT = "INIT"
    AUTH = "AUTH"
    DOWNLOAD = "DOWNLOAD"
    VALIDATION = "VALIDATION"
    CLEANING = "CLEANING"
    TRANSFORMATION = "TRANSFORMATION"
    AGGREGATION = "AGGREGATION"
    SHEETS_UPDATE = "SHEETS_UPDATE"
    RECONCILIATION = "RECONCILIATION"
    IMAGE_GEN = "IMAGE_GEN"
    DELIVERY = "DELIVERY"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class RunStatus(str, Enum):
    """Execution status of a pipeline run."""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class ValidationSeverity(str, Enum):
    """Severity of a validation result check."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ValidationStatus(str, Enum):
    """Outcome status of a validation check."""
    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"


class ValidationResult(BaseModel):
    """Individual rule validation result."""
    check_name: str
    status: ValidationStatus
    severity: ValidationSeverity
    expected: Any = None
    actual: Any = None
    message: str
    timestamp: datetime = Field(default_factory=datetime.now)


class ValidationReport(BaseModel):
    """Consolidated validation outcome for a pipeline run."""
    run_id: str
    checks: List[ValidationResult] = Field(default_factory=list)
    has_critical_failures: bool = False
    passed_count: int = 0
    warning_count: int = 0
    failure_count: int = 0

    def add_result(self, result: ValidationResult) -> None:
        self.checks.append(result)
        if result.status == ValidationStatus.PASSED:
            self.passed_count += 1
        elif result.status == ValidationStatus.WARNING:
            self.warning_count += 1
        elif result.status == ValidationStatus.FAILED:
            self.failure_count += 1
            if result.severity == ValidationSeverity.CRITICAL:
                self.has_critical_failures = True


class RunMetadata(BaseModel):
    """Audit metadata tracked for every pipeline run."""
    run_id: str
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    run_date: date
    report_date: date
    status: RunStatus = RunStatus.PENDING
    source: str = "Innervex"
    row_count: int = 0
    processed_count: int = 0
    error_count: int = 0
    validation_status: str = "PENDING"
    execution_time_seconds: Optional[float] = None
    unresolved_employee_codes: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class RunContext(BaseModel):
    """Runtime context passed across pipeline stages."""
    run_id: str
    report_date: date
    run_date: date
    dry_run: bool = False
    current_stage: PipelineStage = PipelineStage.INIT


class ReportFilterParams(BaseModel):
    """Parameters required to construct an Innervex report request payload."""
    action: str = "FindSchemeTransactionReport"
    from_date: str  # YYYY-MM-DD or DD/MM/YYYY depending on endpoint requirement
    to_date: str
    showroom: Union_Showrooms = "ALL"  # type: ignore[valid-type]
    scheme: str
    sub_scheme: Optional[Any] = None
    emp_code: str = "All"
    promo_code: str = "All"
    export_mode: str = "EXPORT"


# Helper union alias for Showrooms parameter
Union_Showrooms = Any


class RawReportPayload(BaseModel):
    """In-memory representation of raw report data received from Innervex."""
    report_id: str
    report_date: date
    fetched_at: datetime = Field(default_factory=datetime.now)
    row_count: int
    data: List[Dict[str, Any]]
    raw_headers: List[str] = Field(default_factory=list)
    raw_response: Optional[Dict[str, Any]] = None


class CleanedRecord(BaseModel):
    """Standardized 11-column cleaned record for Subhiksham / Viruksham schemes."""
    COSTNAME: Optional[str] = None
    CLIENTID: Optional[str] = None
    GROUPCODE: Optional[str] = None
    MSNO: str
    NAME: Optional[str] = None
    SCHDATE: Optional[str] = None
    RECAMOUNT: float = 0.0
    MOBILENO: Optional[str] = None
    SCHEME: Optional[str] = None
    COMMNAME: Optional[str] = None
    COMMCODE: Optional[str] = None

    # Enriched / processed columns for Google Sheet monthly tabs
    LOCATION_2: Optional[str] = None
    LOCATION: Optional[str] = None
    CODE_NAME: Optional[str] = None


class AggregationMetric(BaseModel):
    """Key metric aggregated by location or store."""
    location: str
    new_enrollment_count: int = 0
    total_amount: float = 0.0
    target_amount: Optional[float] = None
    yet_to_achieve: Optional[float] = None
    achieved_percentage: Optional[float] = None
    daily_average: Optional[float] = None
