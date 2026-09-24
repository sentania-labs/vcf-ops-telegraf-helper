"""Security and credential redaction utilities.

Ensures credentials, bearer tokens, private keys, and authorization headers
never appear in logs, CLI output, or exported reports.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

REDACTED_PLACEHOLDER = "[REDACTED]"

_SENSITIVE_PATTERNS = [
    # JSON / TOML / YAML credential pairs: password = "...", token: "..."
    re.compile(r'((?:password|token|secret|client_secret)["\']?\s*[:=]\s*["\'])([^"\']+)(["\'])', re.IGNORECASE),
    # Key-value pairs without quotes: password=secret, token=xyz123
    re.compile(r'(\b(?:password|token|secret|auth_token)\s*=\s*)([^\s&"\'<>]+)', re.IGNORECASE),
    # Bearer and Suite API token headers
    re.compile(r'((?:Bearer|vRealizeOpsToken)\s+)([a-zA-Z0-9_\-\.]+)', re.IGNORECASE),
    # Basic Authorization headers
    re.compile(r'(Authorization:\s*Basic\s+)([a-zA-Z0-9+/=]+)', re.IGNORECASE),
    # URLs with embedded user:password credentials
    re.compile(r'(https?://[^:\s/]+:)([^@\s]+)(@)', re.IGNORECASE),
    # Explicit CLI token flags: telegraf-utils.sh -t <token>, --token <token>
    re.compile(r'((?:telegraf-utils\.sh.*-t|--token)\s+)([a-zA-Z0-9_\-\.]+)', re.IGNORECASE),
    # Explicit CLI password flags: --password <pass>, sshpass -p <pass>
    re.compile(r'((?:--password|sshpass\s+-p)\s+)([^\s]+)', re.IGNORECASE),
    # PEM Private Key blocks
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----'),
]


def redact_secrets(text: Optional[str], secrets: Iterable[str] | None = None) -> str:
    """Mask secrets and credential patterns from text.

    Args:
        text: Input string that may contain passwords, tokens, or auth headers.
        secrets: Optional explicit collection of secret values to replace.

    Returns:
        Sanitized string with sensitive data redacted.
    """
    if not text:
        return ""

    result = text

    # Redact explicit secret strings provided by caller
    if secrets:
        for secret in secrets:
            if secret and len(secret) > 2:
                result = result.replace(secret, REDACTED_PLACEHOLDER)

    # Redact regex patterns
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.groups == 3:
            result = pattern.sub(r"\g<1>[REDACTED]\g<3>", result)
        elif pattern.groups == 2:
            result = pattern.sub(r"\g<1>[REDACTED]", result)
        else:
            result = pattern.sub(REDACTED_PLACEHOLDER, result)

    return result
