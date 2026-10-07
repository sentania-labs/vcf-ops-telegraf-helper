"""Quiet, cached checks for newer stable releases, with no account credentials."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time

import requests

RELEASES = 'https://github.com/sentania-labs/vcf-ops-telegraf-helper/releases'
LATEST_API = 'https://api.github.com/repos/sentania-labs/vcf-ops-telegraf-helper/releases/latest'
CACHE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class ReleaseNotice:
    version: str
    url: str


def stable_version(value: str) -> tuple[int, int, int] | None:
    """Development and prerelease builds do not show stable update reminders."""
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    return tuple(map(int, match.groups())) if match else None


def release_notice(data: object, current: str) -> ReleaseNotice | None:
    if not isinstance(data, dict) or data.get('draft') is not False or data.get('prerelease') is not False:
        return None
    tag = data.get('tag_name')
    if not isinstance(tag, str) or not tag.startswith('v'):
        return None
    latest = stable_version(tag)
    installed = stable_version(current)
    url = f'{RELEASES}/tag/{tag}'
    if not latest or not installed or latest <= installed or data.get('html_url') != url:
        return None
    return ReleaseNotice(tag, url)


def check_release(current: str, cache_path: Path) -> ReleaseNotice | None:
    """Fail silently offline; cache successful and failed attempts for a day."""
    if stable_version(current) is None:
        return None
    now = time.time()
    try:
        cached = json.loads(cache_path.read_text(encoding='utf-8'))
        age = now - float(cached['checked_at'])
        if 0 <= age < CACHE_SECONDS:
            return release_notice(cached.get('release'), current)
    except (OSError, ValueError, TypeError, KeyError):
        pass
    data = None
    try:
        response = requests.get(LATEST_API, timeout=(2, 3), allow_redirects=False,
                                auth=lambda request: request,
                                headers={'Accept': 'application/vnd.github+json',
                                         'User-Agent': 'vcf-ops-telegraf-helper'})
        response.raise_for_status()
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict):
                data = {key: payload.get(key) for key in ('tag_name', 'html_url', 'draft', 'prerelease')}
    except (requests.RequestException, ValueError):
        pass
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({'checked_at': now, 'release': data}), encoding='utf-8')
    except OSError:
        pass
    return release_notice(data, current)
