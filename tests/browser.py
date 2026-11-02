"""Guest-only browser verification and screenshots of the compact manager UI."""
import json
import os
import subprocess
import sqlite3
import zipfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

inputs = Path('/home/tester/acceptance')
artifacts = Path('/home/tester/browser-artifacts')
artifacts.mkdir(exist_ok=True)
package = os.environ.get('NPF_LAB_PACKAGE', '/home/tester/npf-public.zip')
password = os.environ.get('NPF_LAB_PASSWORD', 'Lab-Manager-Synthetic-26!')
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 1000}, device_scale_factor=1, timezone_id='America/Chicago')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('https://127.0.0.1:5050', wait_until='networkidle')
    page.get_by_label('Administrator password').fill(password)
    page.get_by_role('button', name='Sign in', exact=True).click()
    expect(page.locator('#status')).to_contain_text('running')
    expect(page.locator('#owner-warning')).to_be_hidden()
    if os.environ.get('NPF_EXPECT_BACKEND'):
        expect(page.locator('#npf-listener')).to_contain_text(os.environ['NPF_EXPECT_BACKEND'])
    expect(page.locator('#versions tr')).to_have_count(4)
    page.get_by_role('button', name='Restart NPF', exact=True).click()
    expect(page.locator('#message')).to_have_text('NPF running.', timeout=60000)
    assert page.locator('#staged, #recovery').count() == 0
    for name in ['platform.conf', 'finance.db']:
        with page.expect_download() as download:
            page.get_by_role('link', name='Download ' + name, exact=True).click()
        download.value.save_as(artifacts / ('browser-' + name))
    with page.expect_download() as download:
        page.get_by_role('link', name='Download full application package', exact=True).click()
    package=artifacts/'full-package.zip'; download.value.save_as(package)
    with zipfile.ZipFile(package) as archive:
        assert archive.testzip() is None
        assert 'No-Part-Finance/app.py' in archive.namelist()
        assert 'No-Part-Finance/platform.conf' in archive.namelist()
        full_database=artifacts/'package-finance.db'
        full_database.write_bytes(archive.read('No-Part-Finance/finance.db'))
    with sqlite3.connect(full_database) as connection, sqlite3.connect(artifacts/'browser-finance.db') as baseline:
        assert connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        # Compare every table and row, regardless of this installed fixture's ledger count.
        # The operations suite separately verifies multiple ledgers and credential contents.
        assert list(connection.iterdump()) == list(baseline.iterdump())
    # Cancel a validated ZIP: no separate stage screen and no live changes.
    page.once('dialog', lambda dialog: dialog.dismiss())
    page.locator('#application-upload input[type=file]').set_input_files(package)
    page.get_by_role('button', name='Upload update', exact=True).click()
    expect(page.locator('#message')).to_have_text('Cancelled. Current files are unchanged.', timeout=60000)
    expect(page.locator('#status')).to_contain_text('running')
    # Each file form explicitly uploads and restarts NPF after confirmation.
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('#configuration-file').set_input_files(str(inputs / 'platform.conf'))
    page.locator('#files-upload').get_by_role('button', name='Upload and restart No Part Finance', exact=True).click()
    expect(page.locator('#message')).to_have_text('Files replaced. No Part Finance restarted.', timeout=60000)
    expect(page.locator('#status')).to_have_text('Status: running')
    # Exercise the full database replacement using the just-downloaded synthetic database.
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('#database-file').set_input_files(str(artifacts / 'browser-finance.db'))
    page.locator('#files-upload').get_by_role('button', name='Upload and restart No Part Finance', exact=True).click()
    expect(page.locator('#message')).not_to_have_text('Uploading and validating…', timeout=60000)
    expect(page.locator('#message')).not_to_have_text('Applying…', timeout=60000)
    expect(page.locator('#status')).to_contain_text('running')
    assert 'replaced' in page.locator('#message').inner_text().lower(), page.locator('#message').inner_text()
    # Both selected files use that same single action and one restart operation.
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('#configuration-file').set_input_files(str(inputs/'platform.conf'))
    page.locator('#database-file').set_input_files(str(artifacts/'browser-finance.db'))
    page.locator('#files-upload').get_by_role('button', name='Upload and restart No Part Finance', exact=True).click()
    expect(page.locator('#message')).to_have_text('Files replaced. No Part Finance restarted.', timeout=60000)
    expect(page.locator('#status')).to_have_text('Status: running')
    assert page.locator('#files-upload button').count() == 1
    # Confirm the actual ZIP-update path as well, using the public application package.
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('#application-upload input[type=file]').set_input_files(package)
    page.get_by_role('button', name='Upload update', exact=True).click()
    expect(page.locator('#message')).not_to_have_text('Uploading and validating…', timeout=60000)
    expect(page.locator('#message')).not_to_have_text('Applying…', timeout=180000)
    expect(page.locator('#status')).to_contain_text('running')
    assert 'update' in page.locator('#message').inner_text().lower(), page.locator('#message').inner_text()
    expect(page.locator('#versions tr')).to_have_count(4)
    # Restore has no checkbox: show backup date, offer a full-current-package download,
    # permit cancellation, then restore the version and matching database together.
    assert page.locator('#versions input[type=checkbox]').count() == 0
    page.get_by_role('button',name='Restore this version…',exact=True).first.click()
    modal=page.get_by_role('dialog',name='Restore this version',exact=True)
    expect(modal).to_be_visible()
    expect(modal.locator('#restore-description')).to_contain_text('Database backup:')
    assert 'date unknown' not in modal.locator('#restore-description').inner_text()
    with page.expect_download() as download:
        modal.get_by_role('link',name='Download current full application package',exact=True).click()
    current_package=artifacts/'before-restore-full-package.zip'; download.value.save_as(current_package)
    with zipfile.ZipFile(current_package) as archive:
        assert archive.testzip() is None
        assert 'No-Part-Finance/finance.db' in archive.namelist()
    modal.get_by_role('button',name='Cancel',exact=True).click()
    expect(modal).not_to_be_visible()
    page.get_by_role('button',name='Restore this version…',exact=True).first.click()
    expect(modal).to_be_visible()
    page.screenshot(path=str(artifacts/'restore-confirmation-26.11.png'),full_page=True)
    modal.get_by_role('button',name='Confirm restore',exact=True).click()
    expect(page.locator('#message')).to_have_text('Rollback complete.',timeout=60000)
    expect(page.locator('#status')).to_contain_text('running')
    # An ownerless full database can run, but the manager must explain the missing setup.
    owner_baseline = artifacts/'before-owner-check.db'
    with page.expect_download() as download:
        page.get_by_role('link', name='Download finance.db', exact=True).click()
    download.value.save_as(owner_baseline)
    subprocess.run(['sudo', 'python3', str(Path(__file__).with_name('acceptance.py')), 'ownerless-fixture'], check=True)
    ownerless = inputs/'ownerless.db'
    try:
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#database-file').set_input_files(str(ownerless))
        page.locator('#files-upload button').click()
        expect(page.locator('#message')).to_have_text('Files replaced. No Part Finance restarted.', timeout=60000)
        expect(page.locator('#status')).to_have_text('Status: running')
        expect(page.locator('#owner-warning')).to_be_visible()
        expect(page.locator('#owner-warning')).to_contain_text('Rerun the platform installer')
        page.screenshot(path=str(artifacts/'owner-setup-required-26.11.png'), full_page=True)
    finally:
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#database-file').set_input_files(str(owner_baseline))
        page.locator('#files-upload button').click()
        expect(page.locator('#message')).to_have_text('Files replaced. No Part Finance restarted.', timeout=60000)
        expect(page.locator('#owner-warning')).to_be_hidden()
    assert not errors, errors
    page.screenshot(path=str(artifacts / 'manager-26.11.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('() => document.documentElement.scrollWidth <= window.innerWidth + 1'), 'Horizontal page overflow'
    page.screenshot(path=str(artifacts / 'manager-26.11-mobile.png'), full_page=True)
    (artifacts / 'browser-results.json').write_text(json.dumps({
        'ownerless_server_runs_and_ui_warning_clears_after_owner_restored': True,
        'login': True, 'database_and_configuration_downloads': True,
        'cancelled_zip_leaves_live_files_unchanged': True,
        'configuration_upload_and_restart': True, 'restart_button': True,
        'full_package_includes_all_ledgers_and_credentials': True,
        'database_upload_confirm_and_replace': True,
        'combined_upload_either_or_both_one_button': True,
        'zip_upload_confirm_and_update': True,
        'current_plus_two_previous_versions': True,
        'restore_confirmation_backup_date_download_cancel_and_apply': True,
        'no_keep_current_database_checkbox': True,
        'no_staged_or_recovery_lists': True,
        'narrow_layout_without_page_overflow': True,
        'javascript_errors': errors, 'https_certificate_verified': True,
    }, indent=2))
    browser.close()
