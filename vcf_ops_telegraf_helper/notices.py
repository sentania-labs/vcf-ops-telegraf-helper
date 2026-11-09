"""Embedded third-party notices and open-source license texts."""

from __future__ import annotations

import importlib.resources
from pathlib import Path


def get_third_party_notices() -> str:
    """Return the bundled third-party license notices."""
    # 1. Try modern importlib.resources (Python 3.9+)
    try:
        files = importlib.resources.files("vcf_ops_telegraf_helper")
        notices_file = files / "THIRD-PARTY-NOTICES.txt"
        if hasattr(notices_file, "is_file") and notices_file.is_file():
            return notices_file.read_text(encoding="utf-8")
    except Exception:
        pass

    # 2. Try legacy importlib.resources API
    try:
        return importlib.resources.read_text("vcf_ops_telegraf_helper", "THIRD-PARTY-NOTICES.txt", encoding="utf-8")
    except Exception:
        pass

    # 3. Direct relative path fallback (source checkout or PyInstaller unpack)
    pkg_dir = Path(__file__).resolve().parent
    local_path = pkg_dir / "THIRD-PARTY-NOTICES.txt"
    if local_path.is_file():
        return local_path.read_text(encoding="utf-8")

    # 4. Repo root fallback
    repo_root_path = pkg_dir.parent / "THIRD-PARTY-NOTICES.txt"
    if repo_root_path.is_file():
        return repo_root_path.read_text(encoding="utf-8")

    return "THIRD-PARTY-NOTICES.txt not found."
