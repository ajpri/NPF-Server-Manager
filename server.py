"""Bounded standard-library HTTPS management interface."""
import contextlib
import base64
import hashlib
import re
from collections import defaultdict, deque
import http.cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import ssl
import tempfile
import threading
import time
import urllib.parse

from manager import HERE, VERSION, MAX_UPLOAD, ManagerError, check_password, read_json, snapshot


class HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, manager):
        self.manager = manager
        self.page = (HERE / "index.html").read_bytes().replace(b"{{MANAGER_VERSION}}", VERSION.encode())
        scripts = re.findall(rb"<script>(.*?)</script>", self.page, re.S)
        styles = re.findall(rb"<style>(.*?)</style>", self.page, re.S)
        def hashes(blocks):
            return " ".join("'sha256-" + base64.b64encode(hashlib.sha256(b).digest()).decode() + "'" for b in blocks)
        self.csp = "default-src 'self'; script-src " + hashes(scripts) + "; style-src " + hashes(styles) + "; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        self.sessions = {}
        self.failures = defaultdict(deque)
        self.auth_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(16)
        super().__init__((manager.settings.get('bind', '0.0.0.0'), manager.settings.get('port', 5050)), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(manager.settings['certificate'], manager.settings['private_key'])
        # Handshake in bounded worker threads rather than blocking the accept loop.
        self.tls = tls

    def process_request(self, request, client_address):
        if not self.slots.acquire(False):
            request.close()
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        wrapped = None
        try:
            request.settimeout(30)
            wrapped = self.tls.wrap_socket(request, server_side=True)
            super().process_request_thread(wrapped, client_address)
        except (OSError, ssl.SSLError):
            request.close()
        finally:
            if wrapped:
                wrapped.close()
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'NPF-Server-Manager'
    sys_version = ''

    def log_message(self, *_):
        # Do not log query strings, credentials, upload contents, or configuration.
        pass

    @property
    def manager(self):
        return self.server.manager

    def reply(self, status, content, kind='application/json', headers=None):
        if kind == 'application/json':
            content = json.dumps(content).encode()
        elif isinstance(content, str):
            content = content.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', self.server.csp)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    def error(self, status, text):
        self.close_connection = True
        self.reply(status, {'error': text})

    def origin_check(self):
        origin = self.headers.get('Origin')
        if origin and origin != 'https://' + self.headers.get('Host', ''):
            raise ManagerError('Cross-origin request rejected.')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise ManagerError('Cross-site request rejected.')

    def authenticate(self, change=False):
        token = None
        bearer = self.headers.get('Authorization', '')
        if bearer.startswith('Bearer '):
            token = bearer[7:]
        else:
            cookie = http.cookies.SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie', ''))
                token = cookie['npf_session'].value if 'npf_session' in cookie else None
            except http.cookies.CookieError:
                pass
        epoch = read_json(self.manager.data/'auth.json', {}).get('epoch')
        with self.server.auth_lock:
            record = self.server.sessions.get(token)
            if not record or record['expires'] < time.monotonic() or record['epoch'] != epoch:
                raise PermissionError('Sign in required.')
        if change:
            self.origin_check()
            if not bearer and not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), record['csrf']):
                raise PermissionError('Invalid browser request token.')
        return token, record

    def length(self, limit):
        if self.headers.get('Transfer-Encoding'):
            raise ManagerError('Chunked request uploads are not supported; provide Content-Length.')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
            raise ManagerError('A single valid Content-Length is required.')
        size = int(lengths[0])
        if size > limit:
            raise ManagerError('Request exceeds size limit.')
        return size

    def body_json(self):
        size = self.length(65536)
        body = self.rfile.read(size)
        if len(body) != size:
            raise ManagerError('Incomplete request.')
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError) as error:
            raise ManagerError('Invalid JSON request.') from error
        if not isinstance(value, dict):
            raise ManagerError('Expected a JSON object.')
        for key in ('snapshot', 'restore_database', 'confirmed', 'restart'):
            if key in value and not isinstance(value[key], bool):
                raise ManagerError(key + ' must be a boolean.')
        return value

    def login(self):
        self.origin_check()
        address = self.client_address[0]
        now = time.monotonic()
        with self.server.auth_lock:
            for key in list(self.server.failures):
                queue = self.server.failures[key]
                while queue and queue[0] < now-600:
                    queue.popleft()
                if not queue:
                    del self.server.failures[key]
            failures = self.server.failures[address]
            if len(failures) >= 5 or sum(map(len, self.server.failures.values())) >= 100:
                self.error(429, 'Too many login attempts. Retry in ten minutes.')
                return
            # Count attempts before password verification to limit concurrent guessing.
            failures.append(now)
        values = self.body_json()
        if not check_password(self.manager.data, values.get('password')):
            self.error(401, 'Invalid administrator password.')
            return
        record = {'csrf': secrets.token_urlsafe(32), 'epoch': read_json(self.manager.data/'auth.json')['epoch'], 'expires': now+3600}
        token = secrets.token_urlsafe(32)
        with self.server.auth_lock:
            self.server.sessions = {t: r for t, r in self.server.sessions.items() if r['expires'] > now and r['epoch'] == record['epoch']}
            if len(self.server.sessions) >= 100:
                self.server.sessions.pop(next(iter(self.server.sessions)))
            self.server.sessions[token] = record
            self.server.failures.pop(address, None)
        self.reply(200, {'token': token, 'csrf': record['csrf'], 'expires_in': 3600}, headers={
            'Set-Cookie': f'npf_session={token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=3600'})

    @contextlib.contextmanager
    def api_errors(self):
        """Keep authentication and operation errors consistent across request handlers."""
        try:
            yield
        except PermissionError as error:
            self.error(401, str(error))
        except ManagerError as error:
            self.error(409 if self.command == 'POST' and 'busy' in str(error) else 400, str(error))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            message = ('Operation failed; preserve recovery files. Check private logs and available storage.'
                       if self.command == 'POST' else
                       'Operation failed. Check private service logs and available storage.')
            self.error(500, message)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        with self.api_errors():
            if path == '/':
                self.reply(200, self.server.page, 'text/html; charset=utf-8')
                return
            _, record = self.authenticate()
            if path == '/api/session':
                self.reply(200, {'csrf': record['csrf']})
            elif path == '/api/status':
                self.reply(200, self.manager.status())
            elif path in {'/api/download/application', '/api/download/database', '/api/download/configuration'}:
                self.download(path.rsplit('/', 1)[1])
            else:
                self.error(404, 'Not found.')

    def download(self, kind):
        if not self.manager.lock.acquire(False):
            raise ManagerError('Another operation is busy.')
        name = None
        try:
            if kind == 'configuration':
                path = self.manager.data/'platform.conf'
                if not path.exists():
                    self.error(404, 'No runtime platform.conf exists.')
                    return
                self.reply(200, path.read_bytes(), 'application/octet-stream', {'Content-Disposition': 'attachment; filename="platform.conf"'})
                return
            fd, name = tempfile.mkstemp(dir=self.manager.data/'exports', suffix='.zip' if kind == 'application' else '.db')
            import os
            os.close(fd)
            try:
                if kind == 'application':
                    self.manager.export_application(name)
                else:
                    snapshot(self.manager.database, name)
            except Exception:
                Path(name).unlink(missing_ok=True)
                raise
        finally:
            self.manager.lock.release()
        try:
            path = Path(name)
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip' if kind == 'application' else 'application/octet-stream')
            self.send_header('Content-Length', str(path.stat().st_size))
            self.send_header('Content-Disposition', 'attachment; filename="NPF-full-package.zip"' if kind == 'application' else 'attachment; filename="finance.db"')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            with path.open('rb') as source:
                while chunk := source.read(1024*1024):
                    self.wfile.write(chunk)
        finally:
            if name:
                Path(name).unlink(missing_ok=True)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        with self.api_errors():
            if path == '/api/login':
                self.login()
                return
            token, _ = self.authenticate(change=True)
            if path == '/api/logout':
                self.body_json()
                with self.server.auth_lock:
                    self.server.sessions.pop(token, None)
                self.reply(200, {'result': 'Signed out.'}, headers={'Set-Cookie': 'npf_session=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0'})
                return
            if path.startswith('/api/stage/'):
                self.upload(path.rsplit('/', 1)[1])
                return
            if path.startswith('/api/action/'):
                action = path.rsplit('/', 1)[1]
                result = self.manager.execute(action, self.body_json())
                self.reply(200, result)
            else:
                self.error(404, 'Not found.')

    def upload(self, kind):
        if kind not in {'application', 'database', 'configuration'}:
            raise ManagerError('Unknown upload kind.')
        limit = 1024*1024 if kind == 'configuration' else self.manager.settings.get('max_upload', MAX_UPLOAD)
        size = self.length(limit)
        import shutil
        if shutil.disk_usage(self.manager.data).free < size*2+64*1024*1024:
            raise ManagerError('Insufficient upload storage.')
        if not self.manager.lock.acquire(False):
            raise ManagerError('Another operation is busy.')
        try:
            with tempfile.NamedTemporaryFile(dir=self.manager.data, delete=False) as out:
                temporary = Path(out.name)
                try:
                    remaining = size
                    deadline = time.monotonic()+300
                    while remaining:
                        if time.monotonic() > deadline:
                            raise ManagerError('Upload deadline exceeded.')
                        chunk = self.rfile.read(min(1024*1024, remaining))
                        if not chunk:
                            raise ManagerError('Incomplete upload.')
                        out.write(chunk)
                        remaining -= len(chunk)
                    out.flush()
                    entry = self.manager.stage(temporary, kind)
                finally:
                    # Windows cannot unlink an open NamedTemporaryFile.
                    out.close()
                    temporary.unlink(missing_ok=True)
            self.reply(201, entry)
        finally:
            self.manager.lock.release()
