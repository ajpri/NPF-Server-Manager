"""Run only in remote disposable QEMU guests; uses public NPF code and synthetic data."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import uuid
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from manager import Manager, ManagerError, VERSION, atomic, boot_id, check_password, database_info, metadata, python_for, read_json, recover_database, set_password, snapshot, write_json
from installer import certificate
from server import HTTPServer


class Operations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=os.environ.get('NPF_TEST_ZIP')
        if not source:
            raise unittest.SkipTest('NPF_TEST_ZIP must select a public application-only release, inside a remote test VM.')
        cls.temp=tempfile.TemporaryDirectory()
        cls.base=Path(cls.temp.name)
        cls.data=cls.base/'data'; cls.data.mkdir()
        cls.root=cls.base/'application'; cls.root.mkdir()
        write_json(cls.data/'settings.json',{'application_root':str(cls.root),'database':str(cls.data/'finance.db'),'port':15050,'npf_port':15000,'startup_timeout':5})
        manager=Manager(cls.data)
        cls.release_id=manager.prepare(Path(source),'public integration fixture')
        manager.state['active']=cls.release_id; manager.state['stopped']=True; manager.save()
        code='''import sys
sys.path.insert(0,sys.argv[1])
from database import initializeDatabase,connectDatabase,bindInitialLedger,createPersonalLedger,createIntegrationSecret
from users import createUser
initializeDatabase(sys.argv[2])
c=connectDatabase(sys.argv[2]); bindInitialLedger(c)
owner=createUser(c,'owner','synthetic-owner','Synthetic-NPF-Test-26!','Synthetic','Owner')
createIntegrationSecret(c,'Synthetic integration')
createPersonalLedger(c,owner,'Second synthetic ledger')
c.close()
'''
        subprocess.run([str(python_for(manager.release())),'-c',code,str(manager.app()),str(manager.database)],check=True,capture_output=True)
        cls.schema=database_info(manager.database)['schema']
        cls.settings=manager.settings

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.work=tempfile.TemporaryDirectory(); self.directory=Path(self.work.name)
        self.data=self.directory/'data'; self.data.mkdir()
        self.root=self.directory/'application'; self.root.mkdir()
        settings=dict(self.settings,application_root=str(self.root),database=str(self.data/'finance.db'))
        write_json(self.data/'settings.json',settings)
        shutil.copytree(self.base/'application/releases',self.root/'releases')
        snapshot(self.base/'data/finance.db',self.data/'finance.db')
        write_json(self.data/'state.json',{'active':self.release_id,'history':[],'stopped':True,'boot':boot_id(),'result':'Test fixture'})
        self.manager=Manager(self.data)
        set_password(self.data,'Manager-Synthetic-26!')

    def tearDown(self):
        self.manager.stop_process()
        if self.manager.log_path().exists():
            target=Path('/home/tester/test-runtime-logs'); target.mkdir(exist_ok=True)
            (target/(self._testMethodName+'.log')).write_bytes(self.manager.log_path().read_bytes())
        self.work.cleanup()

    def config(self,content='allowWebRegistration = false\n'):
        path=self.directory/'configuration'; path.write_text(content); return path

    def test_installer_uninstall_delegates_before_bootstrap_and_propagates_exit(self):
        import installer
        registered={'application_root':str(self.root),'data':str(self.data)}
        for code in (0,9):
            with patch('sys.argv',['installer.py','uninstall','--yes']),patch('cli.privileged',return_value=True),patch.object(installer,'read_json',return_value=registered) as registration,patch.object(installer.subprocess,'run',return_value=subprocess.CompletedProcess([],code)) as run,patch.object(installer,'service') as service:
                with self.assertRaises(SystemExit) as result: installer.main()
                self.assertEqual(result.exception.code,code)
                self.assertEqual(run.call_count,1)
                self.assertEqual(run.call_args.args[0][1:],[str(self.root/'manager/cli.py'),'--data',str(self.data),'uninstall','--yes'])
                self.assertEqual(registration.call_args.args[0],Path('/etc/npf-manager.json'))
                service.assert_not_called()

    def test_uninstall_uses_registered_paths_and_requires_privilege(self):
        import installer
        code=self.root/'manager'; code.mkdir()
        write_json(code/'installation.json',{'application_root':str(self.root),'data':str(self.data),'database':str(self.manager.database)})
        with patch.object(installer,'HERE',code):
            record=installer.uninstall_plan(self.data)
            self.assertEqual(record['root'],self.root)
            with self.assertRaises(ManagerError): installer.uninstall_plan(self.directory/'wrong-data')
            with patch('cli.privileged',return_value=False):
                with self.assertRaisesRegex(ManagerError,'privileges'): installer.uninstall(self.data,yes=True)
            with patch('cli.privileged',return_value=True),patch('sys.stdin.isatty',return_value=True),patch('builtins.input',return_value='n'),patch.object(installer,'stop_service') as stop,contextlib.redirect_stdout(io.StringIO()):
                installer.uninstall(self.data)
                stop.assert_not_called()
            self.assertTrue(self.manager.database.exists())
            write_json(code/'installation.json',{'application_root':str(self.root),'data':'/var/lib','database':'/var/lib/finance.db'})
            with self.assertRaisesRegex(ManagerError,'system or home'): installer.uninstall_plan('/var/lib')

    def test_combined_upload_replaces_either_or_both_with_one_restart(self):
        self.manager.execute('start')
        for kinds in [('configuration',),('database',),('configuration','database')]:
            values={'confirmed':True}
            for kind in kinds:
                path=self.config() if kind=='configuration' else self.manager.database
                values[kind]=self.manager.stage(path,kind)['id']
            old=self.manager.process.pid
            original=self.manager.start_process
            with patch.object(self.manager,'start_process',wraps=original) as start:
                self.manager.execute('replace-files',values)
                self.assertEqual(start.call_count,1)
            self.assertNotEqual(self.manager.process.pid,old)
            self.assertEqual(self.manager.status()['npf'],'running')
            record=self.manager.items('recovery')[-1]
            self.assertTrue(record['database'])
            self.assertFalse(self.manager.state['pending_configuration'])

    def test_combined_upload_validates_both_before_changing_either(self):
        atomic(self.data/'platform.conf','allowWebRegistration = false\n')
        self.manager.execute('start')
        old=self.manager.process.pid
        config=self.manager.stage(self.config('allowWebRegistration = true\n'),'configuration')
        database=self.manager.stage(self.manager.database,'database')
        atomic(self.data/'staged'/database['id']/'file',b'invalid database')
        with self.assertRaises(ManagerError):
            self.manager.execute('replace-files',{'configuration':config['id'],'database':database['id'],'confirmed':True})
        self.assertEqual(self.manager.process.pid,old)
        self.assertIn('false',(self.data/'platform.conf').read_text())
        with self.assertRaises(ManagerError): self.manager.execute('replace-files',{'confirmed':True})
        with self.assertRaises(ManagerError): self.manager.execute('replace-files',{'configuration':config['id']})

    def test_combined_upload_failure_restores_both_files(self):
        atomic(self.data/'platform.conf','allowWebRegistration = false\n')
        self.manager.execute('start')
        configuration=self.manager.stage(self.config('allowWebRegistration = true\n'),'configuration')
        copied=self.directory/'candidate.db'; snapshot(self.manager.database,copied)
        with sqlite3.connect(copied) as db:
            db.execute('CREATE TABLE synthetic_replacement(value TEXT)')
        database=self.manager.stage(copied,'database')
        original=self.manager.start_process; calls=[]
        def injected():
            calls.append(1)
            if len(calls)==1: raise ManagerError('Injected combined startup failure')
            return original()
        with patch.object(self.manager,'start_process',side_effect=injected):
            with self.assertRaisesRegex(ManagerError,'previous database and configuration restored'):
                self.manager.execute('replace-files',{'configuration':configuration['id'],'database':database['id'],'confirmed':True})
        self.assertIn('false',(self.data/'platform.conf').read_text())
        with sqlite3.connect(self.manager.database) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='synthetic_replacement'").fetchone())
        self.assertEqual(self.manager.status()['npf'],'running')
        self.assertFalse((self.data/'operation.json').exists())

    def test_version_and_password_boundaries(self):
        self.assertEqual(VERSION,'26.11')
        for length in [7,129]:
            with self.assertRaises(ManagerError): set_password(self.data,'x'*length)
        for length in [8,128]:
            set_password(self.data,'x'*length)
            self.assertTrue(check_password(self.data,'x'*length))
            self.assertFalse(check_password(self.data,'wrong-password'))
        self.assertFalse(check_password(self.data,None))

    def test_consistent_database_export_with_wal(self):
        db=sqlite3.connect(self.manager.database)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE synthetic_marker(value TEXT)'); db.execute("INSERT INTO synthetic_marker VALUES ('committed')"); db.commit()
        export=self.directory/'export.db'; snapshot(self.manager.database,export)
        with sqlite3.connect(export) as copied:
            self.assertEqual(copied.execute('SELECT * FROM synthetic_marker').fetchall(),[('committed',)])
            self.assertEqual(copied.execute('SELECT count(*) FROM users').fetchone()[0],1)
            self.assertTrue(copied.execute('SELECT passwordHash FROM users').fetchone()[0])
            self.assertEqual(copied.execute('SELECT count(*) FROM ledgers').fetchone()[0],2)
            self.assertTrue(copied.execute('SELECT secret FROM integrationSecrets').fetchone()[0])
        db.close()

    def test_full_application_export_includes_all_data_and_current_configuration(self):
        atomic(self.data/'platform.conf','allowWebRegistration = false\n# current configuration\n')
        atomic(self.manager.app()/'platform.conf','stale configuration')
        atomic(self.manager.app()/'finance.db',b'not the managed database')
        atomic(self.manager.app()/'logs/private.log',b'private log')
        db=sqlite3.connect(self.manager.database)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE synthetic_marker(value TEXT)')
            db.execute("INSERT INTO synthetic_marker VALUES ('committed')"); db.commit()
            package=self.directory/'full.zip'
            self.manager.export_application(package)
            with zipfile.ZipFile(package) as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn('No-Part-Finance/app.py',archive.namelist())
                self.assertNotIn('No-Part-Finance/logs/private.log',archive.namelist())
                self.assertEqual(archive.read('No-Part-Finance/platform.conf'),(self.data/'platform.conf').read_bytes())
                copied=self.directory/'copied.db'; copied.write_bytes(archive.read('No-Part-Finance/finance.db'))
            with sqlite3.connect(copied) as connection:
                self.assertEqual(connection.execute('SELECT * FROM synthetic_marker').fetchall(),[('committed',)])
                self.assertEqual(connection.execute('SELECT count(*) FROM ledgers').fetchone()[0],2)
                self.assertTrue(connection.execute('SELECT passwordHash FROM users').fetchone()[0])
                self.assertTrue(connection.execute('SELECT secret FROM integrationSecrets').fetchone()[0])
            self.assertEqual(list((self.data/'exports').iterdir()),[])
        finally:
            db.close()

    def test_restart_controls_and_upload_restart_from_stopped(self):
        self.manager.execute('start')
        original=self.manager.process.pid
        self.manager.execute('restart')
        self.assertNotEqual(self.manager.process.pid,original)
        self.assertEqual(self.manager.status()['npf'],'running')
        self.manager.execute('stop')
        staged=self.manager.stage(self.config(),'configuration')
        self.manager.execute('replace-configuration',{'id':staged['id'],'confirmed':True,'restart':True})
        self.assertEqual(self.manager.status()['npf'],'running')
        self.assertFalse(self.manager.state['stopped'])
        self.assertFalse(self.manager.state['pending_configuration'])

    def test_sqlite_hot_journal_is_recovered_before_restart(self):
        code="""import os,sqlite3,sys
