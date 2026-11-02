# NPF Server Manager 26.11

Installer, updater and local service supervisor for No Part Finance. One x64 NPF installation per machine. Targets Debian/Fedora families with systemd and Windows 11. Linux verification runs only in remote QEMU guests; Windows testing is manual and must not be inferred from Linux results.

## Documentation

- [REST API — OpenAPI 3.1 JSON](openapi.json): endpoints, authentication, file transfers, schemas, responses and examples. Open it in an OpenAPI viewer; the manager does not host a documentation UI or serve this file.
- [CLI and installer reference](docs/CLI.md): every command/option, workflows, confirmation/restart behavior and troubleshooting.

## Install

Download/extract this manager project, then run:

```sh
./installLinux.sh
```

On an elevated Windows 11 terminal, run `installWindows.bat`. The scripts bootstrap prerequisites. Installation asks for paths, a manager administrator password, HTTPS certificate mode and NPF Owner setup. The two passwords are independent. Python 3.11+ is required by the manager; NPF has its own release dependencies. Debian 13 and Fedora 44 are the initial Linux verification targets. Other related distributions require separate verification; immutable Fedora variants are not supported by the dnf-based installer.

Defaults: `/opt/npf` and `/var/lib/npf` on Linux; `C:\Program Files\NPF` and `C:\ProgramData\NPF` on Windows. Management: `https://SERVER:5050`. Include the server LAN DNS name/IP in certificate identities when generating a certificate. A self-signed certificate is retained across restarts and lasts 365 days. Trust its public PEM on clients; never share its private key. Supplied certificate and key must be PEM; encrypted keys requiring interactive service startup are unsupported. Renew/replace through the installer (`--renew-certificate` or `--certificate FILE --private-key FILE`); this restarts the manager service. No plaintext fallback.

```sh
./installLinux.sh --application-root /opt/npf --data /var/lib/npf \
  --port 5050 --certificate-names localhost,127.0.0.1,finance.example.lan
./installLinux.sh --package /path/NPF.zip
./installLinux.sh --adopt /path/old-npf --source-database /path/finance.db
```

Adoption requires the original instance/launcher to be stopped; `--source-stopped` confirms this for automation. Original files remain untouched as a recovery copy. For unattended initial setup, provision an already initialized synthetic/adopted database; fresh Owner setup requires an interactive terminal. `--admin-password-file` supports a protected local input file for the manager password; do not put a password in shell arguments. Delete that input file after installation.

Rerun the same installer to update the manager. This retains manager credentials/certificates/settings and NPF data. NPF updates remain manual. The web page shows the current version and up to two previous versions, then file downloads and uploads. Uploads validate and request confirmation in one flow; temporary upload and recovery lists are managed through the advanced CLI/API.

## CLI

Linux installs `/usr/local/bin/npf-manager`. Windows installs `npf-manager.bat` under the application root. Live operations authenticate to the running HTTPS API; the administrator password is prompted without echo. Root/elevation is required for installation, local password reset and uninstall. On Linux use sudo when the private settings/certificate directory is not readable by the invoking account.

```sh
sudo npf-manager status
sudo npf-manager start
sudo npf-manager stop
sudo npf-manager github
sudo npf-manager stage application /path/NPF.zip
sudo npf-manager apply STAGED_ID              # full database backup required
sudo npf-manager rollback VERSION_ID --yes
sudo npf-manager restart
sudo npf-manager download application /safe/NPF-full-package.zip
sudo npf-manager download database /safe/new-finance.db
sudo npf-manager download configuration /safe/new-platform.conf
sudo npf-manager stage database /safe/replacement.db
sudo npf-manager replace database STAGED_ID --yes
sudo npf-manager stage configuration /safe/platform.conf
sudo npf-manager replace configuration STAGED_ID --yes --no-restart
sudo npf-manager replace configuration STAGED_ID --yes --restart
sudo npf-manager delete staged STAGED_ID --yes
sudo npf-manager delete recovery RECOVERY_ID --yes
sudo npf-manager reset-password
```

