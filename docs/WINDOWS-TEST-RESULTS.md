# Windows 11 verification — release 26.11

Tested on 2026-11-02 (America/Chicago). Windows 11 is tested for the workflows
recorded below; the remaining scenarios are listed at the end. This report
replaces the earlier pending Windows checklist.

Environment: Windows 11 x64 build 26200, Windows PowerShell 5.1.26100.9711,
Python 3.12.10, pywin32 312, cryptography 50.0.2. Source baseline:
`windows-fixes` at `e93e33c`, with the release fixes described below.
Manager 26.11; NPF application 26.10.30; database schema 30.

This run recovered an existing incomplete installation with explicit owner
approval. It was not a clean-machine install. No financial records were
printed or exported. ACL regression fixtures used synthetic text files.
The Linux integration/acceptance suite was not run on this Windows computer.

## Reproduced failures and changes

1. Recursive `icacls /inheritance:r /grant:r ...:(OI)(CI)... /T` left files
   with empty effective ACLs. An elevated disposable fixture reproduced the
   failure. Set inheritable grants on each root directory, then reset descendant
   ACLs to inherit them. Reapply Modify access to releases and persistent data.
   The earlier installation also needed an approved, one-time ownership recovery
   of SYSTEM-owned files in its dedicated Python runtime before ACL restoration.
2. The batch wrapper invokes Windows PowerShell 5.1, whose native argument
   handling stripped the quotes in `struct.calcsize("P")`. The original Python
   probe failed with `NameError: name 'P' is not defined`; using `chr(80)` passed
   under the same shell and interpreter.
3. Service startup failed at `win32job.CreateJobObject(None, None)` with
   `TypeError: None is not a valid string in this context`. A unique string name
   fixes creation with the installed pywin32 version.
4. `sc.exe start` accepted the request before the service crashed, so installation
   incorrectly reported success. Installation now waits for Running and checks
   again after one second, reporting exit codes if the service stops immediately.
   This is a startup check, not a long-term health guarantee.
5. A Windows CRLF checkout produced CSP hashes over CRLF bytes, while the browser
   normalizes HTML inline blocks to LF before checking their hashes. This blocked
   the sign-in handler and caused plain form navigation to `/?`, resembling a
   password problem. Normalize CRLF and CR to LF before serving/hashing HTML.
   The focused smoke check now verifies identical pages and CSP for all three
   newline conventions.
6. After successful NPF sign-in, the application page failed with
   `UnicodeDecodeError` in `pageShell`: `Path.read_text()` used Windows cp1252
   for UTF-8 HTML. Launch NPF runtime, validation and Owner setup through a shared
   command helper using Python `-X utf8`. This leaves the NPF release and database
   contents unchanged and also applies to future managed releases.

## Passed

- Existing base Python and installer Python can import pip after ACL recovery.
- Elevated patched installer completes with exit 0 in default paths containing
  spaces, reusing the existing configuration and Owner-configured database.
- Windows Service is registered Automatic under `NT AUTHORITY\LocalService` and
  remains Running during the subsequent smoke checks, with zero exit codes.
- Manager serves HTML at `https://127.0.0.1:5050`, verified using its installed
  certificate, with Content-Security-Policy present.
- Unauthenticated `/api/status` returns HTTP 401.
- Browser manager sign-in succeeds after the newline/CSP fix; the authenticated
  dashboard shows `Signed in.` and `Status: running`. This verifies login and
  authenticated status retrieval, not the remaining mutation workflows.
- NPF serves its application page at `http://127.0.0.1:5000` with HTTP 200.
- `tests/windows_smoke.py` passes: existing/new nested file access, exact allowed
  ACL identities and LocalService RX/Modify masks, permission rerun, empty
  directory, actual PowerShell 5.1 Python/pip check, missing interpreter rejection,
  pywin32 job creation, and identical browser-compatible CSP for LF/CRLF/CR HTML.
- Python compilation and `git diff --check` pass.
- The managed NPF command reads synthetic UTF-8 HTML despite inherited
  `PYTHONUTF8=0`. The installed application's actual page view returns HTTP 200
  with HTML when invoked in isolation; no live authentication bypass or financial
  data access is used for that check. The running NPF process has `-X utf8`.

Local transcripts are under ignored `artifacts/`; they are not publication
artifacts because they include machine/account details.

## Windows directory rename

New Windows defaults are `C:\Program Files\NPF-Server-Manager` for application
files, `C:\ProgramData\NPF-Server-Manager` for data, and
`C:\ProgramData\NPF-Server-Manager-InstallerRuntime` for the bootstrap environment.
An installer-provisioned Python resides under the new application directory.
Linux defaults and scripts are unchanged by this rename. Existing installations
still follow their registered paths unless explicitly migrated.

With explicit owner approval, this Windows installation was stopped and moved to
those directories. A locked-directory failure restored the original service;
after the relevant windows were closed, migration completed. The move preserved
the database bytes before restart and preserved manager authentication and TLS
files. Virtual-environment base paths, settings, installation manifests, locator,
launcher and service were updated. The old `C:\Program Files\NPF` path is a
compatibility junction to the new directory for the registered system Python and
existing console launchers; it contains no separate installation. Old data and
bootstrap paths are absent.

Post-migration checks passed: service Running at the new paths; both runtimes and
pip use the relocated Python; installed CLI discovery and trusted uninstall
manifest resolve the new directories; read-only database `quick_check` returns
`ok`; trusted manager HTTPS, unauthenticated API rejection and the NPF page work;
the focused Windows smoke checks pass. No uninstall was executed. Cleanup supports
both the new and legacy dedicated bootstrap names.

## Windows installed-apps uninstall registration

The Windows installer now publishes a per-machine, 64-bit uninstall entry named
NPF Server Manager, version 26.11, with publisher, installation path and command.
NoModify and NoRepair are enabled. The installed PowerShell launcher requests UAC
elevation before reading the trusted installation manifest and invoking the
existing CLI. It never supplies `--yes`; the CLI shows the permanent financial
data deletion warning and default-No confirmation. Only the launcher is made
readable to ordinary users, with non-inheriting directory traversal grants.

Successful deferred file cleanup removes registration last. Cancellation leaves
registration intact; a cleanup exception leaves the entry available for
troubleshooting. Registration is refreshed on install/update. Linux does not
install the new launcher or execute the Windows registry code.

Passed on this PC: live registration at the renamed installation paths, readable
launcher from an unelevated session, expected registry fields and disabled
Modify/Repair, and continued Running service. Temporary-fixture regression checks
passed for actual HKLM registration/update, native Windows command-line parsing
with spaces/apostrophes, cancellation before service removal, and actual cleanup
of fixture files/registration while preserving an unrelated file. The launcher
parses under Windows PowerShell 5.1. The real installation was not uninstalled;
complete destructive acceptance remains for a disposable installation.

## Remaining acceptance

Additional authenticated UI/CLI mutation workflows, reboot, controlled shutdown/crash containment,
Owner setup/skip flows, clean prerequisite provisioning, broken-bootstrap rebuild,
custom paths and ports, LAN certificate trust, updates/rollback/file replacement,
failure injection and full installed-system uninstall.
Do not run destructive acceptance scenarios against existing financial data.