c=sqlite3.connect(sys.argv[1])
c.execute('PRAGMA journal_mode=DELETE')
c.execute('PRAGMA cache_size=10')
c.execute('CREATE TABLE synthetic_crash_marker(value BLOB)')
c.executemany('INSERT INTO synthetic_crash_marker VALUES (?)',[(b'a'*4096,)]*200)
c.commit()
c.execute('BEGIN IMMEDIATE')
c.execute('UPDATE synthetic_crash_marker SET value=?',(b'b'*4096,))
os._exit(0)
"""
        subprocess.run([sys.executable,'-c',code,str(self.manager.database)],check=True)
        self.assertTrue(Path(str(self.manager.database)+'-journal').exists())
        with self.assertRaises(ManagerError): database_info(self.manager.database)
        recover_database(self.manager.database)
        self.assertTrue(database_info(self.manager.database)['owner'])
        with sqlite3.connect(self.manager.database) as db:
            self.assertEqual(db.execute('SELECT substr(value,1,1) FROM synthetic_crash_marker LIMIT 1').fetchone()[0],b'a')

    def test_stage_configuration_does_not_apply(self):
        staged=self.manager.stage(self.config(),'configuration')
        self.assertFalse((self.data/'platform.conf').exists())
        self.assertIsNone(self.manager.process)
        self.assertEqual(staged['kind'],'configuration')

    def test_invalid_configuration_cannot_replace(self):
        atomic(self.data/'platform.conf','allowWebRegistration = false\n')
        with self.assertRaises(ManagerError): self.manager.stage(self.config('smtpEnabled = NOT_A_BOOL\n'),'configuration')
        self.assertEqual((self.data/'platform.conf').read_text(),'allowWebRegistration = false\n')

    def test_configuration_no_restart_and_absent_download_state(self):
        staged=self.manager.stage(self.config(),'configuration')
        self.manager.execute('replace-configuration',{'id':staged['id'],'confirmed':True,'restart':False})
        self.assertEqual((self.data/'platform.conf').read_text(),self.config().read_text())
        self.assertTrue(self.manager.state['pending_configuration'])
        self.assertIsNone(self.manager.process)
        self.assertEqual(len(self.manager.items('recovery')),1)
        self.assertFalse(self.manager.items('recovery')[0]['configuration'])
        self.assertTrue(self.manager.items('recovery')[0]['database'])
        self.assertTrue((self.data/'recovery'/self.manager.items('recovery')[0]['id']/'finance.db').exists())

    def test_database_replacement_requires_backup_even_when_stopped(self):
        before=database_info(self.manager.database)
        staged=self.manager.stage(self.manager.database,'database')
        self.manager.execute('replace-database',{'id':staged['id'],'confirmed':True})
        self.assertEqual(database_info(self.manager.database),before)
        self.assertIsNone(self.manager.process)
        record=self.manager.items('recovery')[0]
        self.assertTrue(record['database'])
        self.assertFalse((self.data/'staged'/staged['id']).exists())

    def test_database_rejects_non_npf_and_newer_schema(self):
        invalid=self.directory/'bad.db'
        with sqlite3.connect(invalid) as db: db.execute('CREATE TABLE x (x)')
        with self.assertRaises(ManagerError): self.manager.stage(invalid,'database')
        newer=self.directory/'new.db'; snapshot(self.manager.database,newer)
        with sqlite3.connect(newer) as db: db.execute('PRAGMA user_version=999')
        with self.assertRaises(ManagerError): self.manager.stage(newer,'database')
        self.assertEqual(database_info(self.manager.database)['schema'],self.schema)

    def test_backup_failure_leaves_database_untouched(self):
        staged=self.manager.stage(self.manager.database,'database')
        original=self.manager.database.read_bytes()
        with patch('manager.snapshot',side_effect=OSError('simulated full disk')):
            with self.assertRaises(OSError): self.manager.execute('replace-database',{'id':staged['id'],'confirmed':True})
        self.assertEqual(self.manager.database.read_bytes(),original)
        self.assertTrue((self.data/'staged'/staged['id']).exists())

    def test_archive_ignores_bundled_database_and_config(self):
        package=self.directory/'package.zip'
        source=self.manager.release()/'source.zip'
        with zipfile.ZipFile(source) as src,zipfile.ZipFile(package,'w') as dst:
            for entry in src.infolist():
                if entry.filename.endswith(('finance.db','platform.conf')): continue
                dst.writestr(entry,src.read(entry))
            dst.writestr('No-Part-Finance/finance.db',b'not a real db')
            dst.writestr('No-Part-Finance/platform.conf','smtpPassword = bundled-secret')
        destination=self.directory/'extracted'; self.manager.extract(package,destination)
        self.assertFalse((destination/'finance.db').exists()); self.assertFalse((destination/'platform.conf').exists())
        self.assertEqual(metadata(destination)['schemaVersion'],self.schema)

    def test_archive_rejects_traversal_symlinks_and_limits(self):
        for name,symlink in [('../escape',False),('/absolute',False),('No-Part-Finance/linked',True),('No-Part-Finance/a\\b',False)]:
            package=self.directory/'bad.zip'
            with zipfile.ZipFile(package,'w') as out:
                entry=zipfile.ZipInfo(name)
                if symlink: entry.create_system=3; entry.external_attr=0o120777<<16
                out.writestr(entry,'payload')
            with self.assertRaises(ManagerError): self.manager.extract(package,self.directory/uuid.uuid4().hex)
        self.manager.settings['max_extract']=1
        with self.assertRaises(ManagerError): self.manager.extract(self.manager.release()/'source.zip',self.directory/'oversized')
        self.assertFalse((self.directory/'escape').exists())

    def test_busy_and_confirmation_cannot_be_bypassed(self):
        with self.manager.lock:
            with self.assertRaises(ManagerError): self.manager.execute('start')
        with self.assertRaises(ManagerError): self.manager.execute('rollback',{'id':self.release_id})
        with self.assertRaises(ManagerError): self.manager.execute('replace-configuration',{'id':uuid.uuid4().hex,'confirmed':True})

    def test_real_npf_start_stop_and_readiness(self):
        self.manager.execute('start')
        self.assertEqual(self.manager.status()['npf'],'running')
        pid=self.manager.process.pid
        self.manager.execute('start'); self.assertEqual(pid,self.manager.process.pid)
        self.manager.execute('stop')
        self.assertIsNone(self.manager.process); self.assertTrue(self.manager.state['stopped'])
        self.assertEqual(self.manager.status()['npf'],'stopped')

    def test_configuration_failed_no_restart_preserves_live_database_writes(self):
        atomic(self.data/'platform.conf','allowWebRegistration = false\n')
        self.manager.execute('start')
        pid = self.manager.process.pid
        staged = self.manager.stage(self.config('allowWebRegistration = true\n'),'configuration')
        original = atomic
        failed = []
        def injected(path, content):
            if Path(path) == self.data/'platform.conf' and not failed:
                failed.append(True)
                with sqlite3.connect(self.manager.database) as db:
                    db.execute('CREATE TABLE synthetic_live_write(value TEXT)')
                    db.execute("INSERT INTO synthetic_live_write VALUES ('committed during replacement')")
                raise OSError('Injected configuration write failure')
            return original(path, content)
        with patch('manager.atomic',side_effect=injected):
            with self.assertRaises(ManagerError):
                self.manager.execute('replace-configuration',{'id':staged['id'],'confirmed':True,'restart':False})
        self.assertEqual(self.manager.process.pid,pid)
        self.assertIn('false',(self.data/'platform.conf').read_text())
        self.assertFalse(self.manager.state['pending_configuration'])
        with sqlite3.connect(self.manager.database) as db:
            self.assertEqual(db.execute('SELECT value FROM synthetic_live_write').fetchone()[0],'committed during replacement')
        self.assertFalse((self.data/'operation.json').exists())

    def test_configuration_failed_restart_restores_previous(self):
        atomic(self.data/'platform.conf','allowWebRegistration = false\n')
        self.manager.execute('start')
        staged=self.manager.stage(self.config('allowWebRegistration = true\n'),'configuration')
        original=self.manager.start_process; calls=[]
        def injected():
            calls.append(1)
            if len(calls)==1: raise ManagerError('Injected startup failure')
            return original()
        with patch.object(self.manager,'start_process',side_effect=injected):
            with self.assertRaises(ManagerError): self.manager.execute('replace-configuration',{'id':staged['id'],'confirmed':True,'restart':True})
        self.assertIn('false',(self.data/'platform.conf').read_text())
        self.assertEqual(self.manager.status()['npf'],'running')
        self.assertFalse((self.data/'operation.json').exists())

    def test_failed_update_restores_snapshot_and_history(self):
        staged=self.manager.stage(self.manager.release()/'source.zip','application')
        new=uuid.uuid4().hex
        shutil.copytree(self.manager.release(),self.manager.releases/new)
        self.manager.execute('start')
        old=self.manager.state['active']
        original=self.manager.start_process; calls=[]
        def injected():
            calls.append(1)
            if len(calls)==1:
                with sqlite3.connect(self.manager.database) as db: db.execute('PRAGMA user_version=999')
                raise ManagerError('Injected post-migration startup failure')
            return original()
        with patch.object(self.manager,'prepare',return_value=new),patch.object(self.manager,'start_process',side_effect=injected):
            with self.assertRaises(ManagerError): self.manager.execute('apply',{'id':staged['id'],'snapshot':True})
        self.assertEqual(self.manager.state['active'],old)
        self.assertEqual(database_info(self.manager.database)['schema'],self.schema)
        self.assertEqual(self.manager.status()['npf'],'running')
        self.assertTrue((self.data/'staged'/staged['id']).exists())

    def test_snapshot_opt_out_is_rejected_without_changes(self):
        staged=self.manager.stage(self.manager.release()/'source.zip','application')
        old=self.manager.state['active']
        with patch.object(self.manager,'prepare') as prepare:
            with self.assertRaisesRegex(ManagerError, 'must include finance.db'):
                self.manager.execute('apply',{'id':staged['id'],'snapshot':False})
            prepare.assert_not_called()
        self.assertEqual(self.manager.state['active'],old)
        self.assertFalse((self.data/'operation.json').exists())
        self.assertTrue((self.data/'staged'/staged['id']).exists())

    def test_interrupted_operation_restores_before_start(self):
        backup=self.manager.capture(); self.manager.begin(backup,'database replacement')
        with sqlite3.connect(self.manager.database) as db: db.execute('PRAGMA user_version=999')
        self.manager.recover()
        self.assertEqual(database_info(self.manager.database)['schema'],self.schema)
        self.assertFalse((self.data/'operation.json').exists()); self.assertIsNone(self.manager.process)

    def test_pruning_keeps_independent_recovery(self):
        copy=self.manager.capture()
        history=[]
        for _ in range(4):
            item=uuid.uuid4().hex; history.append(item)
            shutil.copytree(self.manager.release(),self.manager.releases/item)
            directory=self.data/'history'/item; directory.mkdir(parents=True); (directory/'marker').write_text('x')
        self.manager.state['history']=history; self.manager.prune()
        self.assertEqual(self.manager.state['history'],history[:2])
        self.assertTrue(copy.exists()); self.assertFalse((self.data/'history'/history[-1]).exists())

    def test_deferred_update_verifies_before_pruning(self):
        staged=self.manager.stage(self.manager.release()/'source.zip','application')
        new=uuid.uuid4().hex; shutil.copytree(self.manager.release(),self.manager.releases/new)
        old=self.manager.state['active']
        with patch.object(self.manager,'prepare',return_value=new):
            self.manager.execute('apply',{'id':staged['id'],'snapshot':True})
        self.assertIsNone(self.manager.process)
        self.assertTrue(self.manager.state['pending_update'])
        self.assertTrue((self.data/'staged'/staged['id']).exists())
        with self.assertRaises(ManagerError): self.manager.execute('apply',{'id':staged['id']})
        self.manager.execute('start')
        self.assertNotIn('pending_update',self.manager.state)
        self.assertFalse((self.data/'staged'/staged['id']).exists())
        self.assertEqual(self.manager.state['history'],[old])

    def test_running_configuration_no_restart_preserves_pid(self):
        self.manager.execute('start'); pid=self.manager.process.pid
        staged=self.manager.stage(self.config(),'configuration')
        self.manager.execute('replace-configuration',{'id':staged['id'],'confirmed':True,'restart':False})
        self.assertEqual(self.manager.process.pid,pid)
        self.assertTrue(self.manager.state['pending_configuration'])

    def test_database_failed_restart_recovers_previous(self):
        self.manager.execute('start')
        staged=self.manager.stage(self.manager.database,'database')
        original=self.manager.start_process; calls=[]
        def injected():
            calls.append(1)
            if len(calls)==1: raise ManagerError('Injected startup failure')
            return original()
        with patch.object(self.manager,'start_process',side_effect=injected):
            with self.assertRaises(ManagerError): self.manager.execute('replace-database',{'id':staged['id'],'confirmed':True})
        self.assertEqual(self.manager.status()['npf'],'running')
        self.assertTrue((self.data/'staged'/staged['id']).exists())
        self.assertFalse((self.data/'operation.json').exists())

    def test_restore_always_uses_matching_database_and_rejects_keep_current(self):
        previous=uuid.uuid4().hex
        shutil.copytree(self.manager.release(),self.manager.releases/previous)
        self.manager.capture(target=self.data/'history'/previous)
        self.manager.state['history']=[previous]
        atomic(self.data/'platform.conf','allowWebRegistration = false\n# current settings\n')
        configuration=(self.data/'platform.conf').read_bytes()
        self.manager.sync_configuration()
        with sqlite3.connect(self.manager.database) as db:
            db.execute('PRAGMA user_version=999')
            db.execute('CREATE TABLE synthetic_newer_data(value TEXT)')
        details=self.manager.status()['history'][0]
        self.assertIsInstance(details['snapshot_time'],float)
        with self.assertRaisesRegex(ManagerError,'matching database backup'):
            self.manager.execute('rollback',{'id':previous,'confirmed':True,'restore_database':False})
        self.assertEqual(database_info(self.manager.database)['schema'],999)
        self.manager.execute('rollback',{'id':previous,'confirmed':True})
        self.assertEqual(database_info(self.manager.database)['schema'],self.schema)
        self.assertEqual((self.data/'platform.conf').read_bytes(),configuration)
        copies=self.manager.items('recovery')
        self.assertEqual(database_info(self.data/'recovery'/copies[0]['id']/'finance.db')['schema'],999)

    def test_restore_refuses_missing_snapshot_before_stopping(self):
        previous=uuid.uuid4().hex
        shutil.copytree(self.manager.release(),self.manager.releases/previous)
        self.manager.state['history']=[previous]
        self.manager.execute('start')
        pid=self.manager.process.pid
        self.assertFalse(self.manager.status()['history'][0]['snapshot'])
        self.assertIsNone(self.manager.status()['history'][0]['snapshot_time'])
        with self.assertRaisesRegex(ManagerError,'No associated database snapshot'):
            self.manager.execute('rollback',{'id':previous,'confirmed':True})
        self.assertEqual(self.manager.process.pid,pid)
        self.assertEqual(self.manager.status()['npf'],'running')

    def test_snapshot_rollback_and_failed_rollback_recovery(self):
        previous=uuid.uuid4().hex
        shutil.copytree(self.manager.release(),self.manager.releases/previous)
        history=self.data/'history'/previous
        self.manager.capture(target=history)
        self.manager.state['history']=[previous]
        self.manager.execute('start')
        with sqlite3.connect(self.manager.database) as db:
            db.execute('CREATE TABLE synthetic_after_snapshot(value TEXT)')
        self.manager.execute('rollback',{'id':previous,'confirmed':True})
        with sqlite3.connect(self.manager.database) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='synthetic_after_snapshot'").fetchone())
        target=self.manager.state['history'][0]
        self.assertTrue((self.data/'history'/target/'finance.db').exists())
        before=self.manager.state['active']
        original=self.manager.start_process; calls=[]
        def injected():
            calls.append(1)
            if len(calls)==1: raise ManagerError('Injected rollback startup failure')
            return original()
        with patch.object(self.manager,'start_process',side_effect=injected):
            with self.assertRaises(ManagerError): self.manager.execute('rollback',{'id':target,'confirmed':True})
        self.assertEqual(len(calls),2)
        self.assertEqual(self.manager.state['active'],before)
        self.assertEqual(self.manager.status()['npf'],'running')
        self.assertFalse((self.data/'operation.json').exists())

    def test_failed_interruption_recovery_keeps_journal(self):
        backup=self.manager.capture(); self.manager.begin(backup,'test replacement')
        (backup/'finance.db').write_bytes(b'invalid snapshot')
        self.manager.recover()
        self.assertTrue(self.manager.failed)
        self.assertTrue(self.manager.state['stopped'])
        self.assertTrue((self.data/'operation.json').exists())
        with self.assertRaises(ManagerError): self.manager.execute('start')

    def test_github_checksum_mismatch_and_verified_staging(self):
        import hashlib
        archive=(self.manager.release()/'source.zip').read_bytes()
        release={'tag_name':'v26.10.30','draft':False,'prerelease':False,'assets':[
            {'name':'NPF-no-db-test.zip','browser_download_url':'https://github.com/ajpri/No-Part-Finance/releases/download/v26.10.30/NPF-no-db-test.zip'},
            {'name':'NPF-no-db-test.zip.sha256','browser_download_url':'https://github.com/ajpri/No-Part-Finance/releases/download/v26.10.30/NPF-no-db-test.zip.sha256'}]}
        responses=[io.BytesIO(json.dumps(release).encode()),io.BytesIO(archive),io.BytesIO(b'0'*64)]
        with patch('manager.urllib.request.urlopen',side_effect=responses):
            with self.assertRaises(ManagerError): self.manager.github()
        self.assertEqual(self.manager.items('staged'),[])
        responses=[io.BytesIO(json.dumps(release).encode()),io.BytesIO(archive),io.BytesIO(hashlib.sha256(archive).hexdigest().encode())]
        with patch('manager.urllib.request.urlopen',side_effect=responses): entry=self.manager.github()
        self.assertIn('verified',entry['origin'])
        self.assertIsNone(self.manager.process)

    def test_supplied_certificate_mismatch_leaves_previous_untouched(self):
        cert,key=certificate(self.data,['localhost','127.0.0.1'])
        original=Path(cert).read_bytes()
        other=self.directory/'other-cert'; other.mkdir()
        other_cert,other_key=certificate(other,['localhost','127.0.0.1'])
        with self.assertRaises(ssl.SSLError): certificate(self.data,['localhost'],cert,other_key)
        self.assertEqual(Path(cert).read_bytes(),original)
        copied=self.directory/'copied-cert'; copied.mkdir()
        c,k=certificate(copied,['localhost'],cert,key)
        self.assertEqual(Path(c).read_bytes(),original)

    def test_manager_restart_does_not_bypass_failed_retry_limit(self):
        self.manager.retries=3; self.manager.failed=True
        self.manager.result('Synthetic exhausted retry limit')
        restarted=Manager(self.data)
        self.assertEqual(restarted.retries,3)
        self.assertTrue(restarted.failed)
        self.assertEqual(restarted.status()['npf'],'failed')

    def test_http_auth_upload_download_and_reset(self):
        cert,key=certificate(self.data,['localhost','127.0.0.1'])
        self.manager.settings.update(certificate=cert,private_key=key,port=0,bind='127.0.0.1')
        server=HTTPServer(self.manager); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        url='https://127.0.0.1:'+str(server.server_port)
        context=ssl.create_default_context(cafile=cert)
        def request(path,body=None,token=None,headers=None):
            h=dict(headers or {})
            if token: h['Authorization']='Bearer '+token
            data=body if isinstance(body,bytes) else json.dumps(body).encode() if body is not None else None
            return urllib.request.urlopen(urllib.request.Request(url+path,data=data,headers=h),context=context,timeout=30)
        try:
            with request('/') as response:
                page = response.read()
                from manager import VERSION, HERE
                self.assertEqual(VERSION, (HERE/'VERSION').read_text().strip())
                self.assertIn(('Version '+VERSION).encode(), page)
                self.assertNotIn(b'{{MANAGER_VERSION}}', page)
                import base64, hashlib, re
                for block in re.findall(rb'<(?:script|style)>(.*?)</(?:script|style)>', page, re.S):
                    expected = base64.b64encode(hashlib.sha256(block).digest()).decode()
                    self.assertIn("'sha256-"+expected+"'", response.headers['Content-Security-Policy'])
            with self.assertRaises(urllib.error.HTTPError) as error: request('/api/download/application')
            self.assertEqual(error.exception.code,401)
            with self.assertRaises(urllib.error.HTTPError) as error: request('/api/status')
            self.assertEqual(error.exception.code,401)
            with request('/api/login',{'password':'Manager-Synthetic-26!'}) as response: token=json.load(response)['token']
            with request('/api/status',token=token) as response: self.assertEqual(json.load(response)['manager_version'],'26.11')
            with self.manager.lock:
                for path, body, expected in [('/api/download/database', None, 400), ('/api/action/start', {}, 409)]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        request(path, body, token)
                    self.assertEqual(error.exception.code, expected)
                    self.assertIn('busy', json.load(error.exception)['error'])
            for method, path, body, message in [
                ('status', '/api/status', None, 'Operation failed. Check private service logs and available storage.'),
                ('execute', '/api/action/start', {}, 'Operation failed; preserve recovery files. Check private logs and available storage.')]:
                with patch.object(self.manager, method, side_effect=RuntimeError('private diagnostic')):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        request(path, body, token)
                    self.assertEqual(error.exception.code, 500)
                    self.assertEqual(json.load(error.exception)['error'], message)
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/action/stop',{},headers={'Cookie':'npf_session='+token})
            self.assertEqual(error.exception.code,401)
            with self.assertRaises(urllib.error.HTTPError):
                request('/api/stage/configuration',b'x',token,{'Content-Length':str(1024*1024+1)})
            with self.assertRaises(urllib.error.HTTPError):
                request('/api/stage/configuration',b'x',token,{'Transfer-Encoding':'chunked'})
            with self.assertRaises(urllib.error.HTTPError) as error: request('/../../settings.json',token=token)
            self.assertEqual(error.exception.code,404)
            with request('/api/stage/configuration',b'allowWebRegistration = false\n',token) as response: item=json.load(response)['id']
            with request('/api/action/replace-configuration',{'id':item,'confirmed':True,'restart':False},token): pass
            with request('/api/download/configuration',token=token) as response: self.assertIn(b'false',response.read())
            with request('/api/download/database',token=token) as response:
                exported=self.directory/'http.db'; exported.write_bytes(response.read()); self.assertTrue(database_info(exported)['owner'])
            with request('/api/download/application',token=token) as response:
                self.assertEqual(response.headers['Content-Type'],'application/zip')
                body=response.read(); self.assertEqual(len(body),int(response.headers['Content-Length']))
                with zipfile.ZipFile(io.BytesIO(body)) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertIn('No-Part-Finance/finance.db',archive.namelist())
                    self.assertIn('No-Part-Finance/platform.conf',archive.namelist())
            self.assertEqual(list((self.data/'exports').iterdir()),[])
            from cli import Client
            client=Client(self.data,url=url,ca=cert,password='Manager-Synthetic-26!')
            cli_download=self.directory/'cli-private.db'
            client.request('/api/download/database',destination=cli_download)
            if os.name!='nt': self.assertEqual(cli_download.stat().st_mode & 0o777,0o600)
            with self.assertRaises(urllib.error.HTTPError): request('/api/action/stop',{},token,{'Origin':'https://evil.example'})
            set_password(self.data,'Reset-Synthetic-26!')
            with self.assertRaises(urllib.error.HTTPError) as error: request('/api/status',token=token)
            self.assertEqual(error.exception.code,401)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__=='__main__': unittest.main()