CLI downloads use private file permissions and refuse to overwrite existing destinations. Local reset invalidates old sessions. Configuration replacement prompts `Restart NPF now? (y/N)` when neither restart flag is supplied. Noninteractive replacement requires an explicit choice. No means save for next start. No preserves a stopped NPF; an explicit restart starts it. The web page has one shared upload-and-restart button: choose platform.conf, finance.db, or both. Both candidates validate before any live change, and one backup/restart applies them together; startup failure restores both.

`--url https://HOST:PORT --ca /path/trusted-cert.pem` overrides CLI connection settings. A locally generated hash identifies a local ZIP; it does not authenticate its publisher. GitHub release downloads require the published SHA-256 asset to match.

## HTTP API

All endpoints except login and the fixed HTML login/management page require authentication. Login: `POST /api/login`, JSON `{"password":"…"}`. Response supplies a bearer `token` and browser CSRF token; it also sets a Secure/HttpOnly/SameSite=Strict cookie. API clients send `Authorization: Bearer TOKEN`. Tokens expire after one hour or password reset. Login permits five failed attempts per client in ten minutes, with an additional aggregate limit. Browser changes require `X-CSRF-Token` and same-origin requests. Inline CSS and JavaScript live in the single `index.html`, with content-security-policy hashes generated from that exact file. Never log passwords or tokens.

| Method | Endpoint | Input/result |
| --- | --- | --- |
| GET | `/api/session` | Browser CSRF token |
| POST | `/api/logout` | JSON `{}`; invalidate session |
| GET | `/api/status` | NPF state, active/history versions with matching backup availability/time, staged files, recovery copies, result |
| POST | `/api/stage/application` | Raw ZIP body with Content-Length; returns staged ID |
| POST | `/api/stage/database` | Raw full SQLite database; returns staged ID |
| POST | `/api/stage/configuration` | Raw UTF-8 platform.conf; returns staged ID |
| GET | `/api/download/application` | Installed NPF code, full consistent finance.db and current platform.conf ZIP |
| GET | `/api/download/database` | Full consistent SQLite snapshot |
| GET | `/api/download/configuration` | Actual runtime configuration, or 404 if absent |
| POST | `/api/action/start`, `/api/action/stop` | JSON `{}` |
| POST | `/api/action/restart` | JSON `{}`; controlled NPF restart, or start if stopped |
| POST | `/api/action/github` | JSON `{}`; stage latest stable release |
| POST | `/api/action/apply` | `{"id":"…"}`; full database snapshot required (`snapshot:false` rejected) |
| POST | `/api/action/rollback` | `{"id":"…","confirmed":true}`; always restores matching finance.db (`restore_database:false` rejected) |
| POST | `/api/action/replace-files` | `{"database":"optional staged ID","configuration":"optional staged ID","confirmed":true}`; select at least one, back up both files and restart/start NPF once |
| POST | `/api/action/replace-database` | `{"id":"…","confirmed":true}` |
| POST | `/api/action/replace-configuration` | `{"id":"…","confirmed":true,"restart":false}` |
| POST | `/api/action/delete` | `{"category":"staged or recovery","id":"…"}` |

Mutations are synchronous; clients should allow up to 15 minutes for dependency preparation. Another mutation returns busy. Errors use JSON `error` with a failing HTTP status. No CORS, generic file serving or directory listing. Request bodies require Content-Length; chunked upload is rejected. Defaults: 512 MiB upload, 1 MiB configuration, 2 GiB extracted ZIP, 20,000 ZIP entries, 64 KiB JSON, 16 simultaneous request workers, 30-second socket timeout and five-minute upload deadline. Settings may lower/raise upload/extraction and startup limits; restart manager after editing settings.

## State and recovery

The application root contains root-owned `manager/` code and service-owned `releases/ID/{app,venv,release.json,source.zip}`. Private data contains settings/auth, certificates, live database, canonical `platform.conf`, state/operation records, staged files, version-associated history, independent recovery copies and temporary exports. Configuration is copied beside the selected `app.py`; startup formatting is synchronized back. NPF's logs remain beside each release; manager-captured NPF output rotates at 1 MiB with three copies.

