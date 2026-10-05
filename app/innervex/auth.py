"""Authentication strategy interfaces and adapters for Innervex."""

from abc import ABC, abstractmethod
import logging
from typing import Optional
import requests

from app.core.config import InnervexConfig
from app.core.exceptions import AuthenticationError

logger = logging.getLogger("pothys_reporting")


class AuthStrategy(ABC):
    """Abstract authentication strategy for Innervex session establishment."""

    @abstractmethod
    def authenticate(self, session: requests.Session) -> bool:
        """
        Perform legitimate authentication using configured authorized credentials.
        Populates session cookies/headers on success.
        """
        pass

    @abstractmethod
    def is_authenticated(self, session: requests.Session) -> bool:
        """Check whether the provided session contains a valid active authentication."""
        pass


class PlaceholderAuthStrategy(AuthStrategy):
    """
    Phase 0 Placeholder Auth Strategy.

    Prevents any unauthorized or guessed network attempts until the exact
    login endpoint and form parameters are verified in Phase 1.
    """

    def __init__(self, config: InnervexConfig):
        self.config = config

    def authenticate(self, session: requests.Session) -> bool:
        # Intentionally marked as TODO for Phase 1 verification
        msg = (
            "Innervex authentication strategy is in Phase 0 placeholder mode. "
            "Actual authentication endpoint and payload will be verified in Phase 1. "
            "No guessed login endpoints or bypasses are permitted."
        )
        logger.warning(msg)
        raise AuthenticationError(msg)

    def is_authenticated(self, session: requests.Session) -> bool:
        # Check if session cookie is present
        cookie_name = self.config.auth.session_cookie_name
        return cookie_name in session.cookies


def create_auth_strategy(config: InnervexConfig) -> AuthStrategy:
    """Factory function for instantiating the configured authentication strategy."""
    strategy_name = config.auth.strategy.lower()
    if strategy_name == "session_cookie":
        return PlaceholderAuthStrategy(config)
    # Additional legitimate strategies (e.g. form_login) will be registered here in Phase 1
    return PlaceholderAuthStrategy(config)
