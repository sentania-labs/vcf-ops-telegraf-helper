"""Release reminder stays quiet, stable-only and scoped to this repository."""
import json
from unittest.mock import Mock

import pytest
import requests

from vcf_ops_telegraf_helper import updates


def release(tag='v0.7.6', **changes):
    return dict(tag_name=tag, html_url=f'{updates.RELEASES}/tag/{tag}', draft=False,
                prerelease=False, **changes)


@pytest.mark.parametrize('current,expected', [('0.7.5', True), ('0.7.6', False), ('0.8.0', False),
                                             ('0.7.6.dev1', False), ('0.7.6rc1', False)])
def test_newer_stable_only(current, expected):
    assert bool(updates.release_notice(release(), current)) is expected


@pytest.mark.parametrize('data', [None, [], {}, dict(release(), draft=True),
                                 dict(release(), prerelease=True), release('v0.7.6rc1'),
                                 dict(release(), html_url='https://example.com/release'),
                                 dict(release(), html_url='http://github.com/release')])
def test_untrusted_or_unstable_release_ignored(data):
    assert updates.release_notice(data, '0.7.5') is None


def test_cache_reuses_release_and_rechecks_after_expiry(monkeypatch, tmp_path):
    now = 100000
    monkeypatch.setattr(updates.time, 'time', lambda: now)
    response = Mock(status_code=200)
    response.json.return_value = release()
    get = Mock(return_value=response)
    monkeypatch.setattr(updates.requests, 'get', get)
    cache = tmp_path / 'release.json'
    assert updates.check_release('0.7.5', cache).version == 'v0.7.6'
    assert updates.check_release('0.7.6', cache) is None
    get.assert_called_once()
    assert get.call_args.kwargs['timeout'] == (2, 3)
    assert get.call_args.kwargs['allow_redirects'] is False
    now += updates.CACHE_SECONDS
    updates.check_release('0.7.5', cache)
    assert get.call_count == 2


def test_failure_silent_and_cached(monkeypatch, tmp_path):
    get = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(updates.requests, 'get', get)
    cache = tmp_path / 'release.json'
    assert updates.check_release('0.7.5', cache) is None
    assert updates.check_release('0.7.5', cache) is None
    get.assert_called_once()


def test_corrupted_cache_and_readonly_directory(monkeypatch, tmp_path):
    cache = tmp_path / 'release.json'
    cache.write_text('{')
    response = Mock(status_code=200)
    response.json.return_value = release()
    monkeypatch.setattr(updates.requests, 'get', Mock(return_value=response))
    assert updates.check_release('0.7.5', cache)
    blocked = tmp_path / 'file'
    blocked.write_text('x')
    assert updates.check_release('0.7.5', blocked / 'release.json')


def test_development_build_skips_network(monkeypatch, tmp_path):
    get = Mock()
    monkeypatch.setattr(updates.requests, 'get', get)
    assert updates.check_release('0.7.6.dev2+abc', tmp_path / 'release.json') is None
    get.assert_not_called()


def test_cached_link_revalidated(monkeypatch, tmp_path):
    cache = tmp_path / 'release.json'
    monkeypatch.setattr(updates.time, 'time', lambda: 100)
    cache.write_text(json.dumps({'checked_at': 100,
                                'release': dict(release(), html_url='https://example.com')}))
    assert updates.check_release('0.7.5', cache) is None
