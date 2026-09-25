"""Tests for persistent logging and credential redaction in helper logs."""

from __future__ import annotations

import logging
from pathlib import Path

from vcf_ops_telegraf_helper.logger import (
    RedactingFormatter,
    get_log_dir,
    get_log_file_path,
    setup_logging,
    get_logger,
)


def test_get_log_dir_fallback(monkeypatch, tmp_path):
    """Verify log directory resolves properly across platforms."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    log_dir = get_log_dir()
    assert log_dir == tmp_path / ".config" / "vcf-ops-telegraf-helper"
    assert get_log_file_path() == log_dir / "helper.log"


def test_get_log_dir_windows(monkeypatch, tmp_path):
    """Verify Windows LOCALAPPDATA environment variable is honored."""
    win_app_data = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(win_app_data))
    monkeypatch.setattr("sys.platform", "win32")
    log_dir = get_log_dir()
    assert log_dir == win_app_data / "vcf-ops-telegraf-helper"


def test_redacting_formatter():
    """Verify passwords and tokens in log messages are redacted."""
    formatter = RedactingFormatter(fmt="%(message)s")
    record = logging.LogRecord(
        name="vcf_ops_telegraf_helper.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Connecting with password=SuperSecretPassword123 and Authorization: Bearer secret-api-token",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert "SuperSecretPassword123" not in formatted
    assert "secret-api-token" not in formatted
    assert "[REDACTED]" in formatted


def test_setup_logging_writes_file(tmp_path, monkeypatch):
    """Verify setup_logging creates the directory and writes logs."""
    test_log_file = tmp_path / "logs" / "helper.log"
    monkeypatch.setattr("vcf_ops_telegraf_helper.logger.get_log_file_path", lambda: test_log_file)
    monkeypatch.setattr("vcf_ops_telegraf_helper.logger._logging_initialized", False)

    logger = logging.getLogger("vcf_ops_telegraf_helper")
    # Clean existing handlers for test
    old_handlers = list(logger.handlers)
    logger.handlers.clear()

    try:
        path = setup_logging()
        assert path == test_log_file
        test_logger = get_logger("unit_test")
        test_logger.info("Test message for logger verification")

        # Flush handlers
        for h in logger.handlers:
            h.flush()

        assert test_log_file.exists()
        content = test_log_file.read_text(encoding="utf-8")
        assert "Test message for logger verification" in content
    finally:
        for h in logger.handlers:
            h.close()
        logger.handlers = old_handlers
