#!/usr/bin/env python3
"""Run read-only checks for selected Python changes in a Git repository."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


class RepositoryError(RuntimeError):
    """Raised when the target is not a usable Git repository."""


def command(
    args: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_sec: float = 300,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            list(args),
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "argv": list(args)}
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "argv": list(args),
    }


def git_lines(repo: Path, *args: str) -> list[str]:
    result = command(("git", *args), cwd=repo)
    if not result["ok"]:
        raise RepositoryError(
            result.get("stderr") or result.get("error") or f"git {' '.join(args)} failed"
        )
    return [line for line in result["stdout"].splitlines() if line]


def repository_root(path: Path) -> Path:
    result = command(("git", "rev-parse", "--show-toplevel"), cwd=path)
    if not result["ok"]:
        raise RepositoryError(
            result.get("stderr") or result.get("error") or f"{path} is not a Git repository"
        )
    return Path(result["stdout"]).resolve()


def python_files(repo: Path, scope: str) -> dict[str, list[str]]:
    unstaged = (
        git_lines(repo, "diff", "--name-only", "--diff-filter=ACMR", "--", "*.py")
        if scope in ("working", "all")
        else []
    )
    untracked = (
        git_lines(repo, "ls-files", "--others", "--exclude-standard", "--", "*.py")
        if scope in ("working", "all")
        else []
    )
    staged = (
        git_lines(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", "*.py")
        if scope in ("staged", "all")
        else []
    )
    selected = sorted(
        {
            item
            for item in (*unstaged, *untracked, *staged)
            if (repo / item).is_file() and Path(item).suffix == ".py"
        }
    )
    return {
        "unstaged": sorted(set(unstaged)),
        "staged": sorted(set(staged)),
        "untracked": sorted(set(untracked)),
        "selected": selected,
    }


def whitespace_checks(repo: Path, scope: str, files: dict[str, list[str]]) -> list[dict[str, Any]]:
    results = []
    if scope in ("working", "all"):
        results.append(
            {
                "name": "working-diff-whitespace",
                **command(
                    ("git", "-c", "core.whitespace=cr-at-eol", "diff", "--check"),
                    cwd=repo,
                ),
            }
        )
    if scope in ("staged", "all"):
        results.append(
            {
                "name": "staged-diff-whitespace",
                **command(
                    ("git", "-c", "core.whitespace=cr-at-eol", "diff", "--cached", "--check"),
                    cwd=repo,
                ),
            }
        )
    trailing = []
    for relative in files["untracked"]:
        path = repo / relative
        if not path.is_file():
            continue
        for number, raw_line in enumerate(path.read_bytes().splitlines(keepends=True), 1):
            content = raw_line.rstrip(b"\r\n")
            if content.endswith((b" ", b"\t")):
                trailing.append(f"{relative}:{number}: trailing whitespace")
    results.append(
        {
            "name": "untracked-trailing-whitespace",
            "ok": not trailing,
            "stdout": "\n".join(trailing),
            "stderr": "",
        }
    )
    return results


def compile_check(repo: Path, files: list[str]) -> dict[str, Any]:
    if not files:
        return {"name": "py-compile", "ok": True, "skipped": True, "reason": "no Python files"}
    with tempfile.TemporaryDirectory(prefix="vllm-change-check-") as temp_dir:
        environment = os.environ.copy()
        environment["PYTHONPYCACHEPREFIX"] = temp_dir
        result = command(
            (sys.executable, "-m", "py_compile", *(str(repo / item) for item in files)),
            cwd=repo,
            env=environment,
        )
    return {"name": "py-compile", **result}


def pytest_check(repo: Path, targets: list[str]) -> dict[str, Any]:
    if not targets:
        return {"name": "pytest", "ok": True, "skipped": True, "reason": "no targets requested"}
    result = command(
        (sys.executable, "-m", "pytest", *targets),
        cwd=repo,
        timeout_sec=1800,
    )
    return {"name": "pytest", **result}


def run_checks(repo_arg: Path, scope: str, pytest_targets: list[str]) -> dict[str, Any]:
    repo = repository_root(repo_arg.resolve())
    files = python_files(repo, scope)
    checks = whitespace_checks(repo, scope, files)
    checks.append(compile_check(repo, files["selected"]))
    checks.append(pytest_check(repo, pytest_targets))
    tools = {
        "ruff": shutil.which("ruff"),
        "pre-commit": shutil.which("pre-commit"),
        "format.sh": str(repo / "format.sh") if (repo / "format.sh").is_file() else None,
    }
    failures = [check["name"] for check in checks if not check.get("ok", False)]
    return {
        "repository": str(repo),
        "scope": scope,
        "files": files,
        "tools": tools,
        "checks": checks,
        "summary": {"passed": not failures, "failures": failures},
    }


def print_human(report: dict[str, Any]) -> None:
    print(f"repository: {report['repository']}")
    print(f"scope: {report['scope']}")
    print("files:")
    for category in ("unstaged", "staged", "untracked", "selected"):
        values = report["files"][category]
        print(f"  {category}: {', '.join(values) if values else '-'}")
    print("tools:")
    for name, path in report["tools"].items():
        print(f"  {name}: {path or 'missing'}")
    print("checks:")
    for check in report["checks"]:
        state = "passed" if check.get("ok") else "failed"
        if check.get("skipped"):
            state = f"skipped ({check['reason']})"
        print(f"  {check['name']}: {state}")
        detail = check.get("stderr") or check.get("stdout")
        if detail and not check.get("ok"):
            for line in detail.splitlines()[:20]:
                print(f"    {line}")
    print(f"summary: {'passed' if report['summary']['passed'] else 'failed'}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--scope", choices=("working", "staged", "all"), default="all")
    parser.add_argument("--pytest-target", action="append", default=[])
    parser.add_argument(
        "--json-output",
        nargs="?",
        const="-",
        metavar="PATH",
        help="Write JSON to PATH, or stdout when PATH is omitted",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_checks(args.repo, args.scope, args.pytest_target)
    except RepositoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json_output:
        serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.json_output == "-":
            print(serialized, end="")
        else:
            output = Path(args.json_output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(serialized, encoding="utf-8")
    else:
        print_human(report)
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
