"""
Configurable Business Calendar & Reporting Date Resolver.

Distinguishes between:
- Current calendar wall-clock date.
- Previous calendar day (yesterday).
- Previous business/reporting day.
- Scheduled non-working / holiday days.
"""

from datetime import date, timedelta
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger("pothys_reporting")

# Default showroom retail schedule: 7 days a week (Monday through Sunday)
DEFAULT_REPORTING_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class CalendarConfig(BaseModel):
    """Configuration model for business reporting calendar."""
    reporting_days: List[str] = Field(default_factory=lambda: list(DEFAULT_REPORTING_DAYS))
    holidays: List[str] = Field(default_factory=list)  # ISO dates 'YYYY-MM-DD'
    lookback_max_days: int = 7


class BusinessCalendar:
    """
    Evaluates business operational days and computes the intended reporting date.
    """

    def __init__(self, config: Optional[CalendarConfig] = None):
        self.config = config or CalendarConfig()
        self._allowed_weekdays = self._parse_reporting_days(self.config.reporting_days)
        self._holidays = self._parse_holidays(self.config.holidays)

    @staticmethod
    def _parse_reporting_days(days: List[str]) -> Set[int]:
        """Convert day abbreviations (mon, tue, ...) to Python weekday integers (0=Mon, 6=Sun)."""
        day_map = {
            "mon": 0, "monday": 0,
            "tue": 1, "tuesday": 1,
            "wed": 2, "wednesday": 2,
            "thu": 3, "thursday": 3,
            "fri": 4, "friday": 4,
            "sat": 5, "saturday": 5,
            "sun": 6, "sunday": 6,
        }
        weekdays = set()
        for d in days:
            clean = str(d).strip().lower()
            if clean in day_map:
                weekdays.add(day_map[clean])
        return weekdays or set(range(7))

    @staticmethod
    def _parse_holidays(holidays: List[str]) -> Set[date]:
        """Parse holiday string dates to date objects."""
        parsed = set()
        for h in holidays:
            try:
                parsed.add(date.fromisoformat(str(h).strip()))
            except ValueError:
                pass
        return parsed

    def is_reporting_day(self, target_date: date) -> bool:
        """Check whether target_date is an active reporting day."""
        if target_date in self._holidays:
            return False
        return target_date.weekday() in self._allowed_weekdays

    def get_previous_reporting_day(self, from_date: date) -> date:
        """
        Find the most recent reporting day prior to from_date.
        Respects weekends and configured holidays up to lookback_max_days.
        """
        curr = from_date - timedelta(days=1)
        for _ in range(self.config.lookback_max_days):
            if self.is_reporting_day(curr):
                return curr
            curr -= timedelta(days=1)
        # Fallback to simple yesterday if lookback exhausted
        return from_date - timedelta(days=1)

    def resolve_target_date(
        self,
        explicit_date: Optional[date] = None,
        reference_date: Optional[date] = None,
    ) -> Tuple[date, bool, str]:
        """
        Resolve the target date for reporting.

        Returns:
            Tuple of (target_date, is_active_reporting_day, reason_description)
        """
        if explicit_date is not None:
            is_active = self.is_reporting_day(explicit_date)
            return (
                explicit_date,
                is_active,
                f"Explicit date provided: {explicit_date.isoformat()} (Reporting Day: {is_active})",
            )

        ref = reference_date or date.today()
        # By business rule: daily report generated today covers the previous business day
        target = self.get_previous_reporting_day(ref)
        is_active = self.is_reporting_day(target)
        return (
            target,
            is_active,
            f"Derived previous reporting day: {target.isoformat()} (Reference: {ref.isoformat()})",
        )

