"""Persistent file logging with automatic credential redaction."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from typing import Optional

from vcf_ops_telegraf_helper.security.redaction import redact_secrets


def get_log_dir() -> Path:
    """Determine the standard local directory for application logs."""
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "vcf-ops-telegraf-helper"
    return Path.home() / ".config" / "vcf-ops-telegraf-helper"


def get_log_file_path() -> Path:
    """Return the absolute path to the rotating log file."""
    return get_log_dir() / "helper.log"


class RedactingFormatter(logging.Formatter):
    """Logging formatter that automatically scrubs passwords, keys, and tokens."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return redact_secrets(formatted)


_logging_initialized = False


def setup_logging(level: int = logging.INFO) -> Path:
    """Configure rotating file logging with security redaction."""
    global _logging_initialized
    log_file = get_log_file_path()

    if _logging_initialized:
        return log_file

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        formatter = RedactingFormatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.setLevel(level)

        root = logging.getLogger("vcf_ops_telegraf_helper")
        root.setLevel(level)
        root.addHandler(handler)
        _logging_initialized = True
        root.info("Logging initialized at %s", log_file)
    except Exception as exc:
        print(f"Warning: Failed to initialize file logging at {log_file}: {exc}", file=sys.stderr)

    return log_file


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a namespaced logger under vcf_ops_telegraf_helper."""
    if not _logging_initialized:
        setup_logging()
    if name:
        return logging.getLogger(f"vcf_ops_telegraf_helper.{name}")
    return logging.getLogger("vcf_ops_telegraf_helper")
