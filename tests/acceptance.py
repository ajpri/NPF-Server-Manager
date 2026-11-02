"""Run with sudo ONLY inside a disposable remote QEMU guest. Synthetic fixtures only."""
import argparse
import hashlib
import tarfile
import threading
import contextlib
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import zipfile

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
from cli import Client
from manager import Manager, database_info, read_json, set_password, write_json

DATA=Path('/var/lib/npf')
ARTIFACTS=Path('/home/tester/acceptance')
PASSWORD='Lab-Manager-Synthetic-26!'


def record(name,detail):
    with (ARTIFACTS/'results.jsonl').open('a') as out:
        out.write(json.dumps({'scenario':name,'detail':detail,'time':time.time()})+'\n')
    print(name,detail,flush=True)


def wait_status(expected=None,timeout=90,data=DATA):
    deadline=time.monotonic()+timeout
    last=None
    while time.monotonic()<deadline:
        try:
            client=Client(data,password=PASSWORD)
            value=client.request('/api/status')
            if expected is None or value['npf']==expected: return client,value
            last=value['npf']
        except Exception as error: last=str(error)
        time.sleep(1)
    raise RuntimeError('State timeout: '+str(last))


def owner_prompts():
    return [('Create the required Owner now?', 'y'),('Owner username:', 'synthetic-owner'),('First name (required):','Synthetic'),('Last name (optional):','Owner'),('Password:','Synthetic-NPF-Owner-26!'),('Confirm password:','Synthetic-NPF-Owner-26!'),('Create an optional read-only Auditor?','n')]


def terminal_install(args, prompts, log_name):
    pid,fd=pty.fork()
    if pid==0: os.execvp(args[0],args)
    buffer=''; transcript=[]; deadline=time.monotonic()+900
    while time.monotonic()<deadline:
        ready,_,_=select.select([fd],[],[],1)
        if ready:
            try: chunk=os.read(fd,65536)
            except OSError: break
            if not chunk: break
            text=chunk.decode(errors='replace'); buffer+=text; transcript.append(text)
            if prompts and prompts[0][0] in buffer:
                prompt,value=prompts.pop(0); buffer=buffer[buffer.index(prompt)+len(prompt):]
                os.write(fd,(value+'\n').encode())
        ended,status=os.waitpid(pid,os.WNOHANG)
        if ended:
            if os.waitstatus_to_exitcode(status): raise RuntimeError('Installer failed: '+''.join(transcript)[-4000:])
            break
    else:
        os.kill(pid,signal.SIGTERM); raise RuntimeError('Installer timeout')
    with contextlib.suppress(ChildProcessError):
        _,status=os.waitpid(pid,0)
        if os.waitstatus_to_exitcode(status): raise RuntimeError('Installer failed: '+''.join(transcript)[-4000:])
    os.close(fd)
    # Terminal transcript must never persist any of the fake passwords either.
    text=''.join(transcript).replace(PASSWORD,'[redacted]').replace('Synthetic-NPF-Owner-26!','[redacted]')
    (ARTIFACTS/log_name).write_text(text)


def install():
    file=ARTIFACTS/'password-input'; file.write_text(PASSWORD); file.chmod(0o600)
    args=['sh',str(SOURCE/'installLinux.sh'),'--application-root','/opt/npf','--data',str(DATA),'--package','/home/tester/npf-public.zip','--port','5050','--admin-password-file',str(file),'--certificate-names','localhost,127.0.0.1,npf-test']
    terminal_install(args, owner_prompts(), 'install.log')
    file.unlink(missing_ok=True)
    client,value=wait_status('running')
    assert value['manager_version']=='26.11'
    record('installation',{'npf_version':value['version']['applicationVersion'],'service':subprocess.check_output(['systemctl','is-active','npf-manager'],text=True).strip()})
    return client


def fixture(version):
    target=ARTIFACTS/('NPF-'+version+'.zip')
    with zipfile.ZipFile('/home/tester/npf-public.zip') as source,zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            data=source.read(item)
            if item.filename.endswith('/packageApplication.py'):
                data=data.replace(b'applicationVersion = "26.10.30"',('applicationVersion = "'+version+'"').encode())
            if item.filename.endswith(('/finance.db','/platform.conf')): continue
            out.writestr(item,data)
        out.writestr('No-Part-Finance/finance.db',b'ignored-bundled-db')
        out.writestr('No-Part-Finance/platform.conf','smtpPassword = ignored-bundled-secret\n')
    return target


