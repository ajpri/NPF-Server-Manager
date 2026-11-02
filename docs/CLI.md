# NPF Server Manager CLI — 26.11

This reference documents the implemented commands in `cli.py` and `installer.py`. See [the OpenAPI document](../openapi.json) for HTTPS request/response schemas and [README](../README.md) for the project overview. Windows commands are implemented; Windows runtime acceptance remains pending manual testing.

## Command entry points

Linux installation creates `/usr/local/bin/npf-manager`:

```sh
sudo npf-manager --help
sudo npf-manager status
```

Windows installation creates `npf-manager.bat` in the application root, by default `C:\Program Files\NPF`. In PowerShell:

```powershell
& 'C:\Program Files\NPF\npf-manager.bat' --help
& 'C:\Program Files\NPF\npf-manager.bat' status
```

Examples below use the Linux launcher. The subcommands and arguments are the same through the Windows launcher. Quote paths containing spaces. For a source checkout, `python3 cli.py ...` also works when its local configuration is selected correctly; source checkouts cannot perform registered installation removal directly.

Most commands call the running manager's HTTPS API and prompt for the **manager administrator password** without echo. This is separate from an NPF Owner/user password. Each CLI invocation logs in separately; it does not save a bearer token or password.

Installation, `reset-password` and `uninstall` require local root/Administrator privileges. Live API commands require authentication and access to their local settings/certificate files; use `sudo` on Linux when those private files are unreadable by the invoking account. Remote API access does not itself require root.

## Syntax and global options

```text
npf-manager [--data DIRECTORY] [--url HTTPS_URL] [--ca PEM_FILE] COMMAND [COMMAND_OPTIONS]
npf-manager --version
npf-manager --help
```

Put global options **before** the subcommand. Put subcommand flags after its name/arguments.

| Option | Meaning |
| --- | --- |
| `-h`, `--help` | Print global help and exit. Every subcommand also accepts these flags. |
| `--version` | Print the manager version, currently `26.11`, and exit. |
| `--data DIRECTORY` | Select local manager settings/data. Default comes from `/etc/npf-manager.json` on Linux or `%PROGRAMDATA%\NPF-Server-Manager.json` on Windows, falling back to `/var/lib/npf` or `%PROGRAMDATA%\NPF`. This is not an NPF data export destination. |
| `--url HTTPS_URL` | Override the API base URL. Default is `https://127.0.0.1:PORT`, using the selected settings' port or `5050`. Plain HTTP is rejected. |
| `--ca PEM_FILE` | Override the trusted certificate/CA PEM. Otherwise use the selected settings' certificate, or system trust when none is configured. Certificate identity must match the URL hostname/IP. There is no insecure TLS bypass option. |

```sh
sudo npf-manager --data '/var/lib/NPF Custom' status
sudo npf-manager --url https://finance.example.lan:5050 --ca /safe/manager-ca.pem status
```

The client reads `settings.json` from `--data` even when URL and CA are supplied. For a remote-only invocation without access to local private settings, select a readable, otherwise unused location and supply both overrides:

```sh
npf-manager --data "$HOME/.npf-remote" \
  --url https://finance.example.lan:5050 --ca "$HOME/manager-ca.pem" status
```

The CLI does not create that directory or write credentials there. If it already contains `settings.json`, those settings are read.

## Output, confirmation and automation

API commands print formatted JSON. Downloads print `{"saved":"DESTINATION"}` after saving the file. Password reset and uninstall print plain text; `serve` remains in the foreground rather than returning a JSON result. Treat `result` text as an explanation, not a machine-readable status code. In particular, successful deletion currently returns the previous operation's result text; use command success and `status` to confirm removal.

Typical exit statuses are:

| Exit | Meaning |
| --- | --- |
| `0` | Success, help/version, or a declined interactive confirmation. Cancellation does **not** produce a nonzero failure status. |
| `1` | Authentication, validation, permission, filesystem or other operation failure. Handled CLI errors print `Error: ...` on stderr. |
| `2` | Argument/usage error from the command parser. |

