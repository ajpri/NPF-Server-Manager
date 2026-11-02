# Version 26.11 — Linux validation

Linux results recorded on 2026-11-01 (America/Chicago). Guest logs use UTC, so some entries are dated November 2. Subsequent actual Windows 11 verification is recorded separately in [Windows results](WINDOWS-TEST-RESULTS.md).

## Environment

All Linux runtime tests in this report ran in disposable x64 QEMU guests on the separate SSH-accessible Linux test computer. No Linux application, service, prerequisite installation, VM, or runtime test was run on the local development computer.

Portable QEMU 8.2.2 was downloaded as distribution packages and extracted within the approved remote lab directory. Host packages/services/firewall were not modified. Each guest used KVM, four virtual CPUs, 4 GiB RAM, a 24 GiB copy-on-write disk, and user-mode networking with loopback-only host forwarding. The host also acted as a separate HTTPS client to the guest through forwarding, with certificate verification enabled.

| Guest | Purpose | Result |
| --- | --- | --- |
| Debian GNU/Linux 13 (13.7 image), Python 3.13 | Automated suite, installation, systemd, lifecycle, recovery, browser | Passed recorded checks |
| Fedora Linux 44 Cloud, Python 3.14 | Automated suite, installation, systemd, lifecycle, recovery | Passed recorded checks |
| Independent clean Debian 13 guest | Prerequisite bootstrap, adoption, custom paths containing spaces, HTTPS port 443 | Passed recorded checks |

Debian image integrity was checked against the publisher's SHA-512 checksum; Fedora against the publisher's SHA-256 checksum. This does not claim independent OpenPGP signature verification of the image manifests.

NPF integration used the public **v26.10.30** application-only ZIP (schema 30). Tests also used clearly identified synthetic package versions and intentionally broken candidates to exercise updates and recovery. Synthetic databases included multiple ledgers, an Owner password hash, and a generated integration secret. No real financial database or user credentials were used. The screenshot shows repeated installations of public NPF 26.10.30; earlier acceptance also used synthetic 26.11.x packages. These NPF application versions are separate from the manager's 26.11 release.

## Automated results

Command, inside each guest:

```sh
NPF_TEST_ZIP=/home/tester/npf-public.zip python3 -m unittest discover -s tests -v
```

| Distribution | Tests | Result | Final duration |
| --- | --- | --- | --- |
| Debian 13 | 39 | All passed | 37.979 seconds |
| Fedora 44 | 39 | All passed | 41.132 seconds |

Logs: [Debian](validation/debian/tests.log), [Fedora](validation/fedora/tests.log).

The suite verifies password boundaries and reset, multi-ledger/credential-bearing SQLite snapshots, hot-journal recovery, read-only candidate validation, staged configuration/database flows, mandatory replacement backups, invalid/newer-schema database rejection, unsafe ZIP paths/links and extraction limits, busy locking, readiness and duplicate-start prevention, mandatory database backups, snapshot opt-out rejection, update failure restoration, deferred update verification, manual snapshot rollback and failed rollback restoration, interrupted-operation recovery/refusal, retention, supplied certificate validation, release checksum mismatch/success, persisted crash-failure state, API authentication/reset/CSRF, body limits, chunked-upload rejection, private-path denial, export cleanup, and private CLI export permissions, full application-package exports with multiple ledgers/password hashes/integration secrets/committed WAL data, explicit restart including upload-and-restart from stopped state, combined replacement of either/both files with one restart and two-file recovery, and uninstall privilege/registered-path/confirmation guards, and installer dispatch/exit-code propagation before prerequisite setup.

## Linux directory alignment — November 2

Pulled the finalized Windows work at `937c9c8`, then aligned Linux defaults with the same directory name: `/opt/NPF-Server-Manager` for application files and `/var/lib/NPF-Server-Manager` for persistent data. The `npf-manager` command, service/account names and locator remain unchanged. Registered/custom paths are preserved on reruns; no production migration or automatic relocation was added.

