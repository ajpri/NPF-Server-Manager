"""Windows SCM host with contained NPF child processes."""
import os
from pathlib import Path
import sys
import subprocess
import threading
import uuid

if sys.platform=='win32':
    import servicemanager
    import win32api
    import win32event
    import win32job
    import win32service
    import win32serviceutil
    from manager import VERSION, read_json

    class NPFService(win32serviceutil.ServiceFramework):
        _exe_name_=sys.executable
        _exe_args_=subprocess.list2cmdline([str(Path(__file__).resolve())])
        _svc_name_='NPFServerManager'
        _svc_display_name_='NPF Server Manager'
        _svc_description_=f'NPF Server Manager {VERSION}; supervises No Part Finance and its HTTPS management interface.'

        def __init__(self,args):
            super().__init__(args)
            self.stop_event=threading.Event()
            self.server=None
            self.manager=None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.stop_event.set()
            if self.server:
                threading.Thread(target=self.server.shutdown,daemon=True).start()

        def SvcDoRun(self):
            from manager import Manager
            from server import HTTPServer
            # All descendants belong to a kill-on-close job, including on service crashes.
            # pywin32 requires a string name, rather than the Win32 NULL name.
            job=win32job.CreateJobObject(None,'NPFServerManager-'+uuid.uuid4().hex)
            limits=win32job.QueryInformationJobObject(job,win32job.JobObjectExtendedLimitInformation)
            limits['BasicLimitInformation']['LimitFlags']=win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job,win32job.JobObjectExtendedLimitInformation,limits)
            win32job.AssignProcessToJobObject(job,win32api.GetCurrentProcess())
            locator=Path(os.environ['PROGRAMDATA'])/'NPF-Server-Manager.json'
            try:
                self.manager=Manager(read_json(locator)['data'])
                self.manager.start_service()
                self.server=HTTPServer(self.manager)
                if not self.stop_event.is_set(): self.server.serve_forever(poll_interval=.2)
            except Exception:
                servicemanager.LogErrorMsg('NPF Server Manager failed. Inspect private NPF data and recovery records.')
                raise
            finally:
                if self.server: self.server.server_close()
                if self.manager: self.manager.close()
                # Keep the job handle alive for the service lifetime.
                self.job=job

    if __name__=='__main__':
        if len(sys.argv)==1:
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(NPFService)
            servicemanager.StartServiceCtrlDispatcher()
        else:
            sys.exit(win32serviceutil.HandleCommandLine(NPFService) or 0)
