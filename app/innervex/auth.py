"""
Innervex Authentication Strategy and Session Management.

Implements the confirmed authentication flow:
- Endpoint: POST /login.ispx
- Content-Type: application/x-www-form-urlencoded; charset=UTF-8
- Form fields: username, password, Action="Sign in"
- Response: HTTP 200 JSON with status, token, user profile
- Session management: requests.Session cookie jar (JSESSIONID)
"""

from abc import ABC, abstractmethod
import logging
import os
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, PrivateAttr
import requests

from app.core.config import InnervexConfig
from app.core.exceptions import AuthenticationError, InnervexConnectionError

logger = logging.getLogger("pothys_reporting")


class AuthResult(BaseModel):
    """
    Safe authentication result model containing non-sensitive session metadata.

    The actual authentication token is stored as a private attribute in memory,
    completely excluded from model_dump(), serialization, __repr__(), and __str__().
    """
    authenticated: bool = False
    username: Optional[str] = None
    user_id: Optional[str] = None
    branch: Optional[str] = None
    available_showrooms: List[str] = Field(default_factory=list)
    token_present: bool = False
    cookies_present: bool = False
    http_status: int = 200
    status_message: Optional[str] = None

    # Sensitive token kept strictly in-memory and omitted from string representations
    _token: Optional[str] = PrivateAttr(default=None)

    def get_token(self) -> Optional[str]:
        """Access the token strictly in memory when needed by downstream callers."""
        return self._token

    def diagnostics(self) -> Dict[str, Any]:
        """Return safe diagnostic summary without exposing secret values."""
        return {
            "authenticated": self.authenticated,
            "cookies_present": self.cookies_present,
            "token_present": self.token_present,
            "http_status": self.http_status,
        }

    def __repr__(self) -> str:
        return (
            f"AuthResult(authenticated={self.authenticated}, "
            f"username={self.username!r}, "
            f"branch={self.branch!r}, "
            f"token_present={self.token_present}, "
            f"cookies_present={self.cookies_present})"
        )

    def __str__(self) -> str:
        return self.__repr__()


class AuthStrategy(ABC):
    """Abstract authentication strategy for Innervex session establishment."""

    @abstractmethod
    def authenticate(self, session: requests.Session) -> bool:
        """
        Perform legitimate authentication using configured authorized credentials.
        Populates session cookies and internal state on success.
        """
        pass

    @abstractmethod
    def is_authenticated(self, session: requests.Session) -> bool:
        """Check whether the provided session contains a valid active authentication."""
        pass


