#!/usr/bin/env python3
"""Diagnose vLLM workspace, source-install, Git, Python, and optional NPU state."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

MODULE_DISTRIBUTIONS = {
    "vllm": "vllm",
    "vllm_ascend": "vllm-ascend",
    "torch": "torch",
    "torch_npu": "torch-npu",
}
TOOLS = ("vllm", "pytest", "ruff", "pre-commit", "gh", "conda")
SAFE_ENV_NAMES = ("ASCEND_RT_VISIBLE_DEVICES", "NPU_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES")


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout_sec: float = 10,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def find_workspace(start: Path) -> Path | None:
    resolved = start.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / "AGENTS.md").is_file() and (candidate / "README.md").is_file():
            if (candidate / "vllm").exists() or (candidate / "vllm-ascend").exists():
                return candidate
    return None


def git_info(path: Path) -> dict[str, Any]:
    if not (path / ".git").exists():
        return {"present": path.exists(), "is_git": False}

    def value(*args: str) -> str | None:
        result = run_command(("git", *args), cwd=path)
        return result.get("stdout") if result["ok"] else None

    status = value("status", "--porcelain=v1")
    return {
        "present": True,
        "is_git": True,
        "branch": value("branch", "--show-current"),
        "head": value("rev-parse", "HEAD"),
        "upstream": value("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
        "dirty": bool(status),
        "status": status.splitlines() if status else [],
    }


MODULE_PROBE = r"""
import importlib
import importlib.metadata
import importlib.util
import json
import pathlib
import sys

module_name, distribution, should_import = sys.argv[1:4]
result = {"name": module_name, "available": False}
try:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        print(json.dumps(result))
        raise SystemExit(0)
    result["available"] = True
    result["spec_origin"] = spec.origin
    if should_import == "1":
        module = importlib.import_module(module_name)
        result["module_path"] = getattr(module, "__file__", spec.origin)
        result["version"] = getattr(module, "__version__", None)
    try:
        dist = importlib.metadata.distribution(distribution)
        result["distribution_version"] = dist.version
        direct = dist.read_text("direct_url.json")
        if direct:
            direct_data = json.loads(direct)
            result["direct_url"] = direct_data.get("url")
            result["editable"] = bool(
                direct_data.get("dir_info", {}).get("editable", False)
            )
    except importlib.metadata.PackageNotFoundError:
        pass
