"""No network/Docker: archive safety, durable frontend switch, deployment gates."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pull_release', Path(__file__).with_name('pull-release.py'))
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, members):
        path = self.root / 'frontend.tar.gz'
        with tarfile.open(path, 'w:gz') as target:
            for name, kind in members:
                info = tarfile.TarInfo(name)
                if kind == 'link':
                    info.type = tarfile.SYMTYPE
                    info.linkname = '/etc/passwd'
                    target.addfile(info)
                else:
                    content = b'<html>ok</html>'
                    info.size = len(content)
                    target.addfile(info, io.BytesIO(content))
        return path

    def test_safe_frontend(self):
        source = self.archive([('./index.html', 'file'), ('./assets/app.js', 'file')])
        cd.unpack_frontend(source, self.root / 'output')
        self.assertTrue((self.root / 'output/index.html').is_file())

    def test_reject_unsafe_frontend(self):
        for name, kind in [('../outside', 'file'), ('/absolute', 'file'),
                           ('assets/link', 'link'), ('./../outside', 'file')]:
            with self.subTest(name=name):
                source = self.archive([('index.html', 'file'), (name, kind)])
                with self.assertRaises(ValueError):
                    cd.unpack_frontend(source, self.root / 'output')

    def test_reject_duplicate_and_missing_index(self):
        for members in [[('index.html', 'file'), ('index.html', 'file')], [('assets/a', 'file')]]:
            with self.subTest(members=members):
                with self.assertRaises(ValueError):
                    cd.unpack_frontend(self.archive(members), self.root / 'output')

    def test_publish_preserves_mount_inode_and_old_assets(self):
        public = self.root / 'public'
        public.mkdir()
        (public / 'index.html').write_text('old index')
        (public / 'old.js').write_text('old asset')
        source = self.root / 'new'
        source.mkdir()
        (source / 'index.html').write_text('new index')
        (source / 'app.js').write_text('new asset')
        inode = public.stat().st_ino
        with patch.object(cd, 'FRONTEND', public):
            cd.publish_frontend(source)
        self.assertEqual(public.stat().st_ino, inode)
        self.assertEqual((public / 'index.html').read_text(), 'new index')
        self.assertTrue((public / 'old.js').exists())
        self.assertTrue((public / 'app.js').exists())

    def test_download_rejects_non_repo_url_before_network(self):
        with self.assertRaises(ValueError):
            cd.download({'browser_download_url': 'https://evil.test/bundle'}, self.root / 'x')

    def test_download_requires_official_digest(self):
        with self.assertRaises(ValueError):
            cd.download({'browser_download_url': 'https://github.com/' + cd.REPO + '/releases/download/x/y',
                         'size': 1}, self.root / 'x')

    def test_https_gate_happens_before_container_operations(self):
        with patch.object(cd, 'tls_ready', side_effect=RuntimeError('No TLS')), \
                patch.object(cd, 'run') as run:
            with self.assertRaises(RuntimeError):
                cd.deploy(self.root, {'sha': 'a' * 40, 'image': 'resume-backend:' + 'a' * 40})
            run.assert_not_called()


    def test_failed_readiness_restores_old_running_container_without_restoring_db(self):
        public = self.root / 'public'
        public.mkdir()
        (public / 'index.html').write_text('old index')
        (self.root / 'releases').mkdir()
        sha = 'a' * 40
        old = {'Config': {'Image': 'legacy:old'}, 'State': {'Running': True}}
        containers = {'resume-backend': old}
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('docker', 'stop'):
                containers[args[-1]]['State']['Running'] = False
            elif args[:2] == ('docker', 'rename'):
                containers[args[3]] = containers.pop(args[2])
            elif args[:2] == ('docker', 'compose') and 'up' in args:
                containers['resume-backend'] = {
                    'Config': {'Image': 'resume-backend:' + sha},
                    'State': {'Running': True}}
            elif args[:2] == ('docker', 'rm'):
                containers.pop(args[-1], None)
            elif args[:2] == ('docker', 'start'):
                containers[args[-1]]['State']['Running'] = True

        with patch.object(cd, 'ROOT', self.root), patch.object(cd, 'FRONTEND', public), \
                patch.object(cd, 'tls_ready'), patch.object(cd, 'sqlite_backups') as backup, \
                patch.object(cd, 'run', side_effect=fake_run), \
                patch.object(cd, 'container', side_effect=lambda name: containers.get(name)), \
                patch.object(cd, 'wait_healthy', side_effect=RuntimeError('unhealthy')):
            with self.assertRaises(RuntimeError):
                cd.deploy(self.root, {'sha': sha, 'image': 'resume-backend:' + sha})
        self.assertIs(containers['resume-backend'], old)
        self.assertTrue(old['State']['Running'])
        self.assertNotIn('resume-backend-rollback', containers)
        self.assertEqual((public / 'index.html').read_text(), 'old index')
        self.assertTrue((self.root / 'failed.json').exists())
        backup.assert_called_once()
        # Different Compose project per release preserves the renamed rollback container.
        self.assertTrue(any('resume-' + sha[:12] in call for call in calls))


if __name__ == '__main__':
    unittest.main()
