"""Readiness route never needs model credentials or external network in tests."""
import json
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def redis_module(monkeypatch):
    module = ModuleType('redis')
    module.from_url = MagicMock()
    monkeypatch.setitem(sys.modules, 'redis', module)
    return module


def test_readiness_returns_generic_error_without_credentials(monkeypatch, redis_module):
    from common.health import readiness
    monkeypatch.delenv('REDIS_URL', raising=False)
    with patch('common.health.sqlite3.connect', side_effect=RuntimeError('private detail')):
        response = readiness()
    assert response.status_code == 503
    assert json.loads(response.body) == {'status': 'not_ready'}
    assert response.headers['cache-control'] == 'no-store'


def test_readiness_success_uses_local_probes(monkeypatch, redis_module):
    from common.health import readiness
    from conversation import service
    monkeypatch.setenv('REDIS_URL', 'redis://unused:6379/0')
    monkeypatch.setenv('APP_VERSION', 'test-sha')
    with patch('common.health.sqlite3.connect') as connection:
        response = readiness()
    assert response.status_code == 200
    assert json.loads(response.body) == {'status': 'ok', 'version': 'test-sha'}
    assert response.headers['cache-control'] == 'no-store'
    assert connection.call_count == 2
    connection.assert_any_call(service.APP_DB_PATH, timeout=2)
    redis_module.from_url.assert_called_once_with(
        'redis://unused:6379/0', socket_connect_timeout=2, socket_timeout=2)
    redis_module.from_url.return_value.ping.assert_called_once_with()
    redis_module.from_url.return_value.close.assert_called_once_with()


@pytest.mark.parametrize('failure_stage', ['missing_url', 'connect', 'ping'])
def test_readiness_redis_failure_is_private_and_closes_client(monkeypatch, redis_module, failure_stage):
    from common.health import readiness
    monkeypatch.setenv('REDIS_URL', 'redis://private-password@unused:6379/0')
    if failure_stage == 'missing_url':
        monkeypatch.delenv('REDIS_URL')
    elif failure_stage == 'connect':
        redis_module.from_url.side_effect = RuntimeError('private detail')
    else:
        redis_module.from_url.return_value.ping.side_effect = RuntimeError('private detail')
    with patch('common.health.sqlite3.connect'):
        response = readiness()
    assert response.status_code == 503
    assert json.loads(response.body) == {'status': 'not_ready'}
    assert response.headers['cache-control'] == 'no-store'
    if failure_stage == 'missing_url':
        redis_module.from_url.assert_not_called()
    elif failure_stage == 'ping':
        redis_module.from_url.return_value.close.assert_called_once_with()
    else:
        redis_module.from_url.return_value.close.assert_not_called()