class InnervexAuthenticator(AuthStrategy):
    """
    Production authenticator for the Innervex web application.

    Authenticates via POST to /login.ispx with form-encoded credentials,
    maintains cookies in requests.Session, and secures the returned token in memory.
    """

    def __init__(self, config: InnervexConfig):
        self.config = config
        self._auth_result: Optional[AuthResult] = None

    @property
    def auth_result(self) -> Optional[AuthResult]:
        """Retrieve current cached authentication result."""
        return self._auth_result

    def is_authenticated(self, session: requests.Session) -> bool:
        """
        Verify if the session is currently authenticated.
        Checks for active cookies and successful auth state.
        """
        if self._auth_result is None or not self._auth_result.authenticated:
            return False

        cookie_name = self.config.auth.session_cookie_name
        has_session_cookie = cookie_name in session.cookies or len(session.cookies) > 0
        return has_session_cookie

    def authenticate(self, session: requests.Session) -> bool:
        """Fulfill AuthStrategy interface by logging into the provided session."""
        result = self.login(session=session)
        return result.authenticated

    def login(self, session: Optional[requests.Session] = None) -> AuthResult:
        """
        Execute legitimate login against Innervex /login.ispx.

        Args:
            session: Optional existing requests.Session to authenticate.
                     If None, creates and maintains a local session.

        Returns:
            AuthResult: Safe metadata model confirming authentication status.

        Raises:
            AuthenticationError: On credential omission, invalid credentials, or server rejection.
            InnervexConnectionError: On network or connection drop.
        """
        if session is None:
            session = requests.Session()
            session.headers.update(self.config.default_headers)

        # 1. Read credentials from environment variables
        username_env = self.config.auth.env_username_key
        password_env = self.config.auth.env_password_key
        username = os.getenv(username_env)
        password = os.getenv(password_env)

        if not username or not password:
            msg = (
                f"Missing required Innervex credentials in environment. "
                f"Please ensure '{username_env}' and '{password_env}' are set in your .env or environment."
            )
            logger.error(
                f"Authentication failed: credentials missing ({username_env}=%s, {password_env}=%s)",
                bool(username),
                bool(password),
            )
            raise AuthenticationError(msg)

        # 2. Build URL and form-encoded payload
        login_endpoint = self.config.auth.login_endpoint or "/login.ispx"
        login_url = f"{self.config.base_url.rstrip('/')}/{login_endpoint.lstrip('/')}"
        action_val = getattr(self.config.auth, "login_action", "Sign in")

        form_data = {
            self.config.auth.username_field: username,
            self.config.auth.password_field: password,
            "Action": action_val,
        }

        request_headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }

        logger.info("Initiating Innervex authentication POST to %s", login_url)

        # 3. Dispatch POST request
        try:
            response = session.post(
                login_url,
                data=form_data,
                headers=request_headers,
                timeout=self.config.timeout_seconds,
            )
        except requests.exceptions.RequestException as e:
            logger.error("Connection error during Innervex authentication: %s", type(e).__name__)
            raise InnervexConnectionError(
                f"Failed to connect to Innervex at {login_url}: {e}"
            ) from e

        # 4. Validate HTTP status code
        if response.status_code != 200:
            logger.error("Innervex authentication returned HTTP status %d", response.status_code)
            raise AuthenticationError(
                f"Innervex login returned HTTP {response.status_code} from {login_url}",
                details={"status_code": response.status_code},
            )

        # 5. Parse JSON response
        try:
            json_data = response.json()
        except ValueError as e:
            logger.error("Innervex authentication response is not valid JSON")
            raise AuthenticationError(
                f"Innervex login response from {login_url} is not valid JSON: {e}"
            ) from e

        if not isinstance(json_data, dict):
            raise AuthenticationError(
                "Innervex login returned an unexpected JSON structure (expected JSON object)."
            )

        # 6. Verify authentication success in JSON body
        # Supported success indicators: status True / 'true' / 'success' / 'ok' / 1
        raw_status = json_data.get("status")
        raw_success = json_data.get("success")
        is_explicit_failure = (
            raw_status in (False, 0, "false", "False", "failed", "error", "invalid")
            or raw_success is False
            or "error" in json_data
            or "errorMessage" in json_data
        )

        token = json_data.get("token") or json_data.get("authToken") or json_data.get("jwt")
        token_present = bool(token and str(token).strip())

        # Determine success
        is_success = False
        if not is_explicit_failure:
            if raw_status in (True, 1, "true", "True", "success", "SUCCESS", "ok", "OK"):
                is_success = True
            elif raw_success is True:
                is_success = True
            elif token_present:
                # Token present without explicit error indicates successful session
                is_success = True

        if not is_success:
            err_msg = (
                json_data.get("message")
                or json_data.get("errorMessage")
                or json_data.get("error")
                or "Invalid credentials or unauthorized login"
            )
            logger.warning("Innervex authentication rejected by server: %s", err_msg)
            raise AuthenticationError(f"Innervex authentication failed: {err_msg}")

        # 7. Check session cookies
        cookie_name = self.config.auth.session_cookie_name
        has_session_cookie = cookie_name in session.cookies or len(session.cookies) > 0

        # 8. Extract safe profile metadata
        user_info = json_data.get("user") or json_data.get("profile") or {}
        if not isinstance(user_info, dict):
            user_info = {}

        resolved_username = (
            user_info.get("username")
            or user_info.get("name")
            or json_data.get("username")
            or username
        )
        resolved_user_id = str(user_info.get("userId") or json_data.get("userId") or "") or None
        resolved_branch = user_info.get("branch") or json_data.get("branch")

        showrooms = json_data.get("showrooms") or user_info.get("showrooms") or []
        if not isinstance(showrooms, list):
            showrooms = []

        # 9. Create safe AuthResult (never exposes token in repr or serialization)
        auth_result = AuthResult(
            authenticated=True,
            username=str(resolved_username),
            user_id=resolved_user_id,
            branch=str(resolved_branch) if resolved_branch else None,
            available_showrooms=[str(s) for s in showrooms],
            token_present=token_present,
            cookies_present=has_session_cookie,
            http_status=response.status_code,
            status_message=json_data.get("message"),
        )

        if token_present:
            auth_result._token = str(token).strip()

        self._auth_result = auth_result
        logger.info(
            "Innervex authentication successful (token_present=%s, session_cookie_present=%s)",
            token_present,
            has_session_cookie,
        )
        return auth_result

    def logout(self, session: Optional[requests.Session] = None) -> None:
        """Clear cookies and authentication state."""
        if session:
            session.cookies.clear()
        self._auth_result = None
        logger.info("Innervex session cleared / logged out.")


def create_auth_strategy(config: InnervexConfig) -> AuthStrategy:
    """Factory function instantiating the confirmed Innervex authentication strategy."""
    return InnervexAuthenticator(config)
