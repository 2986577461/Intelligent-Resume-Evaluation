"""Readiness route never needs model credentials or external network in tests."""
import json
import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch


def test_readiness_returns_generic_error_without_credentials(monkeypatch):
    from common.health import readiness
    monkeypatch.delenv('REDIS_URL', raising=False)
    with patch('common.health.sqlite3.connect', side_effect=sqlite3.OperationalError('private detail')):
        response = readiness()
    assert response.status_code == 503
    assert json.loads(response.body) == {'status': 'not_ready'}
    assert response.headers['cache-control'] == 'no-store'


def test_readiness_success_uses_local_probes(monkeypatch):
    from common.health import readiness
    from conversation import service
    fake = SimpleNamespace(ping=lambda: True, close=lambda: None)
    monkeypatch.setenv('REDIS_URL', 'redis://unused:6379/0')
    monkeypatch.setenv('APP_VERSION', 'test-sha')
    # Redis is intentionally absent from the minimal CI lock; stub only the probe.
    import sys
    with patch.dict(sys.modules, {'redis': SimpleNamespace(from_url=lambda *a, **k: fake)}), \
            patch('common.health.sqlite3.connect') as connection:
        response = readiness()
    assert response.status_code == 200
    assert json.loads(response.body)['version'] == 'test-sha'
    assert connection.call_count == 2
