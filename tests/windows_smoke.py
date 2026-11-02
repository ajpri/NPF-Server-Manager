"""Manual Windows bootstrap/ACL checks; no installed service or NPF data changes.

Run from an Administrator terminal with a Python containing pywin32:
    python tests/windows_smoke.py
"""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if os.name != 'nt' or not ctypes.windll.shell32.IsUserAnAdmin():
        raise SystemExit('Run this manual check on Windows in an Administrator terminal.')
    import win32security
    import win32job
    from installer import windows_permissions
    from manager import npf_command
    import installer
    import winreg
    import base64
    import uuid
    from types import SimpleNamespace
    from unittest.mock import patch
    import server

    with tempfile.TemporaryDirectory(prefix='NPF Windows smoke ') as temporary:
        base = Path(temporary)
        root = base / 'application with spaces'
        nested = root / 'nested'
        nested.mkdir(parents=True)
        sample = nested / 'existing.txt'
        sample.write_text('synthetic fixture')
        windows_permissions(root, 'RX')
        assert sample.read_text() == 'synthetic fixture'
        created = nested / 'created.txt'
        created.write_text('new synthetic fixture')
        for path in (sample, created):
            acl = win32security.GetNamedSecurityInfo(
                str(path), win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
            assert acl is not None and acl.GetAceCount(), 'Missing effective ACL'
            entries = {win32security.ConvertSidToStringSid(acl.GetAce(i)[2]):
                       acl.GetAce(i)[1] for i in range(acl.GetAceCount())}
            assert set(entries) == {'S-1-5-32-544', 'S-1-5-18', 'S-1-5-19'}, entries
            assert entries['S-1-5-19'] == 0x1200a9, entries  # File read/execute
        windows_permissions(root, 'M')
        assert sample.read_text() == 'synthetic fixture'
        acl = win32security.GetNamedSecurityInfo(
            str(sample), win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
        entries = {win32security.ConvertSidToStringSid(acl.GetAce(i)[2]):
                   acl.GetAce(i)[1] for i in range(acl.GetAceCount())}
        assert entries['S-1-5-19'] == 0x1301bf, entries  # File modify
        empty = base / 'empty'
        empty.mkdir()
        windows_permissions(empty, 'M')
        print('PASS: nested existing/new files, restricted identities, RX/M, rerun, empty directory')

        # Load just the function so the real installer cannot run. Use the same
        # Windows PowerShell 5.1 executable as installWindows.bat.
        wrapper = Path(__file__).resolve().parents[1] / 'installWindows.ps1'
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        probe = base / 'probe.ps1'
        probe.write_text("""$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(WRAPPER,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'PowerShell parse failed' }
[void][System.Management.Automation.Language.Parser]::ParseFile(UNINSTALL,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Uninstall launcher PowerShell parse failed' }
$function=$ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-PythonRuntime' },$true)
. ([scriptblock]::Create($function.Extent.Text))
if (-not (Test-PythonRuntime PYTHON -WithPip)) { throw 'Working Python was rejected' }
if (Test-PythonRuntime 'C:\\NPF-nonexistent-smoke-python.exe') { throw 'Missing Python was accepted' }
Write-Host 'PASS: PowerShell 5.1 Python/pip probe and missing interpreter rejection'
""".replace('WRAPPER', quote(wrapper)).replace('UNINSTALL', quote(wrapper.parent/'uninstallWindows.ps1')).replace('PYTHON', quote(sys.executable)))
        subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                        '-File', str(probe)], check=True)
        # Browser HTML parsing normalizes newlines before evaluating CSP hashes.
        page = b'<style>\np { color: black; }\n</style><script>\nvoid 0;\n</script>'
        expected_csp = None
        for newline in (b'\n', b'\r\n', b'\r'):
            (base / 'index.html').write_bytes(page.replace(b'\n', newline))
            manager = SimpleNamespace(settings={'certificate': 'unused', 'private_key': 'unused'})
            with patch.object(server, 'HERE', base), \
                 patch.object(server.ThreadingHTTPServer, '__init__', return_value=None), \
                 patch.object(server.ssl.SSLContext, 'load_cert_chain'):
                http = server.HTTPServer(manager)
            assert http.page == page, 'HTML newlines differ from browser parsing'
            if expected_csp is None:
                expected_csp = http.csp
            assert http.csp == expected_csp, 'CSP depends on checkout line endings'
        print('PASS: LF, CRLF and CR pages have identical browser-compatible CSP hashes')
        asset = base / 'unicode.html'
        asset.write_text('\u201cSynthetic UTF-8 page\u201d', encoding='utf-8')
        with patch('manager.python_for', return_value=Path(sys.executable)):
            command = npf_command(base, '-c',
                'import sys; from pathlib import Path; '
                'assert sys.flags.utf8_mode == 1; '
                'assert Path(sys.argv[1]).read_text() == "\\u201cSynthetic UTF-8 page\\u201d"', asset)
        # Explicit interpreter mode must override an inherited legacy setting.
        subprocess.run(command, env=dict(os.environ, PYTHONUTF8='0'), check=True)
        print('PASS: managed NPF command reads UTF-8 assets under legacy Windows defaults')
        # Exercise real 64-bit HKLM registration and deferred cleanup using only
        # this temporary installation and a unique, temporary uninstall key.
        fixture = base / "uninstall path with spaces & apostrophe's"
        (fixture/'manager').mkdir(parents=True)
        launcher = fixture/'manager/uninstallWindows.ps1'
        launcher.write_bytes((Path(__file__).resolve().parents[1]/'uninstallWindows.ps1').read_bytes())
        data = base/'synthetic data'
        data.mkdir()
        (data/'sentinel.txt').write_text('synthetic data must survive cancellation')
        unrelated = fixture/'unrelated.txt'
        unrelated.write_text('retain unrelated files')
        test_key = installer.WINDOWS_UNINSTALL_KEY+'-Smoke-'+uuid.uuid4().hex
        access = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        try:
            with patch.object(installer, 'WINDOWS_UNINSTALL_KEY', test_key):
                installer.register_windows_uninstall(fixture)
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, test_key, 0, access) as key:
                    assert winreg.QueryValueEx(key, 'DisplayName')[0] == 'NPF Server Manager'
                    assert winreg.QueryValueEx(key, 'InstallLocation')[0] == str(fixture)
                    assert winreg.QueryValueEx(key, 'NoModify')[0] == 1
                    assert winreg.QueryValueEx(key, 'NoRepair')[0] == 1
                    uninstall_string = winreg.QueryValueEx(key, 'UninstallString')[0]
                # Verify quoting with the Windows parser, including the odd path.
                count = ctypes.c_int()
                parse = ctypes.windll.shell32.CommandLineToArgvW
                parse.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
                parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
                arguments = parse(uninstall_string, ctypes.byref(count))
                try:
                    assert arguments[count.value-2] == '-File'
                    assert arguments[count.value-1] == str(launcher)
                    assert '--yes' not in [arguments[i] for i in range(count.value)]
                finally:
                    ctypes.windll.kernel32.LocalFree(ctypes.cast(arguments, ctypes.c_void_p))
                with patch.object(installer, 'VERSION', 'smoke-update'):
                    installer.register_windows_uninstall(fixture)
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, test_key, 0, access) as key:
                    assert winreg.QueryValueEx(key, 'DisplayVersion')[0] == 'smoke-update'
                with patch('cli.privileged', return_value=True), \
                     patch.object(installer, 'uninstall_plan', return_value={'root':fixture, 'data':data}), \
                     patch.object(sys.stdin, 'isatty', return_value=True), \
                     patch('builtins.input', return_value='n'), \
                     patch.object(installer, 'stop_service', side_effect=AssertionError('Cancellation stopped service')):
                    installer.uninstall(data)
                assert (data/'sentinel.txt').exists() and launcher.exists()
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, test_key, 0, access):
                    pass
                assert fixture.resolve().is_relative_to(base.resolve())
                assert data.resolve().is_relative_to(base.resolve())
                script, _ = installer.windows_cleanup_script(fixture, [fixture/'manager', data], 2147483647)
                encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
                subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded], check=True)
                assert unrelated.exists() and not data.exists() and not launcher.exists()
                try:
                    winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, test_key, 0, access).Close()
                except FileNotFoundError:
                    pass
                else:
                    raise AssertionError('Completed cleanup retained uninstall registration')
        finally:
            try:
                winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, test_key, winreg.KEY_WOW64_64KEY, 0)
            except FileNotFoundError:
                pass
        print('PASS: Windows uninstall registration/update, command quoting, cancellation and fixture-only cleanup')
    # Exercise the exact string-name API used by the service, without assigning
    # this test runner to a kill-on-close job.
    job = win32job.CreateJobObject(None, 'NPFServerManager-' + uuid.uuid4().hex)
    job.Close()
    print('PASS: pywin32 job object creation')


if __name__ == '__main__':
    main()