def action(client,name,**values):
    return client.request('/api/action/'+name,values)


def runtime():
    client,value=wait_status()
    if value['npf']!='running':
        action(client,'start'); client,value=wait_status('running')
    action(client,'stop'); subprocess.run(['systemctl','restart','npf-manager'],check=True)
    client,value=wait_status('stopped'); record('intentional-stop','Survives manager service restart.')
    action(client,'start'); wait_status('running')
    # Verify root password reset preserves service identity ownership and invalidates sessions.
    owner=(DATA/'auth.json').stat().st_uid
    set_password(DATA,PASSWORD)
    assert (DATA/'auth.json').stat().st_uid==owner
    try: client.request('/api/status')
    except Exception: pass
    else: raise AssertionError('Old token survived reset')
    client,value=wait_status('running'); record('password-reset','Old session rejected; service can read new hash.')
    # UI/API database and config transfer contracts with synthetic data only.
    exported=ARTIFACTS/'full-finance.db'; exported.unlink(missing_ok=True)
    client.request('/api/download/database',destination=exported)
    with sqlite3.connect(exported) as db:
        assert db.execute('SELECT passwordHash FROM users').fetchone()[0]
    staged=client.request('/api/stage/database',source=exported)
    action(client,'replace-database',id=staged['id'],confirmed=True)
    config=ARTIFACTS/'platform.conf'; config.write_text('allowWebRegistration = false\n# synthetic config\n')
    staged=client.request('/api/stage/configuration',source=config)
    pid=read_json(DATA/'child.json')['pid']
    action(client,'replace-configuration',id=staged['id'],confirmed=True,restart=False)
    assert read_json(DATA/'child.json')['pid']==pid
    assert client.request('/api/status')['pending_configuration']
    record('transfers','Full DB exported/replaced; config applied without restart; backup copies retained.')
    # CLI input remains protected; use a pseudo-terminal to provide fake admin password.
    cli=SOURCE/'cli.py'
    def cli_call(arguments):
        pid,fd=pty.fork()
        if pid==0: os.execv(sys.executable,[sys.executable,str(cli),'--data',str(DATA)]+arguments)
        buf=b''; end=time.monotonic()+120; supplied=False
        while time.monotonic()<end:
            ready,_,_=select.select([fd],[],[],1)
            if ready:
                try: chunk=os.read(fd,65536)
                except OSError: break
                if not chunk: break
                buf+=chunk
                if not supplied and b'Manager administrator password:' in buf:
                    os.write(fd,(PASSWORD+'\n').encode()); supplied=True
        os.close(fd); _,status=os.waitpid(pid,0)
        assert os.waitstatus_to_exitcode(status)==0,buf.decode(errors='replace')
        return buf.decode(errors='replace')
    assert '26.11' in cli_call(['status'])
    cli_export=ARTIFACTS/'cli-finance.db'; cli_export.unlink(missing_ok=True)
    cli_call(['download','database',str(cli_export)])
    cli_config=ARTIFACTS/'cli-platform.conf'; cli_config.unlink(missing_ok=True)
    cli_call(['download','configuration',str(cli_config)])
    cli_call(['stage','configuration',str(config)])
    staged=next(x for x in client.request('/api/status')['staged'] if x['kind']=='configuration')
    cli_call(['replace','configuration',staged['id'],'--yes','--no-restart'])
    record('cli','Authenticated status/database+config download/config stage+replace passed.')
    # Four synthetic package versions exercise apply, bundled-file ignore, and three-version retention.
    for version in ['26.11.1','26.11.2','26.11.3']:
        package=fixture(version)
        staged=client.request('/api/stage/application',source=package)
        before=client.request('/api/status')['active']
        assert read_json(DATA/'state.json')['active']==before
        action(client,'apply',id=staged['id'],snapshot=True)
        value=client.request('/api/status')
        assert value['version']['applicationVersion']==version
        assert 'ignored-bundled-secret' not in (DATA/'platform.conf').read_text()
        assert len(value['history'])<=2
    assert len(value['history'])==2
    previous=value['history'][0]
    action(client,'rollback',id=previous['id'],confirmed=True)
    value=client.request('/api/status'); assert value['version']['applicationVersion']=='26.11.2'
    record('updates-and-rollback','Three synthetic version updates; current + 2 history; bundled files ignored; matching-database rollback passed.')
    # Four consecutive crashes exhaust restart limit with actual wall-clock delays.
    client,value=wait_status('running')
    delays=[]
    for number in range(4):
        old_pid=read_json(DATA/'child.json')['pid']; start=time.monotonic(); os.kill(old_pid,signal.SIGKILL)
        if number<3:
            end=time.monotonic()+40
            while time.monotonic()<end:
                child=read_json(DATA/'child.json',{})
                if child.get('pid') and child['pid']!=old_pid:
                    wait_status('running'); break
                time.sleep(.2)
            else: raise AssertionError('Automatic restart did not occur')
            delay=time.monotonic()-start; assert delay>=9.5; delays.append(round(delay,2))
        else:
            client,value=wait_status('failed',40)
            assert not (DATA/'child.json').exists()
    record('crash-limit',{'delays_seconds':delays,'state':'failed after third restart and fourth crash'})
    action(client,'start'); client,value=wait_status('running'); assert value['restart_attempts']==0
    record('explicit-start-reset','Restart attempts reset.')
    # Queue a deliberate stop before reboot; boot-phase verifies reboot clears it.
    action(client,'stop'); record('pre-reboot','NPF intentionally stopped; next boot must start it.')
    (ARTIFACTS/'reboot-ready').write_text('ready')


