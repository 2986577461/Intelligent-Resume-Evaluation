#!/usr/bin/env python3
"""Destructive business smoke: ONLY the disposable internal Docker network.

No command-line origin override: never point this at production. It creates an
account and uploads a text PDF. No OCR/search/LLM endpoints are invoked.
"""
import http.cookiejar
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import secrets
import ssl
import time
import urllib.error
import urllib.request

ORIGIN = 'https://www.myzyitzj.cn:19443'


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        value = attrs.get('src') if tag == 'script' else attrs.get('href') if tag == 'link' else None
        if value and value.startswith('/resume/assets/'):
            self.paths.add(value)


def main():
    import fitz  # Already installed in the production image; no extra test SDK.

    # Guard against accidentally running this with production DNS/network access.
    if os.getenv('APP_VERSION') != os.environ['SHA'] or os.getenv('REDIS_URL') != 'redis://smoke-redis:6379/0':
        raise RuntimeError('Smoke requires the disposable runner container')
    context = ssl.create_default_context(cafile=os.environ['SMOKE_CA'])
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPSHandler(context=context),
                                         urllib.request.HTTPCookieProcessor(jar))

    def request(path, expected=200, data=None, method=None, headers=None):
        req = urllib.request.Request(ORIGIN + '/resume/' + path, data=data, method=method,
                                     headers={'Origin': ORIGIN, **(headers or {})})
        try:
            response = opener.open(req, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            if response.status != expected:
                raise RuntimeError(f'Smoke endpoint {path.split("?")[0]}: expected {expected}, got {response.status}')
            return body, response.headers

    def post(path, payload, expected=200):
        return request(path, expected, json.dumps(payload).encode(), 'POST', {'Content-Type': 'application/json'})

    for attempt in range(20):
        try:
            body, _ = request('api/health')
            if json.loads(body)['version'] != os.environ['SHA']:
                raise RuntimeError('Unexpected smoke version')
            break
        except (OSError, RuntimeError):
            if attempt == 19:
                raise
            time.sleep(1)

    body, _ = request('')
    source = Path(os.environ['SMOKE_FRONTEND'])
    if body != (source / 'index.html').read_bytes():
        raise RuntimeError('Served page differs from production build')
    assets = Assets()
    assets.feed(body.decode())
    if not any(path.endswith('.js') for path in assets.paths):
        raise RuntimeError('No production frontend entry asset')
    assets.paths.update('/resume/' + str(path.relative_to(source))
                        for path in (source / 'assets').rglob('*') if path.is_file())
    for path in sorted(assets.paths):
        body, headers = request(path.removeprefix('/resume/'))
        expected = (source / path.removeprefix('/resume/')).read_bytes()
        if hashlib.sha256(body).digest() != hashlib.sha256(expected).digest():
            raise RuntimeError('Served asset differs from production build')
        if not body or 'text/html' in headers.get('Content-Type', ''):
            raise RuntimeError('Static asset missing or incorrectly routed to SPA')

    request('api/auth/me', 401)
    account = {'email': f'smoke-{secrets.token_hex(6)}@example.com', 'password': secrets.token_urlsafe(24)}
    body, headers = post('api/auth/register', account, 201)
    user = json.loads(body)
    cookie = headers.get('Set-Cookie', '').lower()
    if not all(flag in cookie for flag in ('secure', 'httponly', 'samesite=lax')):
        raise RuntimeError('Production cookie flags missing')
    body, _ = request('api/auth/me')
    if json.loads(body) != user:
        raise RuntimeError('Session did not persist')
    body, _ = request('api/conversations')
    if json.loads(body) != []:
        raise RuntimeError('Disposable account has unexpected data')

    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), 'Release smoke resume: Python and Vue engineering.')
        pdf = doc.tobytes()
    boundary = 'smoke-' + secrets.token_hex(12)
    upload = (f'--{boundary}\r\nContent-Disposition: form-data; name="thread_id"\r\n\r\nsmoke-thread\r\n'
              f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="resume.pdf"\r\n'
              'Content-Type: application/pdf\r\n\r\n').encode() + pdf + f'\r\n--{boundary}--\r\n'.encode()
    body, _ = request('api/files/upload', data=upload, method='POST',
                      headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    file_id = json.loads(body)['file_id']
    body, _ = request('api/files/' + file_id)
    if body != pdf:
        raise RuntimeError('Uploaded file did not round-trip')
    request('api/files/' + file_id, method='DELETE')
    request('api/files/' + file_id, 404)

    # Session restoration, logout revocation and login all exercise the real gateway.
    old_cookie = next(cookie.value for cookie in jar if cookie.name == 'resume_session')
    request('api/auth/logout', 204, data=b'', method='POST')
    request('api/auth/me', 401, headers={'Cookie': 'resume_session=' + old_cookie})
    body, _ = post('api/auth/login', account)
    if json.loads(body) != user:
        raise RuntimeError('Login returned wrong account')
    request('api/auth/me')
    request('api/auth/logout', 204, data=b'', method='POST')
    print('PASS: HTTPS/static assets, secure cookie, register/session/logout/login, PDF upload/read/delete; no model calls.')


if __name__ == '__main__':
    main()
