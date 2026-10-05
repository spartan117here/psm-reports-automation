"""Framework-agnostic HTTP client for communicating with Innervex."""

import logging
from typing import Any, Dict, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.config import InnervexConfig
from app.core.exceptions import InnervexConnectionError, InnervexResponseError
from app.innervex.auth import AuthStrategy, create_auth_strategy
from app.innervex.schemas import InnervexRawResponse

logger = logging.getLogger("pothys_reporting")


class InnervexClient:
    """
    HTTP client for the internal Innervex system.

    Provides connection pooling, retry policies, header injection,
    and structured response validation.
    """

    def __init__(self, config: InnervexConfig, auth_strategy: Optional[AuthStrategy] = None):
        self.config = config
        self.auth_strategy = auth_strategy or create_auth_strategy(config)
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create a configured requests.Session with retries and default headers."""
        session = requests.Session()
        session.headers.update(self.config.default_headers)

        # Standard connection retry for transient TCP drops on LAN
        retry_strategy = Retry(
            total=self.config.max_retries,
            backoff_factor=self.config.backoff_factor,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def ensure_authenticated(self) -> bool:
        """Ensure active authenticated session using the configured auth strategy."""
        if not self.auth_strategy.is_authenticated(self.session):
            logger.info("Session not authenticated. Initiating authentication flow...")
            return self.auth_strategy.authenticate(self.session)
        return True

    def post_report(self, endpoint_path: str, form_data: Dict[str, str]) -> InnervexRawResponse:
        """
        Submit a report request to Innervex and return the parsed JSON response.

        Args:
            endpoint_path: Relative URL (e.g., /schemeNewCustRep)
            form_data: Form-encoded key-value parameters.

        Returns:
            Validated InnervexRawResponse containing the 'data' array.
        """
        self.ensure_authenticated()

        url = f"{self.config.base_url.rstrip('/')}/{endpoint_path.lstrip('/')}"
        logger.info(f"Dispatching report request to {url}")

        try:
            response = self.session.post(
                url,
                data=form_data,
                timeout=self.config.timeout_seconds,
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error while connecting to Innervex: {e}")
            raise InnervexConnectionError(f"Failed to connect to Innervex at {url}: {e}") from e

        if response.status_code != 200:
            logger.error(f"Innervex returned HTTP {response.status_code}: {response.text[:200]}")
            raise InnervexResponseError(
                f"Innervex HTTP {response.status_code} error from {url}",
                details={"status_code": response.status_code, "body_snippet": response.text[:200]},
            )

        # Validate non-empty body
        if not response.text or not response.text.strip():
            logger.error(f"Innervex returned an empty response body from {url}")
            raise InnervexResponseError(
                f"Innervex returned an empty response from {url}",
                details={"status_code": response.status_code},
            )

        try:
            json_data = response.json()
        except ValueError as e:
            logger.error(f"Failed to parse JSON response from {url}: {e}")
            raise InnervexResponseError(f"Response from {url} is not valid JSON: {e}") from e

        if not isinstance(json_data, dict):
            raise InnervexResponseError(
                f"Unexpected response structure from Innervex: expected JSON object.",
                details={"received_type": type(json_data).__name__},
            )

        # Detect session expiration returned as JSON
        if json_data.get("Message") == "Session Expired" or json_data.get("Success") is False:
            err_msg = json_data.get("Message", "Session Expired or operation rejected")
            logger.error(f"Innervex session invalid or expired: {err_msg}")
            from app.core.exceptions import AuthenticationError
            raise AuthenticationError(f"Innervex session expired or rejected: {err_msg}")

        if "data" not in json_data:
            raise InnervexResponseError(
                f"Unexpected response structure from Innervex: expected JSON dict with 'data' key.",
                details={"keys_received": list(json_data.keys())},
            )

        if not isinstance(json_data["data"], list):
            raise InnervexResponseError(
                f"Unexpected response structure from Innervex: 'data' field must be an array/list.",
                details={"data_type": type(json_data["data"]).__name__},
            )

        return InnervexRawResponse(**json_data)

    def close(self) -> None:
        """Release session resources."""
        self.session.close()
