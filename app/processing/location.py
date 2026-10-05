"""Location attribution and ECOMM branch resolution logic."""

import logging
import re
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

from app.core.config import MappingsConfig
from app.core.exceptions import UnresolvedEmployeeCodeError

logger = logging.getLogger("pothys_reporting")


class LocationAttribution(NamedTuple):
    """Result of location resolution for a single record."""
    branch: Optional[str]
    location: str
    location_2: str
    is_ecomm: bool
    is_special: bool
    unresolved_code: Optional[str] = None


class LocationResolver:
    """
    Resolves branch names and Google Sheet LOCATION / LOCATION 2 labels
    based on employee codes, cost names, and scheme types.
    """

    def __init__(self, mappings: MappingsConfig, strict_mode: bool = False):
        self.mappings = mappings
        self.strict_mode = strict_mode
        self.employee_to_branch = mappings.employee_to_branch
        self.unresolved_codes: Set[str] = set()

    def resolve(
        self,
        cost_name: Optional[str],
        comm_code: Optional[str],
        scheme_type: str = "ss",  # "ss" or "sv"
    ) -> LocationAttribution:
        """
        Resolve branch and location strings for Subhiksham or Viruksham records.

        Args:
            cost_name: The COSTNAME field from Innervex.
            comm_code: Employee / commission code (e.g. 'TVL012', 'CPT_ECOMM', etc.)
            scheme_type: 'ss' for Subhiksham, 'sv' for Viruksham.

        Returns:
            LocationAttribution with resolved branch, LOCATION, LOCATION 2 labels.
        """
        cname = (cost_name or "").strip().upper()
        code = (comm_code or "").strip().upper()

        # 1. Check special location cases (ONLINE, CORPORATE OFFICE)
        online_label = self.mappings.special_locations.get("online_label", "ONLINE")
        corp_label = self.mappings.special_locations.get("corporate_office_label", "CORPORATE OFFICE")

        if online_label in cname or online_label in code:
            loc = online_label
            loc2 = online_label if scheme_type == "ss" else f"{online_label} SV"
            return LocationAttribution(branch=None, location=loc, location_2=loc2, is_ecomm=True, is_special=True)

        if corp_label in cname or corp_label in code:
            loc = corp_label
            loc2 = corp_label if scheme_type == "ss" else f"{corp_label} SV"
            return LocationAttribution(branch=None, location=loc, location_2=loc2, is_ecomm=False, is_special=True)

        # 2. Check for ECOMM indicator
        is_ecomm = "ECOMM" in cname or "ECOMM" in code or "APP" in cname

        # 3. Match employee branch prefix
        branch: Optional[str] = None
        matched_prefix: Optional[str] = None

        # Sort prefixes by descending length so TPJ is checked appropriately
        sorted_prefixes = sorted(self.employee_to_branch.keys(), key=len, reverse=True)
        for prefix in sorted_prefixes:
            if code.startswith(prefix) or prefix in code:
                branch = self.employee_to_branch[prefix]
                matched_prefix = prefix
                break

        # If no code match, attempt fallback to cost_name
        if not branch and cname:
            for prefix, mapped_branch in self.employee_to_branch.items():
                if mapped_branch in cname or prefix in cname:
                    branch = mapped_branch
                    break

        if not branch:
            # Unresolved employee code
            self.unresolved_codes.add(code)
            msg = f"Unresolved employee code '{code}' (COSTNAME: '{cost_name}'). No branch mapping found."
            logger.warning(msg)

            if self.strict_mode:
                raise UnresolvedEmployeeCodeError(msg, details={"code": code, "cost_name": cost_name})

            # In non-strict mode, label as UNRESOLVED and allow pipeline to flag
            placeholder = f"UNRESOLVED ({code})" if code else "UNRESOLVED"
            return LocationAttribution(
                branch=None,
                location=placeholder,
                location_2=placeholder,
                is_ecomm=is_ecomm,
                is_special=False,
                unresolved_code=code,
            )

        # 4. Format location labels based on scheme type and ecomm status
        naming = self.mappings.location_naming.get(scheme_type)
        ecomm_suffix = naming.ecomm_suffix if naming else " ECOMM"
        store_suffix = naming.store_suffix if naming else ""

        if is_ecomm:
            location_label = f"{branch}{ecomm_suffix}"
            location_2_label = f"{branch}{ecomm_suffix}"
        else:
            location_label = f"{branch}{store_suffix}"
            location_2_label = f"{branch}{store_suffix}"

        return LocationAttribution(
            branch=branch,
            location=location_label,
            location_2=location_2_label,
            is_ecomm=is_ecomm,
            is_special=False,
        )

    def get_all_unresolved_codes(self) -> List[str]:
        """Return list of all unresolved employee codes encountered in this session."""
        return sorted(list(self.unresolved_codes))
