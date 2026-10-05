"""Unit tests for Phase 1B Innervex report extraction, validation, and storage."""

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.cli import build_parser, cmd_download, parse_target_date
from app.core.config import InnervexConfig, ReportItemConfig
from app.core.exceptions import (
    AuthenticationError,
    InnervexConnectionError,
    InnervexResponseError,
)
from app.core.models import RawReportPayload
from app.innervex.auth import InnervexAuthenticator
from app.innervex.client import InnervexClient
from app.innervex.reports import SchemeReportFetcher
from app.innervex.schemas import InnervexRawResponse
from app.repositories.filesystem import FileSystemReportRepository


@pytest.fixture
def mock_innervex_config():
    return InnervexConfig(
        base_url="http://192.168.5.213:4499",
        auth_strategy="form",
        login_path="/login.ispx",
        signin_path="/signin.ispx",
        login_action="Sign in",
        username="test_user",
        password="secret_password_123",
        timeout_seconds=5,
        max_retries=1,
        backoff_factor=0.1,
        default_headers={"User-Agent": "PothysReportBot/1.0"},
        endpoints={"scheme_new_customer": "/schemeNewCustRep"},
    )


@pytest.fixture
def mock_report_config():
    return ReportItemConfig(
        report_id="subhiksham",
        display_name="New Swarna Subhiksham",
        endpoint_key="scheme_new_customer",
        innervex_action="FindSchemeTransactionReport",
        scheme_name="NEW SWARNA SUBHIKSHAM",
        subschemes=[
            "NEW SWARNA SUBHIKSHAM",
            "SWARNALAKSHMI JEWELLERY PURCHASE PLAN W",
            "SWARNA SUBHIKSHAM FLEXI",
            "SWARNA LABHAM SUPER FLEXI",
        ],
        emp_code_filter="All",
        promo_code_filter="All",
        export_mode="EXPORT",
        target_sheet_tab_prefix="Subhiksham",
    )


# 1. Successful report request
def test_successful_report_request(mock_innervex_config, mock_report_config):
    mock_auth = MagicMock(spec=InnervexAuthenticator)
    mock_auth.is_authenticated.return_value = True

    client = InnervexClient(mock_innervex_config, auth_strategy=mock_auth)
    fetcher = SchemeReportFetcher(client)

    sample_data = [
        {"SCHEME": "NEW SWARNA SUBHIKSHAM", "CUST_NAME": "CUST_A", "AMOUNT": 1000},
        {"SCHEME": "NEW SWARNA SUBHIKSHAM", "CUST_NAME": "CUST_B", "AMOUNT": 2000},
    ]

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({"data": sample_data, "screenName": "Scheme New Member List"})
    mock_resp.json.return_value = {"data": sample_data, "screenName": "Scheme New Member List"}

    with patch.object(client.session, "post", return_value=mock_resp):
        payload = fetcher.fetch_report(
            config=mock_report_config,
            report_date=date(2026, 10, 4),
            showroom_codes=["APP", "CBE"],
        )

    assert payload.report_id == "subhiksham"
    assert payload.report_date == date(2026, 10, 4)
    assert payload.row_count == 2
    assert len(payload.data) == 2
    assert payload.raw_headers == ["SCHEME", "CUST_NAME", "AMOUNT"]
    assert payload.raw_response["screenName"] == "Scheme New Member List"


# 2. Authentication failure
def test_authentication_failure(mock_innervex_config, mock_report_config):
    mock_auth = MagicMock(spec=InnervexAuthenticator)
    mock_auth.is_authenticated.return_value = False
    mock_auth.authenticate.side_effect = AuthenticationError(
        "Invalid credentials", details={"status_code": 401}
    )

    client = InnervexClient(mock_innervex_config, auth_strategy=mock_auth)
    fetcher = SchemeReportFetcher(client)

    with pytest.raises(AuthenticationError) as exc_info:
        fetcher.fetch_report(
            config=mock_report_config,
            report_date=date(2026, 10, 4),
        )
    assert "Invalid credentials" in str(exc_info.value)


# 2b. Session expired detected in response JSON
def test_session_expired_in_response(mock_innervex_config, mock_report_config):
    mock_auth = MagicMock(spec=InnervexAuthenticator)
    mock_auth.is_authenticated.return_value = True

    client = InnervexClient(mock_innervex_config, auth_strategy=mock_auth)
    fetcher = SchemeReportFetcher(client)

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.text = '{"Message": "Session Expired", "Success": false}'
    mock_resp.json.return_value = {"Message": "Session Expired", "Success": False}

    with patch.object(client.session, "post", return_value=mock_resp):
        with pytest.raises(AuthenticationError) as exc_info:
            fetcher.fetch_report(
                config=mock_report_config,
                report_date=date(2026, 10, 4),
            )
        assert "Session Expired" in str(exc_info.value)