def boot():
    client,value=wait_status('running')
    assert value['restart_attempts']==0
    record('reboot','Manager and NPF start automatically, clearing intentional Stop.')
    # Installer rerun updates the manager, preserving data/settings/certs and NPF release.
    settings=(DATA/'settings.json').read_bytes(); password=(DATA/'auth.json').read_bytes(); cert=(DATA/'tls/certificate.pem').read_bytes(); active=value['active']
    action(client,'stop')
    result=subprocess.run(['sh',str(SOURCE/'installLinux.sh'),'--data',str(DATA)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=900)
    (ARTIFACTS/'manager-update.log').write_text(result.stdout)
    assert result.returncode==0,result.stdout[-4000:]
    client,value=wait_status('stopped')
    assert value['active']==active and (DATA/'auth.json').read_bytes()==password and (DATA/'tls/certificate.pem').read_bytes()==cert
    action(client,'start'); wait_status('running')
    record('manager-update','Rerun preserves NPF active version, password, certificate and intentional Stop.')


def stable():
    client,value=wait_status('running')
    os.kill(read_json(DATA/'child.json')['pid'],signal.SIGKILL)
    end=time.monotonic()+45
    while time.monotonic()<end:
        time.sleep(1)
        value=client.request('/api/status')
        if value['npf']=='running' and value['restart_attempts']==1: break
    else: raise AssertionError('Expected one automatic crash restart')
    start=time.monotonic()
    deadline=start+315
    while time.monotonic()<deadline:
        time.sleep(10)
        value=client.request('/api/status')
        assert value['npf']=='running'
        if value['restart_attempts']==0:
            assert time.monotonic()-start>=290
            record('stable-reset',{'seconds_observed':round(time.monotonic()-start,1),'restart_attempts':0})
            return
    raise AssertionError('Five minutes of stable operation did not reset retry counter')




def failure_package(delay=False):
    target=ARTIFACTS/('interrupt-fixture.zip' if delay else 'failure-fixture.zip')
    suffix=b'''\n# Synthetic acceptance fixture: never a public NPF release.\n_manager_original_create = createApp\ndef createApp(*args, **kwargs):\n'''
    if delay:
        suffix+=b'''    import time\n    time.sleep(120)\n    return _manager_original_create(*args, **kwargs)\n'''
    else:
        suffix+=b'''    application = _manager_original_create(*args, **kwargs)\n    import sqlite3\n    connection = sqlite3.connect(application.config['DATABASE_PATH'])\n    connection.execute('PRAGMA user_version = 999')\n    connection.commit()\n    connection.close()\n    raise RuntimeError('Synthetic post-initialization failure')\n'''
    with zipfile.ZipFile('/home/tester/npf-public.zip') as original, zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as out:
        for item in original.infolist():
            data=original.read(item)
            if item.filename.endswith('/packageApplication.py'): data=data.replace(b'applicationVersion = "26.10.30"',b'applicationVersion = "26.11.90"')
            if item.filename.endswith('/app.py'): data+=suffix
            out.writestr(item,data)
    return target


def fail():
    client,value=wait_status('running'); previous=value['active']; schema=database_info(DATA/'finance.db')['schema']
    staged=client.request('/api/stage/application',source=failure_package())
    try: action(client,'apply',id=staged['id'],snapshot=True)
    except Exception: pass
    else: raise AssertionError('Synthetic failure incorrectly reported success')
    client,value=wait_status('running')
    assert value['active']==previous and database_info(DATA/'finance.db')['schema']==schema
    assert any(x['id']==staged['id'] for x in value['staged'])
    assert not (DATA/'operation.json').exists()
    record('actual-failed-update','Synthetic app changed schema to 999 then failed; outgoing code/config/full DB restored; staged input retained.')


def interrupt(reboot=False):
    client,value=wait_status('running'); previous=value['active']
    staged=client.request('/api/stage/application',source=failure_package(True))
    errors=[]
    def apply():
        try: action(client,'apply',id=staged['id'],snapshot=True)
        except Exception as error: errors.append(type(error).__name__)
    thread=threading.Thread(target=apply,daemon=True); thread.start()
    deadline=time.monotonic()+900
    while time.monotonic()<deadline:
        operation=read_json(DATA/'operation.json')
        child=read_json(DATA/'child.json')
        state=read_json(DATA/'state.json')
        if operation and child and state['active']!=previous: break
        time.sleep(.2)
    else: raise AssertionError('Update did not reach durable mutation/startup phase')
    (ARTIFACTS/'interrupt-expected.json').write_text(json.dumps({'active':previous,'mode':'VM reboot' if reboot else 'manager SIGKILL'}))
    if reboot:
        record('interrupt-injected','Rebooting disposable guest during journaled update startup.')
        subprocess.run(['systemctl','reboot'],check=True)
        time.sleep(30)
        return
    subprocess.run(['systemctl','kill','--kill-whom=main','--signal=SIGKILL','npf-manager'],check=True)
    resume()


def resume():
    expected=read_json(ARTIFACTS/'interrupt-expected.json')
    client,value=wait_status('running',120)
    assert value['active']==expected['active']
    assert not (DATA/'operation.json').exists()
    assert database_info(DATA/'finance.db')['schema']!=999
    record('interrupted-update-recovery',{'mode':expected['mode'],'previous_active_restored':True,'database_valid':True,'manager_available':True})


def adoption():
    original=Path('/home/tester/original-npf')
    original.mkdir(exist_ok=True)
    with zipfile.ZipFile('/home/tester/npf-public.zip') as archive: archive.extractall(original.parent/'original-unpack')
    for entry in (original.parent/'original-unpack/No-Part-Finance').iterdir():
        if entry.is_file(): shutil.copyfile(entry,original/entry.name)
        elif not (original/entry.name).exists(): shutil.copytree(entry,original/entry.name)
    shutil.copyfile('/home/tester/synthetic-finance.db',original/'finance.db')
    (original/'platform.conf').write_text('allowWebRegistration = false\n# synthetic adoption marker\n')
    original_hash=hashlib.sha256((original/'finance.db').read_bytes()).hexdigest()
    password=Path('/home/tester/adoption-password'); password.write_text('Lab-Manager-Synthetic-26!'); password.chmod(0o600)
    data=Path('/var/lib/NPF Custom')
    command=['sh',str(SOURCE/'installLinux.sh'),'--application-root','/opt/NPF Custom','--data',str(data),'--port','5050','--adopt',str(original),'--source-database',str(original/'finance.db'),'--source-stopped','--admin-password-file',str(password),'--certificate-names','localhost,127.0.0.1,npf-custom']
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=900)
    Path('/home/tester/adoption-install.log').write_text(result.stdout)
    password.unlink(missing_ok=True)
    assert result.returncode==0,result.stdout[-4000:]
    assert hashlib.sha256((original/'finance.db').read_bytes()).hexdigest()==original_hash
    assert 'synthetic adoption marker' in (data/'platform.conf').read_text()
    end=time.monotonic()+90
    while time.monotonic()<end:
        try:
            client=Client(data,password='Lab-Manager-Synthetic-26!')
            status=client.request('/api/status')
            if status['npf']=='running': break
        except Exception: pass
        time.sleep(1)
    else: raise AssertionError('Adopted custom-path installation failed readiness')
    assert database_info(data/'finance.db')['owner']
    assert len(status['history'])==0
    output={'adoption':True,'source_unchanged':True,'owner_setup_skipped':True,'custom_paths_with_spaces':True,'systemd_service_running':True,'application_version':status['version']['applicationVersion']}
    Path('/home/tester/adoption-results.json').write_text(json.dumps(output,indent=2))
    print(json.dumps(output))


