"""Custom exception hierarchy for Pothys Daily Reporting Automation."""

from typing import Any, Dict, Optional


class ReportingAutomationError(Exception):
    """Base exception for all reporting automation pipeline errors."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(ReportingAutomationError):
    """Raised when configuration files are missing, malformed, or invalid."""


class AuthenticationError(ReportingAutomationError):
    """Raised when authentication against Innervex or external services fails."""


class InnervexConnectionError(ReportingAutomationError):
    """Raised when connection to the internal Innervex server fails (LAN/timeout)."""


class InnervexResponseError(ReportingAutomationError):
    """Raised when Innervex returns an invalid status, unexpected format, or error payload."""


class DataValidationError(ReportingAutomationError):
    """Raised when data fails structural or business validation checks."""


class CriticalValidationError(DataValidationError):
    """Raised when a validation check fails with CRITICAL severity, requiring pipeline halt."""


class TransformationError(ReportingAutomationError):
    """Raised during data cleaning, column filtering, or normalization."""


class UnresolvedEmployeeCodeError(TransformationError):
    """Raised when an employee code cannot be attributed to any known branch."""


class IdempotencyConflictError(ReportingAutomationError):
    """Raised when an operation would violate idempotency (e.g. duplicate batch write)."""


class GoogleSheetsError(ReportingAutomationError):
    """Raised when interaction with Google Sheets API fails."""


class GoogleAuthenticationError(GoogleSheetsError, AuthenticationError):
    """Raised when authentication against Google APIs fails."""


class DeliveryError(ReportingAutomationError):
    """Raised when report delivery to recipients fails."""
