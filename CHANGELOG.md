# Release notes

## 26.11 — 2026-11-02

NPF Server Manager installs, supervises and updates one No Part Finance
installation, with an authenticated HTTPS manager, local CLI, full database
backups and version-associated restore.

### Windows

- Use `C:\Program Files\NPF-Server-Manager` for application files and
  `C:\ProgramData\NPF-Server-Manager` for persistent data. Bootstrap Python lives
  under the application directory; the temporary installer environment is
  `C:\ProgramData\NPF-Server-Manager-InstallerRuntime`.
- Register NPF Server Manager in Control Panel and Installed apps. Uninstall
  requests elevation and explicit confirmation before permanent data deletion.
  Cancellation retains the installation; successful cleanup removes registration.
- Fix PowerShell 5.1 interpreter detection, recursive file permissions and
  pywin32 job creation. Check service startup before reporting installation success.
- Normalize HTML newlines before generating CSP hashes so Windows checkouts can
  sign in. Run managed NPF commands in UTF-8 mode to render its HTML correctly.
- Preserve registered paths on installer reruns. Changing defaults does not
  automatically relocate existing installations. The tested machine was migrated
  separately, retaining a compatibility junction for its registered Python.

Linux application/data defaults remain `/opt/npf` and `/var/lib/npf`; Linux
installer and service behavior are unchanged by the Windows registration/path
changes. The REST API version and endpoints remain 26.11.

### Verification

Linux acceptance was recorded on Debian 13 and Fedora 44 in remote disposable
guests. Windows 11 x64 build 26200 was tested directly for installation recovery,
service startup, HTTPS/login/reset, runtime rendering, relocation and installed-app
registration. Focused Windows fixtures verify ACLs, bootstrap probes, CSP/UTF-8
handling, job creation, registration/update, cancellation and cleanup.

See [Linux results](docs/TEST-RESULTS.md) and
[Windows results](docs/WINDOWS-TEST-RESULTS.md) for evidence and limitations.
Windows reboot/crash behavior, clean prerequisite provisioning, complete mutation
workflows and full destructive uninstall are not established by the smoke run.
