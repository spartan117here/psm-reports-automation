"""Location attribution and ECOMM branch resolution logic."""

import json
import logging
from pathlib import Path
import re
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

from app.core.config import MappingsConfig
from app.core.exceptions import UnresolvedEmployeeCodeError

logger = logging.getLogger("pothys_reporting")

# Showroom code to canonical branch mapping for physical in-store purchases
SHOWROOM_TO_BRANCH: Dict[str, str] = {
    "TVC": "TRIVANDRUM",
    "TVM": "TRIVANDRUM",
    "TRIVANDRUM": "TRIVANDRUM",
    "TVL": "TIRUNELVELI",
    "TIRUNELVELI": "TIRUNELVELI",
    "NELLAI": "TIRUNELVELI",
    "CPT": "CHROMEPET",
    "CHROMEPET": "CHROMEPET",
    "KPM": "KANCHEEPURAM",
    "KANCHEEPURAM": "KANCHEEPURAM",
    "KANCHI": "KANCHEEPURAM",
    "PAD": "PADI",
    "PADI": "PADI",
    "SLM": "SALEM",
    "SALEM": "SALEM",
    "PNM": "POONAMALLEE",
    "POONAMALLEE": "POONAMALLEE",
    "CBE": "COIMBATORE",
    "COIMBATORE": "COIMBATORE",
    "TRI": "TRICHY",
    "TPJ": "TRICHY",
    "TRICHY": "TRICHY",
}


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
    based on employee codes, cost names, verified employee registry, and scheme types.
    """

    def __init__(
        self,
        mappings: MappingsConfig,
        strict_mode: bool = False,
        employee_registry: Optional[Dict[str, Dict[str, str]]] = None,
        registry_path: Optional[Path] = None,
    ):
        self.mappings = mappings
        self.strict_mode = strict_mode
        self.employee_to_branch = mappings.employee_to_branch
        self.showroom_to_branch = {**SHOWROOM_TO_BRANCH}
        for k, v in mappings.employee_to_branch.items():
            if k not in self.showroom_to_branch:
                self.showroom_to_branch[k] = v
        self.unresolved_codes: Set[str] = set()

        # Load master verified employee registry
        self.employee_registry: Dict[str, Dict[str, str]] = {"ss": {}, "sv": {}}
        if employee_registry:
            if "ss" in employee_registry or "sv" in employee_registry:
                self.employee_registry = employee_registry
            else:
                self.employee_registry = {
                    "ss": dict(employee_registry),
                    "sv": dict(employee_registry),
                }
        else:
            self._load_employee_registry(registry_path)

    def _load_employee_registry(self, registry_path: Optional[Path] = None):
        """Load employee mapping from authorized JSON registry."""
        path = registry_path
        if not path:
            reg_rel = getattr(self.mappings, "employee_registry_path", "config/employee_mapping.json")
            for candidate in [
                Path(reg_rel),
                Path.cwd() / reg_rel,
                Path(__file__).parent.parent.parent / reg_rel,
            ]:
                if candidate.is_file():
                    path = candidate
                    break

        if path and path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "ss" in data and "sv" in data:
                    self.employee_registry["ss"] = {
                        str(k).strip().upper(): str(v).strip()
                        for k, v in data["ss"].items()
                    }
                    self.employee_registry["sv"] = {
                        str(k).strip().upper(): str(v).strip()
                        for k, v in data["sv"].items()
                    }
                else:
                    flat = {str(k).strip().upper(): str(v).strip() for k, v in data.items()}
                    self.employee_registry["ss"] = flat
                    self.employee_registry["sv"] = flat
                logger.info(
                    f"Loaded {len(self.employee_registry['ss'])} verified employee codes from {path}"
                )
            except Exception as e:
                logger.warning(f"Could not load employee registry from {path} ({e}).")

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
            comm_code: Employee / commission code (e.g. 'TVL012', '1676', '415', etc.)
            scheme_type: 'ss' for Subhiksham, 'sv' for Viruksham.

        Returns:
            LocationAttribution with resolved branch, LOCATION, LOCATION 2 labels.
        """
        cname = (cost_name or "").strip().upper()
        code = (comm_code or "").strip().upper()
        is_ss = scheme_type == "ss"

        # 1. Check special location cases (ONLINE, CORPORATE OFFICE in cost_name or comm_code)
        online_label = self.mappings.special_locations.get("online_label", "ONLINE")
        corp_label = self.mappings.special_locations.get("corporate_office_label", "CORPORATE OFFICE")

        if online_label in cname or online_label in code:
            loc = online_label if is_ss else f"{online_label} SV"
            loc2 = online_label if is_ss else f"{online_label} SV"
            return LocationAttribution(
                branch=None, location=loc, location_2=loc2, is_ecomm=True, is_special=True
            )

        if corp_label in cname or corp_label in code:
            loc = corp_label if is_ss else f"{corp_label} SV"
            loc2 = corp_label if is_ss else f"{corp_label} SV"
            return LocationAttribution(
                branch=None, location=loc, location_2=loc2, is_ecomm=False, is_special=True
            )

        # 2. Check for App / HAPP / ECOMM indicators
        is_app_or_ecomm = (
            "ECOMM" in cname or "ECOMM" in code or
            "APP" in cname or "HAPP" in cname or "ECOM" in cname
        )

        # 3. If Physical Showroom (NOT App/ECOMM)
        if not is_app_or_ecomm:
            # Physical store COSTNAME takes precedence for in-store purchases
            branch = None
            if cname in self.showroom_to_branch:
                branch = self.showroom_to_branch[cname]
            else:
                for s_code, b_name in self.showroom_to_branch.items():
                    if s_code in cname:
                        branch = b_name
                        break

            # If cost_name did not match, check employee registry or prefix
            if not branch:
                emp_map = (
                    self.employee_registry.get("ss", {})
                    if is_ss
                    else self.employee_registry.get("sv", {})
                )
                if code in emp_map:
                    branch = emp_map[code].replace(" SV", "").replace(" ECOMM", "").strip()
                else:
                    sorted_prefixes = sorted(self.employee_to_branch.keys(), key=len, reverse=True)
                    for prefix in sorted_prefixes:
                        if code.startswith(prefix) or prefix in code:
                            branch = self.employee_to_branch[prefix]
                            break

            if branch:
                naming = self.mappings.location_naming.get(scheme_type)
                store_suffix = naming.store_suffix if naming else ("" if scheme_type == "ss" else " SV")
                label = f"{branch}{store_suffix}"
                return LocationAttribution(
                    branch=branch,
                    location=label,
                    location_2=label,
                    is_ecomm=False,
                    is_special=False,
                )

        # 4. If App / HAPP / ECOMM enrollment
        if is_app_or_ecomm:
            # Check employee code in verified registry first
            if code:
                emp_map = (
                    self.employee_registry.get("ss", {})
                    if is_ss
                    else self.employee_registry.get("sv", {})
                )
                if code in emp_map:
                    base_branch = emp_map[code]
                    clean_branch = base_branch.replace(" SV", "").replace(" ECOMM", "").strip()
                    if clean_branch == "CORPORATE OFFICE":
                        label = "CORPORATE OFFICE" if is_ss else "CORPORATE OFFICE SV"
                        return LocationAttribution(
                            branch=None,
                            location=label,
                            location_2=label,
                            is_ecomm=False,
                            is_special=True,
                        )
                    naming = self.mappings.location_naming.get(scheme_type)
                    ecomm_suffix = naming.ecomm_suffix if naming else (" ECOMM" if scheme_type == "ss" else " SV ECOMM")
                    label = f"{clean_branch}{ecomm_suffix}"
                    return LocationAttribution(
                        branch=clean_branch,
                        location=label,
                        location_2=label,
                        is_ecomm=True,
                        is_special=False,
                    )

                # Check showroom prefix in employee code
                sorted_prefixes = sorted(self.employee_to_branch.keys(), key=len, reverse=True)
                for prefix in sorted_prefixes:
                    if code.startswith(prefix) or prefix in code:
                        base_branch = self.employee_to_branch[prefix]
                        naming = self.mappings.location_naming.get(scheme_type)
                        ecomm_suffix = naming.ecomm_suffix if naming else (" ECOMM" if scheme_type == "ss" else " SV ECOMM")
                        label = f"{base_branch}{ecomm_suffix}"
                        return LocationAttribution(
                            branch=base_branch,
                            location=label,
                            location_2=label,
                            is_ecomm=True,
                            is_special=False,
                        )

                # Code is provided but unknown in registry
                self.unresolved_codes.add(code)
                msg = f"Unresolved employee code '{code}' (COSTNAME: '{cost_name}'). No branch mapping found in employee registry."
                logger.warning(msg)
                if self.strict_mode:
                    raise UnresolvedEmployeeCodeError(msg, details={"code": code, "cost_name": cost_name})
                placeholder = f"UNRESOLVED ({code})"
                return LocationAttribution(
                    branch=None,
                    location=placeholder,
                    location_2=placeholder,
                    is_ecomm=True,
                    is_special=False,
                    unresolved_code=code,
                )

            # If employee code is NOT provided (empty/blank):
            # Confirmed organic unassisted direct online app enrollment
            label = "ONLINE" if is_ss else "ONLINE SV"
            return LocationAttribution(
                branch=None,
                location=label,
                location_2=label,
                is_ecomm=True,
                is_special=True,
            )

        # 5. Non-app, unmapped showroom
        self.unresolved_codes.add(code or cname)
        msg = f"Unresolved showroom/code '{code or cname}' (COSTNAME: '{cost_name}'). No branch mapping found."
        logger.warning(msg)
        if self.strict_mode:
            raise UnresolvedEmployeeCodeError(msg, details={"code": code, "cost_name": cost_name})
        placeholder = f"UNRESOLVED ({code})" if code else "UNRESOLVED"
        return LocationAttribution(
            branch=None,
            location=placeholder,
            location_2=placeholder,
            is_ecomm=False,
            is_special=False,
            unresolved_code=code,
        )

    def get_all_unresolved_codes(self) -> List[str]:
        """Return list of all unresolved employee codes encountered in this session."""
        return sorted(list(self.unresolved_codes))
