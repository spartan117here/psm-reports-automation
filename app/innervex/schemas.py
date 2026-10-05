"""Innervex request payload and response data validation schemas."""

from datetime import date
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class InnervexReportRequestPayload(BaseModel):
    """URL-encoded POST form payload for Innervex report extraction."""
    Action: str = Field(default="FindSchemeTransactionReport", description="Innervex dispatch action")
    fromDate: str = Field(..., description="Report start date")
    toDate: str = Field(..., description="Report end date")
    Showroom: str = Field(..., description="Showroom identifier or comma-separated list")
    Scheme: str = Field(..., description="Target scheme name")
    SubScheme: Optional[str] = Field(default=None, description="Comma-separated or single sub-scheme string")
    EmpCode: str = Field(default="All", description="Employee code filter")
    PromoCode: str = Field(default="All", description="Promotion code filter")
    Export: str = Field(default="EXPORT", description="Export command mode")

    def to_form_dict(self) -> Dict[str, str]:
        """Convert payload to form-encoded dictionary string values."""
        data: Dict[str, str] = {
            "Action": self.Action,
            "fromDate": self.fromDate,
            "toDate": self.toDate,
            "Showroom": self.Showroom,
            "Scheme": self.Scheme,
            "EmpCode": self.EmpCode,
            "PromoCode": self.PromoCode,
            "Export": self.Export,
        }
        if self.SubScheme is not None:
            data["SubScheme"] = self.SubScheme
        return data


class InnervexRawResponse(BaseModel):
    """Schema for validating JSON payload returned by Innervex endpoints."""
    data: List[Dict[str, Any]] = Field(default_factory=list, description="Array of report record dictionaries")
    screenName: Optional[str] = None
    success: Optional[bool] = None
    message: Optional[str] = None
    total_records: Optional[int] = None

    model_config = ConfigDict(extra="allow")
