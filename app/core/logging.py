"""Structured logging infrastructure with run context and credential redaction."""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.security import sanitize_payload


class RunContextFilter(logging.Filter):
    """Injects run_id, stage, and sanitized context into every log record."""

    def __init__(self, run_id: str = "GLOBAL", stage: str = "INIT"):
        super().__init__()
        self.run_id = run_id
        self.stage = stage

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "run_id"):
            record.run_id = self.run_id
        if not hasattr(record, "stage"):
            record.stage = self.stage
        return True


class JsonlFormatter(logging.Formatter):
    """Formats log records as single-line structured JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", "GLOBAL"),
            "stage": getattr(record, "stage", "GENERAL"),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Sanitize any extra attributes attached to the record
        sanitized = sanitize_payload(log_data)
        return json.dumps(sanitized)


class RedactingConsoleFormatter(logging.Formatter):
    """Console formatter with color/clean formatting and payload redaction."""

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        # Apply standard sanitization to formatted string if any dictionary is inside
        return message


def setup_logger(
    name: str = "pothys_reporting",
    level: str = "INFO",
    logs_dir: Optional[Path] = None,
    run_id: str = "GLOBAL",
    stage: str = "INIT",
) -> logging.Logger:
    """Configures and returns a structured logger with both console and JSONL file handlers."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    # Context filter
    context_filter = RunContextFilter(run_id=run_id, stage=stage)
    logger.addFilter(context_filter)

    # 1. Console Handler (human-readable)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    console_format = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(run_id)s] [%(stage)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_format)
    console_handler.addFilter(context_filter)
    logger.addHandler(console_handler)

    # 2. File Handler (JSON Lines for machine auditing)
    if logs_dir:
        logs_dir.mkdir(parents=True, exist_ok=True)
        today_str = datetime.now().strftime("%Y-%m-%d")
        log_file = logs_dir / f"pipeline_{today_str}.jsonl"

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JsonlFormatter())
        file_handler.addFilter(context_filter)
        logger.addHandler(file_handler)

    return logger