# 3. HTTP failure & Network connection failure
def test_http_failure_500(mock_innervex_config, mock_report_config):
    mock_auth = MagicMock(spec=InnervexAuthenticator)
    mock_auth.is_authenticated.return_value = True

    client = InnervexClient(mock_innervex_config, auth_strategy=mock_auth)
    fetcher = SchemeReportFetcher(client)

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    with patch.object(client.session, "post", return_value=mock_resp):
        with pytest.raises(InnervexResponseError) as exc_info:
            fetcher.fetch_report(
                config=mock_report_config,
                report_date=date(2026, 10, 4),
            )
        assert "HTTP 500" in str(exc_info.value)


def test_network_connection_failure(mock_innervex_config, mock_report_config):
    mock_auth = MagicMock(spec=InnervexAuthenticator)
    mock_auth.is_authenticated.return_value = True

    client = InnervexClient(mock_innervex_config, auth_strategy=mock_auth)
    fetcher = SchemeReportFetcher(client)

    with patch.object(
        client.session, "post", side_effect=requests.exceptions.ConnectionError("Connection refused")
    ):
        with pytest.raises(InnervexConnectionError) as exc_info:
            fetcher.fetch_report(
                config=mock_report_config,
                report_date=date(2026, 10, 4),
            )
        assert "Failed to connect to Innervex" in str(exc_info.value)


# 4. Empty response body
def test_empty_response_body(mock_innervex_config, mock_report_config):
    mock_auth = MagicMock(spec=InnervexAuthenticator)
    mock_auth.is_authenticated.return_value = True

    client = InnervexClient(mock_innervex_config, auth_strategy=mock_auth)
    fetcher = SchemeReportFetcher(client)

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.text = ""

    with patch.object(client.session, "post", return_value=mock_resp):
        with pytest.raises(InnervexResponseError) as exc_info:
            fetcher.fetch_report(
                config=mock_report_config,
                report_date=date(2026, 10, 4),
            )
        assert "empty response" in str(exc_info.value).lower()


# 5. Invalid JSON response
def test_invalid_json_response(mock_innervex_config, mock_report_config):
    mock_auth = MagicMock(spec=InnervexAuthenticator)
    mock_auth.is_authenticated.return_value = True

    client = InnervexClient(mock_innervex_config, auth_strategy=mock_auth)
    fetcher = SchemeReportFetcher(client)

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.text = "<html><body>502 Bad Gateway</body></html>"
    mock_resp.json.side_effect = ValueError("Invalid JSON")

    with patch.object(client.session, "post", return_value=mock_resp):
        with pytest.raises(InnervexResponseError) as exc_info:
            fetcher.fetch_report(
                config=mock_report_config,
                report_date=date(2026, 10, 4),
            )
        assert "not valid JSON" in str(exc_info.value)


# 6. Missing data field in JSON
def test_missing_data_field(mock_innervex_config, mock_report_config):
    mock_auth = MagicMock(spec=InnervexAuthenticator)
    mock_auth.is_authenticated.return_value = True

    client = InnervexClient(mock_innervex_config, auth_strategy=mock_auth)
    fetcher = SchemeReportFetcher(client)

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({"status": "success", "screenName": "Scheme New Member List"})
    mock_resp.json.return_value = {"status": "success", "screenName": "Scheme New Member List"}

    with patch.object(client.session, "post", return_value=mock_resp):
        with pytest.raises(InnervexResponseError) as exc_info:
            fetcher.fetch_report(
                config=mock_report_config,
                report_date=date(2026, 10, 4),
            )
        assert "'data' key" in str(exc_info.value)


# 7. data field is not an array/list
def test_data_field_not_array(mock_innervex_config, mock_report_config):
    mock_auth = MagicMock(spec=InnervexAuthenticator)
    mock_auth.is_authenticated.return_value = True

    client = InnervexClient(mock_innervex_config, auth_strategy=mock_auth)
    fetcher = SchemeReportFetcher(client)

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({"data": "string_instead_of_list"})
    mock_resp.json.return_value = {"data": "string_instead_of_list"}

    with patch.object(client.session, "post", return_value=mock_resp):
        with pytest.raises(InnervexResponseError) as exc_info:
            fetcher.fetch_report(
                config=mock_report_config,
                report_date=date(2026, 10, 4),
            )
        assert "must be an array/list" in str(exc_info.value)