Installer wrappers can propagate other child-process exit statuses. Unexpected exceptions may include a traceback; there is no larger stable error-code taxonomy.

`replace`, `rollback` and `delete` ask `Continue? (y/N)` unless `--yes` is supplied. Uninstall has its own permanent-deletion prompt, also defaulting to No. Enter selects No; only `y` confirms. Noninteractive confirmation requires `--yes`. Configuration replacement additionally requires an explicit `--restart` or `--no-restart` when there is no interactive terminal.

`--yes` answers the destructive-action confirmation; it does **not** supply the API password or answer the configuration restart question. There is no CLI password argument, environment-variable password option or stored-login command. For fully scripted live operations, use an HTTPS API client with protected credential input and the [OpenAPI contract](../openapi.json). The installers separately support a protected `--admin-password-file` for initial setup.

Live requests have a 900-second timeout. Operations such as dependency preparation may take minutes. If a client disconnects or times out, query `status` before repeating a mutation. Conflicting operations report busy rather than running concurrently.

## Status and process controls

### `status`

```sh
sudo npf-manager status
```

Returns `manager_version`, NPF state (`starting`, `running`, `stopped`, `failed` or `busy`), active release ID and version metadata, last result, pending configuration flag, crash retry count, prior versions, staged inputs and independent recovery copies. `operation` identifies a manager action in progress when present. Upload/download locks do not necessarily appear as `busy` in status.

History entries include `snapshot` and `snapshot_time`; times are Unix seconds, possibly fractional. The active NPF application version (`version.applicationVersion`) is independent of the manager version.

### `start`, `stop`, `restart`

```sh
sudo npf-manager start
sudo npf-manager stop
sudo npf-manager restart
```

- `start` starts NPF and resets its crash retry limit/failed latch. An already running NPF remains running. It also verifies a deferred application update.
- `stop` stops NPF while keeping the manager/API available. Intentional Stop survives ordinary manager restarts; explicit Start, Restart, upload-and-restart or an OS reboot permits launch again.
- `restart` performs a controlled NPF stop/start, or starts NPF if it was stopped. It resets the retry limit and can verify a deferred update. It does not restart the manager service.

## Application updates

### `github`

```sh
sudo npf-manager github
```

Downloads the latest stable published application-only NPF ZIP and verifies its published SHA-256 asset. Drafts/prereleases and missing/mismatched checksums are rejected. Returns a staged item with an `id`. It does **not** install or restart NPF. Continue with `apply ID`.

### `stage KIND FILE`

```sh
sudo npf-manager stage application /safe/NPF.zip
sudo npf-manager stage database /safe/finance.db
sudo npf-manager stage configuration /safe/platform.conf
```

`KIND` is `application`, `database` or `configuration`. Validates and retains a candidate without changing live files. The returned JSON includes its opaque 32-character lowercase hexadecimal ID, kind, Unix timestamp, SHA-256 and origin. Application candidates additionally include application/schema versions; database candidates include `schema` and `owner`.

Default limits are 512 MiB for application/database inputs and 1 MiB for configuration. Application/database limits are configurable. ZIP validation rejects unsafe paths/types, duplicates and oversized extraction; the extracted default limit is 2 GiB and 20,000 entries. Database validation checks the full NPF structure and exercises the active release's migration on a disposable copy; unsupported/newer schemas are rejected. Staging can report `owner: false`; NPF can start without an Owner, and manager status reports `owner_setup_required: true` until setup is completed.

A local upload hash identifies its bytes; it does not authenticate the publisher. Staged inputs persist until successfully applied or explicitly deleted, including after a failed application. IDs are visible through `status`.

### `apply ID`

