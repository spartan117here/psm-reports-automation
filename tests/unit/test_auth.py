"""
Unit tests for Innervex authentication, session management, and secret redaction.
All tests use mocked HTTP responses and fake credentials. Real servers are never touched.
"""

import json
import os
from unittest.mock import MagicMock, patch
import pytest
import requests

from app.core.config import InnervexConfig
from app.core.exceptions import AuthenticationError, InnervexConnectionError
from app.innervex.auth import AuthResult, InnervexAuthenticator


@pytest.fixture
def innervex_config() -> InnervexConfig:
    """Fixture providing standard Innervex configuration."""
    return InnervexConfig(
        base_url="http://192.168.5.213:4499",
        timeout_seconds=5,
    )


@pytest.fixture
def authenticator(innervex_config: InnervexConfig) -> InnervexAuthenticator:
    """Fixture providing InnervexAuthenticator instance."""
    return InnervexAuthenticator(innervex_config)


def test_auth_missing_credentials_raises_error(authenticator: InnervexAuthenticator, monkeypatch):
    """Verify AuthenticationError is raised if environment variables are missing."""
    monkeypatch.delenv("INNERVEX_USERNAME", raising=False)
    monkeypatch.delenv("INNERVEX_PASSWORD", raising=False)

    with pytest.raises(AuthenticationError) as exc_info:
        authenticator.login()
    assert "Missing required Innervex credentials" in str(exc_info.value)


def test_auth_successful_login(authenticator: InnervexAuthenticator, monkeypatch):
    """Test 1 & 6 & 7: Verify successful login with mock response, token, and cookies."""
    monkeypatch.setenv("INNERVEX_USERNAME", "test_user")
    monkeypatch.setenv("INNERVEX_PASSWORD", "test_secret_pass")

    fake_response = MagicMock(spec=requests.Response)
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "status": True,
        "token": "fake-jwt-token-xyz123",
        "user": {
            "username": "test_user",
            "userId": "U1001",
            "branch": "CHROMEPET",
            "showrooms": ["CPT", "PAD", "TVL"],
        },
        "message": "Login successful",
    }

    session = requests.Session()
    session.cookies.set("JSESSIONID", "mock-session-cookie-val", domain="192.168.5.213")

    with patch.object(session, "post", return_value=fake_response) as mock_post:
        result = authenticator.login(session=session)

        # 1. Verify request payload
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["data"]["username"] == "test_user"
        assert call_kwargs["data"]["password"] == "test_secret_pass"
        assert call_kwargs["data"]["Action"] == "Sign in"

        # 2. Verify result metadata
        assert result.authenticated is True
        assert result.username == "test_user"
        assert result.user_id == "U1001"
        assert result.branch == "CHROMEPET"
        assert result.available_showrooms == ["CPT", "PAD", "TVL"]
        assert result.token_present is True
        assert result.cookies_present is True
        assert result.http_status == 200

        # 3. Verify token is accessible via method
        assert result.get_token() == "fake-jwt-token-xyz123"

        # 4. Verify authenticator state
        assert authenticator.is_authenticated(session) is True


def test_auth_failed_login_status_false(authenticator: InnervexAuthenticator, monkeypatch):
    """Test 2: Verify login failure when JSON status is false."""
    monkeypatch.setenv("INNERVEX_USERNAME", "test_user")
    monkeypatch.setenv("INNERVEX_PASSWORD", "wrong_password")

    fake_response = MagicMock(spec=requests.Response)
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "status": False,
        "error": "Invalid username or password",
    }

    session = requests.Session()
    with patch.object(session, "post", return_value=fake_response):
        with pytest.raises(AuthenticationError) as exc_info:
            authenticator.login(session=session)
        assert "Invalid username or password" in str(exc_info.value)
        assert authenticator.is_authenticated(session) is False


