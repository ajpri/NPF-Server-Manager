# NPF Server Manager

Version: **26.11**.

Status: Implementation specification for version 26.11. Linux validation results are recorded in [docs/TEST-RESULTS.md](docs/TEST-RESULTS.md). Windows runtime validation remains pending the user's manual testing.

## 1. Purpose and scope

NPF Server Manager installs, updates, and operates [No Part Finance (NPF)](https://github.com/ajpri/No-Part-Finance) on the local computer. It manages one NPF installation per computer through a background service, a basic LAN web interface, a local CLI, and an HTTP API.

The first release targets x86-64 (x64):

- Debian and derivatives with systemd.
- Fedora and related distributions with systemd.
- Windows 11.

ARM support is deferred. Linux is the primary acceptance target. Windows support must be implemented, with testing performed manually by the user; Linux results do not establish Windows compatibility. Record exact supported and tested OS releases in implementation documentation rather than claiming every derivative is verified.

Out of scope: multiple managed installations, remote installation onto other computers, scheduled/automatic NPF updates, full offline prerequisite installation, and a styled frontend.

## 2. Installation and storage

### Entry points and defaults

Provide platform entry points named `installLinux` and `installWindows`, with appropriate platform extensions. They must be easy to run and automatically install required prerequisites, including Python when needed, virtual-environment support, Python dependencies, and service/certificate tooling. Use platform-appropriate prerequisite installation; do not replace a working system Python unnecessarily.

| Platform | Default application location | Default persistent data/backup location |
| --- | --- | --- |
| Linux | `/opt/npf` | `/var/lib/npf` |
| Windows 11 | `C:\Program Files\NPF` | `C:\ProgramData\NPF` |

Offer these defaults and allow custom locations. Persist paths for services, CLI operations, installer reruns, updates, and recovery. Separate manager settings and secrets from NPF packages, databases, configuration, and version history.

Use a dedicated service identity with access to the required managed files. Installation and local administrator password reset require root/elevation or equivalent explicitly granted local administrative permission. Do not give unauthenticated callers administrative capabilities.

The actual database path is authoritative, including any `FINANCE_DATABASE_PATH` override. NPF expects runtime `platform.conf` beside `app.py`; account for that when designing release directories and persistent storage. Never substitute `platform.conf.sample` for runtime configuration.

### Fresh installation

1. Detect whether an existing managed installation is present before creating a new one.
2. Establish selected paths, prerequisites, manager settings, administrator password, management port, and certificate mode.
3. Attempt to download the latest stable published NPF GitHub release, excluding drafts and prereleases. Allow a local NPF ZIP if GitHub is unavailable.
4. Validate and prepare NPF and its isolated Python dependencies.
5. Offer NPF's initial Owner setup interactively in the terminal using NPF's own supported credential rules. Allow deferral and start both the manager and NPF without an Owner. The authenticated management page must clearly show that Owner setup remains required before NPF sign-in. Rerunning the installer must offer deferred Owner setup even for a completed installation.
6. Stop the foreground setup instance cleanly before handing NPF over to the manager service.
7. Register and start the manager service and report NPF and management addresses and the installation outcome.

A fresh installation must not silently seed personal data or credentials from a bundled `finance.db` or runtime `platform.conf`. Use explicit adoption or replacement operations for existing state. The safe default sample may be included with application files.

NPF's current setup starts its HTTP server before prompting. Do not run foreground and managed instances concurrently. Identify completion using authoritative NPF setup state rather than database-file existence. Existing initialized installations skip Owner setup. The manager password is separate from NPF Owner credentials.

If prerequisites cannot be downloaded, stop with a clear explanation and preserve completed setup for a safe rerun. Do not report failed prerequisite/service installation as complete. Deferred Owner creation is an explicit supported outcome, with services enabled and a management warning. Full offline prerequisite provisioning is not required.

### Adopting an existing installation

Offer adoption by selecting an existing application folder and its actual database path. Verify the installation, stop its NPF instance, back it up, and copy it into the selected managed layout. Preserve its complete database and runtime configuration. Keep original files as a recovery copy.

Do not proceed if the original instance cannot be stopped safely. Identify and disable conflicting launch mechanisms explicitly; never allow both installations to write the same database. Recreate dependencies for the managed environment rather than assuming a copied virtual environment is portable. Do not initialize new Owner credentials for an already initialized database.

### Updating the manager

Rerunning the platform installer on a managed installation updates NPF Server Manager itself. Preserve paths, settings, administrator password, certificates/private keys, and NPF data. Preserve any intentional NPF Stop state during the update. Do not implicitly apply an NPF update.

Prepare the manager update before replacing running files. Preserve the previous manager and settings for recovery if its service cannot start. Installer interruptions must leave enough state to resume or recover without destroying NPF data.

## 3. Service and process behavior

Install one manager service: systemd on Linux and a Windows Service on Windows. The manager supervises NPF as a child process. Windows requires a service-aware host; registering an ordinary Python console command alone is insufficient.

- At boot, start the manager and NPF automatically.
- The manager remains available when NPF is stopped or fails.
- Intentional Stop suppresses NPF restarts until explicit Start, Restart, upload-and-restart or the next reboot. An ordinary manager restart must not clear this state.
- Start must not create a second NPF instance. Stop must stop the managed process and its descendants cleanly.
- Stop NPF when stopping the manager service; do not leave an orphaned server.
- For unexpected NPF exits, allow three automatic restart attempts within five minutes, waiting 10 seconds before each attempt. If another exit occurs after the third attempt before five continuous minutes of stable running, leave NPF failed and stopped. Do not resume retries merely because attempts age out.
- Explicit Start or five continuous minutes of stable operation resets the retry limit. Preserve the retry count and failed latch across ordinary manager restarts; reboot clears them when starting the installation again.
- Planned stops for updates, rollback, or file replacement do not count as crashes.

Display state, active NPF version, last operation/result, and whether configuration changes await restart. Distinguish starting, running, stopped, busy, and failed states. Keep errors understandable and exclude secrets.

Operations are serialized across web UI, CLI, and API. Conflicting requests must report busy rather than modifying the installation concurrently. Preserve an operation record before changing active state so an interrupted operation can be identified and recovered before NPF is launched again.

## 4. Management HTTP server and web UI

Use Python's built-in HTTP server with custom handlers for management routes. Do not introduce Flask as a manager dependency. This choice does not change NPF's own dependencies.

Serve the UI and API over HTTPS on port 5050 by default, configurable during installation. Make it accessible from other LAN computers, with a configurable listening address. Keep the NPF application port separate. Do not enable an automatic plaintext fallback or generic directory/file serving.

Python documents limitations of `http.server`; this choice was explicitly retained. Implement authentication, request limits, upload handling, routing, and browser protections rather than assuming the standard library supplies them. See [Python documentation](https://docs.python.org/3.12/library/http.server.html).

### Certificates

Offer either a supplied certificate and matching private key or a generated self-signed certificate. Persist certificate settings and protect private keys. Do not regenerate certificates at every start or include keys in NPF transfers or version snapshots.

Allow the administrator to supply the names/IP addresses clients use, include them in generated certificate identities, and explain certificate trust setup for browsers and API clients. Do not require clients to disable verification. Document certificate format, expiry, replacement, and renewal procedures. Reject invalid certificate/key combinations clearly.

### Administrator authentication

Use one manager administrator password, 8–128 characters, set during installation. Store a salted password hash. Use hidden input in local setup/reset flows and keep passwords out of command-line arguments and logs.

Provide a privileged local CLI password reset that does not require the old password. Reset invalidates existing manager sessions and credentials derived from the old password. NPF database/configuration replacement does not alter manager credentials.

Require authentication for status, process control, uploads, downloads, updates, rollback, and retained-file management. Browser authentication uses protected sessions, logout, expiry, login throttling, and cross-site request protection for changes. The API uses the same administrator credential over HTTPS; document its authentication contract. These mechanisms must work with the built-in HTTP server.

### Appearance and functions

Use white background, ordinary document flow, standard text, native links, buttons, file inputs, form fields, and simple tables. Minimal CSS is allowed for spacing and narrow-screen usability. Use vanilla JavaScript only where needed. Keep semantic labels and keyboard operation. Keep HTML, CSS, and JavaScript together in one `index.html` file.

No frontend frameworks, component libraries, dark mode, animations, decorative graphics, gradients, shadows, or elaborate dashboard layouts. This is the final product appearance, not a placeholder.

Show status and Start/Stop/Restart, followed by the current application version and up to two previous versions with rollback controls. Put application ZIP upload and GitHub update controls alongside versions. Follow with a Downloads section for the full application package, platform.conf and finance.db, then an Uploads section. Put optional platform.conf and finance.db inputs on separate rows in one form with a single “Upload and restart No Part Finance” button. Choose either file or both. Validate all selected inputs before any live change, capture one full backup, apply them together and restart/start NPF once; restore both previous files if startup fails. This button confirms replacement and restart/start NPF; CLI configuration prompts still default to No. Keep the page plain and compact. Do not show queues of staged files or recovery copies; advanced retained-file management remains available through CLI/API. Uploads validate internally and immediately request confirmation before applying; cancellation discards that candidate without changing live files.

## 5. NPF application updates

Updates are manual only. An administrator retrieves the latest stable published GitHub release or uploads a ZIP through web UI, local CLI, or API. Stage the package; uploading/downloading alone must not replace files or restart NPF. Applying it requires explicit confirmation. In the web UI this follows validation within the same upload/update flow, without a separate staging screen. CLI/API retain separate stage and apply operations.

Validate package structure and required NPF files before use. Verify GitHub assets against the release's published SHA-256 checksum and report missing/mismatched verification information rather than claiming verification. For local packages, compute an identifier/hash but do not describe a locally computed hash as proof of publisher authenticity.

Reject unsafe ZIP paths, links that escape managed storage, malformed archives, and archives exceeding configured compressed/extracted limits. Never execute an upload merely to discover its metadata. Ignore bundled `finance.db` and runtime `platform.conf` during application updates, preserving the installation's current files, even when the current configuration is absent. Their presence alone does not cause rejection.

### Apply workflow

1. Validate/stage files and prepare dependencies without disturbing the active installation.
2. Record the previous running/stopped state and suspend crash-restart handling.
3. Stop NPF before preserving outgoing state and switching application files.
4. Back up outgoing application files and runtime configuration, recording configuration absence when applicable.
5. Always include a verified full-installation database snapshot; no interface may opt out. Associate it with the outgoing version before the incoming code can migrate the database. Abort on required backup failure.
6. Switch application files and carry forward the actual database and current runtime configuration. NPF owns supported schema migrations; the manager must not invent database downgrades.
7. Check startup if NPF was previously running. If it was intentionally stopped, preserve that state and report startup as not yet verified; do not prune previous recoverable history until startup succeeds.
8. Report success/failure and prune history only after successful startup verification.

Use isolated dependencies for retained application versions or otherwise ensure rollback restores matching dependencies as well as code.

### Failed-update recovery

If incoming startup fails, stop it, restore the outgoing application/dependencies and configuration, and restore its mandatory pre-update database snapshot. Try starting the recovered installation and report both the update failure and recovery outcome.

Never assume replacing code reverses a schema migration. If legacy recovery materials lack a database snapshot, restore old code only when database compatibility is established; otherwise leave NPF stopped with recovery guidance. If recovery fails, retain recovery materials, leave NPF stopped, and keep the manager available. Do not discard failed state before recovery is verified.

## 6. History and manual rollback

Keep three installed versions total after verified successful updates: current plus two previous versions. Track version identity, source package, installation time, database schema, and associated snapshot availability/time. Prune only after an application update succeeds; delete snapshots associated with a pruned version. Failed updates do not prune previous history. An update applied while NPF is stopped may temporarily retain extra outgoing history until its first startup verification succeeds.

Staged uploads remain until successfully applied or explicitly deleted. Failed application does not consume them. Replacement and manual-rollback recovery copies are separate from version history and remain until explicitly deleted. Never delete files needed by an in-progress operation.

Manual rollback always restores the selected application version and its matching database snapshot. Remove the keep-current-database choice from UI/CLI/API. Show a “Restore this version…” button and a confirmation displaying application version and database-backup date, an optional “Download current full application package” link (including the entire current finance.db), “Confirm restore” and “Cancel”. Explain that restoration replaces financial and credential state and loses subsequent changes. Keep current runtime platform.conf unchanged. Disable restore when the matching database backup is absent.

Refuse restore without a valid matching database backup, even if the current database appears compatible. Before rollback, stop NPF and preserve current application/dependencies, full database, and configuration. Abort on backup failure. Restore the prior running/stopped state; if stopped, report startup as unverified.

If rollback startup fails, stop the failed instance, restore the captured pre-rollback state, and try starting it again. Report both outcomes; on recovery failure leave NPF stopped and retain recovery materials. The manager remains available throughout.

### Full application package download

Provide an authenticated full application-package ZIP through web UI, CLI and API. Include the currently installed NPF application files, a consistent SQLite backup of the actual full finance.db (all ledgers, financial history, password hashes and integration secrets), and current platform.conf when present. Never substitute NPF's selected-ledger/credential-free browser export or an old bundled database. Exclude virtual environments, logs, caches, other backup packages, manager passwords and TLS private keys. Build the complete archive before streaming it with Content-Length; remove temporary exports on completion or disconnect. Downloads do not restart NPF.

## 7. Full database transfer

Provide equivalent download/export, stage/upload, and explicit replacement through web UI, local CLI, and API. Use the actual configured database path.

Full database means all ledgers, Users, memberships, financial history, password hashes, integration secrets, and other installation state. It is not NPF's selected-ledger, credential-free browser export. These are private administrative downloads.

After a managed-process crash, let SQLite recover its own hot journal with the writer stopped before attempting read-only integrity checks; this is separate from NPF schema migration. Create consistent standalone exports with SQLite's backup API, including committed data otherwise residing in sidecars. Do not copy a live writable database file directly. Keep temporary exports outside public directories and remove them after transfer or failed-transfer cleanup.

For replacement:

1. Stage the upload without changing live data.
2. Validate SQLite integrity, expected NPF structure, and compatibility with installed NPF. Permit only supported older-schema migration paths; reject unsupported newer schemas.
3. Require explicit Replace database confirmation, explaining the installation-wide data/credential replacement.
4. Stop NPF and create/verify a full recovery snapshot of the current database. All backups, including application-update and configuration-replacement backups, must include the full finance.db.
5. Apply with correct permissions and no stale SQLite sidecars. Restart/check if previously running or restart was explicitly requested; otherwise keep stopped.
6. On startup failure, stop NPF, restore the previous database, and attempt to resume the previously running installation. Report both outcomes. On recovery failure leave stopped and retain materials.

Invalid uploads and failed pre-replacement backups leave the current installation unchanged. Retain recovery snapshots until explicitly deleted; retain failed staged uploads for inspection or retry.

## 8. Configuration transfer

Provide download, stage/upload, and explicit replacement of runtime `platform.conf` through web UI, local CLI, and API. Download the actual file and preserve its contents; if absent, report absence rather than supplying the sample. Treat it as private because it may contain SMTP credentials.

Validate the staged configuration against installed NPF before replacement, preserving comments and supported formatting. Preserve the previous file or record its absence; abort if preserving recovery state fails. Invalid uploads do not alter current configuration.

When applying, ask **Restart NPF now? (y/N)**. CLI Enter selects No; the web UI explicitly requests restart through its upload-and-restart buttons.

- Yes: perform a controlled restart and startup check, or start NPF if it was stopped.
- No: save without restarting; report changes pending until next start/restart.
- Already stopped with No: keep NPF stopped; apply changes on next start. An explicit Yes or web upload-and-restart starts NPF.

Provide mutually exclusive CLI `--restart` and `--no-restart` flags to bypass the question with an explicit answer. Noninteractive replacement must supply a choice. The API accepts an explicit restart boolean. These choices do not bypass validation or replacement safeguards. Downloads and staging never restart NPF.

If the elected restart fails, stop the failed instance, restore previous configuration or its absence, and try startup again. Report replacement failure and recovery outcome; leave stopped if recovery fails. Selecting No does not claim a successful runtime check. Keep recovery copies until explicitly deleted.

### Local uninstall

Provide `npf-manager uninstall` on Linux and Windows, requiring local root/Administrator privileges. Also support `installLinux.sh uninstall`, `installWindows.bat uninstall`, `installWindows.ps1 uninstall`, and `installer.py uninstall`, forwarding confirmation and --yes to the installed cleanup routine before prerequisite bootstrapping. Resolve the installed runtime/code from privileged installation registration rather than service-writable settings. Display the trusted registered application/data paths and ask confirmation with default No. Noninteractive use requires `--yes`. Remove the service and launcher, installed manager/NPF code and private runtime, and all managed data including finance.db, platform.conf, credentials, certificates, stages, history and backups. Uninstall is destructive by design; provide no HTTP uninstall endpoint. Keep original adopted sources, unrelated files and shared OS prerequisites. Remove a Linux service account only when recorded as created by this installation. On Windows, remove the dedicated installer runtime and finish locked-file cleanup after the CLI exits. Use a root/Administrator-owned installation manifest, reject unsafe/system paths, and refuse deletion if another manager still holds the installation lock. Source checkouts cannot be uninstalled through this command; older installed copies must first rerun the installer to register cleanup paths.

### Source organization

Keep six application Python modules: manager.py, server.py, cli.py, installer.py, npf_bridge.py and windows_service.py. Installation and uninstallation share installer.py; NPF subprocess and Windows Service entry points stay separate. All development checks live under tests/: the regression suite, one phased remote acceptance runner and one browser/screenshot helper. Do not ship test helpers with the installed application. Single-file CLI/API replacements and combined web uploads share the same validation, full-backup and recovery implementation, preserving their explicit restart choices.

## 9. Shared implementation contracts

Web UI, CLI, and API must call the same operation logic so validation, staging, backups, locking, and recovery cannot be bypassed by a different interface. Document exact command/endpoint names, parameters, authentication, success/failure responses, and noninteractive behavior with implementation.

Set and document bounded upload sizes, extraction sizes, request timeouts, temporary-export cleanup, and available-disk checks. Reject oversized or incomplete inputs before active state changes. Do not remove user-retained recovery files automatically to make space.

Define startup success using the supervised process and an NPF readiness response, not merely process creation or an arbitrary open port. Use bounded startup/shutdown deadlines. Record schema compatibility using a supported upstream contract or explicit verified version/schema mapping; unknown compatibility must not be treated as safe. Validation must not migrate or alter the live database.

Operations interrupted by manager failure/reboot must be detected from durable state and recovered before normal NPF startup. Suppress crash retries during recovery. Report actual outcomes, retaining materials if automatic recovery cannot finish.

Implementation details may use straightforward defaults without reopening agreed product scope. Before claiming completion, document the selected Python/runtime baseline, supported OS release matrix, prerequisite bootstrap, Windows service host, storage subdirectories, authentication/session settings, certificate lifecycle, operation limits, startup checks, and recovery checkpoints. Verify upstream integration points before depending on them; report unsupported cases explicitly.

## 10. Acceptance and testing

### Required environment and restrictions

Linux acceptance testing must run on a separate Linux computer supplied by the user, reached through SSH, using virtual machines created with portable QEMU on that remote computer. Connection and host information are supplied separately and must not be stored in this specification.

**Do not run tests locally.** Do not install services/prerequisites, launch NPF or the manager, create test databases, or run QEMU/test VMs on the local development computer. Local document/source editing and static review are allowed. Run automated tests, integration checks, service tests, and runtime verification inside the remote test VMs. The SSH host is the VM host, not an installation test target.

Do not begin remote provisioning before the host/access information and applicable host constraints are supplied. Keep VM disks, portable QEMU, downloaded images, and test artifacts within the agreed remote location. Do not replace host packages or change host services/network/firewall as part of guest tests. Record the QEMU invocation, guest images/releases, network access method, and acceleration available. Keep the remote management interface reachable from a separate client for LAN-equivalent verification; do not publicly expose it.

Use disposable x64 guests representing both Debian and Fedora families. Additional derivative compatibility must be reported separately. Use synthetic financial data, fake credentials/SMTP, and controlled package fixtures. Never use or modify the user's real NPF database, configuration, or credentials. Keep enough evidence to distinguish real-release verification from injected failure fixtures.

Windows 11 testing is performed manually by the user. Supply a corresponding Windows checklist and record results as pending until the user reports them. Do not claim Windows has passed based on code review or Linux results.

### Linux acceptance scenarios

Run applicable scenarios on both Debian-family and Fedora-family guests. Each result must identify guest OS/release, build/revision, input fixture, expected behavior, actual result, and relevant sanitized logs.

| ID | Scenario | Required result |
| --- | --- | --- |
| L01 | Fresh install with default paths and missing prerequisites | Prerequisites provisioned; separate manager password and NPF Owner setup complete; one enabled systemd manager service starts NPF; HTTPS status reachable from another client. |
| L02 | Custom paths/port and rerun | Selected values persist; no duplicate service, NPF process, Owner, or lost data. Test paths containing spaces. |
| L03 | GitHub/download failure | Local ZIP fallback works; unavailable prerequisites stop clearly; rerun resumes completed setup without claiming partial success. |
| L04 | Existing-install adoption | Source stopped/backed up; all ledgers, credentials, configuration, and overridden database location preserved in managed layout; original files remain; no duplicate writer. |
| L05 | Boot and process control | Reboot starts manager/NPF; intentional Stop leaves manager available; manager restart preserves Stop; explicit Start or reboot permits launch; manager stop leaves no orphan. |
| L06 | Crash recovery | Verify 10-second delays, three-attempt limit, failed state after exhaustion, explicit-Start reset, stable-five-minute reset, and no retries during planned stops. |
| L07 | HTTPS and administrator access | Supplied/self-signed modes work with trusted clients; cert/key mismatch fails clearly; password lengths 7/8/128/129 enforce boundaries; privileged reset invalidates old access; unauthenticated operations and cross-site changes are rejected. |
| L08 | Application staging via UI, CLI, API and GitHub | Staging never changes live files or restarts NPF; web UI requests confirmation directly after validation without a staged-files list; CLI/API expose the stage and apply only explicitly; stable selection excludes prereleases; checksum mismatch is rejected. |
| L09 | Successful application update | Current database/configuration carried forward; bundled replacements ignored; outgoing version/snapshot captured before migration; actual database override respected; database snapshot is mandatory and opt-out is rejected. |
| L10 | History and cleanup | Repeated successful updates retain current plus two prior versions; associated snapshots pruned together; failed updates do not prune; unrelated staged and recovery files survive until applied/deleted. |
| L11 | Failed update | Inject startup/migration failure; restore old application/dependencies/configuration/database from the mandatory snapshot; manager reports actual outcome and stays available. |
| L12 | Manual rollback | Always restore matching database, even with a newer current schema; keep-current requests and missing snapshots refused; show backup date and optional full-current-package download; failed rollback restores pre-rollback state; current configuration retained by default. |
| L13 | Full database downloads via UI, CLI, API | Export integrity verified during synthetic writes; includes all ledgers and credentials; restore/export comparison proves committed data preserved; no public/temp-file leak. |
| L14 | Database replacement via UI, CLI, API | Upload stages only; explicit replacement validates/backups first; preserves prior running/stopped state unless restart is explicitly requested; invalid/newer-schema input and backup failure leave live state intact; startup failure recovers old database. |
| L15 | Configuration transfer via UI, CLI, API | Actual file downloaded; absent file reported; staged validation and backup enforced; CLI y/N defaults No; web upload-and-restart, CLI flags and API choices work; No preserves stopped state; failed elected restart restores prior file/absence. |
| L16 | Unsafe inputs and conflicts | Reject traversal/link escape, malformed/oversized/incomplete archives/files, invalid database/configuration, and concurrent mutations without corrupting state or exposing private files. |
| L17 | Storage and interruption failures | Inject insufficient disk, unwritable paths, interrupted upload, manager termination and VM reboot during mutation; recover or stop with accurate guidance; required backups are not silently skipped. |
| L18 | Manager update | Installer rerun updates manager while preserving settings, password, certificates, NPF data/history and Stop state; no implicit NPF update; failed manager startup has documented recovery. |
| L19 | Basic UI | Stock controls and white background; keyboard labels usable; status/errors and all required operations accessible without frameworks or decorative styling. |

### Windows manual checklist

Provide the user with Windows 11 x64 steps covering fresh installation/prerequisites, custom paths, adoption, actual Windows Service installation, reboot/control/crash behavior, HTTPS/login/reset, ZIP staging/update/rollback, database/configuration transfers through all interfaces, restart defaults/flags, retention, and manager update. Record tested revision and manual outcomes. Mark untested Windows behavior explicitly.

### Completion evidence

Acceptance requires recorded Linux results from the remote VMs, relevant automated results, failure/recovery evidence, sanitized logs, and remaining limitations. Add Windows manual results when supplied. A completed spec is not itself a passing implementation; use the recorded test results and limitations to assess the implementation. Missing SSH access blocks runtime acceptance, not specification authoring, and does not authorize local testing as a substitute.

## 11. Upstream reference baseline

Inspected through authenticated GitHub CLI on 2026-11-01 at [`838faafec0fc472cf614b66ec9df3f5a296215b3`](https://github.com/ajpri/No-Part-Finance/tree/838faafec0fc472cf614b66ec9df3f5a296215b3). This section records the original source inspection. Subsequent runtime verification against the public v26.10.30 package and explicitly synthetic test fixtures is documented in the validation report; recheck contracts when supporting new upstream versions.

- Python/Flask/SQLite application; Python 3.12 documented installation baseline; dependencies include `pywebpush`.
- Launchers create `.venv`, install dependencies, and run `app.py`, requiring Python beforehand.
- Database defaults to `finance.db`; `FINANCE_DATABASE_PATH` overrides it.
- Runtime `platform.conf` lives beside `app.py`; startup may update its formatting and sample.
- Initial Owner setup is interactive and starts the HTTP server before the prompt.
- Startup handles supported schema upgrades with verified migration backups/recovery; newer unsupported schemas are rejected.
- Browser database exports are selected-ledger and credential-free; trusted full-installation snapshots are different.
- CLI ZIPs may include configuration/logs even with `--no-db`.
- Observed release `v26.10.30` publishes an application-only ZIP and `.sha256` asset.
- NPF's production hosting/reverse-proxy boundary remains separate from the manager's HTTPS listener.

References: [README](https://github.com/ajpri/No-Part-Finance/blob/838faafec0fc472cf614b66ec9df3f5a296215b3/README.md), [launcher](https://github.com/ajpri/No-Part-Finance/blob/838faafec0fc472cf614b66ec9df3f5a296215b3/startLinux.sh), [configuration sample](https://github.com/ajpri/No-Part-Finance/blob/838faafec0fc472cf614b66ec9df3f5a296215b3/platform.conf.sample), [packaging code](https://github.com/ajpri/No-Part-Finance/blob/838faafec0fc472cf614b66ec9df3f5a296215b3/packageApplication.py), [release](https://github.com/ajpri/No-Part-Finance/releases/tag/v26.10.30).