Only the two disposable default-path Linux installations were replaced. Each old synthetic installation was uninstalled, then freshly installed by accepting the new default application/data prompts. Existing old default directories were verified absent. Both complete 39-test regression suites passed. Installed checks verified new settings/manifest paths, CLI discovery, launcher/systemd paths, service enablement, and running NPF: [structured results](validation/linux-directory-results.json).

At the renamed paths, actual CLI/API exports and replacements, application updates, three-version retention, matching-database restore, crash retry limits and explicit restart passed on both distributions. Both installer uninstall entry points removed all managed application/data files and the test helper restored only the synthetic installations from its temporary archive. Reboot started NPF automatically; installer reruns retained the selected paths and intentional Stop state. Logs: [Debian installation](validation/debian/linux-paths-reinstall.log), [runtime](validation/debian/linux-paths-runtime.log), [reboot/rerun](validation/debian/linux-paths-boot.log), [uninstall](validation/debian/linux-paths-uninstall.log); [Fedora installation](validation/fedora/linux-paths-reinstall.log), [runtime](validation/fedora/linux-paths-runtime.log), [reboot/rerun](validation/fedora/linux-paths-boot.log), [uninstall](validation/fedora/linux-paths-uninstall.log).

The Debian [browser workflows](validation/linux-paths-browser-results.json) and updated [API/CLI documentation checks](validation/linux-paths-documentation-results.json) passed using the new installation and its trusted newly generated certificate, with no JavaScript errors. Both services were healthy before the guests were shut down after evidence collection. No runtime tests or services ran locally. Windows code and its recorded verification were retained; Windows was not retested for this Linux-default change.

## Windows fixes branch verification — November 2

Owner creation can be deferred: installation completes and the manager and NPF start without an Owner. Authenticated status exposes `owner_setup_required`, and the single-file UI warns when setup is required. Rerunning an already completed installation offers protected terminal Owner setup when no Owner exists; existing Owners are preserved.

The complete 39-test suites passed in both remote guests, including actual ownerless NPF startup and installer publication/resume checks. Real fresh installations then declined Owner creation, verified the active systemd service, authenticated HTTPS status and running NPF, reran the completed installer, and created an Owner through upstream protected terminal prompts. Both fixtures restored the original independent synthetic service afterward: [Debian](validation/debian/debian-windows-fixes-ownerless.log), [Fedora](validation/fedora/fedora-windows-fixes-ownerless.log).

The Debian [browser run](validation/windows-fixes-browser-results.json) exercised the existing workflows plus replacement with a fresh ownerless database, continued NPF operation, the setup warning, and restoration of the original database with the warning clearing. HTTPS verification passed and no JavaScript errors occurred. [Owner warning screenshot](screenshots/owner-setup-required-26.11.png). The updated [OpenAPI/CLI checks](validation/windows-fixes-documentation-results.json) passed against the installed service.

The Windows bootstrap now probes the selected base interpreter and temporary environment, rebuilds an unusable temporary environment when possible, and reports interpreter/prerequisite failures clearly. An official portable PowerShell runtime inside the disposable Debian guest parsed the script and verified acceptance of working Python/pip and rejection of missing/broken Python and missing pip: [results](validation/windows-bootstrap-results.json). This is a parser/probe check, **not Windows runtime acceptance**. Automatic Windows environment repair, service registration and the batch pause/exit-code behavior remain for manual Windows 11 testing. The batch installer pauses by default; `NPF_INSTALLER_NO_PAUSE=1` suppresses the pause for automation.

No application or runtime tests ran locally. Both disposable guests were shut down after verification. Version remains 26.11; earlier reboot/stability/uninstall evidence below was retained rather than rerun for this branch.

## Internal simplification verification — November 2

HTTP handlers share error handling while retaining existing routes, response codes and messages. Runtime version output, the served HTML and service descriptions read `VERSION`. Recovery failure reporting and backup restoration share helpers; the redundant update-journal cleanup was removed without changing when recovery files become eligible for deletion. The application still has six Python modules and a single HTML/CSS/JS file.

