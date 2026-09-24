"""Tests for security and credential redaction."""

from __future__ import annotations

from vcf_ops_telegraf_helper.security.redaction import REDACTED_PLACEHOLDER, redact_secrets


def test_redact_explicit_secrets():
    """Verify explicit secret tokens and passwords are fully masked."""
    text = "Connecting with user root and token secret_token_xyz to cloud proxy"
    redacted = redact_secrets(text, secrets=["secret_token_xyz"])
    assert "secret_token_xyz" not in redacted
    assert REDACTED_PLACEHOLDER in redacted


def test_redact_pattern_matches():
    """Verify common CLI patterns and JSON credential blocks are redacted."""
    cli_cmd = "/bin/bash telegraf-utils.sh -t auth_secret_token_999 -v 10.10.10.1 -c 10.10.10.2"
    redacted = redact_secrets(cli_cmd)
    assert "auth_secret_token_999" not in redacted
    assert "-t [REDACTED]" in redacted

    bearer_header = "Authorization: Bearer super_secret_jwt_token_here"
    redacted_bearer = redact_secrets(bearer_header)
    assert "super_secret_jwt_token_here" not in redacted_bearer
    assert "Bearer [REDACTED]" in redacted_bearer

    url_with_creds = "https://admin:my_secret_password@vcf-ops.local/suite-api"
    redacted_url = redact_secrets(url_with_creds)
    assert "my_secret_password" not in redacted_url
    assert "https://admin:[REDACTED]@vcf-ops.local/suite-api" == redacted_url

    key_block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...\n-----END RSA PRIVATE KEY-----"
    assert redact_secrets(key_block) == REDACTED_PLACEHOLDER


def test_no_over_redaction_on_standard_flags():
    """Verify that common admin commands such as mkdir -p and systemctl -t are NOT redacted."""
    cmd1 = "mkdir -p /etc/telegraf/telegraf.d"
    assert redact_secrets(cmd1) == cmd1

    cmd2 = "systemctl -t service --all"
    assert redact_secrets(cmd2) == cmd2


def test_redact_empty_or_clean_text():
    """Verify text without secrets is unmodified."""
    clean = "Everything is running smoothly on web01"
    assert redact_secrets(clean) == clean
    assert redact_secrets("") == ""