def test_auth_http_error(authenticator: InnervexAuthenticator, monkeypatch):
    """Test 3: Verify HTTP 401 or 500 triggers AuthenticationError."""
    monkeypatch.setenv("INNERVEX_USERNAME", "test_user")
    monkeypatch.setenv("INNERVEX_PASSWORD", "test_pass")

    fake_response = MagicMock(spec=requests.Response)
    fake_response.status_code = 401

    session = requests.Session()
    with patch.object(session, "post", return_value=fake_response):
        with pytest.raises(AuthenticationError) as exc_info:
            authenticator.login(session=session)
        assert "HTTP 401" in str(exc_info.value)


def test_auth_network_connection_error(authenticator: InnervexAuthenticator, monkeypatch):
    """Verify network drop raises InnervexConnectionError."""
    monkeypatch.setenv("INNERVEX_USERNAME", "test_user")
    monkeypatch.setenv("INNERVEX_PASSWORD", "test_pass")

    session = requests.Session()
    with patch.object(session, "post", side_effect=requests.exceptions.ConnectTimeout("Connection timed out")):
        with pytest.raises(InnervexConnectionError):
            authenticator.login(session=session)


def test_auth_malformed_json_response(authenticator: InnervexAuthenticator, monkeypatch):
    """Test 4: Verify non-JSON response body raises AuthenticationError."""
    monkeypatch.setenv("INNERVEX_USERNAME", "test_user")
    monkeypatch.setenv("INNERVEX_PASSWORD", "test_pass")

    fake_response = MagicMock(spec=requests.Response)
    fake_response.status_code = 200
    fake_response.json.side_effect = ValueError("Invalid JSON")

    session = requests.Session()
    with patch.object(session, "post", return_value=fake_response):
        with pytest.raises(AuthenticationError) as exc_info:
            authenticator.login(session=session)
        assert "not valid JSON" in str(exc_info.value)


def test_auth_response_without_success_or_token(authenticator: InnervexAuthenticator, monkeypatch):
    """Test 5: Response is JSON but lacks success flag or token."""
    monkeypatch.setenv("INNERVEX_USERNAME", "test_user")
    monkeypatch.setenv("INNERVEX_PASSWORD", "test_pass")

    fake_response = MagicMock(spec=requests.Response)
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "status": "pending_verification",
        "message": "User not active",
    }

    session = requests.Session()
    with patch.object(session, "post", return_value=fake_response):
        with pytest.raises(AuthenticationError) as exc_info:
            authenticator.login(session=session)
        assert "User not active" in str(exc_info.value)


def test_auth_secret_redaction():
    """Test 8: Ensure token and credentials are never leaked in repr, str, or dict dumps."""
    secret_token = "TOP_SECRET_JWT_TOKEN_NEVER_LEAK"
    result = AuthResult(
        authenticated=True,
        username="super_user",
        branch="CHROMEPET",
        token_present=True,
        cookies_present=True,
    )
    result._token = secret_token

    # 1. str() and repr() must NOT leak token
    result_repr = repr(result)
    result_str = str(result)
    assert secret_token not in result_repr
    assert secret_token not in result_str

    # 2. model_dump() must NOT leak token
    dump = result.model_dump()
    assert "_token" not in dump
    assert "token" not in dump
    assert secret_token not in json.dumps(dump)

    # 3. diagnostics() must be clean
    diag = result.diagnostics()
    assert diag["authenticated"] is True
    assert diag["token_present"] is True
    assert secret_token not in str(diag)


def test_auth_logout(authenticator: InnervexAuthenticator, monkeypatch):
    """Verify logout clears session cookies and internal state."""
    monkeypatch.setenv("INNERVEX_USERNAME", "test_user")
    monkeypatch.setenv("INNERVEX_PASSWORD", "test_pass")

    session = requests.Session()
    session.cookies.set("JSESSIONID", "val123")

    fake_response = MagicMock(spec=requests.Response)
    fake_response.status_code = 200
    fake_response.json.return_value = {"status": True, "token": "tok"}

    with patch.object(session, "post", return_value=fake_response):
        authenticator.login(session=session)
        assert authenticator.is_authenticated(session) is True

        authenticator.logout(session=session)
        assert authenticator.is_authenticated(session) is False
        assert len(session.cookies) == 0
