"""Offline status, webhook, safe diagnostics and production frontend checks."""
import importlib.util
from contextlib import ExitStack, contextmanager, nullcontext
import json
import os
import subprocess
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

spec = importlib.util.spec_from_file_location('pull_report', Path(__file__).with_name('pull-release.py'))
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)
SHA = 'b' * 40


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.object(cd, 'ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def status(self):
        return json.loads((self.root / 'deployment-status.json').read_text())

    def test_status_has_timestamps_safe_error_and_retry_classification(self):
        report = cd.DeploymentReport()
        report.phase('download_backend', SHA)
        with patch.object(cd, 'container', return_value=None):
            report.finish('failed', urllib.error.URLError('secret signed URL'))
        status = self.status()
        self.assertTrue(status['retryable'])
        self.assertGreaterEqual(status['finished_at'], status['started_at'])
        self.assertEqual(status['error_type'], 'URLError')
        self.assertNotIn('secret', json.dumps(status))
        self.assertEqual((self.root / 'deployment-status.json').stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root / 'deployment-status.pending').exists())

    def test_failed_release_is_not_retryable(self):
        cd.atomic_json(self.root / 'failed.json', {'sha': SHA})
        report = cd.DeploymentReport()
        report.phase('start_backend', SHA)
        report.value['rollback'] = 'verified'
        with patch.object(cd, 'container', return_value=None):
            report.finish('failed', RuntimeError('unhealthy'))
        self.assertFalse(self.status()['retryable'])
        self.assertEqual(self.status()['rollback'], 'verified')

    def test_diagnostics_exclude_env_health_output_and_exception(self):
        container = {'State': {'Running': False, 'ExitCode': 1, 'OOMKilled': True,
                              'Error': 'secret', 'Health': {'Status': 'unhealthy', 'Log': ['private resume']}},
                     'Config': {'Env': ['API_KEY=secret']}}
        with patch.object(cd, 'container', return_value=container):
            diagnostics = cd.safe_diagnostics()
        value = json.dumps(diagnostics)
        self.assertNotIn('secret', value)
        self.assertNotIn('private resume', value)
        self.assertTrue(diagnostics['resume-backend']['OOMKilled'])

    def opener(self):
        response = Mock(status=204)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        return opener

    def test_webhook_deduplicates_repeated_failure_but_not_recovery(self):
        os.environ['RESUME_DEPLOY_WEBHOOK_URL'] = 'https://notify.example/webhook?token=secret'
        opener = self.opener()
        with patch.object(cd.urllib.request, 'build_opener', return_value=opener), \
                patch.object(cd, 'container', return_value=None):
            for _ in range(2):
                report = cd.DeploymentReport()
                report.phase('download_backend', SHA)
                report.finish('failed', TimeoutError('secret'))
            self.assertEqual(opener.open.call_count, 1)
            self.assertEqual(self.status()['notification'], 'deduplicated')
            report.finish('succeeded')
            self.assertEqual(opener.open.call_count, 2)
        request = opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data)['status'], 'succeeded')
        self.assertNotIn('secret', request.data.decode())

    def test_webhook_failure_does_not_fail_success_and_retries_next_poll(self):
        os.environ['RESUME_DEPLOY_WEBHOOK_URL'] = 'https://notify.example/webhook'
        opener = self.opener()
        opener.open.side_effect = TimeoutError('private webhook URL')
        with patch.object(cd.urllib.request, 'build_opener', return_value=opener):
            report = cd.DeploymentReport()
            report.phase('cleanup', SHA)
            report.finish('succeeded')
            self.assertEqual(self.status()['status'], 'succeeded')
            self.assertEqual(self.status()['notification'], 'failed')
            opener.open.side_effect = None
            cd.retry_notification()
            self.assertEqual(self.status()['notification'], 'sent')

    def test_webhook_rejects_http_userinfo_and_fragment_without_network(self):
        for url in ('http://notify.example', 'https://user:secret@notify.example', 'https://notify.example/#secret'):
            with self.subTest(url=url), patch.object(cd.urllib.request, 'build_opener') as build:
                os.environ['RESUME_DEPLOY_WEBHOOK_URL'] = url
                report = cd.DeploymentReport()
                report.finish('succeeded')
                build.assert_not_called()
                self.assertEqual(self.status()['notification'], 'failed')

    def test_webhook_non_2xx_does_not_record_delivery(self):
        os.environ['RESUME_DEPLOY_WEBHOOK_URL'] = 'https://notify.example/webhook'
        opener = self.opener()
        opener.open.return_value.__enter__.return_value.status = 500
        with patch.object(cd.urllib.request, 'build_opener', return_value=opener):
            report = cd.DeploymentReport()
            report.finish('succeeded')
        self.assertEqual(self.status()['notification'], 'failed')
        self.assertFalse((self.root / 'notification.json').exists())

    def test_webhook_never_follows_redirects(self):
        self.assertIsNone(cd.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example'))

    def test_noop_and_blocked_polls_preserve_last_report(self):
        for filename in ('current.json', 'failed.json'):
            with self.subTest(filename=filename):
                cd.atomic_json(self.root / filename, {'sha': SHA})
                cd.atomic_json(self.root / 'deployment-status.json', {'status': 'failed'})
                release = {'tag_name': 'resume-' + SHA, 'draft': False, 'prerelease': False}
                with patch.object(cd, 'api', return_value=release), patch.object(cd, 'download') as download:
                    expected = self.assertRaises(cd.ReleaseBlocked) if filename == 'failed.json' else nullcontext()
                    with expected:
                        cd.poll_release(SimpleNamespace(check_only=False), cd.DeploymentReport())
                    download.assert_not_called()
                self.assertEqual(self.status(), {'status': 'failed'})
                (self.root / filename).unlink()

    def test_frontend_checks_detect_spa_fallback_or_wrong_version(self):
        source = self.root / 'frontend'
        (source / 'assets').mkdir(parents=True)
        (source / 'index.html').write_text('<script src="/resume/assets/main.js"></script>')
        (source / 'assets/main.js').write_text('console.log("new");')
        (source / 'assets/lazy.js').write_text('console.log("lazy");')
        def run(*args):
            if '--output' in args and args[args.index('--output') + 1] != '/dev/null':
                path = Path(args[args.index('--output') + 1])
                relative = args[-1].split('/resume/')[1]
                path.write_bytes((source / relative).read_bytes())
            return SimpleNamespace(stdout='401')
        with patch.object(cd, 'run', side_effect=run) as mocked:
            cd.gateway_frontend(source)
            self.assertEqual(mocked.call_count, 5)
        def fallback(*args):
            if '--output' in args:
                Path(args[args.index('--output') + 1]).write_text('old SPA fallback')
            return SimpleNamespace(stdout='200')
        with patch.object(cd, 'run', side_effect=fallback):
            with self.assertRaisesRegex(RuntimeError, 'differs'):
                cd.gateway_frontend(source)

    def test_frontend_rejects_no_entry_and_traversal(self):
        for html in ('<html>empty</html>', '<script src="/resume/assets/../../secret.js"></script>'):
            (self.root / 'index.html').write_text(html)
            with patch.object(cd, 'run') as run:
                with self.assertRaises((RuntimeError, ValueError)):
                    cd.gateway_frontend(self.root)
                run.assert_not_called()


    def deployment(self, *, running=True, fail_start=False, fail_cleanup=False):
        stage = self.root / 'stage'
        (stage / 'frontend').mkdir(parents=True)
        (stage / 'frontend/index.html').write_text('new index')
        public = self.root / 'public'
        public.mkdir()
        (public / 'index.html').write_text('old index')
        (self.root / 'releases').mkdir()
        old_sha = 'a' * 40
        cd.atomic_json(self.root / 'current.json', {'sha': old_sha, 'image': 'resume-backend:' + old_sha})
        containers = {'resume-backend': {'Config': {'Image': 'resume-backend:' + old_sha},
                                        'State': {'Running': running, 'Health': {'Status': 'healthy'}}}}
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('docker', 'stop'):
                containers[args[-1]]['State']['Running'] = False
            elif args[:2] == ('docker', 'rename'):
                containers[args[3]] = containers.pop(args[2])
            elif args[:2] == ('docker', 'compose') and 'up' in args:
                containers['resume-backend'] = {'Config': {'Image': 'resume-backend:' + SHA},
                                                'State': {'Running': True, 'Health': {'Status': 'healthy'}}}
            elif args[:2] == ('docker', 'rm'):
                if fail_cleanup and args[-1] == 'resume-backend-rollback':
                    raise subprocess.CalledProcessError(1, args)
                containers.pop(args[-1], None)
            elif args[:2] == ('docker', 'start'):
                if fail_start:
                    raise subprocess.CalledProcessError(1, args)
                containers[args[-1]]['State']['Running'] = True
            return SimpleNamespace(stdout='', returncode=0)

        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in [('FRONTEND', public), ('tls_ready', Mock()), ('sqlite_backups', Mock()),
                            ('run', Mock(side_effect=fake_run)),
                            ('container', Mock(side_effect=lambda name: containers.get(name))),
                            ('wait_healthy', Mock()), ('gateway_health', Mock()), ('gateway_frontend', Mock())]:
            stack.enter_context(patch.object(cd, name, value))
        report = cd.DeploymentReport()
        report.phase('verify_release', SHA)
        manifest = {'sha': SHA, 'image': 'resume-backend:' + SHA}
        return stage, manifest, report, containers, calls

    def test_success_commits_version_only_after_gateway_smoke(self):
        stage, manifest, report, containers, calls = self.deployment()
        def gateway(source):
            self.assertEqual(json.loads((self.root / 'current.json').read_text())['sha'], 'a' * 40)
        cd.gateway_frontend.side_effect = gateway
        cd.deploy(stage, manifest, report)
        report.finish('succeeded')
        self.assertEqual(json.loads((self.root / 'current.json').read_text())['sha'], SHA)
        self.assertNotIn('resume-backend-rollback', containers)
        self.assertEqual(self.status()['status'], 'succeeded')
        self.assertFalse((self.root / 'failed.json').exists())

    def test_gateway_failure_recovers_and_verifies_old_version(self):
        stage, manifest, report, containers, calls = self.deployment()
        cd.gateway_frontend.side_effect = RuntimeError('asset missing')
        with self.assertRaisesRegex(RuntimeError, 'asset missing'):
            cd.deploy(stage, manifest, report)
        report.finish('failed', RuntimeError('asset missing'))
        self.assertEqual(report.value['rollback'], 'verified')
        cd.wait_healthy.assert_any_call('resume-backend', 'a' * 40)
        cd.gateway_health.assert_any_call('a' * 40)
        self.assertEqual(containers['resume-backend']['Config']['Image'], 'resume-backend:' + 'a' * 40)
        self.assertEqual((self.root / 'public/index.html').read_text(), 'old index')
        self.assertEqual(json.loads((self.root / 'current.json').read_text())['sha'], 'a' * 40)
        self.assertIn('resume-backend', self.status()['failure_diagnostics'])
        self.assertFalse(self.status()['retryable'])

    def test_failed_recovery_is_not_reported_as_restored(self):
        stage, manifest, report, containers, calls = self.deployment(fail_start=True)
        cd.gateway_frontend.side_effect = RuntimeError('asset missing')
        with self.assertRaisesRegex(RuntimeError, 'recovery failed') as error:
            cd.deploy(stage, manifest, report)
        report.finish('failed', error.exception)
        self.assertEqual(self.status()['rollback'], 'failed')
        self.assertEqual(self.status()['rollback_error_type'], 'CalledProcessError')
        self.assertFalse(self.status()['retryable'])
        self.assertTrue((self.root / 'releases' / SHA / 'FAILED').exists())

    def test_stopped_old_version_is_not_started_during_recovery(self):
        stage, manifest, report, containers, calls = self.deployment(running=False)
        cd.wait_healthy.side_effect = RuntimeError('new backend unhealthy')
        with self.assertRaisesRegex(RuntimeError, 'new backend unhealthy'):
            cd.deploy(stage, manifest, report)
        self.assertFalse(containers['resume-backend']['State']['Running'])
        self.assertFalse(any(call[:2] == ('docker', 'start') for call in calls))
        self.assertEqual(report.value['rollback'], 'state_restored')

    def test_cleanup_failure_preserves_successful_release(self):
        stage, manifest, report, containers, calls = self.deployment(fail_cleanup=True)
        cd.deploy(stage, manifest, report)
        report.finish('succeeded')
        self.assertEqual(self.status()['cleanup'], 'failed')
        self.assertEqual(self.status()['status'], 'succeeded')
        self.assertEqual(json.loads((self.root / 'current.json').read_text())['sha'], SHA)

    def test_main_records_retryable_network_failure_and_quarantines_invalid_release(self):
        for error in (TimeoutError('signed URL secret'), ValueError('invalid checksum')):
            with self.subTest(error=type(error).__name__):
                def poll(args, report):
                    report.phase('download_backend', SHA)
                    raise error
                with patch('sys.argv', ['pull-release.py']), patch.object(cd, 'poll_release', side_effect=poll), \
                        patch.object(cd, 'container', return_value=None):
                    with self.assertRaises(type(error)):
                        cd.main()
                self.assertEqual(self.status()['retryable'], isinstance(error, TimeoutError))
                self.assertNotIn('secret', json.dumps(self.status()))

    def test_check_only_does_not_write_status_or_send_notifications(self):
        cd.atomic_json(self.root / 'current.json', {'sha': SHA})
        release = {'tag_name': 'resume-' + SHA, 'draft': False, 'prerelease': False}
        with patch('sys.argv', ['pull-release.py', '--check-only']), \
                patch.object(cd, 'api', return_value=release), patch.object(cd, 'retry_notification') as retry:
            cd.main()
            retry.assert_not_called()
        self.assertFalse((self.root / 'deployment-status.json').exists())

    def test_in_progress_workflow_waits_without_failure_notification(self):
        release = {'tag_name': 'resume-' + SHA, 'draft': False, 'prerelease': False,
                   'assets': [{'name': name, 'size': 100} for name in ('manifest.json', 'backend.tar.gz', 'frontend.tar.gz')]}
        manifest = {'sha': SHA, 'schema': 1, 'image': 'resume-backend:' + SHA, 'run_id': 1}
        workflow = {'head_sha': SHA, 'event': 'workflow_dispatch', 'head_branch': 'main',
                    'path': '.github/workflows/release.yml', 'status': 'in_progress'}
        def download(asset, path):
            path.write_text(json.dumps(manifest))
        with patch.object(cd, 'api', side_effect=[release, workflow]), \
                patch.object(cd.shutil, 'disk_usage', return_value=SimpleNamespace(free=10 * 1024**3)), \
                patch.object(cd, 'download', side_effect=download) as fetch, patch.object(cd, 'deploy') as deploy:
            cd.poll_release(SimpleNamespace(check_only=False), cd.DeploymentReport())
            self.assertEqual(fetch.call_count, 1)
            deploy.assert_not_called()
        self.assertEqual(self.status()['status'], 'waiting')
        self.assertFalse((self.root / 'notification.json').exists())


    @contextmanager
    def publishing_run(self, **updates):
        release = {'tag_name': 'resume-' + SHA, 'draft': False, 'prerelease': False,
                   'assets': [{'name': name, 'size': 100} for name in ('manifest.json', 'backend.tar.gz', 'frontend.tar.gz')]}
        manifest = {'sha': SHA, 'schema': 1, 'image': 'resume-backend:' + SHA, 'run_id': 1}
        workflow = {'head_sha': SHA, 'event': 'workflow_dispatch', 'head_branch': 'main',
                    'path': '.github/workflows/release.yml', 'status': 'completed', 'conclusion': 'success', **updates}
        def download(asset, path):
            self.assertEqual(asset['name'], 'manifest.json')
            path.write_text(json.dumps(manifest))
        with patch.object(cd, 'api', side_effect=[release, workflow]), \
                patch.object(cd.shutil, 'disk_usage', return_value=SimpleNamespace(free=10 * 1024**3)), \
                patch.object(cd, 'download', side_effect=download) as fetch, patch.object(cd, 'deploy') as deploy:
            yield fetch, deploy

    def test_completed_main_manual_release_is_approved_in_check_only(self):
        with self.publishing_run() as (fetch, deploy):
            cd.poll_release(SimpleNamespace(check_only=True), None)
            self.assertEqual(fetch.call_count, 1)
            deploy.assert_not_called()
        self.assertFalse((self.root / 'deployment-status.json').exists())

    def test_non_main_release_is_rejected_even_while_workflow_is_running(self):
        for branch in ('dev', 'feature/example', 'main-preview'):
            for status in ('in_progress', 'completed'):
                with self.subTest(branch=branch, status=status), \
                        self.publishing_run(head_branch=branch, status=status) as (fetch, deploy):
                    with self.assertRaisesRegex(ValueError, 'Publishing workflow'):
                        cd.poll_release(SimpleNamespace(check_only=False), cd.DeploymentReport())
                    self.assertEqual(fetch.call_count, 1)
                    deploy.assert_not_called()
                    self.assertNotEqual(self.status()['status'], 'waiting')

    def test_main_release_rejects_wrong_identity_and_unsuccessful_runs(self):
        for updates in ({'event': 'push'}, {'event': 'pull_request'}, {'event': 'workflow_run'},
                        {'head_sha': 'c' * 40}, {'path': '.github/workflows/ci.yml'},
                        {'conclusion': 'failure'}, {'conclusion': 'cancelled'}, {'conclusion': 'skipped'}):
            with self.subTest(updates=updates), self.publishing_run(**updates) as (fetch, deploy):
                with self.assertRaisesRegex(ValueError, 'Publishing workflow'):
                    cd.poll_release(SimpleNamespace(check_only=False), cd.DeploymentReport())
                self.assertEqual(fetch.call_count, 1)
                deploy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