```sh
sudo npf-manager apply aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

Installs the selected staged application ZIP, prepares isolated dependencies, and backs up the outgoing version with the **entire finance.db** and configuration. There is no snapshot opt-out. Current database/configuration carry forward; copies bundled in the ZIP are ignored.

If NPF was running, it is stopped/restarted and checked; failed startup attempts restoration. If it was stopped, it stays stopped, with startup verification deferred until `start` or `restart`. Further restore/replacement/deletion/update application is blocked until verification; staging and downloads remain available. Verified history retains the current version plus two previous versions.

Applying a specific staged ID is the explicit action; this command has no extra `--yes` prompt/flag.

## Downloads and restore

### `download KIND DESTINATION`

```sh
sudo npf-manager download application /safe/NPF-full-package.zip
sudo npf-manager download database /safe/current-finance.db
sudo npf-manager download configuration /safe/current-platform.conf
```

| Kind | Download contents |
| --- | --- |
| `application` | Current installed NPF application files, a consistent copy of the **entire actual finance.db**, and current platform.conf if present. Excludes virtual environments, logs, caches, nested backups, manager authentication and TLS private keys. |
| `database` | Verified consistent full-installation SQLite snapshot, including every ledger, financial history, users, password hashes and integration secrets. This is different from NPF's selected-ledger/credential-free browser export. |
| `configuration` | Actual current platform.conf, including its comments/formatting and any SMTP credentials. Absence is an error; a sample file is not substituted. |

Downloads do not restart NPF. Destination parent directories must already exist. The CLI refuses to overwrite an existing destination and saves with private permissions (`0600` on POSIX; explicit restricted ACL on Windows). If a transfer raises an error while writing, it removes the incomplete file. Treat exported databases/packages/configuration as private installation data.

### `rollback ID [--yes]`

```sh
sudo npf-manager download application /safe/before-restore.zip
sudo npf-manager status
sudo npf-manager rollback bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb --yes
```

Choose `ID` from `history[].id`; a version label such as `26.10.30` is **not** an ID. Despite the command name, restore always installs that version **and its matching database backup**. Financial data and credentials added since the historical snapshot are replaced. Keeping the current database is not supported, and a missing/invalid matching snapshot is rejected before stopping NPF. There is no `--restore-database` choice.

Downloading the current full package first is optional. Regardless of download, the manager automatically captures the current application/database/configuration before restore. Current platform.conf stays unchanged. The prior running/stopped state is preserved; startup failure attempts to restore the captured pre-restore state. Recovery copies remain until explicitly deleted.

## Replace files

### `replace KIND ID [--yes] [--restart | --no-restart]`

`KIND` is `database` or `configuration`, and `ID` must refer to a staged input of that kind. Every replacement backs up the entire current finance.db and configuration (or records its absence). Failure to make the required backup prevents replacement.

```sh
sudo npf-manager stage configuration /safe/new-platform.conf
sudo npf-manager replace configuration cccccccccccccccccccccccccccccccc --yes --no-restart

