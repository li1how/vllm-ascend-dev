"""Exercise source checks with fake packages and pip, without installing anything."""

import os
import shlex
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


if __name__ == "__main__":
    unittest.main()
