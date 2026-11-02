"""Runs only inside the selected NPF release's own virtual environment."""
import contextlib
import os
from pathlib import Path
import signal
import sys
import threading


def main():
    mode, app_path, database, configuration, port = sys.argv[1:]
    os.environ['FINANCE_DATABASE_PATH'] = database
    sys.path.insert(0, app_path)
    os.chdir(app_path)
    if mode == 'validate-config':
        from platformConfiguration import loadConfiguration
        loadConfiguration(configuration)
        return
    if mode == 'validate-db':
        from database import initializeDatabase
        initializeDatabase(database)
        return
    import app
    application = app.createApp(databasePathOverride=database, configurationDirectory=configuration)
    if mode == 'setup':
        from database import connectDatabase
        from users import configureOnStartup
        with contextlib.closing(connectDatabase(database)) as connection:
            connection.configuration = application.config['PLATFORM_CONFIGURATION']
            connection.operationalLogs = application.config['OPERATIONAL_LOGS']
            configureOnStartup(connection)
            if not connection.execute("SELECT 1 FROM ledgerAccess WHERE role='owner'").fetchone():
                raise RuntimeError('Owner setup incomplete; rerun the installer to resume.')
        return
    if mode != 'run':
        raise RuntimeError('Unknown bridge operation.')
    from werkzeug.serving import make_server
    def wrapped(environ, start_response):
        # NPF's /health is authenticated. Keep it unchanged and expose readiness
        # only to the local supervisor, before NPF's authentication middleware.
        if environ.get('PATH_INFO') == '/__npf_manager_ready' and environ.get('REMOTE_ADDR') in {'127.0.0.1', '::1'}:
            import json
            from werkzeug.wrappers import Response
            with application.app_context():
                result = application.view_functions['health']()
                result['manager_instance'] = os.environ.get('NPF_MANAGER_READINESS', '')
            return Response(json.dumps(result), content_type='application/json')(environ, start_response)
        return application(environ, start_response)
    server = make_server('0.0.0.0', int(port), wrapped, threaded=True)
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    shutdown = threading.Event()
    def watch_stop():
        path = os.environ.get('NPF_MANAGER_STOP_FILE')
        while not shutdown.wait(.2):
            if path and Path(path).exists():
                server.shutdown()
                return
    if os.name == 'nt':
        threading.Thread(target=watch_stop, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        shutdown.set()
        server.server_close()


if __name__ == '__main__':
    main()
