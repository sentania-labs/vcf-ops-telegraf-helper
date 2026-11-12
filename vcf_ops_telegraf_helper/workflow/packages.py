"""Download a Telegraf release on this computer, to send to a machine that cannot download it.

InfluxData publishes its releases over HTTPS. The download is checked against the size the
server announced, its SHA-256 is computed here, and the machine checks the copy it received
against that SHA-256 before installing it, so a damaged or cut-short copy is never installed.
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Optional, Tuple

import requests

from vcf_ops_telegraf_helper.logger import get_logger
from vcf_ops_telegraf_helper.storage.state import DEFAULT_STATE_DIR

logger = get_logger("packages")

_fetch_lock = threading.Lock()
RELEASES = "https://dl.influxdata.com/telegraf/releases"
DOWNLOAD_HOST = "dl.influxdata.com"
DEFAULT_PACKAGE_DIR = DEFAULT_STATE_DIR / "packages"


def package_name(version: str, is_windows: bool, is_arm: bool) -> str:
    arch = "arm64" if is_arm else "amd64"
    return f"telegraf-{version}_windows_{arch}.zip" if is_windows else f"telegraf-{version}_linux_{arch}.tar.gz"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_telegraf_package(
    version: str,
    is_windows: bool,
    is_arm: bool = False,
    directory: Optional[Path] = None,
    session: Optional[requests.Session] = None,
) -> Tuple[Path, str]:
    """Return (path, sha256) of the release archive, downloading it unless a complete copy is kept."""
    # Two machines in one run may both need the package: the first downloads it, the second finds it kept
    with _fetch_lock:
        return _fetch(version, is_windows, is_arm, directory, session)


def _fetch(version, is_windows, is_arm, directory, session) -> Tuple[Path, str]:
    directory = Path(directory) if directory else DEFAULT_PACKAGE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    name = package_name(version, is_windows, is_arm)
    target = directory / name
    marker = directory / f"{name}.sha256"
    if target.exists() and marker.exists():
        recorded = marker.read_text(encoding="utf-8").strip()
        if recorded and _sha256(target) == recorded:
            return target, recorded

    http = session or requests.Session()
    url = f"{RELEASES}/{name}"
    logger.info("Downloading %s", url)
    partial = directory / f"{name}.{os.getpid()}.{threading.get_ident()}.part"
    with http.get(url, stream=True, timeout=(10, 60)) as response:
        if response.status_code != 200:
            raise RuntimeError(f"the download site answered HTTP {response.status_code} for {name}")
        expected = response.headers.get("Content-Length")
        digest = hashlib.sha256()
        written = 0
        with open(partial, "wb") as handle:
            for block in response.iter_content(chunk_size=1024 * 1024):
                if block:
                    handle.write(block)
                    digest.update(block)
                    written += len(block)
    if expected and expected.isdigit() and int(expected) != written:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"the download of {name} was cut short ({written} of {expected} bytes)")
    if written == 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"the download of {name} was empty")
    os.replace(partial, target)
    sha256 = digest.hexdigest()
    marker.write_text(sha256, encoding="utf-8")
    return target, sha256
