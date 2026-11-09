"""Tests for third-party license notices generation and validation."""

from __future__ import annotations

import pytest

from scripts.generate_notices import (
    collect_bundled_distributions,
    generate_notices_text,
    verify_notices,
)
from vcf_ops_telegraf_helper.notices import get_third_party_notices


def test_get_third_party_notices():
    """Verify runtime notice loader returns non-empty valid content."""
    text = get_third_party_notices()
    assert len(text) > 5000
    assert "THIRD-PARTY SOFTWARE NOTICES AND LICENSES" in text
    assert "SOURCE CODE AVAILABILITY AND RELINKING INSTRUCTIONS" in text
    assert "GNU LESSER GENERAL PUBLIC LICENSE Version 3" in text
    assert "GNU LESSER GENERAL PUBLIC LICENSE Version 2.1" in text
    assert "https://github.com/sentania-labs/vcf-ops-telegraf-helper" in text


def test_collect_bundled_distributions():
    """Verify distribution collector finds all critical runtime dependencies."""
    dists = collect_bundled_distributions()
    names = {n.lower().replace("-", "_") for n in dists}
    expected = {
        "pyside6",
        "paramiko",
        "cryptography",
        "truststore",
        "pywinrm",
        "click",
        "rich",
        "pydantic",
        "requests",
        "tzdata",
    }
    for exp in expected:
        assert exp in names, f"Expected {exp} in bundled distributions"


def test_generate_and_verify_notices():
    """Verify generator produces valid notices that pass verification."""
    dists = collect_bundled_distributions()
    text = generate_notices_text(dists)
    # verify_notices should not raise
    verify_notices(text, dists)


def test_verify_notices_fails_when_license_missing():
    """Verify verify_notices raises an error when a distribution has no license text."""
    dists = collect_bundled_distributions()
    text = generate_notices_text(dists)

    click_ver = dists["click"].version
    target = f"Package: click {click_ver}\nLicense: BSD-3-Clause\n" + ("-" * 80)
    replacement = f"{target}\n[NO LICENSE TEXT LOCATED]"
    corrupted = text.replace(target, replacement)
    with pytest.raises(RuntimeError) as exc_info:
        verify_notices(corrupted, dists)
    assert "has no license text" in str(exc_info.value)


def test_verify_notices_fails_when_source_pointer_missing():
    """Verify verify_notices raises an error when source pointer is missing."""
    dists = collect_bundled_distributions()
    text = generate_notices_text(dists)

    corrupted = text.replace("https://github.com/sentania-labs/vcf-ops-telegraf-helper", "https://example.com")
    with pytest.raises(RuntimeError) as exc_info:
        verify_notices(corrupted, dists)
    assert "Missing repository source pointer URL" in str(exc_info.value)