# 8. Correct request payload construction
def test_correct_request_payload_construction(mock_report_config):
    showrooms = ["APP", "CBE", "CPT"]
    target_date = date(2026, 10, 4)

    payload = SchemeReportFetcher.build_payload(
        config=mock_report_config,
        report_date=target_date,
        showroom_codes=showrooms,
    )

    form_dict = payload.to_form_dict()
    assert form_dict["Action"] == "FindSchemeTransactionReport"
    assert form_dict["fromDate"] == "2026-10-04"
    assert form_dict["toDate"] == "2026-10-04"
    assert form_dict["Showroom"] == "'APP','CBE','CPT'"
    assert form_dict["Scheme"] == "NEW SWARNA SUBHIKSHAM"
    assert (
        form_dict["SubScheme"]
        == "'NEW SWARNA SUBHIKSHAM','SWARNALAKSHMI JEWELLERY PURCHASE PLAN W','SWARNA SUBHIKSHAM FLEXI','SWARNA LABHAM SUPER FLEXI'"
    )
    assert form_dict["EmpCode"] == "All"
    assert form_dict["PromoCode"] == "All"
    assert form_dict["Export"] == "EXPORT"


# 9. Correct report date handling
def test_report_date_parsing():
    # Explicit date
    parsed = parse_target_date("2026-10-04")
    assert parsed == date(2026, 10, 4)

    # Default to yesterday
    default_date = parse_target_date(None)
    assert default_date == date.today() - timedelta(days=1)

    # Invalid format raises exit
    with pytest.raises(SystemExit):
        parse_target_date("04-10-2026")


# 10. Raw output path and collision preservation
def test_raw_output_path_and_collision(tmp_path: Path):
    repo = FileSystemReportRepository(tmp_path)
    target_date = date(2026, 10, 4)

    payload = RawReportPayload(
        report_id="subhiksham",
        report_date=target_date,
        row_count=1,
        data=[{"COL1": "VAL1"}],
        raw_headers=["COL1"],
        raw_response={"data": [{"COL1": "VAL1"}], "screenName": "Scheme New Member List"},
    )

    # Save first time
    saved_path = repo.save_raw_report(payload)
    expected_path = tmp_path / "raw" / "2026" / "10" / "04" / "innervex_subhiksham_memberlist_2026-10-04.json"
    assert saved_path == expected_path
    assert saved_path.exists()

    with open(saved_path, "r", encoding="utf-8") as f:
        saved_data = json.load(f)
    assert "data" in saved_data
    assert saved_data["data"][0]["COL1"] == "VAL1"
    assert saved_data["screenName"] == "Scheme New Member List"

    # Save second time: should NOT overwrite, should use collision filename with timestamp
    second_path = repo.save_raw_report(payload)
    assert second_path != saved_path
    assert second_path.exists()
    assert "innervex_subhiksham_memberlist_2026-10-04_" in second_path.name


# 11. No secret leakage and safe CLI output formatting
def test_cli_download_no_secret_leakage(monkeypatch, capsys, tmp_path: Path):
    from unittest.mock import MagicMock, patch
    from app.innervex.auth import AuthResult

    monkeypatch.setenv("INNERVEX_USERNAME", "SECRET_AGENT_USER")
    monkeypatch.setenv("INNERVEX_PASSWORD", "SUPER_SECRET_PASS_999")

    # Mock login and report response
    auth_result = AuthResult(
        authenticated=True,
        http_status=200,
        cookies_present=True,
        token_present=True,
        token="SECRET_TOKEN_ABC_123",
    )

    sample_report_json = {
        "data": [{"SCHEME": "NEW SWARNA SUBHIKSHAM", "CUST_NAME": "Sensitive Customer Name"}],
        "screenName": "Scheme New Member List",
    }

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.text = json.dumps(sample_report_json)
    mock_resp.json.return_value = sample_report_json

    parser = build_parser()
    args = parser.parse_args(["download", "--report-date", "2026-10-04"])

    mock_saved_path = Path("data/raw/2026/10/04/innervex_subhiksham_memberlist_2026-10-04.json")
    with patch("app.innervex.auth.InnervexAuthenticator.login", return_value=auth_result), \
         patch("app.innervex.client.InnervexClient.post_report", return_value=InnervexRawResponse(**sample_report_json)), \
         patch("app.repositories.filesystem.FileSystemReportRepository.save_raw_report", return_value=mock_saved_path):
        exit_code = cmd_download(args)

    assert exit_code == 0
    captured = capsys.readouterr()
    output = captured.out

    # Check required output fields
    assert "Innervex Scheme Memberlist Download" in output
    assert "Report: Subhiksham" in output
    assert "Report date: 2026-10-04" in output
    assert "HTTP status: 200" in output
    assert "Response format: JSON" in output
    assert "Records received: 1" in output
    assert "Raw response saved: data/raw/2026/10/04/innervex_subhiksham_memberlist_2026-10-04" in output
    assert "Status: SUCCESS" in output

    # CRITICAL: Verify NO secret leakage and NO customer PII leakage in CLI output
    assert "SECRET_AGENT_USER" not in output
    assert "SUPER_SECRET_PASS_999" not in output
    assert "SECRET_TOKEN_ABC_123" not in output
    assert "JSESSIONID" not in output
    assert "Sensitive Customer Name" not in output
