#!/usr/bin/env python3
"""Standard-library tests for runtime_doctor.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "runtime_doctor.py"
SPEC = importlib.util.spec_from_file_location("runtime_doctor", SCRIPT)
assert SPEC and SPEC.loader
runtime_doctor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_doctor)


def git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, stdout=subprocess.PIPE)


def make_repo(path: Path) -> None:
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.email", "tests@example.com")
    git(path, "config", "user.name", "Skill Tests")
    (path / "tracked.txt").write_text("clean\n")
    git(path, "add", "tracked.txt")
    git(path, "commit", "-qm", "initial")


class RuntimeDoctorTests(unittest.TestCase):
    def test_git_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            make_repo(repo)
            self.assertFalse(runtime_doctor.git_info(repo)["dirty"])
            (repo / "tracked.txt").write_text("dirty\n")
            info = runtime_doctor.git_info(repo)
            self.assertTrue(info["dirty"])
            self.assertTrue(info["head"])

    def test_editable_source_mismatch_and_missing_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            make_repo(workspace / "vllm")
            make_repo(workspace / "vllm-ascend")

            def fake_module(
                name: str,
                distribution: str,
                *,
                timeout_sec: float,
                import_module: bool = True,
            ) -> dict:
                if name == "vllm":
                    path = workspace / "elsewhere" / "vllm" / "__init__.py"
                elif name == "vllm_ascend":
                    path = workspace / "vllm-ascend" / "vllm_ascend" / "__init__.py"
                else:
                    path = workspace / "site-packages" / name / "__init__.py"
                return {
                    "name": name,
                    "available": True,
                    "module_path": str(path),
                    "editable": name in ("vllm", "vllm_ascend"),
                }

            with (
                mock.patch.object(runtime_doctor, "module_info", side_effect=fake_module),
                mock.patch.object(runtime_doctor.shutil, "which", return_value=None),
            ):
                report = runtime_doctor.diagnose(workspace, "skip", 1)
            codes = {issue["code"] for issue in report["issues"]}
            self.assertIn("vllm-source", codes)
            self.assertIn("tool-ruff", codes)
            self.assertNotIn("vllm_ascend-source", codes)

    def test_json_doctor_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            (workspace / "README.md").write_text("# test\n")
            (workspace / "AGENTS.md").write_text("# test\n")
            make_repo(workspace / "vllm")
            make_repo(workspace / "vllm-ascend")
            output = workspace / "doctor.json"
            result = subprocess.run(
                (
                    sys.executable,
                    str(SCRIPT),
                    "--workspace",
                    str(workspace),
                    "--timeout-sec",
                    "5",
                    "--json-output",
                    str(output),
                ),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            self.assertIn(result.returncode, (0, 1), result.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(report["workspace"], str(workspace))
            self.assertIn("repositories", report)
            self.assertIn("modules", report)

    def test_path_match(self) -> None:
        self.assertTrue(runtime_doctor.path_is_within("/a/source/pkg/file.py", Path("/a/source")))
        self.assertFalse(runtime_doctor.path_is_within("/a/other/file.py", Path("/a/source")))

    def test_npu_device_node_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dev_root = Path(temp_dir)
            (dev_root / "davinci0").touch()
            (dev_root / "davinci_manager").touch()
            (dev_root / "davinci_not_a_device").touch()
            devices = runtime_doctor.npu_device_nodes(dev_root)
        self.assertEqual(devices["accelerators"], [str(dev_root / "davinci0")])
        self.assertEqual(devices["control"], [str(dev_root / "davinci_manager")])

    def test_npu_without_device_nodes_is_not_verifiable(self) -> None:
        devices = {"accelerators": [], "control": []}
        with (
            mock.patch.object(runtime_doctor, "npu_device_nodes", return_value=devices),
            mock.patch.object(runtime_doctor.shutil, "which", return_value="/usr/bin/npu-smi"),
            mock.patch.object(runtime_doctor, "run_command") as run_command,
        ):
            info = runtime_doctor.npu_info("auto", 1)
        run_command.assert_not_called()
        self.assertEqual(info["status"], "not_verifiable")
        self.assertEqual(info["npu_smi"]["status"], "skipped")
        self.assertEqual(info["device_probe"]["status"], "not_verifiable")

        issues: list[dict[str, str]] = []
        runtime_doctor.add_npu_issues(issues, info, "auto")
        self.assertEqual(issues[0]["code"], "npu-environment")
        self.assertEqual(issues[0]["severity"], "warning")
        self.assertIn("unsandboxed", issues[0]["message"])

    def test_npu_probe_timeout_is_not_unavailable(self) -> None:
        devices = {"accelerators": ["/dev/davinci0"], "control": []}
        timed_out = {
            "ok": False,
            "error_type": "timeout",
            "error": "command timed out after 1s",
        }
        with (
            mock.patch.object(runtime_doctor, "npu_device_nodes", return_value=devices),
            mock.patch.object(runtime_doctor.shutil, "which", return_value="/usr/bin/npu-smi"),
            mock.patch.object(
                runtime_doctor,
                "run_command",
                side_effect=(timed_out.copy(), timed_out.copy()),
            ),
        ):
            info = runtime_doctor.npu_info("required", 1)
        self.assertEqual(info["status"], "timeout")
        self.assertEqual(info["npu_smi"]["status"], "timeout")
        self.assertEqual(info["device_probe"]["status"], "timeout")

        issues: list[dict[str, str]] = []
        runtime_doctor.add_npu_issues(issues, info, "required")
        self.assertTrue(all(issue["severity"] == "error" for issue in issues))
        device_issue = next(issue for issue in issues if issue["code"] == "npu-device")
        self.assertIn("timed out", device_issue["message"])
        self.assertNotIn("no available", device_issue["message"])

    def test_npu_probe_reports_unavailable_only_after_completed_probe(self) -> None:
        devices = {"accelerators": ["/dev/davinci0"], "control": []}
        smi_result = {"ok": True, "returncode": 0, "stdout": "ok", "stderr": ""}
        probe_result = {
            "ok": True,
            "returncode": 0,
            "stdout": json.dumps({"available": False, "device_count": 0}),
            "stderr": "",
        }
        with (
            mock.patch.object(runtime_doctor, "npu_device_nodes", return_value=devices),
            mock.patch.object(runtime_doctor.shutil, "which", return_value="/usr/bin/npu-smi"),
            mock.patch.object(
                runtime_doctor,
                "run_command",
                side_effect=(smi_result, probe_result),
            ),
        ):
            info = runtime_doctor.npu_info("auto", 1)
        self.assertEqual(info["status"], "unavailable")
        self.assertEqual(info["device_probe"]["status"], "unavailable")

        issues: list[dict[str, str]] = []
        runtime_doctor.add_npu_issues(issues, info, "auto")
        self.assertEqual(len(issues), 1)
        self.assertIn("no available NPU device", issues[0]["message"])


if __name__ == "__main__":
    unittest.main()
