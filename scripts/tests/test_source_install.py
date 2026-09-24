"""Exercise source checks with fake packages and pip, without installing anything."""

import os
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "install-vllm-source.sh"
FUNCTIONS = (
    SCRIPT.read_text()
    .split("check_source_resolution() {", 1)[1]
    .split("# ---- 参数解析 ----", 1)[0]
)
FUNCTIONS = "check_source_resolution() {" + FUNCTIONS


class SourceInstallTests(unittest.TestCase):
    def check(self, mode, *, pip_status=0, config=""):
        with tempfile.TemporaryDirectory(prefix="source checks ") as directory:
            root = Path(directory)
            package = root / "vllm" / "vllm"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("")
            site = root / "site"
            site.mkdir()
            if mode == "valid":
                (site / "vllm").symlink_to(package, target_is_directory=True)
            elif mode == "wrong":
                (site / "vllm").mkdir()
                (site / "vllm" / "__init__.py").write_text("")
            pip_log = root / "pip.log"
            wrapper = root / "python"
            wrapper.write_text(
                "#!/bin/bash\nset -e\n"
                'if [[ "$1" == -m && "$2" == pip ]]; then\n'
                '  printf "%s\\n" "$@" >> "$SCRIPT_DIR/pip.log"\n'
                f"  if [[ {pip_status} != 0 ]]; then exit {pip_status}; fi\n"
                '  ln -s "$SCRIPT_DIR/vllm/vllm" "$SCRIPT_DIR/site/vllm"\n'
                "else\n"
                f'  exec {shlex.quote(sys.executable)} -S -B "$@"\nfi\n'
            )
            wrapper.chmod(0o755)
            command = (
                "set -euo pipefail\n"
                f"SCRIPT_DIR={shlex.quote(directory)}\nexport SCRIPT_DIR\n"
                f"PYTHON_BIN={shlex.quote(str(wrapper))}\n"
                "BUILD_TMP_ENV=()\nBUILD_TMP_DIR=\"$SCRIPT_DIR/site\"\nws_log_step() { :; }\nws_log_warn() { :; }\n"
                + FUNCTIONS
                + "\ncheck_vllm_resolution\n"
            )
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=root,
                timeout=10,
                env={
                    **os.environ,
                    "PYTHONPATH": str(site),
                    "PIP_CONFIG_SETTINGS": config,
                },
                text=True,
                capture_output=True,
            )
            return result, pip_log.read_text() if pip_log.exists() else ""

    def test_namespace_without_installed_source_fails_without_reinstall(self):
        result, pip_log = self.check("namespace")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(pip_log, "")

    def test_correct_source_does_not_reinstall_even_from_workspace_root(self):
        result, pip_log = self.check("valid")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pip_log, "")

    def test_wrong_source_does_not_trigger_reinstall(self):
        result, pip_log = self.check("wrong")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(pip_log, "")

    def test_explicit_editable_settings_do_not_change_resolution(self):
        result, pip_log = self.check("namespace", config="editable_mode=strict")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(pip_log, "")


class SystemCertificateTests(unittest.TestCase):
    def test_system_bundle_and_explicit_overrides(self):
        common = SCRIPT.parent / "lib" / "common.sh"
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "system ca.pem"
            bundle.touch()
            for override in ("", "/custom/ca.pem"):
                with self.subTest(override=override):
                    command = (
                        f"source {shlex.quote(str(common))}\n"
                        f"WS_SYSTEM_CA_FILE={shlex.quote(str(bundle))}\n"
                        "ws_use_system_ca\n"
                        'printf "%s\\n" "$SSL_CERT_FILE" "$REQUESTS_CA_BUNDLE" "$PIP_CERT"'
                    )
                    result = subprocess.run(
                        ["bash", "-c", command],
                        check=True,
                        text=True,
                        capture_output=True,
                        env={
                            **os.environ,
                            "SSL_CERT_FILE": override,
                            "REQUESTS_CA_BUNDLE": override,
                            "PIP_CERT": override,
                        },
                    )
                    self.assertEqual(
                        result.stdout.splitlines(), [override or str(bundle)] * 3
                    )


