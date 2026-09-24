"""General utilities including local timezone formatting."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

CHICAGO_TZ = ZoneInfo("America/Chicago")


def local_now_formatted() -> str:
    """Return the current local time in America/Chicago without UTC or Z suffix.

    Returns:
        Formatted string, e.g. '2026-09-24 13:35:00 CDT'.
    """
    now = datetime.now(CHICAGO_TZ)
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")
