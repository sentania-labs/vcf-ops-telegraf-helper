"""General utilities including local timezone formatting and version handling."""

from __future__ import annotations

from datetime import datetime
import enum
import re
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

CHICAGO_TZ = ZoneInfo("America/Chicago")


class UpgradeDecision(str, enum.Enum):
    """Decision outcome when evaluating an agent version for install/upgrade."""

    INSTALL = "install"
    UPGRADE = "upgrade"
    UNCHANGED_EQUAL = "unchanged_equal"
    UNCHANGED_NEWER = "unchanged_newer"
    UNKNOWN = "unknown"


def parse_telegraf_version(val: Optional[str]) -> Optional[Tuple[int, ...]]:
    """Extract semantic version numbers from a version string.

    Examples:
        '1.40.2' -> (1, 40, 2)
        'Telegraf 1.32.1 (git: HEAD@c7c00e62)' -> (1, 32, 1)
        'Telegraf v1.40.1' -> (1, 40, 1)
        '1.30' -> (1, 30, 0)
    """
    if not val:
        return None
    match = re.search(r"\b(?:v)?(\d+)\.(\d+)(?:\.(\d+))?\b", str(val))
    if not match:
        return None
    parts = [int(p) if p is not None else 0 for p in match.groups()]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def evaluate_telegraf_upgrade(
    installed_version: Optional[str],
    requested_version: Optional[str],
) -> tuple[UpgradeDecision, str]:
    """Compare installed version against requested version and determine action.

    Returns:
        (decision, explanation)
    """
    v_inst = parse_telegraf_version(installed_version)
    v_req = parse_telegraf_version(requested_version)
    req_clean = requested_version.strip() if requested_version else "unknown"

    if not v_inst or not v_req:
        return (
            UpgradeDecision.UNKNOWN,
            f"could not parse versions (installed: {installed_version}, requested: {requested_version})",
        )

    if v_inst < v_req:
        return (
            UpgradeDecision.UPGRADE,
            f"installed version ({installed_version}) is older than requested {req_clean}",
        )
    elif v_inst == v_req:
        return (
            UpgradeDecision.UNCHANGED_EQUAL,
            f"installed version ({installed_version}) matches requested {req_clean}",
        )
    else:
        return (
            UpgradeDecision.UNCHANGED_NEWER,
            f"installed version ({installed_version}) is newer than requested {req_clean}; downgrade not permitted",
        )


def local_now_formatted() -> str:
    """Return the current local time in America/Chicago without UTC or Z suffix.

    Returns:
        Formatted string, e.g. '2026-09-24 13:35:00 CDT'.
    """
    now = datetime.now(CHICAGO_TZ)
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")