class InstallFlowTests(unittest.TestCase):
    """Run the real shell entrypoint with fake pip and source packages."""

    def run_install(self, *, fail=False, explicit_cache=True, include_helper=True):
        temp = tempfile.TemporaryDirectory(prefix='install fixture ')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'scripts/lib').mkdir(parents=True)
        script = root / 'scripts/install-vllm-source.sh'
        shutil.copy2(SCRIPT, script)
        for repo, module in (('vllm', 'vllm'), ('vllm-ascend', 'vllm_ascend')):
            package = root / repo / module
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('')
        wrapper = root / 'python-wrapper'
        wrapper.write_text(
            '#!/bin/bash\nset -e\n'
            'if [[ "$1" == -m && "$2" == pip ]]; then\n'
            '  printf "%s\\n" "$PWD|$*|${VLLM_ASCEND_BUILD_CACHE_DIR:-}|${MAX_JOBS:-}" >> "$SCRIPT_DIR/pip.log"\n'
            '  if [[ "$3" == install && "$PWD" == */vllm-ascend ]]; then\n'
            '    printf "%s\\n" \'{"event":"cache_result","status":"HIT"}\' >> "$VLLM_ASCEND_BUILD_CACHE_EVENT_LOG"\n'
            '    printf "%s\\n" \'{"event":"cache_result","status":"MISS"}\' >> "$VLLM_ASCEND_BUILD_CACHE_EVENT_LOG"\n'
            f'    exit {23 if fail else 0}\n'
            '  fi\n  exit 0\nfi\n'
            'export PYTHONPATH="$SCRIPT_DIR/vllm:$SCRIPT_DIR/vllm-ascend"\n'
            f'exec {shlex.quote(sys.executable)} -B "$@"\n'
        )
        wrapper.chmod(0o755)
        common = SCRIPT.parent / 'lib/common.sh'
        (root / 'scripts/lib/common.sh').write_text(
            common.read_text() + '\nws_select_python_env() { PYTHON_BIN=' + shlex.quote(str(wrapper)) + '; }\n'
        )
        helper = SCRIPT.parents[1] / '.agents/skills/remote-init/scripts/cache-transfer.py'
        destination = root / '.agents/skills/remote-init/scripts/cache-transfer.py'
        destination.parent.mkdir(parents=True)
        if include_helper:
            shutil.copy2(helper, destination)
        argv = ['bash', str(script), '--tmp-dir', str(root / 'tmp'), '--jobs', '7']
        if explicit_cache:
            argv += ['--build-cache-dir', str(root / 'native cache')]
        result = subprocess.run(argv, cwd=root, text=True, capture_output=True, timeout=20,
                                env={**os.environ, 'VLLM_ASCEND_BUILD_CACHE_DIR': str(root / 'environment cache')})
        summary = json.loads(next((root / 'log').glob('install-source.*/summary.json')).read_text())
        return root, result, summary

    def test_cli_cache_overrides_environment_and_preserves_install_order(self):
        root, result, summary = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(summary['cacheDir'], str(root / 'native cache'))
        self.assertEqual(summary['jobs'], 7)
        self.assertEqual(summary['cacheResults'], {'HIT': 1, 'MISS': 1, 'BYPASS': 0})
        calls = (root / 'pip.log').read_text().splitlines()
        installs = [line for line in calls if '|-m pip install -e .' in line]
        self.assertIn('/vllm|', installs[0])
        self.assertIn('/vllm-ascend|', installs[1])
        self.assertTrue(any('pip uninstall -y vllm vllm-ascend' in line for line in calls))
        self.assertTrue((root / 'log/last-native-build.json').is_file())

    def test_build_failure_keeps_exit_code_log_and_event_summary(self):
        root, result, summary = self.run_install(fail=True, explicit_cache=False)
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertEqual(summary['exitCode'], 23)
        self.assertEqual(summary['cacheDir'], str(root / 'environment cache'))
        self.assertEqual(summary['cacheResults']['HIT'], 1)
        self.assertTrue(next((root / 'log').glob('install-source.*/ascend-build.log')).is_file())
        self.assertFalse((root / 'log/last-native-build.json').exists())

    def test_missing_optional_cache_helper_keeps_success_and_summary(self):
        root, result, summary = self.run_install(include_helper=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(summary['exitCode'], 0)
        self.assertIn('buildOriginError', summary)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse((root / 'log/last-native-build.json').exists())


if __name__ == "__main__":
    unittest.main()