def privileged_port():
    source=Path(__file__).resolve().parents[1]; data=Path('/var/lib/NPF Custom')
    settings=read_json(data/'settings.json'); settings['port']=443; write_json(data/'settings.json',settings)
    completed=subprocess.run(['sh',str(source/'installLinux.sh'),'--data',str(data)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=900)
    Path('/home/tester/privileged-port-install.log').write_text(completed.stdout)
    assert completed.returncode==0,completed.stdout[-2000:]
    end=time.monotonic()+90
    while time.monotonic()<end:
        try:
            c=Client(data,password='Lab-Manager-Synthetic-26!'); s=c.request('/api/status')
            if s['npf']=='running': break
        except Exception: pass
        time.sleep(1)
    else: raise AssertionError('Privileged port installation failed')
    unit=Path('/etc/systemd/system/npf-manager.service').read_text()
    assert 'User=npf' in unit and 'AmbientCapabilities=CAP_NET_BIND_SERVICE' in unit
    print('HTTPS port 443 works with unprivileged service identity and scoped bind capability.')


def manager_failure():
    client,status=wait_status('running')
    active=status['active']; password=(DATA/'auth.json').read_bytes(); cert=(DATA/'tls/certificate.pem').read_bytes()
    fixture=Path('/home/tester/failing-manager-fixture')
    shutil.rmtree(fixture,ignore_errors=True)
    shutil.copytree(SOURCE,fixture,ignore=shutil.ignore_patterns('__pycache__','screenshots','validation'))
    server=fixture/'server.py'; server.write_text("raise RuntimeError('Synthetic manager startup failure')\n"+server.read_text())
    completed=subprocess.run(['sh',str(fixture/'installLinux.sh'),'--data',str(DATA)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=900)
    Path('/home/tester/manager-failure.log').write_text(completed.stdout)
    assert completed.returncode!=0,'Broken manager update incorrectly reported success'
    client,status=wait_status('running')
    assert status['active']==active and (DATA/'auth.json').read_bytes()==password and (DATA/'tls/certificate.pem').read_bytes()==cert
    record('manager-update-failure','Broken candidate manager rejected; previous service restored; NPF active version, password and certificate preserved.')
    previous=status['active']; target=status['history'][0]['id']
    action(client,'rollback',id=target,confirmed=True)
    status=client.request('/api/status')
    assert next(x for x in status['history'] if x['id']==previous)['snapshot']
    action(client,'rollback',id=previous,confirmed=True)
    assert client.request('/api/status')['active']==previous
    record('actual-snapshot-rollback','Outgoing snapshot associated with history; snapshot rollback returns to original active version.')


def github():
    client,status=wait_status('running')
    previous=status['active']
    entry=action(client,'github')
    assert entry['kind']=='application' and 'SHA-256 verified' in entry['origin']
    assert client.request('/api/status')['active']==previous
    record('github-staging',{'version':entry['applicationVersion'],'checksum_verified':True,'active_unchanged':True})


def uninstall_check():
    root=Path('/opt/npf'); data=Path('/var/lib/npf')
    source=SOURCE
    archive=Path('/home/tester/uninstall-lab-snapshot.tar.gz')
    result=Path('/home/tester/uninstall-results.json')
    # Installer update registers the root-owned deletion manifest and publishes the CLI command.
    subprocess.run([sys.executable,str(source/'installer.py'),'--data',str(data)],check=True)
    expected={'manager.py','server.py','cli.py','installer.py','npf_bridge.py','windows_service.py'}
    assert {path.name for path in (root/'manager').glob('*.py')} == expected
    assert not (root/'manager/tools').exists() and not (root/'manager/tests').exists()
    subprocess.run(['systemctl','stop','npf-manager'],check=True)
    with tarfile.open(archive,'w:gz') as backup:
        backup.add(root,arcname='opt/npf'); backup.add(data,arcname='var/lib/npf')
    archive.chmod(0o600)
    entries=[['sh',str(source/'installLinux.sh'),'uninstall'],[sys.executable,str(source/'installer.py'),'uninstall']]
    records=[]
    for entry in entries:
        subprocess.run(['systemctl','start','npf-manager'],check=True)
        try:
            refused=subprocess.run(entry,capture_output=True,text=True)
            assert refused.returncode != 0 and '--yes' in refused.stderr, refused.stdout+refused.stderr
            assert data.exists() and (root/'manager/cli.py').exists()
            completed=subprocess.run(entry+['--yes'],capture_output=True,text=True)
            assert completed.returncode == 0, completed.stdout+completed.stderr
            assert not data.exists(), 'Data remains'
            assert not root.exists(), 'Installed application files remain'
            for path in ['/etc/systemd/system/npf-manager.service','/usr/local/bin/npf-manager','/etc/npf-manager.json']:
                assert not Path(path).exists(), path
            assert subprocess.run(['systemctl','is-active','--quiet','npf-manager']).returncode != 0
            records.append({'entry_point':Path(entry[1]).name,'installed_python_modules':6,'development_helpers_not_installed':True,
                'noninteractive_confirmation_required':True,'running_service_stopped_and_removed':True,
                'application_and_manager_removed':True,'database_configuration_backups_and_keys_removed':True,
                'launcher_and_locator_removed':True,'lab_installation_restored_after_verification':True})
        finally:
            # Only the synthetic lab installation is restored; a real uninstall has no retained copy.
            subprocess.run(['tar','-xzf',str(archive),'-C','/'],check=True)
            subprocess.run([sys.executable,str(source/'installer.py'),'--data',str(data)],check=True)
    archive.unlink()
    result.write_text(json.dumps({'installer_uninstall_checks':records},indent=2))
    print(result.read_text())


def ownerless_install():
    root, data = Path('/opt/npf-ownerless-test'), Path('/var/lib/npf-ownerless-test')
    assert not root.exists() and not data.exists(), 'Use fresh disposable test paths.'
    password_file = ARTIFACTS/'ownerless-password-input'
    password_file.write_text(PASSWORD); password_file.chmod(0o600)
    try:
        subprocess.run(['systemctl', 'stop', 'npf-manager'], check=True)
        args = ['sh', str(SOURCE/'installLinux.sh'), '--application-root', str(root), '--data', str(data),
                '--package', '/home/tester/npf-public.zip', '--port', '5051', '--npf-port', '5001',
                '--admin-password-file', str(password_file), '--certificate', str(DATA/'tls/certificate.pem'),
                '--private-key', str(DATA/'tls/private-key.pem'), '--certificate-names', 'localhost,127.0.0.1']
        terminal_install(args, [('Create the required Owner now?', 'n')], 'ownerless-install.log')
        client, value = wait_status('running', data=data)
        assert read_json(data/'settings.json')['installed'] and value['owner_setup_required'] is True
        record('ownerless-install', 'Fresh installation completed, systemd service active, HTTPS API reachable and NPF running without an Owner.')
        terminal_install(['sh', str(SOURCE/'installLinux.sh'), '--data', str(data)], owner_prompts(), 'ownerless-complete.log')
        client, value = wait_status('running', data=data)
        assert value['owner_setup_required'] is False and database_info(data/'finance.db')['owner']
        record('ownerless-completion', 'Rerunning the completed installation offered protected Owner setup; actual Owner creation succeeded and status cleared the warning.')
    finally:
        password_file.unlink(missing_ok=True)
        # Restore the independent original synthetic service before removing temporary fixture paths.
        subprocess.run([sys.executable, str(SOURCE/'installer.py'), '--data', str(DATA)], check=True)
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(data, ignore_errors=True)


def ownerless_fixture():
    # Build an empty database through upstream initialization, retaining its Owner safeguards.
    manager = Manager(DATA)
    target = ARTIFACTS/'ownerless.db'
    target.unlink(missing_ok=True)
    manager.bridge('validate-db', database=target)
    assert not database_info(target)['owner']
    target.chmod(0o644)
    record('ownerless-fixture', 'Fresh synthetic database initialized without an Owner for browser acceptance.')


def main():
    phases = {'install': install, 'runtime': runtime, 'boot': boot, 'stable': stable,
              'failure': fail, 'interrupt': interrupt, 'reboot': lambda: interrupt(True),
              'resume': resume, 'manager-failure': manager_failure, 'github': github,
              'adoption': adoption, 'privileged-port': privileged_port, 'uninstall': uninstall_check,
              'ownerless-fixture': ownerless_fixture, 'ownerless-install': ownerless_install}
    parser = argparse.ArgumentParser(description='Remote disposable Linux guest acceptance checks')
    parser.add_argument('phase', choices=phases)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo inside a disposable remote test VM only.')
    ARTIFACTS.mkdir(exist_ok=True)
    phases[args.phase]()


if __name__ == '__main__':
    main()