Both complete 37-test suites passed again; the HTTP regression now also checks rendered version/CSP hashes, GET busy 400 versus POST busy 409, and sanitized method-specific 500 responses. Installer updates succeeded in both guests, with CLI and systemd descriptions reporting 26.11. Real failed updates and manager SIGKILL during journaled updates restored the previous application and valid database on both distributions: [Debian failure](validation/debian/debian-refactor-failure.log), [Debian interruption](validation/debian/debian-refactor-interrupt.log), [Fedora failure](validation/fedora/fedora-refactor-failure.log), [Fedora interruption](validation/fedora/fedora-refactor-interrupt.log).

The Debian [browser checks](validation/refactor-browser-results.json) passed with verified HTTPS and no JavaScript errors. The existing [OpenAPI/CLI checks](validation/refactor-documentation-results.json) passed again, covering 21 documented paths, 105 examples and actual authenticated responses. Both disposable guests were shut down afterward. Windows received source syntax checks only; runtime verification remains manual. Earlier uninstall, reboot, five-minute stability and custom-path results below were retained rather than rerun for this internal change.

## Consolidation verification

The final build has six application Python modules. Installation and removal now share installer.py; the CLI and both installer entry points use the same uninstall implementation. Eight separate development scripts have become tests/acceptance.py (named guest phases) and tests/browser.py (browser/screenshot checks), alongside the regression suite. No development helpers are installed with the application. See [the testing guide](../tests/README.md).

Single-file CLI/API replacements and combined web uploads now use one validation/full-backup/recovery path. Configuration-only failure without restart restores configuration while preserving the existing NPF process and database writes committed during the operation; a new regression check verifies this explicitly.

After consolidation, the complete 37-test suites passed in both guests. The new acceptance runner also exercised real failed NPF updates, failed manager updates, matching-database restore and full installer-driven uninstall in both guests. Current installed code contained exactly six Python files and no test/helper directories. The consolidated browser helper passed all existing download, upload, restart and restore checks on Debian with verified HTTPS and no JavaScript errors. Existing boot/five-minute/custom-path evidence below was retained; those unchanged behaviors were not all rerun during this organizational change.

Final installed recovery logs: [Debian application failure](validation/debian/debian-consolidation-failure.log), [Debian manager recovery](validation/debian/debian-consolidation-manager-failure.log), [Fedora application failure](validation/fedora/fedora-consolidation-failure.log), [Fedora manager recovery](validation/fedora/fedora-consolidation-manager-failure.log).

## Installed-service and browser results

| Check | Evidence/result |
| --- | --- |
| Terminal Owner setup | Actual upstream protected password prompts completed during initial installation; interrupted setup/service installation resumed without creating another Owner. |
| Default installation | Actual `npf` account and enabled systemd service; HTTPS manager and supervised NPF running in both families. |
| Adoption/custom layout | Clean Debian guest bootstrapped prerequisites, adopted an initialized synthetic installation, preserved the source database/configuration, skipped Owner setup, and ran from paths containing spaces. [Result](validation/adoption-results.json) |
| Custom privileged port | HTTPS 443 worked while retaining `User=npf`, with only the network-bind capability added. [Result](validation/privileged-port-test.log) |
| Intentional Stop | Manager remained available; Stop survived an ordinary manager service restart. |
| Reboot | Manager and NPF started automatically; reboot cleared intentional Stop. |
| Crash limit | Three restart delays measured approximately 10.25–11.48 seconds. A fourth crash exhausted the limit and left NPF failed/stopped. Explicit Start reset the count. |
| Stable-run reset | Both guests observed 300.1 seconds of uninterrupted running after a crash restart, then verified the retry count reset to zero. |
| Password reset | Old session rejected; new login succeeded; service ownership/readability of the password hash preserved. |
| CLI | Authenticated status, full database/configuration download, configuration staging and replacement with `--no-restart` passed. |
| Updates/history | Repeated synthetic application versions staged and explicitly applied. Existing data/configuration survived; bundled replacements ignored. Three-version retention and retained recovery copies verified. |
| Matching-database restore | Restore always uses the selected version’s matching database snapshot. Tests cover a newer current schema, rejection of keep-current requests and missing snapshots, preserved current configuration, automatic outgoing backup and failed-startup recovery. Browser confirmation/download/cancel/actual restore passed. |
| Failed NPF update | Actual synthetic candidate changed the database schema to 999 and failed startup. Previous application/configuration/database were restored and the failed stage retained. |
| Manager killed during update | Debian recovered a journaled operation after SIGKILL of the manager, restoring previous active state and a valid database. |
| VM reboot during update | Fedora recovered a journaled operation after guest reboot, restoring previous active state and a valid database. |
| Manager update | Installer rerun preserved active NPF version, password, certificate and intentional Stop state. |
| Failed manager update | Actual broken manager candidate failed startup; installer restored the previous manager service and preserved NPF state/password/certificate. |
| GitHub | Actual stable release retrieved and SHA-256 verified in both guests; package staged without changing active NPF. |
| LAN-equivalent HTTPS | Separate remote-host client reached the guest listener through QEMU user networking with the supplied public certificate trusted. |
| Uninstall | Actual installed CLI cleanup was exercised through installLinux.sh uninstall and installer.py uninstall in both guests. Both required explicit --yes noninteractively, then stopped/removed the running service and deleted manager code/launcher, NPF releases, database/configuration, keys and all backups. Test-only synthetic installations were restored afterward. [Debian evidence](validation/debian/uninstall-results.json), [Fedora evidence](validation/fedora/uninstall-results.json). |
| Browser | Chromium/Playwright ran in Debian guest, with the certificate trusted through NSS. Login, full application-package ZIP, both standalone file downloads, Restart NPF, cancelled ZIP upload, confirmed ZIP update and the shared upload-and-restart button with configuration only, database only and both files together passed. Packaged finance.db matched every table/row in the standalone full database export. Current plus two previous versions displayed with no staged/recovery queues. Restore confirmation showed the matching backup date and current full-package download; cancellation and actual restore passed with no database-choice checkbox. No JavaScript errors; the 390-pixel page had no horizontal document overflow. |

