#!/usr/bin/env python3
"""Standard-library tests for change_check.py."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "change_check.py"
SPEC = importlib.util.spec_from_file_location("change_check", SCRIPT)
assert SPEC and SPEC.loader
change_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(change_check)


def git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, stdout=subprocess.PIPE)


def make_repo(path: Path) -> None:
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.email", "tests@example.com")
    git(path, "config", "user.name", "Skill Tests")
    (path / "tracked.py").write_bytes(b"value = 1\r\n")
    git(path, "add", "tracked.py")
    git(path, "commit", "-qm", "initial")


class ChangeCheckTests(unittest.TestCase):
    def test_scopes_include_staged_unstaged_and_untracked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            make_repo(repo)
            (repo / "tracked.py").write_bytes(b"value = 2\r\n")
            (repo / "staged.py").write_text("staged = True\n")
            git(repo, "add", "staged.py")
            (repo / "untracked.py").write_text("untracked = True\n")

            working = change_check.python_files(repo, "working")
            staged = change_check.python_files(repo, "staged")
            all_files = change_check.python_files(repo, "all")
            self.assertEqual(set(working["selected"]), {"tracked.py", "untracked.py"})
            self.assertEqual(staged["selected"], ["staged.py"])
            self.assertEqual(
                set(all_files["selected"]), {"tracked.py", "staged.py", "untracked.py"}
            )

    def test_compile_detects_syntax_error_without_source_pycache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            make_repo(repo)
            (repo / "broken.py").write_text("def broken(:\n")
            result = change_check.compile_check(repo, ["broken.py"])
            self.assertFalse(result["ok"])
            self.assertFalse((repo / "__pycache__").exists())

    def test_crlf_is_allowed_but_real_trailing_space_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            make_repo(repo)
            (repo / "tracked.py").write_bytes(b"value = 2\r\n")
            files = change_check.python_files(repo, "working")
            checks = change_check.whitespace_checks(repo, "working", files)
            self.assertTrue(all(check["ok"] for check in checks), checks)

            (repo / "tracked.py").write_bytes(b"value = 2 \r\n")
            files = change_check.python_files(repo, "working")
            checks = change_check.whitespace_checks(repo, "working", files)
            self.assertFalse(checks[0]["ok"])

    def test_json_report_shape_and_syntax_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            make_repo(repo)
            (repo / "bad.py").write_text("if True print('bad')\n")
            report = change_check.run_checks(repo, "working", [])
            self.assertFalse(report["summary"]["passed"])
            self.assertIn("py-compile", report["summary"]["failures"])
            self.assertIn("tools", report)


if __name__ == "__main__":
    unittest.main()