Windows uses a pywin32 service-aware Python entry point and a kill-on-close job for child containment. Controlled NPF shutdown uses a private stop signal file before a bounded forced-stop fallback. Windows runtime results remain pending manual testing.

Keep three verified installed versions (current plus two prior). Every backup includes a full finance.db snapshot, including configuration-replacement backups. There is no snapshot opt-out. Old snapshots prune with their versions only after successful update. Independent database/configuration/rollback recovery copies remain until deleted. Failed updates retain staged inputs. Updates applied while stopped await next startup verification before pruning.

NPF owns schema migration. This release recognizes the literal NPF version/schema metadata and multi-ledger schema contract (22+). It validates candidate databases by running the target release's own migration initialization against a disposable snapshot. Restoring a previous version always restores its matching finance.db backup; keeping the current database is not offered. The confirmation shows the backup date and offers a current full-package download. Current platform.conf remains unchanged. A full automatic backup is made first; versions without a matching database backup cannot be restored. Do not treat a credential-free NPF browser export as a complete installation recovery snapshot.

If startup fails during update/replacement/rollback, the manager attempts the specified restoration and records its outcome. If recovery cannot be completed, NPF stays stopped and `operation.json` plus recovery files are retained. Before manual recovery stop the manager service, preserve the entire data directory, inspect the operation record and private logs, and restore matching application/database/configuration state. Do not delete the operation record merely to force a restart. Never copy a live database directly.

## Uninstall

Run `sudo npf-manager uninstall` on Linux, or the installed `npf-manager.bat uninstall` from an Administrator terminal on Windows. The installers also accept `sudo sh installLinux.sh uninstall` and `installWindows.bat uninstall` (or `installWindows.ps1 uninstall` in an Administrator PowerShell). Append `--yes` for unattended deletion. These commands use the installed cleanup routine without downloading or installing prerequisites. `python installer.py uninstall` is supported too. Confirm the displayed application/data paths and permanent deletion; the default answer is No. Use `uninstall --yes` for unattended deletion.

Uninstall stops/removes the service and deletes managed NPF releases, manager code/runtime/launcher, finance.db, platform.conf, authentication, certificates, staged files, version history and all backups. Download any full package you want to keep first. Adopted original installations and unrelated files in a custom application directory are preserved. Shared OS prerequisites (such as system Python) remain installed. Linux removes the service account only when installation records show this manager created it. Windows also removes the installer bootstrap environment; an OS helper finishes deleting locked files after the CLI exits, retries temporary locks, and writes any failure to the displayed temporary log path. Uninstall is privileged and local only, does not need the manager password, and is never available over HTTP. Older installations must rerun the installer once to register trusted cleanup paths.

## Source layout

The application has six Python modules: `manager.py` for process/data operations, `server.py` for HTTPS/authentication, `cli.py` for commands, `installer.py` for installation and removal, `npf_bridge.py` for running NPF in its own environment, and `windows_service.py` for Windows Service integration. `index.html` contains the complete UI.

Development checks live only under `tests/`: one regression suite, one acceptance runner with named phases, and one browser/screenshot helper. They are not installed with the application. See [the remote testing guide](tests/README.md) for commands and fixture requirements.

## Verification

See [SPEC.md](SPEC.md) for acceptance requirements, [Linux results](docs/TEST-RESULTS.md) for recorded validation, and [Windows checklist](docs/WINDOWS-CHECKLIST.md) for pending manual testing. [Desktop screenshot](docs/screenshots/manager-26.11.png) and [mobile screenshot](docs/screenshots/manager-26.11-mobile.png) show the actual single-file UI with synthetic test state. Do not run tests on the local development computer. Run `python3 -m unittest discover -s tests -v` only inside disposable remote Debian/Fedora QEMU guests, with `NPF_TEST_ZIP` pointing at a public application-only release package. Runtime verification uses synthetic financial data only. Remote host/SSH details are deliberately excluded from this project documentation.

Runtime release numbers come from `VERSION`, including CLI output, the web page and service descriptions. When preparing a release, update that file and the release-specific documentation/OpenAPI metadata together.
