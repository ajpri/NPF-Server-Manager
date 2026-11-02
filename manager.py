"""NPF Server Manager: shared operations and process supervision."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
VERSION = (HERE/'VERSION').read_text(encoding='utf-8').strip()
if not re.fullmatch(r'\d+\.\d+(?:\.\d+)*', VERSION):
    raise ValueError('VERSION must contain a numeric release number.')
DEFAULT_DATA = Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData')) / 'NPF' if os.name == 'nt' else Path('/var/lib/npf')
MAX_UPLOAD = 512 * 1024 * 1024
MAX_EXTRACT = 2 * 1024 * 1024 * 1024


class ManagerError(Exception):
    pass


def read_json(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def atomic(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.stat() if path.exists() else None
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        if previous and os.name != 'nt' and os.geteuid() == 0:
            os.fchown(fd, previous.st_uid, previous.st_gid)
        with os.fdopen(fd, 'wb') as out:
            out.write(content.encode() if isinstance(content, str) else content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path, value):
    atomic(path, json.dumps(value, indent=2) + '\n')


def private_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        Path(path).chmod(0o700)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise ManagerError('Invalid item identifier.')
    return value


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def boot_id():
    if os.name != 'nt':
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.GetTickCount64.restype = ctypes.c_ulonglong
    # Rounded boot timestamp tolerates slight clock/measurement drift.
    return str(round((time.time() - kernel.GetTickCount64() / 1000) / 60))


def python_for(release):
    return Path(release) / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def metadata(app):
    result = {}
    version_file = 'packageApplication.py' if (Path(app) / 'packageApplication.py').exists() else 'app.py'
    for filename, key in [(version_file, 'applicationVersion'), ('database.py', 'schemaVersion')]:
        tree = ast.parse((Path(app) / filename).read_text(encoding='utf-8-sig'))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == key for t in node.targets):
                try:
                    result[key] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
    if not isinstance(result.get('applicationVersion'), str) or not isinstance(result.get('schemaVersion'), int):
        raise ManagerError('NPF package needs literal applicationVersion and schemaVersion metadata.')
    if result['schemaVersion'] < 22:
        raise ManagerError('This manager supports the multi-ledger NPF schema contract (22 or newer).')
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', result['applicationVersion']):
        raise ManagerError('Unrecognized NPF version format.')
    return result


def database_info(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ManagerError('Database does not exist.')
    try:
        with contextlib.closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
            if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ManagerError('SQLite integrity check failed.')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'users', 'accounts', 'ledgerAccess', 'ledgers', 'transactions'} <= tables:
                raise ManagerError('Not a supported full-installation NPF database.')
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise ManagerError('Database foreign-key verification failed.')
            return {'schema': db.execute('PRAGMA user_version').fetchone()[0],
                    'owner': db.execute("SELECT 1 FROM ledgerAccess WHERE role='owner' LIMIT 1").fetchone() is not None}
    except sqlite3.Error as error:
        raise ManagerError('Cannot read a valid NPF SQLite database.') from error


def recover_database(path):
    """Allow SQLite itself to roll back a hot journal, with the managed writer stopped."""
    path = Path(path).resolve()
    if not path.exists():
        return
    with contextlib.closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=15)) as connection:
        connection.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()


def snapshot(source, target):
    source, target = Path(source).resolve(), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name('.snapshot-' + uuid.uuid4().hex)
    try:
        with contextlib.closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=15)) as src:
            with contextlib.closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst)
        database_info(temporary)
        if os.name != 'nt':
            temporary.chmod(0o600)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def install_database(source, target):
    target = Path(target)
    # Called only with managed NPF stopped. Retain old database in verified recovery snapshot.
    for suffix in ('-wal', '-shm', '-journal'):
        Path(str(target) + suffix).unlink(missing_ok=True)
    snapshot(source, target)


def set_password(data, password):
    if not 8 <= len(password) <= 128:
        raise ManagerError('Administrator password must contain 8–128 characters.')
    salt = secrets.token_bytes(16)
    record = {'salt': salt.hex(), 'hash': hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex(),
              'epoch': uuid.uuid4().hex}
    write_json(Path(data) / 'auth.json', record)


def check_password(data, password):
    record = read_json(Path(data) / 'auth.json', {})
    if not isinstance(password, str) or not 8 <= len(password) <= 128 or not record:
        return False
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(record['salt']), n=16384, r=8, p=1).hex()
    return secrets.compare_digest(record['hash'], value)


class InstanceLock:
    def __init__(self, data):
        self.file = (Path(data) / 'service.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                self.file.write(b'0')
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise ManagerError('Manager is already running; use its API/CLI.') from error

    def close(self):
        self.file.close()


class Manager:
    def __init__(self, data):
        self.data = Path(data).resolve()
        self.settings = read_json(self.data / 'settings.json')
        if not self.settings:
            raise ManagerError('Not installed. Run the platform installer first.')
        self.root = Path(self.settings['application_root'])
        self.releases = self.root / 'releases'
        self.database = Path(self.settings['database'])
        for path in [self.data, self.data / 'staged', self.data / 'recovery', self.data / 'history', self.data / 'exports', self.releases]:
            private_dir(path)
        self.state = read_json(self.data / 'state.json', {'active': None, 'history': [], 'stopped': False, 'boot': boot_id(), 'result': 'Installed.'})
        if self.state.get('boot') != boot_id():
            self.state.update(boot=boot_id(), stopped=False, restart_attempts=0, failed=False)
            self.save()
        self.lock = threading.Lock()
        self.process = None
        self.ready = False
        self.process_log = None
        self.started_at = 0
        self.retries = self.state.get('restart_attempts', 0)
        self.next_retry = 0
        self.failed = self.state.get('failed', False)
        self.busy = None
        self.closed = threading.Event()
        self.instance_lock = None

    def save(self):
        if hasattr(self, 'retries'): self.state['restart_attempts'] = self.retries
        if hasattr(self, 'failed'): self.state['failed'] = self.failed
        write_json(self.data / 'state.json', self.state)

    def release(self, item=None):
        return self.releases / identifier(item or self.state.get('active'))

    def app(self, item=None):
        return self.release(item) / 'app'

    def release_meta(self, item=None):
        return read_json(self.release(item) / 'release.json')

    def result(self, message):
        self.state['result'] = message
        self.save()

    def items(self, category):
        base = self.data / category
        return [read_json(p / 'item.json') for p in sorted(base.iterdir()) if p.is_dir() and (p / 'item.json').exists()]

    def history_item(self, item):
        directory = self.data/'history'/item
        snapshot_path = directory/'finance.db'
        exists = snapshot_path.is_file()
        saved = read_json(directory/'item.json', {})
        return dict(id=item, **self.release_meta(item), snapshot=exists,
                    snapshot_time=(saved.get('time') or snapshot_path.stat().st_mtime) if exists else None)

    def status(self):
        running = self.process is not None and self.process.poll() is None
        return {'manager_version': VERSION, 'npf': 'busy' if self.busy else (('running' if self.ready else 'starting') if running else ('failed' if self.failed else 'stopped')),
                'operation': self.busy, 'active': self.state.get('active'), 'version': self.release_meta() if self.state.get('active') else None,
                'result': self.state.get('result'), 'pending_configuration': self.state.get('pending_configuration', False),
                'restart_attempts': self.retries, 'history': [self.history_item(x) for x in self.state['history']],
                'staged': self.items('staged'), 'recovery': self.items('recovery')}

    def log_path(self):
        return self.data / 'npf.log'

    def rotate_log(self):
        path = self.log_path()
        if path.exists() and path.stat().st_size > 1024 * 1024:
            for i in range(2, 0, -1):
                previous = Path(str(path) + f'.{i}')
                if previous.exists():
                    previous.replace(str(path) + f'.{i+1}')
            path.replace(str(path) + '.1')

    def sync_configuration(self, to_app=True):
        persistent = self.data / 'platform.conf'
        runtime = self.app() / 'platform.conf'
        source, target = (persistent, runtime) if to_app else (runtime, persistent)
        if source.exists():
            atomic(target, source.read_bytes())
        else:
            target.unlink(missing_ok=True)

    def bridge(self, mode, *, release=None, database=None, config=None, timeout=90):
        item = release or self.state['active']
        command = [str(python_for(self.release(item))), str(HERE / 'npf_bridge.py'), mode, str(self.app(item)),
                   str(database or self.database), str(config or self.app(item)), str(self.settings.get('npf_port', 5000))]
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        if completed.returncode:
            raise ManagerError('NPF validation failed. Check package/database/configuration compatibility; live files were not used for validation.')
        return completed.stdout.decode().strip()

    def validate_database(self, source, release=None):
        item = release or self.state['active']
        info = database_info(source)
        target_schema = self.release_meta(item)['schemaVersion']
        if info['schema'] < 22 or info['schema'] > target_schema:
            raise ManagerError('Database schema is unsupported by the selected application.')
        # Exercise upstream initialization/migrations on a disposable copy only.
        with tempfile.TemporaryDirectory(dir=self.data) as directory:
            copied = Path(directory) / 'finance.db'
            snapshot(source, copied)
            self.bridge('validate-db', release=item, database=copied, config=directory)
        return info

    def validate_configuration(self, source, release=None):
        with tempfile.TemporaryDirectory(dir=self.data) as directory:
            shutil.copyfile(source, Path(directory) / 'platform.conf')
            self.bridge('validate-config', release=release, config=directory)

    def start_process(self):
        if self.process and self.process.poll() is None:
            return
        if not self.state.get('active'):
            raise ManagerError('No active NPF version.')
        self.sync_configuration()
        recover_database(self.database)
        if not database_info(self.database)['owner']:
            raise ManagerError('NPF Owner setup must be completed in the installer.')
        self.rotate_log()
        self.process_log = self.log_path().open('ab', buffering=0)
        kwargs = {'start_new_session': True} if os.name != 'nt' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        command = [str(python_for(self.release())), str(HERE/'npf_bridge.py'), 'run', str(self.app()), str(self.database), str(self.app()), str(self.settings.get('npf_port', 5000))]
        self.readiness_token = secrets.token_hex(32)
        (self.data/'stop-child').unlink(missing_ok=True)
        environment = dict(os.environ, NPF_MANAGER_READINESS=self.readiness_token, NPF_MANAGER_STOP_FILE=str(self.data/'stop-child'))
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=self.process_log, stderr=subprocess.STDOUT, cwd=self.app(), env=environment, **kwargs)
        write_json(self.data/'child.json', {'pid': self.process.pid})
        self.started_at = time.monotonic()
        try:
            deadline = time.monotonic() + self.settings.get('startup_timeout', 60)
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{self.settings.get("npf_port",5000)}/__npf_manager_ready', timeout=1) as response:
                        health = json.load(response)
                    if (health.get('status') == 'ok' and health.get('manager_instance') == self.readiness_token and health.get('applicationVersion') == self.release_meta()['applicationVersion']
                            and health.get('schemaVersion') == self.release_meta()['schemaVersion']):
                        if self.process.poll() is not None:
                            break
                        self.state['pending_configuration'] = False
                        self.sync_configuration(False)
                        self.failed = False
                        self.ready = True
                        self.save()
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(0.2)
            raise ManagerError('NPF failed readiness verification. See private npf.log.')
        except Exception:
            self.stop_process()
            raise

    def stop_process(self):
        self.ready = False
        if self.process:
            if self.process.poll() is None:
                if os.name == 'nt':
                    atomic(self.data/'stop-child', 'stop')
                else:
                    os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    if os.name != 'nt':
                        os.killpg(self.process.pid, signal.SIGKILL)
                    else:
                        subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],capture_output=True,timeout=10)
                    self.process.wait(timeout=10)
            self.process = None
        if self.process_log:
            self.process_log.close()
            self.process_log = None
        (self.data/'child.json').unlink(missing_ok=True)
        if self.state.get('active'):
            self.sync_configuration(False)

    def launch(self):
        pending = self.state.get('pending_update')
        try:
            self.start_process()
        except Exception as error:
            if pending:
                try:
                    self.restore(pending['backup'])
                    self.state.pop('pending_update', None)
                    self.state.pop('unverified', None)
                    self.result('Deferred update failed startup; previous installation restored.')
                except Exception:
                    self.recovery_failed('Deferred update and recovery failed; NPF stopped. Preserve recovery files.')
            raise error
        if pending:
            self.state.pop('pending_update', None)
            self.state.pop('unverified', None)
            self.prune()
            shutil.rmtree(pending['backup'], ignore_errors=True)
            shutil.rmtree(self.data/'staged'/pending['stage'], ignore_errors=True)
        self.save()

    def monitor(self):
        while not self.closed.wait(0.5):
            if not self.lock.acquire(blocking=False):
                continue
            try:
                if self.state.get('stopped') or self.failed or not self.state.get('active'):
                    continue
                if self.process and self.process.poll() is None:
                    if self.retries and time.monotonic()-self.started_at >= 300:
                        self.retries = 0
                        self.save()
                    continue
                if self.process:
                    self.stop_process()
                    self.next_retry = time.monotonic()+10
                if not self.next_retry:
                    self.next_retry = time.monotonic()+10
                if time.monotonic() < self.next_retry:
                    continue
                if self.retries >= 3:
                    self.failed = True
                    self.result('NPF crash restart limit reached; use Start to retry.')
                    continue
                self.retries += 1
                self.next_retry = 0
                try:
                    self.launch()
                    if self.state.get('unverified'):
                        self.state.pop('unverified')
                        self.prune()
                except Exception as error:
                    self.next_retry = time.monotonic()+10
                    self.result('NPF automatic restart failed ('+type(error).__name__+'); retry '+str(self.retries)+' of 3. See private npf.log.')
            finally:
                self.lock.release()

    def stage(self, source, kind, origin='local; publisher unverified'):
        if kind not in {'application', 'database', 'configuration'}:
            raise ManagerError('Unknown upload type.')
        source = Path(source)
        limit = self.settings.get('max_upload', MAX_UPLOAD) if kind != 'configuration' else 1024*1024
        if not source.is_file() or source.stat().st_size > limit:
            raise ManagerError('Upload missing or exceeds size limit.')
        if shutil.disk_usage(self.data).free < source.stat().st_size*2+64*1024*1024:
            raise ManagerError('Insufficient space to stage upload.')
        item = uuid.uuid4().hex
        directory = self.data/'staged'/item
        private_dir(directory)
        try:
            shutil.copyfile(source, directory/'file')
            details = {'id': item, 'kind': kind, 'time': time.time(), 'sha256': digest(directory/'file'), 'origin': origin}
            if kind == 'application':
                self.extract(directory/'file', directory/'preview')
                details.update(metadata(directory/'preview'))
                shutil.rmtree(directory/'preview')
            elif kind == 'database':
                details.update(self.validate_database(directory/'file'))
            else:
                self.validate_configuration(directory/'file')
            write_json(directory/'item.json', details)
            return details
        except Exception:
            shutil.rmtree(directory)
            raise

    def extract(self, archive, destination):
        destination = Path(destination)
        private_dir(destination)
        try:
            with zipfile.ZipFile(archive) as z:
                members = z.infolist()
                if len(members) > 20000 or sum(m.file_size for m in members) > self.settings.get('max_extract', MAX_EXTRACT):
                    raise ManagerError('Extracted archive exceeds limits.')
                if shutil.disk_usage(destination).free < sum(m.file_size for m in members)+64*1024*1024:
                    raise ManagerError('Insufficient space for extraction.')
                names = [PurePosixPath(m.filename) for m in members]
                root = '' if any(str(p)=='app.py' for p in names) else 'No-Part-Finance/'
                used = set()
                for m,p in zip(members,names):
                    mode = m.external_attr >> 16
                    if ('\\' in m.filename or p.is_absolute() or '..' in p.parts or ':' in m.filename
                            or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0,stat.S_IFREG,stat.S_IFDIR})):
                        raise ManagerError('Unsafe archive path or file type.')
                    if root and not m.filename.startswith(root):
                        raise ManagerError('Package must use the No-Part-Finance root folder.')
                    relative = m.filename[len(root):]
                    if not relative or m.is_dir():
                        continue
                    normalized = relative.casefold()
                    if normalized in used:
                        raise ManagerError('Duplicate archive paths.')
                    used.add(normalized)
                    if PurePosixPath(relative).name in {'finance.db','finance.db-wal','finance.db-shm','platform.conf'} or PurePosixPath(relative).parts[0] in {'.venv','venv','logs','backups','packages'}:
                        continue
                    target = destination/relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(m) as src, target.open('wb') as out:
                        shutil.copyfileobj(src,out,1024*1024)
                    target.chmod(0o600)
            for name in ['app.py','database.py','users.py','platformConfiguration.py','packageApplication.py','requirements.txt']:
                if not (destination/name).is_file():
                    raise ManagerError('Package is missing required NPF application files.')
            metadata(destination)
        except (zipfile.BadZipFile, RuntimeError) as error:
            raise ManagerError('Malformed or encrypted ZIP package.') from error

    def prepare(self, source, origin):
        item = uuid.uuid4().hex
        release = self.releases/item
        private_dir(release)
        try:
            self.extract(source, release/'app')
            details = dict(metadata(release/'app'), time=time.time(), source_sha256=digest(source), origin=origin)
            write_json(release/'release.json', details)
            shutil.copyfile(source,release/'source.zip')
            log = self.data/'dependencies.log'
            with log.open('ab') as out:
                subprocess.run([sys.executable,'-m','venv',str(release/'venv')],check=True,stdout=out,stderr=out,timeout=180)
                subprocess.run([str(python_for(release)),'-m','pip','install','--disable-pip-version-check','-r',str(release/'app/requirements.txt')],check=True,stdout=out,stderr=out,timeout=600)
            return item
        except Exception:
            shutil.rmtree(release,ignore_errors=True)
            raise

    def export_application(self, target):
        """Caller holds the operation lock; export code plus a complete SQLite snapshot."""
        if not self.state['active']:
            raise ManagerError('No installed NPF application to download.')
        excluded = {'finance.db', 'finance.db-wal', 'finance.db-shm', 'finance.db-journal',
                    'platform.conf', '.venv', 'venv', '__pycache__', 'logs', 'backups', 'packages'}
        with tempfile.TemporaryDirectory(dir=self.data/'exports') as temporary:
            database = Path(temporary)/'finance.db'
            snapshot(self.database, database)
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                app = self.app()
                for root, directories, files in os.walk(app, followlinks=False):
                    directories[:] = sorted(name for name in directories if name not in excluded and not (Path(root)/name).is_symlink())
                    for name in sorted(files):
                        path = Path(root)/name
                        if name in excluded or name.endswith('.pyc'):
                            continue
                        if path.is_symlink() or not path.is_file():
                            raise ManagerError('Cannot package an unsafe application file.')
                        archive.write(path, 'No-Part-Finance/' + path.relative_to(app).as_posix())
                archive.write(database, 'No-Part-Finance/finance.db')
                configuration = self.data/'platform.conf'
                if configuration.exists():
                    archive.write(configuration, 'No-Part-Finance/platform.conf')

    def capture(self, target=None):
        item = uuid.uuid4().hex
        directory = Path(target) if target else self.data/'recovery'/item
        private_dir(directory)
        if not self.process or self.process.poll() is not None:
            recover_database(self.database)
        snapshot(self.database,directory/'finance.db')
        configuration = self.data/'platform.conf'
        if configuration.exists():
            atomic(directory/'platform.conf',configuration.read_bytes())
        details = {'id': item, 'kind':'recovery', 'time':time.time(), 'active':self.state['active'],
                   'database':True, 'configuration':configuration.exists(), 'stopped':self.state['stopped'],
                   'history':list(self.state['history']), 'pending_configuration':self.state.get('pending_configuration',False)}
        write_json(directory/'item.json',details)
        return directory

    def begin(self, backup, operation):
        write_json(self.data/'operation.json',{'backup':str(backup), 'operation':operation})

    def restore_configuration(self, backup, record=None):
        directory = Path(backup)
        record = record or read_json(directory/'item.json')
        if record['configuration']:
            atomic(self.data/'platform.conf', (directory/'platform.conf').read_bytes())
        else:
            (self.data/'platform.conf').unlink(missing_ok=True)
        self.state['pending_configuration'] = record['pending_configuration']
        self.sync_configuration()

    def restore(self, backup, restart=True):
        directory = Path(backup)
        record = read_json(directory/'item.json')
        self.stop_process()
        if record['database']:
            install_database(directory/'finance.db',self.database)
        elif database_info(self.database)['schema'] != self.release_meta(record['active'])['schemaVersion']:
            raise ManagerError('No matching database snapshot; compatibility cannot be established. NPF remains stopped.')
        self.state.update(active=record['active'],history=record['history'],stopped=record['stopped'],pending_configuration=record['pending_configuration'])
        self.restore_configuration(directory, record)
        self.save()
        if restart and not self.state['stopped']:
            self.start_process()
        (self.data/'operation.json').unlink(missing_ok=True)

    def recovery_failed(self, message):
        self.failed = True
        self.state['stopped'] = True
        self.result(message)

    def recover_backup(self, backup, success, failure, restart=True):
        try:
            self.restore(backup, restart=restart)
            self.result(success)
        except Exception:
            self.recovery_failed(failure)

    def recover(self):
        record = read_json(self.data/'operation.json')
        if record:
            self.recover_backup(record['backup'],
                'Interrupted '+record['operation']+' recovered; previous state restored.',
                'Interrupted operation could not be recovered. Preserve operation.json and recovery files; NPF remains stopped.',
                restart=False)

    def prune(self):
        old = list(self.state['history'])
        self.state['history'] = old[:2]
        self.save()
        protected = {r['active'] for r in self.items('recovery')}
        for item in old[2:]:
            shutil.rmtree(self.data/'history'/item,ignore_errors=True)
            if item not in protected and item != self.state['active']:
                shutil.rmtree(self.release(item),ignore_errors=True)

    def apply_application(self, item):
        directory = self.data/'staged'/identifier(item)
        entry = read_json(directory/'item.json',{})
        if entry.get('kind')!='application':
            raise ManagerError('Select a staged application package.')
        new = self.prepare(directory/'file',entry['origin'])
        try:
            self.validate_database(self.database,new)
            if (self.data/'platform.conf').exists():
                self.validate_configuration(self.data/'platform.conf',new)
            self.stop_process()
            old = self.state['active']
            backup = self.capture()
            # Version history uses a copy; operation recovery remains independently retained.
            history = self.data/'history'/old
            if history.exists():
                shutil.rmtree(history)
            shutil.copytree(backup,history)
            self.begin(backup,'application update')
            self.state['active'] = new
            self.state['history'] = [old]+[x for x in self.state['history'] if x!=old]
            self.sync_configuration()
            self.save()
            if not self.state['stopped']:
                self.start_process()
                (self.data/'operation.json').unlink(missing_ok=True)
                shutil.rmtree(backup)
                self.prune()
            else:
                self.state['unverified'] = True
                self.state['pending_update'] = {'backup': str(backup), 'stage': item}
                (self.data/'operation.json').unlink(missing_ok=True)
            self.result('Application updated.' if not self.state['stopped'] else 'Application files updated; startup verification pending next Start.')
            if not self.state.get('unverified'):
                shutil.rmtree(directory)
        except Exception as error:
            if (self.data/'operation.json').exists():
                try:
                    self.restore(read_json(self.data/'operation.json')['backup'])
                    self.result('Update failed; previous installation restored.')
                except Exception:
                    self.recovery_failed('Update and automatic recovery failed. NPF stopped; preserve recovery files and operation.json.')
            elif not self.state['stopped'] and not self.process:
                with contextlib.suppress(Exception):
                    self.start_process()
            raise ManagerError(self.state.get('result') if (self.data/'operation.json').exists() else str(error)) from error

    def rollback(self, item):
        item = identifier(item)
        if item not in self.state['history']:
            raise ManagerError('Select a retained version.')
        source = self.data/'history'/item/'finance.db'
        if not source.exists():
            raise ManagerError('No associated database snapshot.')
        self.validate_database(source,item)
        if (self.data/'platform.conf').exists():
            self.validate_configuration(self.data/'platform.conf',item)
        self.stop_process()
        backup = self.capture()
        outgoing_history = self.data/'history'/self.state['active']
        if outgoing_history.exists(): shutil.rmtree(outgoing_history)
        shutil.copytree(backup,outgoing_history)
        self.begin(backup,'rollback')
        try:
            old=self.state['active']
            install_database(source,self.database)
            self.state.update(active=item,history=[old]+[x for x in self.state['history'] if x!=item])
            self.sync_configuration()
            self.save()
            if not self.state['stopped']:
                self.start_process()
            (self.data/'operation.json').unlink(missing_ok=True)
            self.result('Rollback complete.' if not self.state['stopped'] else 'Rollback files applied; startup unverified while stopped.')
        except Exception as error:
            self.recover_backup(backup,
                'Rollback failed; pre-rollback state restored.',
                'Rollback and recovery failed; NPF stopped. Preserve recovery materials.')
            raise ManagerError(self.state['result']) from error

    def replace(self, item, kind, restart=False):
        self._replace_files({kind: item}, restart, single_kind=kind)

    def replace_files(self, database=None, configuration=None):
        selected = {kind: item for kind, item in [('database', database), ('configuration', configuration)] if item is not None}
        self._replace_files(selected, restart=True)

    def _replace_files(self, selected, restart, single_kind=None):
        """One validation/backup/recovery path for CLI replacements and combined web uploads."""
        if not selected:
            raise ManagerError('Choose platform.conf, finance.db, or both.')
        directories = {}
        for kind, item in selected.items():
            directory = self.data/'staged'/identifier(item)
            if kind not in {'database', 'configuration'} or read_json(directory/'item.json', {}).get('kind') != kind:
                raise ManagerError('Staged file type does not match operation.')
            validator = self.validate_database if kind == 'database' else self.validate_configuration
            validator(directory/'file')
            directories[kind] = directory
        running = self.process is not None and self.process.poll() is None
        should_restart = restart or (running and 'database' in directories)
        if should_restart:
            self.stop_process()
        backup = self.capture()
        self.begin(backup, (single_kind or 'file')+' replacement')
        try:
            if 'database' in directories:
                install_database(directories['database']/'file', self.database)
            if 'configuration' in directories:
                atomic(self.data/'platform.conf', (directories['configuration']/'file').read_bytes())
                self.sync_configuration()
                self.state['pending_configuration'] = not should_restart
            if should_restart:
                self.state['stopped'] = False
                self.retries = 0
                self.failed = False
                self.save()
                self.start_process()
            self.save()
            (self.data/'operation.json').unlink(missing_ok=True)
            result = single_kind.title()+' replaced.' if single_kind else 'Files replaced. No Part Finance restarted.'
            if single_kind == 'configuration' and not should_restart:
                result += ' Changes take effect on next start/restart.'
            self.result(result)
        except Exception as error:
            try:
                if not should_restart and set(directories) == {'configuration'}:
                    # The live writer is still running: restore only configuration, never its database.
                    self.restore_configuration(backup)
                    self.save()
                    (self.data/'operation.json').unlink(missing_ok=True)
                else:
                    self.restore(backup)
                self.result('Replacement failed; previous '+single_kind+' restored.' if single_kind else
                            'File replacement failed; previous database and configuration restored.')
            except Exception:
                self.recovery_failed('Replacement and recovery failed; NPF stopped. Preserve recovery files.')
            raise ManagerError(self.state['result']) from error
        for directory in directories.values():
            shutil.rmtree(directory)

    def github(self):
        request=urllib.request.Request('https://api.github.com/repos/ajpri/No-Part-Finance/releases/latest',headers={'User-Agent':'NPF-Server-Manager/'+VERSION})
        with urllib.request.urlopen(request,timeout=30) as response:
            release=json.load(response)
        if release.get('draft') or release.get('prerelease'):
            raise ManagerError('A stable published release is required.')
        assets=release.get('assets',[])
        package=next((x for x in assets if '-no-db-' in x['name'] and x['name'].endswith('.zip')),None)
        checksum=next((x for x in assets if package and x['name']==package['name']+'.sha256'),None)
        if not package or not checksum:
            raise ManagerError('Stable release lacks an application-only ZIP or SHA-256 checksum; supply a verified local ZIP.')
        with tempfile.TemporaryDirectory(dir=self.data) as temp:
            target=Path(temp)/'release.zip'
            for asset,path,limit in [(package,target,self.settings.get('max_upload',MAX_UPLOAD)),(checksum,Path(temp)/'checksum',65536)]:
                url=asset['browser_download_url']
                if not url.startswith('https://github.com/ajpri/No-Part-Finance/releases/download/'):
                    raise ManagerError('Unexpected release download URL.')
                with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'NPF-Server-Manager/'+VERSION}),timeout=60) as response, path.open('wb') as out:
                    total=0
                    while chunk:=response.read(1024*1024):
                        total+=len(chunk)
                        if total>limit:
                            raise ManagerError('Release asset exceeds download limit.')
                        out.write(chunk)
            expected=(Path(temp)/'checksum').read_text().split()[0].lower()
            if not re.fullmatch('[a-f0-9]{64}',expected) or digest(target)!=expected:
                raise ManagerError('Release checksum mismatch.')
            return self.stage(target,'application','GitHub '+release['tag_name']+'; SHA-256 verified')

    def execute(self, action, values=None):
        values=values or {}
        if not self.lock.acquire(blocking=False):
            raise ManagerError('Another operation is busy. Retry when it finishes.')
        self.busy=action
        try:
            if self.state.get('pending_update') and action not in {'start','restart','stop','github'}:
                raise ManagerError('The previous update awaits startup verification; Start NPF before further changes.')
            if (self.data/'operation.json').exists() and action not in {'stop'}:
                raise ManagerError('Unresolved recovery operation; inspect recovery files before further changes.')
            if action in {'start','restart'}:
                if action=='restart':
                    self.stop_process()
                self.state['stopped']=False
                self.retries=0
                self.next_retry=0
                self.failed=False
                self.save()
                self.launch()
                if self.state.pop('unverified',False):
                    self.prune()
                self.result('NPF running.')
            elif action=='stop':
                self.state['stopped']=True
                self.save()
                self.stop_process()
                self.result('NPF intentionally stopped until Start or reboot.')
            elif action=='github':
                return self.github()
            elif action=='apply':
                if values.get('snapshot',True) is not True:
                    raise ManagerError('Backups must include finance.db; snapshot opt-out is no longer supported.')
                self.apply_application(values['id'])
            elif action=='rollback':
                if not values.get('confirmed'):
                    raise ManagerError('Explicit rollback confirmation required.')
                if values.get('restore_database',True) is not True:
                    raise ManagerError('Restore requires the matching database backup; keeping the current database is not supported.')
                self.rollback(values['id'])
            elif action=='replace-files':
                if values.get('confirmed') is not True:
                    raise ManagerError('Explicit replacement confirmation required.')
                self.replace_files(values.get('database'), values.get('configuration'))
            elif action in {'replace-database','replace-configuration'}:
                if not values.get('confirmed'):
                    raise ManagerError('Explicit replacement confirmation required.')
                if action=='replace-configuration' and not isinstance(values.get('restart'),bool):
                    raise ManagerError('Supply restart as a boolean.')
                self.replace(values['id'],action.removeprefix('replace-'),values.get('restart',False))
            elif action=='delete':
                category=values.get('category')
                if category not in {'staged','recovery'}:
                    raise ManagerError('Only staged files or recovery copies may be explicitly deleted.')
                item=identifier(values['id'])
                if (self.data/'operation.json').exists():
                    raise ManagerError('Resolve recovery before deleting files.')
                shutil.rmtree(self.data/category/item)
            else:
                raise ManagerError('Unknown action.')
            return {'result':self.state.get('result','Complete.')}
        except Exception:
            if not self.state.get('stopped') and (not self.process or self.process.poll() is not None):
                self.next_retry=time.monotonic()+10
            raise
        finally:
            self.busy=None
            self.lock.release()

    def start_service(self):
        self.instance_lock=InstanceLock(self.data)
        # systemd KillMode=control-group and Windows service job containment remove children on service failure.
        stale=read_json(self.data/'child.json')
        if stale and os.name!='nt':
            pid=stale.get('pid')
            try:
                command=Path(f'/proc/{pid}/cmdline').read_bytes()
                if str(HERE/'npf_bridge.py').encode() in command and str(self.database).encode() in command:
                    os.killpg(pid,signal.SIGTERM)
                    for _ in range(50):
                        if not Path(f'/proc/{pid}').exists(): break
                        time.sleep(.1)
                    else: os.killpg(pid,signal.SIGKILL)
            except (FileNotFoundError,ProcessLookupError):
                pass
        self.recover()
        for export in (self.data/'exports').iterdir():
            if export.is_file(): export.unlink()
        if not self.state.get('stopped') and not self.failed:
            try:
                self.launch()
                if self.state.pop('unverified',False): self.prune()
            except Exception:
                self.next_retry=time.monotonic()+10
                self.result('NPF startup failed; bounded retry pending. See private npf.log.')
        self.monitor_thread=threading.Thread(target=self.monitor,daemon=True,name='NPF supervisor')
        self.monitor_thread.start()

    def close(self):
        self.closed.set()
        if hasattr(self,'monitor_thread'):
            self.monitor_thread.join(timeout=90)
        with self.lock:
            self.stop_process()
        if self.instance_lock:
            self.instance_lock.close()
