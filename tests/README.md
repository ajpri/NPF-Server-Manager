# Release verification

Run the Linux commands below **only inside disposable remote QEMU guests**. Do not start the manager, install prerequisites, run the Linux suite or run browser checks on the local development computer. Use synthetic financial data and the public NPF application-only ZIP. The separate focused Windows smoke check is documented at the end.

The Linux verification helpers are:

- `test_manager.py`: automated regression suite, including database integrity, update/restore failure recovery, authentication, file transfers and privileged uninstall guards.
- `acceptance.py`: one installed-system acceptance runner. Named phases cover installation, lifecycle, failures, network checks, custom paths and removal.
- `browser.py`: authenticated HTTPS browser checks and actual UI screenshots. Requires guest-only Playwright, Chromium and a properly trusted management certificate.

## Automated suite

From the source directory in a guest:

```sh
NPF_TEST_ZIP=/home/tester/npf-public.zip python3 -m unittest discover -s tests -v
```

The fixtures are created in temporary guest directories and use their own application port. No real financial data is needed.

## Installed-system checks

The acceptance runner targets the synthetic installation at `/opt/npf` and `/var/lib/npf`. It keeps guest evidence under `/home/tester/acceptance`. These are lab defaults, not product path restrictions. Run from the source checkout, as root in a disposable guest:

```sh
sudo python3 tests/acceptance.py install
sudo python3 tests/acceptance.py runtime
sudo python3 tests/acceptance.py failure
sudo python3 tests/acceptance.py ownerless-install
sudo python3 tests/acceptance.py interrupt
sudo python3 tests/acceptance.py manager-failure
sudo python3 tests/acceptance.py github
sudo python3 tests/acceptance.py uninstall
```

`install` supports the upstream terminal Owner setup. `runtime` covers process controls, password reset, CLI/API transfers, updates, matching-database restore and crash retry exhaustion; it ends with NPF intentionally stopped. Reboot the disposable guest, then run `boot` to check automatic startup and manager-update preservation. `stable` verifies the five-minute reset interval.

`ownerless-install` temporarily replaces the synthetic test service with a fresh installation on independent fixture paths/ports, declines Owner creation, verifies the running manager/NPF and setup status, then reruns the installed configuration and creates an Owner through protected terminal prompts. It restores the original synthetic service in a finally block and removes only those temporary fixture paths. `ownerless-fixture` prepares a fresh empty NPF database for the browser warning check.

`failure` injects a failed NPF migration. `interrupt` kills the manager during a journaled update; `reboot` reboots the guest at that point instead, after which `resume` checks recovery. `manager-failure` tests a broken manager update and restoration. `github` retrieves a real checksum-verified stable release.

Use `adoption` and `privileged-port` on an independent clean disposable guest with `/home/tester/synthetic-finance.db` prepared beforehand. The former preserves an original NPF copy and installs into paths containing spaces; the latter checks HTTPS port 443 under the unprivileged service identity.

`uninstall` tests both installer entry points, removes the actual test service and every managed data/code file, then restores the synthetic lab installation from an explicit test-only archive. It must never target a real user's installation. A real uninstall retains no such archive.

## Browser and screenshots

After the installed test service is running and its certificate is trusted in the guest browser:

```sh
/home/tester/ui-env/bin/python tests/browser.py
```

The browser helper uses the same synthetic lab credentials as the acceptance runner (or `NPF_LAB_PASSWORD`). Outputs go to `/home/tester/browser-artifacts`; no private database/package exports should be copied into this repository. Only screenshots and sanitized result records belong in `docs/`. The browser run also replaces the synthetic database with a freshly initialized ownerless database, checks the setup warning while NPF remains running, and restores the original database in a finally block to verify the warning clears.

The test helpers are not included in an installed NPF Server Manager. Actual Windows 11 verification and remaining scenarios are recorded in [the Windows results](../docs/WINDOWS-TEST-RESULTS.md).

## Focused Windows smoke checks

`windows_smoke.py` is a separate manual Windows check, not part of the Linux
acceptance runner. From an Administrator terminal with pywin32 installed, run:

```powershell
& 'C:\ProgramData\NPF-Server-Manager-InstallerRuntime\Scripts\python.exe' tests/windows_smoke.py
```

It checks the installer permission helper using temporary synthetic files,
including effective ACL identities/masks and inheritance for newly created files.
It also checks the real bootstrap function in Windows PowerShell 5.1, pywin32
job creation, newline-independent browser CSP hashes, and UTF-8 asset reading
through the managed NPF command. It does not start/stop an installed service or
access NPF data.

It also creates a unique temporary Windows uninstall registry entry and a
synthetic installation under the temporary directory. It verifies registration,
update, command quoting, default-No cancellation, and cleanup of that fixture
and registry entry while preserving unrelated files. It never uninstalls the
real NPF installation.