sudo npf-manager stage database /safe/replacement-finance.db
sudo npf-manager replace database bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb --yes
```

| Input/choice | Running NPF | Stopped NPF |
| --- | --- | --- |
| Configuration, `--no-restart` | Save without restarting; report pending until next start/restart. | Save and remain stopped. |
| Configuration, `--restart` | Controlled restart and readiness check. | Start and check NPF. |
| Database, default or `--no-restart` | Stop/replace/restart; replacing a live database requires stopping its writer. | Replace and remain stopped. |
| Database, `--restart` | Stop/replace/restart and check. | Replace and start/check NPF. |

For configuration, omitting both restart flags prompts `Restart NPF now? (y/N)`; Enter selects No. A noninteractive configuration replacement must supply one flag. Database replacement does not ask this question and defaults to preserving its prior running/stopped state. The two flags are mutually exclusive.

NPF owns database migration. A replacement database must be a full supported installation database; selected-ledger exports and databases newer than the active release are unsuitable. On failed elected startup, the previous state is restored where possible. A configuration-only failure without restart restores configuration while preserving the running process and its concurrent database writes.

The web UI/API can replace either or both files with one backup and one restart via `/api/action/replace-files`. There is **no combined-upload CLI subcommand** in 26.11; separate CLI `replace` commands are separate operations.

## Retained-file deletion

### `delete CATEGORY ID [--yes]`

```sh
sudo npf-manager delete staged aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --yes
sudo npf-manager delete recovery dddddddddddddddddddddddddddddddd --yes
```

`CATEGORY` is `staged` or `recovery`. Obtain IDs from `status`. This permanently removes that candidate/recovery copy; it cannot delete history entries directly. Deleting recovery material reduces what can be recovered later. Do not delete files needed by an unresolved or deferred update; the manager rejects such deletion.

Success currently returns the previous `result` message, so refresh `status` and check the appropriate list. Missing filesystem entries can currently surface as a generic operation error rather than a dedicated not-found response.

## Local administration

### `reset-password`

```sh
sudo npf-manager reset-password
```

Requires local root/Administrator privileges, prompts twice for a new 8–128-character manager password and does not require the old password or a running API. Existing sessions/tokens become invalid. It does not change NPF Owner/user passwords, database contents or certificates.

### `uninstall [--yes]`

```sh
sudo npf-manager uninstall
sudo sh installLinux.sh uninstall
```

Both call the same privileged local cleanup routine. The Windows equivalents are the installed `npf-manager.bat uninstall`, `installWindows.bat uninstall` and `installWindows.ps1 uninstall` from an Administrator terminal. The source installer also supports `python3 installer.py uninstall`.

The default confirmation answer is No. Appending `--yes` explicitly approves unattended permanent deletion of **all managed data**, including finance.db, configuration, passwords, certificates, stages, history and backups, as well as the service, manager/NPF code, launcher and private runtime. It does not need the manager password or expose an HTTP uninstall endpoint. Download a full package beforehand if you want a copy.

Original adopted sources, unrelated files and shared OS prerequisites remain. Linux removes the service account only when recorded as created by this installation. Windows also removes the dedicated installer runtime, using an OS helper after CLI exit to remove locked files; cleanup failures are recorded at the displayed temporary log path. Older installed versions may need one installer rerun to register trusted cleanup paths.

The installer `uninstall` branches run before prerequisite bootstrapping. Confirmation and failures propagate through their exit status. The selected registered code/data paths are checked before deletion; an arbitrary source checkout or alternate `--data` cannot silently remove a different installation.

### `serve`

```text
npf-manager [--data DIRECTORY] serve
```

Runs the manager HTTPS server/supervisor in the foreground. This is primarily the Linux service entry point, not an installation command. It requires a configured data directory and its permissions; an installation lock prevents a second manager from running against the same data. The installed service normally invokes it automatically. Service shutdown also stops the managed NPF child. The Windows Service uses its dedicated SCM host instead.

## Installer command reference

Use the platform wrapper to provision prerequisites and register/update services:

```sh
sudo sh installLinux.sh [install|update] [OPTIONS]
sudo sh installLinux.sh uninstall [--yes]
```

Windows: `installWindows.bat [install|update] [OPTIONS]` or `installWindows.ps1 ...` from an Administrator terminal. `python3 installer.py ...` accepts the same install/update/uninstall arguments but assumes prerequisites are already available. The default action is `install`; rerunning it updates the manager and preserves an existing installation.

| Option | Purpose |
| --- | --- |
| `-h`, `--help` | Show installer parser help. The Python entry point avoids wrapper prerequisite provisioning when merely inspecting help. |
| `--application-root DIRECTORY` | Initial application/code location; defaults `/opt/npf` or `%ProgramFiles%\NPF`. Must be a separate tree from persistent data. Existing installations reject a different application root. |
| `--data DIRECTORY` | Persistent settings/database/backups location, discovered from the locator by default, then `/var/lib/npf` or `%PROGRAMDATA%\NPF`. Use the existing selected location on reruns. |
| `--package ZIP` | Local initial NPF package when not downloading the stable GitHub release. Does not update NPF on a completed-installation rerun. |
| `--adopt DIRECTORY` | Copy an existing initialized NPF installation into the managed layout. Preserve original files. |
| `--source-database FILE` | Actual source database for adoption; defaults to `ADOPT_DIRECTORY/finance.db`. |
| `--source-stopped` | Confirm the original adopted NPF/launcher is stopped for automation. A process using the configured NPF port still causes refusal. |
| `--admin-password-file FILE` | Protected file containing the initial manager password. Its trailing line ending is stripped. No manager password is taken as a command-line value. Delete this input file after setup. |
| `--port NUMBER` | Initial HTTPS management port; default prompt offers `5050`. |
| `--npf-port NUMBER` | Initial managed NPF port; default `5000`. Ports must be distinct and between 1 and 65535. |
| `--bind ADDRESS` | Initial manager listener address; default `0.0.0.0`. |
| `--certificate FILE` | Supplied TLS certificate PEM; requires matching `--private-key`. |
| `--private-key FILE` | Matching TLS private-key PEM. Keys requiring an interactive passphrase are unsuitable for unattended service startup. |
| `--certificate-names NAMES` | Comma-separated names/IPs for generated self-signed certificates. Include the actual client-facing server names/IPs. |
| `--renew-certificate` | Generate/replace the installed certificate through the installer; supply names or a new matching certificate/key pair as appropriate. |
| `--yes` | Only for `uninstall`; bypass the permanent-deletion confirmation. Not a general unattended-install flag. |

On completed-installation reruns, retain the existing application/data paths, ports and bind settings; flags intended for first installation do not migrate these settings. Certificate replacement/renewal is supported on rerun. NPF upgrades are manual `stage`/`apply` operations, independent of manager updates. Full offline prerequisite installation is outside 26.11.

Fresh NPF setup offers interactive terminal Owner creation using NPF's own protected prompts. Skipping it allows installation and both services to start; the manager UI warns that an Owner is still required for NPF sign-in. Rerun the same installer with the same data path to complete Owner setup later. A manager password file configures only the manager password. Adoption can use an already initialized database after its original writer is stopped. The installer targets x64 Debian/Fedora systemd families and Windows 11; ARM is not supported in this release.

## Common failures

| Symptom | Next step |
| --- | --- |
| TLS verification fails | Use the correct trusted public certificate/CA and a URL name/IP covered by that certificate. Do not disable certificate verification. |
| Permission denied reading settings | Use local elevation or select a readable remote-only data location with explicit `--url` and `--ca`. |
| Sign-in required / invalid password | Use the manager password, not an NPF user password. Sessions expire after one hour and are invalid after manager restart/reset/logout. |
| Busy | Let the current operation finish, then read `status` before retrying. |
| Previous update awaits verification | Use `start` or `restart` to verify/recover the deferred update before further mutations. |
| Unresolved recovery operation | Preserve private recovery files/logs and inspect the recorded operation. Do not delete the journal just to force a start. |
| Wrong identifier/type | Copy the opaque ID from the relevant staged/history/recovery entry, not a version label or filename. |
| Noninteractive confirmation/restart error | Supply `--yes` and, for configuration replacement, an explicit restart flag. Authentication still needs protected credential input. |
| Existing download destination | Choose a new filename; downloads intentionally do not overwrite existing files. |

On Windows, `installWindows.bat` pauses before closing and preserves its exit status. Set `NPF_INSTALLER_NO_PAUSE=1` to suppress the pause in automation, or invoke PowerShell directly. Bootstrap runtime checks attempt to recreate a broken temporary installer environment; an unusable base Python installation must be repaired first.
