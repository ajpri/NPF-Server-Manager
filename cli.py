"""Local CLI; all live operations use the manager's authenticated API."""
import argparse
import getpass
import json
import os
from pathlib import Path
import ssl
import sys
import urllib.error
import urllib.request

from manager import DEFAULT_DATA, ManagerError, VERSION, atomic, read_json, set_password


def default_data():
    locator = Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData'))/'NPF-Server-Manager.json' if os.name=='nt' else Path('/etc/npf-manager.json')
    return Path(read_json(locator,{}).get('data',str(DEFAULT_DATA)))


def privileged():
    if os.name=='nt':
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    return os.geteuid()==0


class Client:
    def __init__(self, data, url=None, ca=None, password=None):
        self.data=Path(data)
        self.settings=read_json(self.data/'settings.json',{})
        self.url=url or 'https://127.0.0.1:'+str(self.settings.get('port',5050))
        if not self.url.startswith('https://'):
            raise ManagerError('HTTPS is required.')
        self.context=ssl.create_default_context(cafile=ca or self.settings.get('certificate'))
        self.token=None
        result=self.request('/api/login',{'password':password if password is not None else getpass.getpass('Manager administrator password: ')})
        self.token=result['token']

    def request(self,path,values=None,source=None,destination=None):
        headers={}
        if self.token:
            headers['Authorization']='Bearer '+self.token
        body=None
        stream=None
        if source is not None:
            stream=Path(source).open('rb')
            body=stream
            headers.update({'Content-Type':'application/octet-stream','Content-Length':str(Path(source).stat().st_size)})
        elif values is not None:
            body=json.dumps(values).encode()
            headers['Content-Type']='application/json'
        request=urllib.request.Request(self.url+path,data=body,headers=headers)
        try:
            with urllib.request.urlopen(request,context=self.context,timeout=900) as response:
                if destination is not None:
                    path=Path(destination)
                    # Refuse accidental overwrite of an existing database or config.
                    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    if os.name=='nt':
                        import subprocess
                        import win32api
                        try:
                            subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',win32api.GetUserNameEx(2)+':F','*S-1-5-32-544:F','*S-1-5-18:F'],check=True,stdout=subprocess.DEVNULL)
                        except Exception:
                            os.close(descriptor); path.unlink(missing_ok=True); raise
                    with os.fdopen(descriptor,'wb') as out:
                        try:
                            while chunk:=response.read(1024*1024): out.write(chunk)
                        except Exception:
                            out.close(); path.unlink(missing_ok=True); raise
                    return {'saved':str(path)}
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                detail=json.load(error)['error']
            except Exception:
                detail='HTTP request failed: '+str(error.code)
            raise ManagerError(detail) from error
        finally:
            if stream: stream.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description='NPF Server Manager '+VERSION)
    parser.add_argument('--version',action='version',version=VERSION)
    parser.add_argument('--data',type=Path)
    parser.add_argument('--url',help='HTTPS management URL')
    parser.add_argument('--ca',help='Trusted management certificate/CA PEM')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ['status','start','stop','restart','github','reset-password','serve']:
        sub.add_parser(name)
    uninstall_parser=sub.add_parser('uninstall'); uninstall_parser.add_argument('--yes',action='store_true')
    stage=sub.add_parser('stage'); stage.add_argument('kind',choices=['application','database','configuration']); stage.add_argument('file',type=Path)
    download=sub.add_parser('download'); download.add_argument('kind',choices=['application','database','configuration']); download.add_argument('destination',type=Path)
    apply=sub.add_parser('apply'); apply.add_argument('id')
    rollback=sub.add_parser('rollback'); rollback.add_argument('id'); rollback.add_argument('--yes',action='store_true')
    replace=sub.add_parser('replace'); replace.add_argument('kind',choices=['database','configuration']); replace.add_argument('id'); replace.add_argument('--yes',action='store_true')
    restart=replace.add_mutually_exclusive_group(); restart.add_argument('--restart',dest='restart',action='store_true'); restart.add_argument('--no-restart',dest='restart',action='store_false'); replace.set_defaults(restart=None)
    delete=sub.add_parser('delete'); delete.add_argument('category',choices=['staged','recovery']); delete.add_argument('id'); delete.add_argument('--yes',action='store_true')
    args=parser.parse_args(argv)
    args.data=args.data or default_data()
    if args.command=='uninstall':
        from installer import uninstall
        uninstall(args.data,yes=args.yes)
        return
    if args.command=='serve':
        serve(args.data)
        return
    if args.command=='reset-password':
        if not privileged(): raise ManagerError('Local administrator/root privileges are required.')
        password=getpass.getpass('New manager administrator password: ')
        if password!=getpass.getpass('Confirm password: '): raise ManagerError('Passwords do not match.')
        set_password(args.data,password)
        print('Administrator password reset. Previous sessions are invalid.')
        return
    if args.command in {'replace','rollback','delete'} and not args.yes:
        consequence=('Restore this application version and its matching database backup? Newer financial data will be replaced. Download the current full package first if needed.' if args.command=='rollback' else 'This can replace financial and credential state.' if args.command=='replace' else 'This permanently deletes the retained copy.')
        if not sys.stdin.isatty(): raise ManagerError('Use --yes for explicit noninteractive confirmation.')
        if input(consequence+' Continue? (y/N) ').strip().lower()!='y': return
    if args.command=='replace' and args.kind=='configuration' and args.restart is None:
        if not sys.stdin.isatty(): raise ManagerError('Noninteractive configuration replacement requires --restart or --no-restart.')
        args.restart=input('Restart NPF now? (y/N) ').strip().lower()=='y'
    client=Client(args.data,args.url,args.ca)
    if args.command=='status': result=client.request('/api/status')
    elif args.command=='download': result=client.request('/api/download/'+args.kind,destination=args.destination)
    elif args.command=='stage': result=client.request('/api/stage/'+args.kind,source=args.file)
    else:
        values={}
        action=args.command
        if action=='apply': values={'id':args.id}
        if action=='rollback': values={'id':args.id,'confirmed':True}
        if action=='replace':
            action='replace-'+args.kind
            values={'id':args.id,'confirmed':True,'restart':bool(args.restart)}
        if action=='delete': values={'category':args.category,'id':args.id}
        result=client.request('/api/action/'+action,values)
    print(json.dumps(result,indent=2))


def serve(data):
    import signal
    import threading
    from manager import Manager
    from server import HTTPServer
    manager=Manager(data)
    server=None
    try:
        manager.start_service()
        server=HTTPServer(manager)
        def stop(*_): threading.Thread(target=server.shutdown,daemon=True).start()
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
        server.serve_forever(poll_interval=.2)
    finally:
        if server: server.server_close()
        manager.close()


if __name__=='__main__':
    try:
        main()
    except (ManagerError,OSError,ValueError) as error:
        print('Error: '+str(error),file=sys.stderr)
        sys.exit(1)