except BaseException as exc:
    result["import_error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(result))
"""


def module_info(
    module_name: str,
    distribution: str,
    *,
    timeout_sec: float,
    import_module: bool = True,
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")
    result = run_command(
        (
            sys.executable,
            "-c",
            MODULE_PROBE,
            module_name,
            distribution,
            "1" if import_module else "0",
        ),
        timeout_sec=timeout_sec,
        env=environment,
    )
    if not result["ok"]:
        return {
            "name": module_name,
            "available": False,
            "probe_error": result.get("error")
            or result.get("stderr")
            or f"probe exited {result.get('returncode')}",
        }
    try:
        parsed = json.loads(result["stdout"].splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {"name": module_name, "available": False, "probe_error": str(exc)}
    return parsed


def path_is_within(path_value: str | None, expected: Path) -> bool:
    if not path_value:
        return False
    try:
        Path(path_value).resolve().relative_to(expected.resolve())
    except (OSError, ValueError):
        return False
    return True


def module_source_path(module: dict[str, Any]) -> str | None:
    direct_url = module.get("direct_url")
    if isinstance(direct_url, str) and direct_url.startswith("file://"):
        direct_url = direct_url.removeprefix("file://")
    return module.get("module_path") or module.get("spec_origin") or direct_url


NPU_PROBE = r"""
import json
result = {}
try:
    import torch
    import torch_npu
    result["torch_npu_version"] = getattr(torch_npu, "__version__", None)
    result["available"] = bool(torch.npu.is_available())
    result["device_count"] = int(torch.npu.device_count())
except BaseException as exc:
    result["available"] = False
    result["error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(result))
"""


def npu_info(mode: str, timeout_sec: float) -> dict[str, Any]:
    if mode == "skip":
        return {"checked": False}
    command = shutil.which("npu-smi")
    smi = (
        run_command((command, "info"), timeout_sec=timeout_sec)
        if command
        else {"ok": False, "error": "npu-smi not found"}
    )
    if isinstance(smi.get("stdout"), str):
        smi["stdout"] = smi["stdout"][:4000]
    if isinstance(smi.get("stderr"), str):
        smi["stderr"] = smi["stderr"][:2000]
    environment = os.environ.copy()
    result = run_command(
        (sys.executable, "-c", NPU_PROBE),
        timeout_sec=timeout_sec,
        env=environment,
    )
    device: dict[str, Any]
    if result["ok"]:
        try:
            device = json.loads(result["stdout"].splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            device = {"available": False, "probe_error": str(exc)}
    else:
        device = {
            "available": False,
            "probe_error": result.get("error") or result.get("stderr"),
        }
    return {
        "checked": True,
        "mode": mode,
        "visible_devices": {
            name: os.environ[name] for name in SAFE_ENV_NAMES if name in os.environ
        },
        "npu_smi_path": command,
        "npu_smi": smi,
        "device_probe": device,
    }


def add_issue(
    issues: list[dict[str, str]],
    severity: str,
    code: str,
    message: str,
) -> None:
    issues.append({"severity": severity, "code": code, "message": message})


def diagnose(workspace: Path, npu_mode: str, timeout_sec: float) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    repositories = {}
    for name in ("vllm", "vllm-ascend"):
        repo_path = workspace / name
        repositories[name] = git_info(repo_path)
        if not repositories[name]["is_git"]:
            add_issue(issues, "error", f"{name}-repo", f"{repo_path} is not a Git repository")

    modules = {
        name: module_info(
            name,
            distribution,
            timeout_sec=timeout_sec,
            import_module=name != "torch_npu",
        )
        for name, distribution in MODULE_DISTRIBUTIONS.items()
    }
    expected_roots = {
        "vllm": workspace / "vllm",
        "vllm_ascend": workspace / "vllm-ascend",
    }
    for name, expected in expected_roots.items():
        module = modules[name]
        if not module.get("available"):
            add_issue(issues, "error", f"{name}-import", f"{name} is not importable")
            continue
        actual = module_source_path(module)
        if not path_is_within(actual, expected):
            add_issue(
                issues,
                "error",
                f"{name}-source",
                f"{name} resolves to {actual}, expected source under {expected}",
            )
    for name in ("torch", "torch_npu"):
        module = modules[name]
        if not module.get("available"):
            severity = "warning" if name == "torch_npu" and npu_mode == "skip" else "error"
            add_issue(
                issues,
                severity,
                f"{name}-import",
                f"{name} is not importable"
                + (f": {module['probe_error']}" if module.get("probe_error") else ""),
            )
        elif module.get("import_error"):
            add_issue(issues, "error", f"{name}-import", str(module["import_error"]))

    tools = {name: shutil.which(name) for name in TOOLS}
    for name, path in tools.items():
        if path is None:
            add_issue(issues, "warning", f"tool-{name}", f"optional command not found: {name}")

    npu = npu_info(npu_mode, timeout_sec)
    if npu.get("checked") and npu_mode == "required":
        if not npu.get("npu_smi", {}).get("ok"):
            add_issue(issues, "error", "npu-smi", "npu-smi check failed")
        if not npu.get("device_probe", {}).get("available"):
            add_issue(issues, "error", "npu-device", "torch_npu reports no available device")
    elif npu.get("checked"):
        if not npu.get("npu_smi", {}).get("ok"):
            add_issue(issues, "warning", "npu-smi", "npu-smi check failed")
        if not npu.get("device_probe", {}).get("available"):
            add_issue(issues, "warning", "npu-device", "torch_npu reports no available device")

    return {
        "workspace": str(workspace),
        "python": {
            "executable": sys.executable,
            "version": sys.version.splitlines()[0],
        },
        "repositories": repositories,
        "modules": modules,
        "tools": tools,
        "npu": npu,
        "issues": issues,
        "summary": {
            "errors": sum(issue["severity"] == "error" for issue in issues),
            "warnings": sum(issue["severity"] == "warning" for issue in issues),
        },
    }


def print_human(report: dict[str, Any]) -> None:
    print(f"workspace: {report['workspace']}")
    print(f"python: {report['python']['executable']} ({report['python']['version']})")
    print("repositories:")
    for name, info in report["repositories"].items():
        print(
            f"  {name}: branch={info.get('branch') or '-'} "
            f"head={info.get('head') or '-'} upstream={info.get('upstream') or '-'} "
            f"dirty={info.get('dirty', False)}"
        )
    print("modules:")
    for name, info in report["modules"].items():
        print(
            f"  {name}: version={info.get('version') or info.get('distribution_version') or '-'} "
            f"path={module_source_path(info) or '-'} "
            f"editable={info.get('editable', False)}"
        )
    print("tools:")
    for name, path in report["tools"].items():
        print(f"  {name}: {path or 'missing'}")
    if report["npu"].get("checked"):
        print(
            "npu: "
            f"npu-smi={'ok' if report['npu']['npu_smi'].get('ok') else 'failed'} "
            f"available={report['npu']['device_probe'].get('available', False)}"
        )
    print("issues:")
    if not report["issues"]:
        print("  none")
    for issue in report["issues"]:
        print(f"  [{issue['severity']}] {issue['code']}: {issue['message']}")
    print(
        f"summary: errors={report['summary']['errors']} warnings={report['summary']['warnings']}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--npu-mode", choices=("skip", "auto", "required"), default="skip")
    parser.add_argument("--timeout-sec", type=float, default=20)
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
    if args.timeout_sec <= 0:
        print("error: --timeout-sec must be positive", file=sys.stderr)
        return 2
    workspace = args.workspace.resolve() if args.workspace else find_workspace(Path.cwd())
    if workspace is None or not workspace.is_dir():
        print("error: could not locate a vLLM workspace", file=sys.stderr)
        return 2
    report = diagnose(workspace, args.npu_mode, args.timeout_sec)
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
    return 1 if report["summary"]["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
