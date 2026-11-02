# Remote Linux verification

Run these commands **only inside disposable remote QEMU guests**. Do not start the manager, install prerequisites, run tests or run browser checks on the local development computer. Use synthetic financial data and the public NPF application-only ZIP.

This directory contains three files of Python code:

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
sudo python3 tests/acceptance.py interrupt
sudo python3 tests/acceptance.py manager-failure
sudo python3 tests/acceptance.py github
sudo python3 tests/acceptance.py uninstall
```

`install` supports the upstream terminal Owner setup. `runtime` covers process controls, password reset, CLI/API transfers, updates, matching-database restore and crash retry exhaustion; it ends with NPF intentionally stopped. Reboot the disposable guest, then run `boot` to check automatic startup and manager-update preservation. `stable` verifies the five-minute reset interval.

`failure` injects a failed NPF migration. `interrupt` kills the manager during a journaled update; `reboot` reboots the guest at that point instead, after which `resume` checks recovery. `manager-failure` tests a broken manager update and restoration. `github` retrieves a real checksum-verified stable release.

Use `adoption` and `privileged-port` on an independent clean disposable guest with `/home/tester/synthetic-finance.db` prepared beforehand. The former preserves an original NPF copy and installs into paths containing spaces; the latter checks HTTPS port 443 under the unprivileged service identity.

`uninstall` tests both installer entry points, removes the actual test service and every managed data/code file, then restores the synthetic lab installation from an explicit test-only archive. It must never target a real user's installation. A real uninstall retains no such archive.

## Browser and screenshots

After the installed test service is running and its certificate is trusted in the guest browser:

```sh
/home/tester/ui-env/bin/python tests/browser.py
```

The browser helper uses the same synthetic lab credentials as the acceptance runner (or `NPF_LAB_PASSWORD`). Outputs go to `/home/tester/browser-artifacts`; no private database/package exports should be copied into this repository. Only screenshots and sanitized result records belong in `docs/`.

The test helpers are not included in an installed NPF Server Manager. Windows runtime acceptance remains manual; see [the checklist](../docs/WINDOWS-CHECKLIST.md).
