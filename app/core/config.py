"""Configuration loading and validation using Pydantic and PyYAML."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from app.core.exceptions import ConfigurationError


class StorageConfig(BaseModel):
    data_dir: Path = Path("data")
    raw_dir: Path = Path("data/raw")
    processed_dir: Path = Path("data/processed")
    logs_dir: Path = Path("logs")
    temp_dir: Path = Path("tmp")


class ExecutionConfig(BaseModel):
    default_date_rule: str = "yesterday"
    idempotency_enabled: bool = True
    allow_partial_runs: bool = False
    max_retries: int = 3
    retry_backoff_seconds: int = 5


class ValidationPolicyConfig(BaseModel):
    halt_on_critical_failure: bool = True
    on_unresolved_employee_code: str = "FLAG_AND_CONTINUE"
    on_duplicate_records: str = "FLAG_AND_DEDUPLICATE"


class SecurityConfig(BaseModel):
    redact_sensitive_logs: bool = True
    sensitive_keys: List[str] = Field(default_factory=lambda: [
        "password", "secret", "token", "session_id", "jsessionid", "cookie", "authorization"
    ])


class AppSettings(BaseModel):
    name: str = "pothys-reporting-automation"
    version: str = "0.1.0"
    timezone: str = "Asia/Kolkata"
    environment: str = "development"


class AppConfig(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    validation_policy: ValidationPolicyConfig = Field(default_factory=ValidationPolicyConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @property
    def name(self) -> str:
        return self.app.name

    @property
    def version(self) -> str:
        return self.app.version

    @property
    def timezone(self) -> str:
        return self.app.timezone

    @property
    def environment(self) -> str:
        return self.app.environment


class InnervexAuthConfig(BaseModel):
    strategy: str = "session_cookie"
    login_endpoint: Optional[str] = None
    login_method: str = "POST"
    username_field: str = "username"
    password_field: str = "password"
    session_cookie_name: str = "JSESSIONID"
    env_username_key: str = "INNERVEX_USERNAME"
    env_password_key: str = "INNERVEX_PASSWORD"


class InnervexConfig(BaseModel):
    base_url: str = "http://192.168.5.213:4499"
    timeout_seconds: int = 60
    max_retries: int = 3
    backoff_factor: float = 2.0
    default_headers: Dict[str, str] = Field(default_factory=dict)
    auth: InnervexAuthConfig = Field(default_factory=InnervexAuthConfig)
    endpoints: Dict[str, str] = Field(default_factory=dict)


class ReportItemConfig(BaseModel):
    report_id: str
    display_name: str
    endpoint_key: str
    innervex_action: str
    scheme_name: str
    subschemes: List[str] = Field(default_factory=list)
    showroom_mode: str = "ALL_31"
    emp_code_filter: str = "All"
    promo_code_filter: str = "All"
    export_mode: str = "EXPORT"
    preamble_rows: int = 3
    raw_expected_column_count: int = 21
    output_columns: List[str] = Field(default_factory=list)
    discard_columns: List[str] = Field(default_factory=list)
    target_sheet_tab_prefix: str
    deduplication_key: str = "MSNO"


class ReportsConfig(BaseModel):
    reports: Dict[str, ReportItemConfig] = Field(default_factory=dict)


class LocationNamingConfig(BaseModel):
    store_suffix: str = ""
    ecomm_suffix: str = " ECOMM"


class MappingsConfig(BaseModel):
    employee_to_branch: Dict[str, str] = Field(default_factory=dict)
    showroom_codes: List[str] = Field(default_factory=list)
    special_locations: Dict[str, str] = Field(default_factory=dict)
    location_naming: Dict[str, LocationNamingConfig] = Field(default_factory=dict)


class TargetsConfig(BaseModel):
    fiscal_year: str = "2026-2027"
    q2: Dict[str, float] = Field(default_factory=dict)
    h1_multiplier: float = 2.0
    calculation_rules: Dict[str, str] = Field(default_factory=dict)


class AppConfigBundle(BaseModel):
    """Aggregate configuration bundle holding all subsystem settings."""
    project_root: Path
    app: AppConfig
    innervex: InnervexConfig
    reports: ReportsConfig
    mappings: MappingsConfig
    targets: TargetsConfig

    def get_report(self, report_id: str) -> ReportItemConfig:
        if report_id not in self.reports.reports:
            raise ConfigurationError(f"Report definition '{report_id}' not found in reports.yaml")
        return self.reports.reports[report_id]


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(f"Required configuration file not found at: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f) or {}
            return content
    except Exception as e:
        raise ConfigurationError(f"Failed to parse YAML file {path}: {e}") from e


def load_config(project_root: Optional[Path] = None) -> AppConfigBundle:
    """Load, validate, and return the complete configuration bundle."""
    if project_root is None:
        # Defaults to parent directory of app/
        project_root = Path(__file__).resolve().parent.parent.parent

    # Load environment variables from .env if present
    env_file = project_root / ".env"
    if env_file.is_file():
        load_dotenv(dotenv_path=env_file)

    config_dir = project_root / "config"

    app_data = _read_yaml(config_dir / "app.yaml")
    innervex_data = _read_yaml(config_dir / "innervex.yaml")
    reports_data = _read_yaml(config_dir / "reports.yaml")
    mappings_data = _read_yaml(config_dir / "mappings.yaml")
    targets_data = _read_yaml(config_dir / "targets.yaml")

    # Support environment variable override for base URL
    if os.getenv("INNERVEX_BASE_URL"):
        innervex_data.setdefault("innervex", {})["base_url"] = os.getenv("INNERVEX_BASE_URL")

    app_config = AppConfig(**app_data)
    innervex_config = InnervexConfig(**innervex_data.get("innervex", {}))
    reports_config = ReportsConfig(**reports_data)
    mappings_config = MappingsConfig(**mappings_data)
    targets_config = TargetsConfig(**targets_data.get("targets", {}))

    return AppConfigBundle(
        project_root=project_root,
        app=app_config,
        innervex=innervex_config,
        reports=reports_config,
        mappings=mappings_config,
        targets=targets_config,
    )
