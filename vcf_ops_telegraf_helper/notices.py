"""Embedded third-party notices and open-source license texts."""

from __future__ import annotations

from importlib.resources import files


def get_third_party_notices() -> str:
    """Return the bundled third-party license notices; raise if the file is missing."""
    return files("vcf_ops_telegraf_helper").joinpath("THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
