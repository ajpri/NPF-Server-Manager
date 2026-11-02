"""Runs inside the selected NPF release; keeps its serving and authentication policy."""
import contextlib
import ipaddress
import json
import os
import re
from pathlib import Path
import secrets
import signal
import socket
import sys
import tempfile
import threading


def listener(module, directory, override):
    from platformConfiguration import loadConfiguration
    configuration = loadConfiguration(directory)
    # Older releases do not know these two settings. Read only their serving
    # values, using NPF's last-active-value/Extra-data boundary conventions.
    path = Path(directory)/'platform.conf'
    if path.exists() and 'listenAddress' not in configuration:
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line == '# --- Extra data (ignored) ---':
                break
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = (part.strip() for part in line.split('=', 1))
            if key in {'listenAddress', 'serverPort'}:
                configuration[key] = value
    address = str(configuration.get('listenAddress', '0.0.0.0'))
    address = '127.0.0.1' if address.lower() == 'localhost' else str(ipaddress.IPv4Address(address))
    configured_port = str(configuration.get('serverPort', 5000))
    port = configured_port if override == 'auto' else override
    for value in (configured_port, port):
        if not value.isascii() or not value.isdecimal() or len(value) > 9 or not 1 <= int(value) <= 65535:
            raise ValueError('Invalid NPF server port.')
    target = '127.0.0.1' if address == '0.0.0.0' else address
    # Validate locality without sending a request/secret to a nonlocal address.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.bind((target, 0))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect((target, int(port)))
        peer = route.getsockname()[0]
    if not ipaddress.IPv4Address(target).is_loopback and peer != target:
        raise ValueError('NPF listenAddress must be assigned to this computer.')
    helpers = [callable(getattr(module, name, None)) for name in ('createHttpServer', 'closeHttpServer')]
    requirements = (Path(module.__file__).parent/'requirements.txt').read_text(encoding='utf-8-sig')
    requires_waitress = any(re.match(r'waitress\b', line.strip(), re.I) for line in requirements.splitlines())
    if (any(helpers) or requires_waitress) and not all(helpers):
        raise RuntimeError('Incomplete NPF HTTP helper API; refusing a legacy fallback.')
    return {'listen_address': address, 'port': int(port),
            'port_source': 'npf-configuration' if override == 'auto' else 'manager-override',
            'backend': 'waitress' if all(helpers) else 'werkzeug-legacy',
            'readiness_host': target, 'readiness_peer': peer}


def create_application(module, database, directory, endpoint):
    path = Path(directory)/'platform.conf'
    # Legacy formatters would move new serving settings into ignored Extra data.
    # Keep the canonical settings usable across a rollback and subsequent upgrade.
    original = path.read_bytes() if endpoint['backend'] == 'werkzeug-legacy' and path.exists() else None
    try:
        application = module.createApp(databasePathOverride=database, configurationDirectory=directory)
    finally:
        if original is not None:
            fd, name = tempfile.mkstemp(dir=directory)
            temporary = Path(name)
            try:
                with os.fdopen(fd, 'wb') as out:
                    out.write(original)
                    out.flush()
                    os.fsync(out.fileno())
                if os.name != 'nt' and path.exists():
                    temporary.chmod(path.stat().st_mode & 0o777)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    application.config['PLATFORM_CONFIGURATION'].update(listenAddress=endpoint['listen_address'], serverPort=endpoint['port'])
    return application


def run_server(module, application, endpoint):
    original_wsgi = application.wsgi_app
    token = os.environ.get('NPF_MANAGER_READINESS', '')
    def wrapped(environ, start_response):
        if environ.get('PATH_INFO') == '/__npf_manager_ready':
            from werkzeug.wrappers import Response
            forwarded = any(key == 'HTTP_FORWARDED' or key.startswith('HTTP_X_FORWARDED_') for key in environ)
            supplied = environ.get('HTTP_X_NPF_MANAGER_READINESS', '')
            # Runs before NPF's TrustedProxy. Native helpers intentionally leave
            # the actual socket peer and forwarding headers intact for NPF.
            if (not token or not supplied.isascii() or not secrets.compare_digest(supplied, token) or forwarded
                    or environ.get('REMOTE_ADDR') != endpoint['readiness_peer']):
                return Response('Not found.', status=404)(environ, start_response)
            with application.app_context():
                result = dict(application.view_functions['health']())
            result.update(manager_instance=token, listener=endpoint)
            return Response(json.dumps(result), content_type='application/json', headers={'Cache-Control': 'no-store'})(environ, start_response)
        return original_wsgi(environ, start_response)
    application.wsgi_app = wrapped
    native = endpoint['backend'] == 'waitress'
    if native:
        # Pass the Flask object: NPF's helper needs app.config for request limits.
        server = module.createHttpServer(application, endpoint['listen_address'], endpoint['port'])
    else:
        from werkzeug.serving import make_server
        print('Compatibility backend: Werkzeug for an NPF release without HTTP helpers.', flush=True)
        server = make_server(endpoint['listen_address'], endpoint['port'], application, threaded=True)
    thread = threading.Thread(target=server.run if native else server.serve_forever, name='npf-http', daemon=True)
    stopping = threading.Event()
    def stop(*_):
        stopping.set()
    def watch_stop():
        path = os.environ.get('NPF_MANAGER_STOP_FILE')
        while not stopping.wait(.2):
            if path and Path(path).exists():
                stopping.set()
    try:
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        thread.start()
        if os.environ.get('NPF_MANAGER_STOP_FILE'):
            threading.Thread(target=watch_stop, name='npf-stop-file', daemon=True).start()
        while not stopping.wait(.2):
            if not thread.is_alive():
                raise RuntimeError('NPF HTTP serving thread exited unexpectedly.')
    finally:
        stopping.set()
        if native:
            if thread.ident is not None:
                module.closeHttpServer(server, thread)
            else:
                from waitress import wasyncore
                server.task_dispatcher.shutdown()
                wasyncore.close_all(server._map)
        else:
            if thread.is_alive():
                server.shutdown()
            if thread.ident is not None:
                thread.join()
            server.server_close()


def main():
    mode, app_path, database, directory, port = sys.argv[1:]
    os.environ['FINANCE_DATABASE_PATH'] = database
    sys.path.insert(0, app_path)
    os.chdir(app_path)
    if mode == 'validate-db':
        from database import initializeDatabase
        initializeDatabase(database)
        return
    import app
    endpoint = listener(app, directory, port)
    if mode in {'describe', 'validate-config'}:
        print(json.dumps(endpoint))
        return
    application = create_application(app, database, directory, endpoint)
    if mode == 'setup':
        from database import connectDatabase
        from users import configureOnStartup
        with contextlib.closing(connectDatabase(database)) as connection:
            connection.configuration = application.config['PLATFORM_CONFIGURATION']
            connection.operationalLogs = application.config['OPERATIONAL_LOGS']
            configureOnStartup(connection)
        return
    if mode != 'run':
        raise RuntimeError('Unknown bridge operation.')
    run_server(app, application, endpoint)


if __name__ == '__main__':
    main()
