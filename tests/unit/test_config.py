"""Unit tests for configuration loading and validation."""

from pathlib import Path
import pytest

from app.core.config import AppConfigBundle, load_config
from app.core.exceptions import ConfigurationError


def test_load_config_success(config_bundle: AppConfigBundle):
    """Verify that configuration loads and validates all sections."""
    assert config_bundle.app.name == "pothys-reporting-automation"
    assert config_bundle.app.timezone == "Asia/Kolkata"

    # Innervex config
    assert config_bundle.innervex.base_url == "http://192.168.5.213:4499"
    assert config_bundle.innervex.timeout_seconds == 60

    # Reports config
    assert "subhiksham" in config_bundle.reports.reports
    assert "viruksham" in config_bundle.reports.reports
    subhiksham = config_bundle.get_report("subhiksham")
    assert subhiksham.raw_expected_column_count == 21
    assert len(subhiksham.output_columns) == 11

    # Mappings config
    assert config_bundle.mappings.employee_to_branch["CPT"] == "CHROMEPET"
    assert config_bundle.mappings.employee_to_branch["TVL"] == "TIRUNELVELI"

    # Targets config
    assert config_bundle.targets.q2["CPT"] == 216000000.0
    assert config_bundle.targets.h1_multiplier == 2.0


def test_get_unknown_report_raises_error(config_bundle: AppConfigBundle):
    """Verify querying an unconfigured report raises ConfigurationError."""
    with pytest.raises(ConfigurationError):
        config_bundle.get_report("non_existent_report")


def test_telegram_config_defaults(tmp_path: Path, monkeypatch):
    """Verify default Telegram configuration when environment variables and .env are unset."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "app.yaml").write_text("app: {}\ntelegram:\n  enabled: false\n  dry_run: true\n", encoding="utf-8")
    (config_dir / "innervex.yaml").write_text("innervex: {}\n", encoding="utf-8")
    (config_dir / "reports.yaml").write_text("reports: {}\n", encoding="utf-8")
    (config_dir / "mappings.yaml").write_text("mappings: {}\n", encoding="utf-8")
    (config_dir / "targets.yaml").write_text("targets: {}\n", encoding="utf-8")

    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
    monkeypatch.delenv("TELEGRAM_DRY_RUN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    bundle = load_config(tmp_path)
    assert bundle.app.telegram.enabled is False
    assert bundle.app.telegram.dry_run is True
    assert bundle.app.telegram.bot_token is None
    assert bundle.app.telegram.chat_id is None


def test_telegram_config_env_overrides(tmp_path: Path, monkeypatch):
    """Verify that TELEGRAM_* environment variables override defaults in TelegramConfig."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "app.yaml").write_text("app: {}\ntelegram:\n  enabled: false\n  dry_run: true\n", encoding="utf-8")
    (config_dir / "innervex.yaml").write_text("innervex: {}\n", encoding="utf-8")
    (config_dir / "reports.yaml").write_text("reports: {}\n", encoding="utf-8")
    (config_dir / "mappings.yaml").write_text("mappings: {}\n", encoding="utf-8")
    (config_dir / "targets.yaml").write_text("targets: {}\n", encoding="utf-8")

    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    monkeypatch.setenv("TELEGRAM_TIMEOUT_SECONDS", "45")

    bundle = load_config(tmp_path)
    assert bundle.app.telegram.enabled is True
    assert bundle.app.telegram.dry_run is False
    assert bundle.app.telegram.bot_token == "123456:TEST_TOKEN"
    assert bundle.app.telegram.chat_id == "987654321"
    assert bundle.app.telegram.timeout_seconds == 45


def test_telegram_config_loads_from_dotenv(tmp_path: Path, monkeypatch):
    """Regression test: verify that load_config loads .env settings into TelegramConfig."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)

    (config_dir / "app.yaml").write_text("app: {}\ntelegram:\n  enabled: false\n  dry_run: true\n", encoding="utf-8")
    (config_dir / "innervex.yaml").write_text("innervex: {}\n", encoding="utf-8")
    (config_dir / "reports.yaml").write_text("reports: {}\n", encoding="utf-8")
    (config_dir / "mappings.yaml").write_text("mappings: {}\n", encoding="utf-8")
    (config_dir / "targets.yaml").write_text("targets: {}\n", encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_ENABLED=true\n"
        "TELEGRAM_DRY_RUN=false\n"
        "TELEGRAM_BOT_TOKEN=dummy_token_123\n"
        "TELEGRAM_CHAT_ID=1688169109\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
    monkeypatch.delenv("TELEGRAM_DRY_RUN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    bundle = load_config(tmp_path)
    assert bundle.app.telegram.enabled is True
    assert bundle.app.telegram.dry_run is False
    assert bundle.app.telegram.bot_token == "dummy_token_123"
    assert bundle.app.telegram.chat_id == "1688169109"

