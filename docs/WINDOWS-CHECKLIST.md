# Windows 11 x64 manual acceptance — 26.11

Status: pending user testing. Record the Windows build, manager revision and NPF version for each run. Use only synthetic data in a disposable/test installation.

- [ ] Elevated installer provisions missing Python/dependencies and registers an actual automatic Windows Service.
- [ ] Default and custom paths work, including spaces; port selection persists.
- [ ] Manager password and interactive NPF Owner setup complete separately; interrupted setup resumes.
- [ ] Adopt an initialized installation without changing originals or duplicating writers.
- [ ] Supplied and generated certificates work from another LAN computer with proper trust.
- [ ] Password boundaries and privileged CLI reset work; old sessions stop working.
- [ ] Reboot starts both; Stop keeps manager available; manager restart preserves intentional Stop; reboot clears it.
- [ ] Crash retry delay/count/reset match the spec; service Stop/crash leaves no NPF orphan.
- [ ] UI/CLI/API stage ZIPs without applying; explicit update carries forward database/configuration.
- [ ] Updates ignore bundled database/configuration; retain current plus two previous verified versions.
- [ ] Every backup includes finance.db; restore always uses the matching database backup, shows its date and offers a current full-package download; failures restore previous state or remain safely stopped.
- [ ] Restart NPF button works; full application-package download includes current code/configuration and all database data.
- [ ] Full database downloads preserve all ledgers/credentials; replacement validates, backs up and restores on failure.
- [ ] Configuration download/replacement works; CLI restart defaults No; web upload-and-restart, both CLI flags and API boolean work.
- [ ] Staged/recovery retention and explicit deletion match the spec.
- [ ] Installer rerun updates manager without implicitly updating NPF or resetting settings/secrets/certificates.
- [ ] Invalid uploads, insufficient space, conflicting requests and interruption do not corrupt the installation.
- [ ] One shared upload button supports either/both files, restarts once, and restores both on failed startup.
- [ ] Both Windows installer entry points accept uninstall and --yes, skip prerequisite downloads, and propagate cancellation/failure exit status.
- [ ] Local uninstall requires Administrator/confirmation, stops/removes service, and removes all managed code/data/backups plus bootstrap runtime after CLI exit; unrelated files and shared prerequisites remain.
- [ ] Stock HTML/CSS/JS UI has no decorative styling and is usable by keyboard.

Record failures and sanitized logs before accepting Windows support. Linux results do not mark these boxes complete.
