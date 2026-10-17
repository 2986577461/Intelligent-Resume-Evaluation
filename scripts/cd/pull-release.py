#!/usr/bin/env python3
"""Root-installed pull deployer. Releases contain data/images, never remote scripts.

The repository is public. No GitHub PAT is stored on the production server.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path('/opt/apps/resume')
REPO = '2986577461/Intelligent-Resume-Evaluation'
ORIGIN = 'https://www.myzyitzj.cn:19443'
FRONTEND = Path('/root/resume/frontend')
COMPOSE = ROOT / 'compose.yaml'
MAX_ASSET = 1_500_000_000


def run(*args, check=True, **kwargs):
    return subprocess.run(args, check=check, capture_output=True, text=True,
                          timeout=kwargs.pop('timeout', 90), **kwargs)


def api(path):
    request = urllib.request.Request('https://api.github.com/' + path, headers={
        'User-Agent': 'resume-pull-deployer', 'Accept': 'application/vnd.github+json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def download(asset, destination):
    url = asset['browser_download_url']
    if not url.startswith(f'https://github.com/{REPO}/releases/download/'):
        raise ValueError('Unexpected release asset URL')
    expected = asset.get('digest', '')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', expected):
        raise ValueError('Missing GitHub asset digest')
    size = asset['size']
    if not 0 < size <= MAX_ASSET:
        raise ValueError('Invalid asset size')
    if destination.exists() and destination.stat().st_size == size and 'sha256:' + sha256(destination) == expected:
        return
    if size > 8 * 1024 * 1024:
        ranged_download(url, destination, size, expected)
        return
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as response, destination.open('wb') as target:
                count = 0
                while block := response.read(65536):
                    count += len(block)
                    if count > size:
                        raise ValueError('Asset exceeded declared size')
                    target.write(block)
            if destination.stat().st_size != size or 'sha256:' + sha256(destination) != expected:
                raise ValueError('Asset checksum mismatch')
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)


def ranged_download(url, destination, size, expected):
    """Bounded parallel HTTP ranges; resume private partial files across timer runs."""
    chunk = 2 * 1024 * 1024
    parts = destination.with_name(destination.name + '.parts')
    parts.mkdir(exist_ok=True)
    ranges = [(start, min(start + chunk, size)) for start in range(0, size, chunk)]
    # Reuse an interrupted sequential download, never trust it until full SHA256.
    if destination.exists() and destination.stat().st_size < size:
        with destination.open('rb') as source:
            for start, stop in ranges:
                data = source.read(stop - start)
                part = parts / str(start)
                if data and not part.exists():
                    part.write_bytes(data)

    def fetch(bounds):
        start, stop = bounds
        part = parts / str(start)
        if part.exists() and part.stat().st_size > stop - start:
            part.unlink()
        for attempt in range(4):
            have = part.stat().st_size if part.exists() else 0
            if have == stop - start:
                return
            offset = start + have
            request = urllib.request.Request(url, headers={
                'Range': f'bytes={offset}-{stop - 1}', 'Accept-Encoding': 'identity'})
            try:
                with urllib.request.urlopen(request, timeout=35) as response:
                    if response.status != 206 or response.headers.get('Content-Range') != f'bytes {offset}-{stop - 1}/{size}':
                        raise ValueError('Unexpected HTTP range response')
                    with part.open('ab') as target:
                        while block := response.read(65536):
                            have += len(block)
                            if have > stop - start:
                                raise ValueError('Range exceeded expected length')
                            target.write(block)
                if have == stop - start:
                    return
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2)
        raise RuntimeError('Incomplete download range')

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, ranges))
    assembled = destination.with_name(destination.name + '.assembled')
    with assembled.open('wb') as target:
        for start, stop in ranges:
            part = parts / str(start)
            if part.stat().st_size != stop - start:
                raise ValueError('Range file length mismatch')
            with part.open('rb') as source:
                shutil.copyfileobj(source, target)
    if assembled.stat().st_size != size or 'sha256:' + sha256(assembled) != expected:
        shutil.rmtree(parts)
        assembled.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise ValueError('Assembled asset checksum mismatch')
    assembled.replace(destination)
    shutil.rmtree(parts)


@contextmanager
def staging_directory(sha):
    directory = ROOT / 'staging' / sha
    directory.mkdir(parents=True, exist_ok=True)
    yield directory
    # Exceptions leave partial downloads for the next timer invocation.
    shutil.rmtree(directory)


def unpack_frontend(archive, destination):
    """Reject traversal, links, devices, duplicates and decompression bombs."""
    with tarfile.open(archive, 'r:gz') as source:
        entries = source.getmembers()
        total = 0
        seen = set()
        for entry in entries:
            name = entry.name.removeprefix('./')
            if name == '.' and entry.isdir():
                continue
            parts = Path(name).parts
            if (not name or name.startswith('/') or '..' in parts or
                    not (entry.isfile() or entry.isdir()) or name in seen):
                raise ValueError('Unsafe frontend archive member')
            seen.add(name)
            total += entry.size
        if total > 200 * 1024 * 1024 or len(entries) > 10000:
            raise ValueError('Frontend archive too large')
        source.extractall(destination, filter='data')
    if not (destination / 'index.html').is_file():
        raise ValueError('Frontend index missing')


def container(name):
    result = run('docker', 'inspect', name, check=False)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def compose_env(image, sha):
    return os.environ | {'RESUME_IMAGE': image, 'RESUME_SHA': sha}


def wait_healthy(name, sha):
    for _ in range(40):
        state = container(name)
        if state and state['State'].get('Health', {}).get('Status') == 'healthy':
            value = run('docker', 'exec', name, 'python', '-c',
                        "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').read().decode())")
            if json.loads(value.stdout).get('version') == sha:
                return
        time.sleep(3)
    raise RuntimeError('Backend readiness failed')


def tls_ready():
    # Real certificate verification and expected hostname, bypass NAT hairpin only.
    run('curl', '--fail', '--silent', '--show-error', '--noproxy', '*',
        '--resolve', 'www.myzyitzj.cn:19443:127.0.0.1',
        '--connect-to', 'www.myzyitzj.cn:19443:127.0.0.1:443',
        '--max-time', '12', ORIGIN + '/_tls-health')
    # The build runner needn't reach production. Verify the public NAT mapping
    # on the deployment host before touching the running application.
    run('curl', '--fail', '--silent', '--show-error', '--noproxy', '*',
        '--connect-timeout', '8', '--max-time', '15', ORIGIN + '/_tls-health')


def gateway_health(sha):
    result = run('curl', '--fail', '--silent', '--show-error', '--noproxy', '*',
                 '--connect-to', 'www.myzyitzj.cn:19443:127.0.0.1:443',
                 '--max-time', '15', ORIGIN + '/resume/api/health')
    if json.loads(result.stdout).get('version') != sha:
        raise RuntimeError('HTTPS upstream is not the new backend')


def sqlite_backups(directory):
    for source in (Path('/root/resume/data/conversation-db/app.db'),
                   Path('/root/resume/data/agent-db/conversations.db')):
        if not source.exists():
            raise RuntimeError('Expected persistent database missing')
        target = directory / (source.parent.name + '.sqlite3')
        with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('SQLite backup integrity check failed')


def publish_frontend(source):
    # Keep the bind-mount directory inode; copy assets first and atomically replace
    # index last. Old hashed assets remain available to already-open browser tabs.
    for path in source.rglob('*'):
        relative = path.relative_to(source)
        if relative == Path('index.html') or path.is_dir():
            continue
        target = FRONTEND / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        for parent in target.relative_to(FRONTEND).parents:
            (FRONTEND / parent).chmod(0o755)
        shutil.copyfile(path, target)
        target.chmod(0o644)
    pending = FRONTEND / '.index.pending'
    shutil.copyfile(source / 'index.html', pending)
    pending.chmod(0o644)
    pending.replace(FRONTEND / 'index.html')


def deploy(stage, manifest):
    sha = manifest['sha']
    image = manifest['image']
    tls_ready()
    run('docker', 'compose', 'version')
    run('docker', 'network', 'inspect', 'ithome_net')
    print('Preflight HTTPS and Compose passed.', flush=True)
    previous = container('resume-backend')
    rollback_name = 'resume-backend-rollback'
    if container(rollback_name):
        raise RuntimeError('Prior rollback container exists; investigate before next deploy')
    old_running = bool(previous and previous['State']['Running'])
    old_state = json.loads((ROOT / 'current.json').read_text()) if (ROOT / 'current.json').exists() else None
    print('Loading verified image.', flush=True)
    run('docker', 'load', '-i', str(stage / 'backend.tar.gz'), timeout=600)
    run('docker', 'image', 'inspect', image)
    env = compose_env(image, sha)
    run('docker', 'compose', '-p', 'resume-' + sha[:12], '-f', str(COMPOSE), 'config', '--quiet', env=env)
    release = ROOT / 'releases' / sha
    release.mkdir(parents=True, exist_ok=False)
    if (FRONTEND / 'index.html').exists():
        shutil.copyfile(FRONTEND / 'index.html', release / 'previous-index.html')
    try:
        if old_running:
            run('docker', 'stop', '--time', '30', 'resume-backend')
        sqlite_backups(release)
        if previous:
            run('docker', 'rename', 'resume-backend', rollback_name)
        run('docker', 'compose', '-p', 'resume-' + sha[:12], '-f', str(COMPOSE), 'up', '-d', '--no-build', '--pull', 'never', env=env)
        print('Waiting for backend readiness.', flush=True)
        wait_healthy('resume-backend', sha)
        publish_frontend(stage / 'frontend')
        gateway_health(sha)
        state = dict(manifest, deployed_at=time.time(), previous=old_state)
        # Keep history bounded; don't recursively retain all earlier manifests.
        if state['previous']:
            state['previous'].pop('previous', None)
        pending = ROOT / 'current.pending'
        pending.write_text(json.dumps(state, indent=2) + '\n')
        pending.replace(ROOT / 'current.json')
        (release / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    except Exception:
        # Never restore DB files automatically: that could discard accepted writes.
        # Our first upgrade is additive; incompatible migrations need a separate plan.
        current = container('resume-backend')
        if current and current['Config']['Image'] == image:
            run('docker', 'rm', '-f', 'resume-backend', check=False)
        if container(rollback_name):
            run('docker', 'rename', rollback_name, 'resume-backend')
            if old_running:
                run('docker', 'start', 'resume-backend')
        elif old_running and previous and container('resume-backend'):
            run('docker', 'start', 'resume-backend')
        if (release / 'previous-index.html').exists():
            shutil.copyfile(release / 'previous-index.html', FRONTEND / '.index.pending')
            (FRONTEND / '.index.pending').chmod(0o644)
            (FRONTEND / '.index.pending').replace(FRONTEND / 'index.html')
        (release / 'FAILED').write_text('Deployment failed; previous container/index restored. DB not restored.\n')
        # A failure stops automatic retries of the same release until investigated.
        (ROOT / 'failed.json').write_text(json.dumps(manifest) + '\n')
        raise
    if container(rollback_name):
        run('docker', 'rm', rollback_name)
    # Only remove images recorded in this project's history, never global prune.
    keep = {image}
    if old_state:
        keep.add(old_state['image'])
    for folder in sorted((ROOT / 'releases').iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[3:]:
        record = folder / 'manifest.json'
        if record.exists():
            old = json.loads(record.read_text())
            if old['image'] not in keep:
                run('docker', 'image', 'rm', old['image'], check=False)
                shutil.rmtree(folder)
    print('Deployment succeeded:', sha, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        release = api(f'repos/{REPO}/releases/latest')
        tag = release['tag_name']
        if release['draft'] or release['prerelease'] or not re.fullmatch(r'resume-[a-f0-9]{40}', tag):
            raise ValueError('Latest release is not an approved resume release')
        sha = tag.removeprefix('resume-')
        if (ROOT / 'current.json').exists() and json.loads((ROOT / 'current.json').read_text())['sha'] == sha:
            print('Already deployed:', sha)
            return
        if (ROOT / 'failed.json').exists() and json.loads((ROOT / 'failed.json').read_text())['sha'] == sha:
            raise RuntimeError('Release previously failed; manual investigation required')
        assets = {a['name']: a for a in release['assets']}
        if not {'manifest.json', 'backend.tar.gz', 'frontend.tar.gz'} <= assets.keys():
            raise ValueError('Release assets incomplete')
        if assets['manifest.json']['size'] > 16384:
            raise ValueError('Manifest oversized')
        if shutil.disk_usage(ROOT).free < 6 * 1024**3:
            raise RuntimeError('At least 6 GiB free disk is required before deployment')
        with staging_directory(sha) as stage:
            download(assets['manifest.json'], stage / 'manifest.json')
            manifest = json.loads((stage / 'manifest.json').read_text())
            if (manifest['sha'] != sha or manifest['image'] != f'resume-backend:{sha}' or manifest['schema'] != 1):
                raise ValueError('Invalid manifest identity')
            workflow = api(f'repos/{REPO}/actions/runs/{int(manifest["run_id"])}')
            if not (workflow['head_sha'] == sha and workflow['event'] == 'workflow_dispatch' and
                    workflow['head_branch'] == 'dev' and workflow['path'] == '.github/workflows/release.yml' and
                    workflow['status'] == 'completed' and workflow['conclusion'] == 'success'):
                raise RuntimeError('Publishing workflow has not completed successfully')
            print('Approved release:', tag, flush=True)
            if args.check_only:
                return
            for name in ('backend.tar.gz', 'frontend.tar.gz'):
                if not re.fullmatch('[a-f0-9]{64}', manifest['sha256'][name]):
                    raise ValueError('Invalid manifest checksum')
                print('Downloading:', name, flush=True)
                download(assets[name], stage / name)
                if sha256(stage / name) != manifest['sha256'][name]:
                    raise ValueError('Manifest checksum mismatch')
            unpack_frontend(stage / 'frontend.tar.gz', stage / 'frontend')
            deploy(stage, manifest)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Requests can contain signed download URLs; never print raw exceptions.
        print('Pull deployment failed:', type(error).__name__, flush=True)
        if type(error) is RuntimeError:
            print(str(error), flush=True)
        if isinstance(error, subprocess.CalledProcessError):
            print('Failed operation:', ' '.join(str(p) for p in error.cmd[:3]), flush=True)
        raise SystemExit(1)