Installed acceptance records: [Debian](validation/debian/results.jsonl), [Fedora](validation/fedora/results.jsonl). Stable reset and network evidence are stored alongside those records. [Browser result](validation/browser-results.json).

The public NPF source was kept distinct from synthetic failure fixtures. Earlier failed development runs exposed and led to fixes for version metadata lookup, authenticated NPF health, unprivileged service ownership/locator access, starting-versus-running state, SQLite hot journals, deferred update recovery, and snapshot association during manual rollback. The recorded final results above use the corrected implementation.

## Screenshots

- [Desktop, authenticated manager](screenshots/manager-26.11.png)
- [Mobile-width page](screenshots/manager-26.11-mobile.png)
- [Restore confirmation](screenshots/restore-confirmation-26.11.png)

These are actual screenshots from the remote VM browser. HTML, CSS and JavaScript are contained in one `index.html`; the UI uses native controls and a white background.

## API and CLI documentation validation

On 2026-11-02, openapi.json passed formal OpenAPI 3.1 validation in the remote disposable Debian guest. Its 21 paths matched the implemented handlers, and 105 request/response examples passed schema validation. All 14 CLI subcommands and CLI/installer flags were compared with actual --help output. Live login, session/status, all three downloads, unauthorized/CSRF rejection and logout matched the documented response shapes. These checks used existing synthetic lab credentials without exporting them. No application code changed, and the full runtime suite was not repeated for this documentation-only change. [Validation record and document hashes](validation/documentation-results.json).

## Validation boundaries

Windows 11 was tested separately after this Linux run; see [Windows results](WINDOWS-TEST-RESULTS.md) for its actual coverage and remaining scenarios. Other Debian/Fedora derivatives and older OS releases were not separately tested.

The upstream integration contract was exercised against v26.10.30. The tests do not establish compatibility with every historical NPF schema or unknown future release. Migration/failure handling uses upstream initialization on disposable copies plus controlled fault fixtures; this is not a historical-release upgrade matrix.

Storage/backup failures and unsafe inputs were exercised through targeted automated injection and bounded-input tests. These results are not a claim that every possible host/storage/network failure has been reproduced. Full offline prerequisite installation and ARM remain outside this release.

Remote SSH connection information and private keys are deliberately absent from the specification and this report. VM disks and detailed lab artifacts are retained on the remote test computer for follow-up. Disposable guests were shut down after evidence collection to free host resources.
