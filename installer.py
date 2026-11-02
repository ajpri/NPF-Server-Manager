"""Interactive, resumable platform installer; never imported by the running service."""
import argparse
import base64
import contextlib
import getpass
import ipaddress
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time

from manager import DEFAULT_DATA, HERE, VERSION, InstanceLock, Manager, ManagerError, atomic, database_info, metadata, private_dir, python_for, read_json, set_password, snapshot, write_json

FILES=['manager.py','server.py','cli.py','npf_bridge.py','windows_service.py','installer.py','installLinux.sh','installWindows.bat','installWindows.ps1','requirements.txt','index.html','README.md','SPEC.md','VERSION']


def ask(prompt, default=None):
    if not sys.stdin.isatty():
        if default is None: raise ManagerError(prompt+' requires interactive input or an explicit installer option.')
        return default
    value=input(prompt+(f' [{default}]' if default is not None else '')+': ').strip()
    return value or default


def certificate(data,names,cert=None,key=None):
    tls=data/'tls'; private_dir(tls)
    target_cert=tls/'certificate.pem'; target_key=tls/'private-key.pem'
    if bool(cert)!=bool(key): raise ManagerError('Supply both --certificate and --private-key.')
    with tempfile.TemporaryDirectory(dir=data) as work:
        work=Path(work); c=work/'cert.pem'; k=work/'key.pem'
        if cert:
            shutil.copyfile(cert,c); shutil.copyfile(key,k)
        else:
            identities=[]
            for name in names:
                try: identities.append(('IP',str(ipaddress.ip_address(name))))
                except ValueError:
                    import re
                    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',name): raise ManagerError('Invalid certificate hostname.')
                    identities.append(('DNS',name))
            if os.name=='nt':
                from cryptography import x509
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import rsa
                from cryptography.x509.oid import NameOID
                import datetime
                secret=rsa.generate_private_key(public_exponent=65537,key_size=3072)
                subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,names[0])])
                now=datetime.datetime.now(datetime.timezone.utc)
                signed=x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(secret.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5)).not_valid_after(now+datetime.timedelta(days=365)).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(v)) if t=='IP' else x509.DNSName(v) for t,v in identities]),critical=False).sign(secret,hashes.SHA256())
                c.write_bytes(signed.public_bytes(serialization.Encoding.PEM)); k.write_bytes(secret.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            else:
                subprocess.run(['openssl','req','-x509','-newkey','rsa:3072','-sha256','-nodes','-days','365','-subj','/CN='+names[0],'-addext','subjectAltName='+','.join(t+':'+v for t,v in identities),'-keyout',str(k),'-out',str(c)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        check=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); check.load_cert_chain(c,k)
        atomic(target_cert,c.read_bytes()); atomic(target_key,k.read_bytes())
    return str(target_cert),str(target_key)


def linux_identity(root,data):
    import pwd
    try: user=pwd.getpwnam('npf')
    except KeyError:
        subprocess.run(['useradd','--system','--home-dir',str(data),'--no-create-home','--shell','/usr/sbin/nologin','npf'],check=True)
        user=pwd.getpwnam('npf')
    for path in [data,root/'releases']:
        if path.exists():
            shutil.chown(path,user.pw_uid,user.pw_gid)
            for directory,folders,files in os.walk(path):
                for name in folders+files:
                    entry=Path(directory)/name
                    if not entry.is_symlink(): os.chown(entry,user.pw_uid,user.pw_gid)
    return user


def service(root,data,settings):
    code=root/'manager'
    if os.name!='nt':
        import pwd
        try: pwd.getpwnam('npf')
        except KeyError: settings['created_service_account']=True
        linux_identity(root,data)
        def quoted(value): return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
        unit='''[Unit]\nDescription=NPF Server Manager '''+VERSION+'''\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\nUser=npf\nGroup=npf\nExecStart='''+quoted(sys.executable)+' '+quoted(code/'cli.py')+' --data '+quoted(data)+''' serve
Restart=on-failure
RestartSec=5
TimeoutStopSec=120
KillMode=control-group
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths='''+quoted(data)+' '+quoted(root/'releases')+'''\n\n[Install]\nWantedBy=multi-user.target\n'''
        if settings['port'] < 1024 or settings.get('npf_port',5000) < 1024:
            unit=unit.replace('UMask=0077','UMask=0077\nAmbientCapabilities=CAP_NET_BIND_SERVICE\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE')
        # Custom locations under /home require explicit access rather than silently failing.
        if str(root).startswith('/home/') or str(data).startswith('/home/'):
            unit=unit.replace('ProtectHome=true','ProtectHome=false')
        atomic('/etc/systemd/system/npf-manager.service',unit)
        atomic('/etc/npf-manager.json',json.dumps({'data':str(data),'application_root':str(root)}))
        Path('/etc/npf-manager.json').chmod(0o644)
        launcher='#!/bin/sh\nexec '+__import__('shlex').quote(sys.executable)+' '+__import__('shlex').quote(str(code/'cli.py'))+' --data '+__import__('shlex').quote(str(data))+' "$@"\n'
        atomic('/usr/local/bin/npf-manager',launcher); Path('/usr/local/bin/npf-manager').chmod(0o755)
        subprocess.run(['systemctl','daemon-reload'],check=True)
        subprocess.run(['systemctl','enable','--now','npf-manager'],check=True)
    else:
        runtime=root/'runtime'
        runtime_python=runtime/'Scripts/python.exe'
        if not runtime_python.exists(): subprocess.run([sys.executable,'-m','venv',str(runtime)],check=True)
        subprocess.run([str(runtime_python),'-m','pip','install','-r',str(code/'requirements.txt')],check=True)
        # Administrators, SYSTEM and LocalService only; language-independent SIDs.
        for location,access in [(root,'RX'),(root/'releases','M'),(data,'M')]:
            subprocess.run(['icacls',str(location),'/inheritance:r','/grant:r','*S-1-5-32-544:(OI)(CI)F','*S-1-5-18:(OI)(CI)F','*S-1-5-19:(OI)(CI)'+access,'/T','/Q'],check=True,stdout=subprocess.DEVNULL)
        locator=Path(os.environ['PROGRAMDATA'])/'NPF-Server-Manager.json'
        atomic(locator,json.dumps({'data':str(data),'application_root':str(root)}))
        action='update' if subprocess.run(['sc.exe','query','NPFServerManager'],capture_output=True).returncode==0 else 'install'
        subprocess.run([str(runtime_python),str(code/'windows_service.py'),'--startup','auto','--username','NT AUTHORITY\\LocalService',action],check=True)
        subprocess.run(['sc.exe','failure','NPFServerManager','reset=','86400','actions=','restart/5000/restart/5000/restart/5000'],check=True)
        subprocess.run(['sc.exe','start','NPFServerManager'],check=True)
        atomic(root/'npf-manager.bat','@echo off\n"'+str(runtime_python)+'" "'+str(code/'cli.py')+'" --data "'+str(data)+'" %*\n')


def uninstall_plan(data):
    # Read installation paths from root/Administrator-owned code, never mutable service settings.
    record = read_json(HERE/'installation.json')
    if not record:
        raise ManagerError('Rerun the installer once to register this installation before uninstalling.')
    root = Path(record['application_root'])
    stored_data = Path(record['data'])
    database = Path(record['database'])
    if any(not path.is_absolute() or path.is_symlink() for path in (root, stored_data, database)):
        raise ManagerError('Refusing unsafe installation paths.')
    root, stored_data, database = root.resolve(), stored_data.resolve(), database.resolve()
    forbidden = {Path('/').resolve(), Path.home().resolve()}
    if os.name == 'nt':
        forbidden.update(Path(os.environ[name]).resolve() for name in ('SystemRoot', 'ProgramFiles', 'PROGRAMDATA', 'USERPROFILE') if name in os.environ)
    else:
        forbidden.update(Path(value) for value in ('/usr', '/usr/local', '/etc', '/var', '/var/lib', '/opt', '/home', '/tmp'))
    if root in forbidden or stored_data in forbidden or len(root.parts)<3 or len(stored_data.parts)<3:
        raise ManagerError('Refusing to remove a system or home directory.')
    if HERE.resolve() != root/'manager' or Path(data).resolve() != stored_data:
        raise ManagerError('Uninstall must run from the registered installed CLI and data location.')
    if root == stored_data or root in stored_data.parents or stored_data in root.parents:
        raise ManagerError('Application and data paths must be separate directory trees.')
    if database.parent != stored_data:
        raise ManagerError('Database path is outside registered data; move/preserve it before uninstalling.')
    return dict(record, root=root, data=stored_data, database=database)


def remove_path(path):
    path = Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def stop_service():
    if os.name != 'nt':
        if Path('/etc/systemd/system/npf-manager.service').exists():
            subprocess.run(['systemctl', 'disable', '--now', 'npf-manager'], check=True)
    else:
        import win32service
        import win32serviceutil
        try:
            status = win32serviceutil.QueryServiceStatus('NPFServerManager')[1]
        except Exception as error:
            if getattr(error, 'winerror', None) == 1060:
                return
            raise
        if status != win32service.SERVICE_STOPPED:
            win32serviceutil.StopService('NPFServerManager')
            deadline = time.monotonic()+120
            while win32serviceutil.QueryServiceStatus('NPFServerManager')[1] != win32service.SERVICE_STOPPED:
                if time.monotonic() >= deadline:
                    raise ManagerError('Service did not stop; no installation files were removed.')
                time.sleep(.25)
        win32serviceutil.RemoveService('NPFServerManager')


def uninstall(data, yes=False):
    from cli import privileged
    if not privileged():
        raise ManagerError('Uninstall requires local administrator/root privileges.')
    installation = uninstall_plan(data)
    root, data = installation['root'], installation['data']
    print('Remove NPF Server Manager and No Part Finance, including ALL financial data, configuration, certificates and backups.')
    print('Application: '+str(root)+'\nData: '+str(data))
    if not yes:
        if not sys.stdin.isatty():
            raise ManagerError('Use --yes for explicit noninteractive uninstall confirmation.')
        if input('Permanently uninstall and delete all data? (y/N) ').strip().lower() != 'y':
            print('Uninstall cancelled.')
            return
    stop_service()
    # Refuse deletion if a manually launched manager still holds the installation lock.
    guard = InstanceLock(data)
    try:
        if os.name != 'nt':
            Path('/etc/systemd/system/npf-manager.service').unlink(missing_ok=True)
            Path('/usr/local/bin/npf-manager').unlink(missing_ok=True)
            Path('/etc/npf-manager.json').unlink(missing_ok=True)
            subprocess.run(['systemctl', 'daemon-reload'], check=True)
            subprocess.run(['systemctl', 'reset-failed', 'npf-manager'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            (Path(os.environ['PROGRAMDATA'])/'NPF-Server-Manager.json').unlink(missing_ok=True)
    finally:
        guard.close()
    owned = [data, *(root/name for name in ('manager', 'manager.previous', 'manager.new', 'releases', 'runtime', 'npf-manager.bat'))]
    if os.name == 'nt':
        # Windows locks the running Python/CLI. An OS-provided helper removes them after this process exits.
        owned.append(Path(os.environ['PROGRAMDATA'])/'NPF-InstallerRuntime')
        def literal(value):
            return "'"+str(value).replace("'", "''")+"'"
        script = "$ErrorActionPreference='Stop'; $p=Get-Process -Id "+str(os.getpid())+" -ErrorAction SilentlyContinue; if($p){$p.WaitForExit()}; "
        failure_log=Path(os.environ.get('TEMP',os.environ['SystemRoot']))/('NPF-uninstall-'+str(os.getpid())+'.log')
        script += 'try { '
        for path in owned:
            script += '$deadline=(Get-Date).AddSeconds(120); while(Test-Path -LiteralPath '+literal(path)+'){try{Remove-Item -LiteralPath '+literal(path)+' -Recurse -Force}catch{if((Get-Date) -ge $deadline){throw}; Start-Sleep -Milliseconds 500}}; '
        script += 'if((Test-Path -LiteralPath '+literal(root)+') -and -not (Get-ChildItem -LiteralPath '+literal(root)+' -Force | Select-Object -First 1)){Remove-Item -LiteralPath '+literal(root)+'}; '
        script += '} catch { $_ | Out-File -LiteralPath '+literal(failure_log)+' -Encoding utf8; exit 1 };'
        command = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', command], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, cwd=os.environ['SystemRoot'])
        print('Service removed. File cleanup completes after this command exits. Cleanup errors, if any: '+str(failure_log))
    else:
        os.chdir('/')
        for path in owned:
            remove_path(path)
        with contextlib.suppress(OSError):
            root.rmdir()  # Preserve unrelated files in a custom application directory.
        if installation.get('created_service_account'):
            subprocess.run(['userdel', 'npf'], check=True)
        print('NPF Server Manager, No Part Finance and all managed data have been removed.')


def main():
    parser=argparse.ArgumentParser(description='Install/update NPF Server Manager '+VERSION)
    parser.add_argument('command',nargs='?',choices=['install','update','uninstall'],default='install')
    parser.add_argument('--yes',action='store_true',help='Confirm permanent uninstall without prompting')
    parser.add_argument('--application-root',type=Path)
    parser.add_argument('--data',type=Path)
    parser.add_argument('--package',type=Path)
    parser.add_argument('--adopt',type=Path)
    parser.add_argument('--source-database',type=Path)
    parser.add_argument('--source-stopped',action='store_true')
    parser.add_argument('--admin-password-file',type=Path,help='Protected input file for unattended installation; never a command-line password')
    parser.add_argument('--port',type=int)
    parser.add_argument('--npf-port',type=int,default=5000)
    parser.add_argument('--bind',default='0.0.0.0')
    parser.add_argument('--certificate',type=Path)
    parser.add_argument('--private-key',type=Path)
    parser.add_argument('--certificate-names',help='Comma-separated DNS names/IPs; self-signed mode')
    parser.add_argument('--renew-certificate',action='store_true')
    args=parser.parse_args()
    from cli import default_data,privileged
    if not privileged(): raise ManagerError('Run installer with administrator/root privileges.')
    if args.command=='uninstall':
        locator=Path(os.environ.get('PROGRAMDATA','C:/ProgramData'))/'NPF-Server-Manager.json' if os.name=='nt' else Path('/etc/npf-manager.json')
        registered=read_json(locator,{})
        if not registered.get('application_root') or not registered.get('data'):
            raise ManagerError('No registered uninstall paths found. Rerun the installer once if this is an older installation.')
        root=Path(registered['application_root'])
        runtime=root/'runtime/Scripts/python.exe' if os.name=='nt' else Path(sys.executable)
        command=[str(runtime),str(root/'manager/cli.py'),'--data',str(args.data or registered['data']),'uninstall']
        if args.yes: command.append('--yes')
        raise SystemExit(subprocess.run(command,check=False).returncode)
    if args.yes:
        raise ManagerError('--yes is only supported with uninstall.')
    if platform.machine().lower() not in {'x86_64','amd64'}: raise ManagerError(f'Version {VERSION} supports x64 only.')
    if sys.version_info<(3,11): raise ManagerError('Python 3.11 or newer is required; upgrade the OS Python first.')
    data=(args.data or default_data()).resolve()
    existing=read_json(data/'settings.json')
    complete=existing and existing.get('installed')
    if existing:
        root=Path(existing['application_root'])
        if args.application_root and args.application_root.resolve()!=root: raise ManagerError('Installer updates retain selected paths; migration is a separate operation.')
        settings=existing
    else:
        root=(args.application_root or Path(ask('Application directory',str(Path(os.environ.get('ProgramFiles','C:/Program Files'))/'NPF') if os.name=='nt' else '/opt/npf'))).resolve()
        if args.data is None:
            data=Path(ask('Persistent data directory',str(data))).resolve()
            if (data/'settings.json').exists(): raise ManagerError('Selected data location already has settings; rerun with --data.')
        if root==data or root in data.parents or data in root.parents: raise ManagerError('Application and persistent-data locations must be separate directory trees.')
        settings={'application_root':str(root),'database':str(data/'finance.db'),'port':args.port or int(ask('Management HTTPS port','5050')),'npf_port':args.npf_port,'bind':args.bind,'installed':False,'manager_version':VERSION}
    if not 1<=settings['port']<=65535 or not 1<=settings['npf_port']<=65535 or settings['port']==settings['npf_port']: raise ManagerError('Choose distinct valid manager and NPF ports.')
    private_dir(data); root.mkdir(parents=True,exist_ok=True); private_dir(root/'releases')
    if os.name!='nt': root.chmod(0o755)
    old_code=root/'manager'
    if complete:
        subprocess.run(['systemctl','stop','npf-manager'],check=False) if os.name!='nt' else subprocess.run(['sc.exe','stop','NPFServerManager'],capture_output=True)
        if os.name=='nt': time.sleep(5)
    if not (data/'auth.json').exists():
        if args.admin_password_file:
            password=args.admin_password_file.read_text().rstrip('\r\n')
        else:
            password=getpass.getpass('Manager administrator password (8–128 characters): ')
            if password!=getpass.getpass('Confirm password: '): raise ManagerError('Passwords do not match.')
        set_password(data,password)
    if not settings.get('certificate') or args.renew_certificate or args.certificate:
        cert=args.certificate; key=args.private_key
        if not cert and not args.certificate_names:
            mode=ask('Certificate mode: generated or supplied','generated')
            if mode=='supplied': cert=Path(ask('Certificate PEM path')); key=Path(ask('Private key PEM path'))
            elif mode!='generated': raise ManagerError('Choose generated or supplied certificate mode.')
        names=(args.certificate_names or ask('Self-signed certificate names/IPs (comma-separated)','localhost,127.0.0.1,'+socket.gethostname())).split(',')
        settings['certificate'],settings['private_key']=certificate(data,[n.strip() for n in names],cert,key)
    write_json(data/'settings.json',settings)
    manager=Manager(data)
    guard=InstanceLock(data)
    try:
        if not complete:
            if not manager.state.get('active'):
                package=args.package
                package_origin='local installation package; publisher unverified'
                if args.adopt:
                    source=args.adopt.resolve(); db=(args.source_database or source/'finance.db').resolve()
                    if not args.source_stopped:
                        if ask('Stop the original NPF and disable its launcher. Confirm it is stopped (yes/no)','no')!='yes': raise ManagerError('Adoption cancelled; original must be stopped first.')
                    # Refuse a running source on the configured port even when a flag was supplied.
                    with socket.socket() as probe:
                        probe.settimeout(1)
                        if probe.connect_ex(('127.0.0.1',settings['npf_port']))==0: raise ManagerError('NPF port is in use; stop the source before adoption.')
                    database_info(db)
                    with tempfile.TemporaryDirectory(dir=data) as temp:
                        archive=Path(temp)/'adopt.zip'
                        import zipfile
                        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as out:
                            for entry in source.rglob('*'):
                                relative=entry.relative_to(source)
                                if entry.is_file() and not entry.is_symlink() and relative.parts[0] not in {'.venv','.git','logs','backups','packages'} and entry.suffix not in {'.db','.sqlite'}:
                                    out.write(entry,'No-Part-Finance/'+relative.as_posix())
                        item=manager.prepare(archive,'adopted local installation')
                    snapshot(db,manager.database)
                    if (source/'platform.conf').exists(): atomic(data/'platform.conf',(source/'platform.conf').read_bytes())
                else:
                    if not package:
                        try:
                            stage=manager.github(); package=data/'staged'/stage['id']/'file'; package_origin=stage['origin']
                        except Exception:
                            print('GitHub download failed. Supply a local NPF ZIP or rerun later.')
                            package=Path(ask('Local NPF ZIP path'))
                    item=manager.prepare(package,package_origin)
                manager.state['active']=item; manager.save(); manager.sync_configuration()
            needs_setup=not manager.database.exists() or not database_info(manager.database)['owner']
            if needs_setup:
                command=[str(python_for(manager.release())),str(HERE/'npf_bridge.py'),'setup',str(manager.app()),str(manager.database),str(manager.app()),str(settings['npf_port'])]
                subprocess.run(command,check=True)
                if not database_info(manager.database)['owner']: raise ManagerError('Owner setup incomplete; rerun to resume.')
                manager.sync_configuration(False)
        # Publish manager files only after initial NPF setup is complete.
        pending=root/'manager.new'
        shutil.rmtree(pending,ignore_errors=True); pending.mkdir()
        for name in FILES:
            source=HERE/name
            if source.exists(): shutil.copyfile(source,pending/name)
        backup=root/'manager.previous'
        shutil.rmtree(backup,ignore_errors=True)
        if old_code.exists(): old_code.rename(backup)
        pending.rename(old_code)
        settings.update(manager_version=VERSION)
        write_json(data/'settings.json',settings)
    finally:
        guard.close()
    try:
        service(root,data,settings)
        if os.name!='nt':
            time.sleep(1)
            subprocess.run(['systemctl','is-active','--quiet','npf-manager'],check=True)
    except Exception as error:
        if (root/'manager.previous').exists():
            shutil.rmtree(root/'manager',ignore_errors=True)
            (root/'manager.previous').rename(root/'manager')
            if os.name!='nt': subprocess.run(['systemctl','restart','npf-manager'],check=False)
        raise ManagerError('Manager service setup failed. Previous manager code restored if available; NPF data retained. '+str(error)) from error
    write_json(root/'manager/installation.json', {'application_root':str(root.resolve()), 'data':str(data.resolve()), 'database':str(Path(settings['database']).resolve()), 'created_service_account':settings.get('created_service_account',False)})
    settings['installed']=True
    write_json(data/'settings.json',settings)
    print('NPF Server Manager '+VERSION+' installed. HTTPS port '+str(settings['port'])+'.')
    print('Trust certificate: '+settings['certificate']+'. Include the server LAN name/IP in certificate identities.')
    print('CLI: '+('/usr/local/bin/npf-manager' if os.name!='nt' else str(root/'npf-manager.bat')))


if __name__=='__main__':
    try: main()
    except (ManagerError,OSError,ValueError,subprocess.SubprocessError) as error:
        print('Installation stopped: '+str(error),file=sys.stderr)
        sys.exit(1)
