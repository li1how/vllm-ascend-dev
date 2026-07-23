#!/usr/bin/env python3
"""Compare baseline and candidate OpenAI-compatible serving responses."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from serving_common import (
    ServingError,
    http_json,
    join_url,
    normalize_response,
    redact_data,
    redact_log_file,
    wait_for_health,
    write_json,
)

DEFAULT_FIELDS = ("output_text", "finish_reason", "output_tokens")


class ConfigError(ValueError):
    """Invalid comparison case."""


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a JSON object")
    return value


def load_case(path: Path) -> dict[str, Any]:
    try:
        case = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot load case {path}: {exc}") from exc
    return _object(case, "case")


def find_workspace(start: Path) -> Path:
    for candidate in (start.resolve(), *start.resolve().parents):
        if (candidate / "AGENTS.md").is_file() and (candidate / ".agents" / "skills").is_dir():
            return candidate
    return Path.cwd().resolve()


def default_output_dir(case_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return find_workspace(case_path.parent) / "tmp" / "vllm-serving-validation" / stamp


def validate_side(side: dict[str, Any], name: str, case_dir: Path) -> dict[str, Any]:
    base_url = side.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise ConfigError(f"{name}.base_url must be a non-empty string")
    command = side.get("command")
    if command is not None:
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(item, str) and item for item in command)
        ):
            raise ConfigError(f"{name}.command must be a non-empty JSON array of strings")
    env = side.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(key, str) and isinstance(value, (str, int, float, bool))
        for key, value in env.items()
    ):
        raise ConfigError(f"{name}.env must map names to scalar values")
    result = dict(side)
    if result.get("cwd") is not None:
        cwd = Path(str(result["cwd"]))
        result["cwd"] = str(cwd if cwd.is_absolute() else (case_dir / cwd).resolve())
    result.setdefault("health_path", "/v1/models")
    result.setdefault("startup_timeout_sec", 1200)
    result.setdefault("shutdown_grace_sec", 30)
    for key in ("startup_timeout_sec", "shutdown_grace_sec"):
        if not isinstance(result[key], (int, float)) or result[key] <= 0:
            raise ConfigError(f"{name}.{key} must be positive")
    return result


def validate_case(case: dict[str, Any], case_path: Path) -> dict[str, Any]:
    request = _object(case.get("request"), "request")
    if not isinstance(request.get("endpoint"), str) or not request["endpoint"]:
        raise ConfigError("request.endpoint must be a non-empty string")
    _object(request.get("payload"), "request.payload")
    if request["payload"].get("stream") is True:
        raise ConfigError("streaming responses are not supported")
    validated_request = dict(request)
    validated_request.setdefault("warmup_requests", 0)
    validated_request.setdefault("requests", 1)
    validated_request.setdefault("timeout_sec", 1800)
    if (
        not isinstance(validated_request["warmup_requests"], int)
        or validated_request["warmup_requests"] < 0
        or not isinstance(validated_request["requests"], int)
        or validated_request["requests"] < 1
        or not isinstance(validated_request["timeout_sec"], (int, float))
        or validated_request["timeout_sec"] <= 0
    ):
        raise ConfigError("request counts and timeout are invalid")
    compare = _object(case.get("compare", {}), "compare")
    fields = compare.get("fields", list(DEFAULT_FIELDS))
    if not isinstance(fields, list) or not fields or not all(isinstance(item, str) for item in fields):
        raise ConfigError("compare.fields must be a non-empty string array")
    validated_compare = dict(compare)
    validated_compare["fields"] = fields
    validated_compare.setdefault("exact_token_ids", False)
    if "prompt_tokens_min" in validated_compare and not isinstance(
        validated_compare["prompt_tokens_min"], int
    ):
        raise ConfigError("compare.prompt_tokens_min must be an integer")
    return {
        "name": str(case.get("name", case_path.stem)),
        "request": validated_request,
        "baseline": validate_side(_object(case.get("baseline"), "baseline"), "baseline", case_path.parent),
        "candidate": validate_side(
            _object(case.get("candidate"), "candidate"), "candidate", case_path.parent
        ),
        "compare": validated_compare,
    }


def stop_process(process: subprocess.Popen[bytes], grace_sec: float) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_sec
    if process.poll() is None:
        try:
            process.wait(timeout=grace_sec)
        except subprocess.TimeoutExpired:
            pass
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


@contextmanager
def service(
    side: Mapping[str, Any],
    name: str,
    output_dir: Path,
    *,
    use_env_proxy: bool,
) -> Iterator[None]:
    command = side.get("command")
    if command is None:
        yield
        return
    log_path = output_dir / f"{name}-service.log"
    log_handle = log_path.open("wb")
    environment = os.environ.copy()
    environment.update({key: str(value) for key, value in side.get("env", {}).items()})
    try:
        process = subprocess.Popen(
            command,
            cwd=side.get("cwd"),
            env=environment,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        log_handle.close()
        raise
    try:
        wait_for_health(
            str(side["base_url"]),
            str(side["health_path"]),
            timeout_sec=float(side["startup_timeout_sec"]),
            use_env_proxy=use_env_proxy,
            process_exited=process.poll,
        )
        yield
    finally:
        stop_process(process, float(side["shutdown_grace_sec"]))
        log_handle.close()
        redact_log_file(log_path, side.get("env", {}))


def run_requests(
    side: Mapping[str, Any],
    request: Mapping[str, Any],
    name: str,
    output_dir: Path,
    *,
    use_env_proxy: bool,
) -> list[dict[str, Any]]:
    url = join_url(str(side["base_url"]), str(request["endpoint"]))
    total = int(request["warmup_requests"]) + int(request["requests"])
    records = []
    for index in range(total):
        result = http_json(
            url,
            payload=dict(request["payload"]),
            timeout_sec=float(request["timeout_sec"]),
            use_env_proxy=use_env_proxy,
        )
        response = result.json_object()
        raw_path = output_dir / f"{name}-response-{index + 1}.json"
        write_json(raw_path, response)
        if index >= int(request["warmup_requests"]):
            records.append(
                {
                    "request_index": index - int(request["warmup_requests"]) + 1,
                    "latency_sec": result.elapsed_sec,
                    "normalized": normalize_response(response),
                    "response_file": str(raw_path),
                }
            )
    return records


def compare_records(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    settings: Mapping[str, Any],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    if len(baseline) != len(candidate):
        return [{"field": "request_count", "baseline": len(baseline), "candidate": len(candidate)}]
    minimum = settings.get("prompt_tokens_min")
    for index, (base, cand) in enumerate(zip(baseline, candidate), 1):
        base_norm = base["normalized"]
        cand_norm = cand["normalized"]
        for field in settings["fields"]:
            if base_norm.get(field) != cand_norm.get(field):
                mismatches.append(
                    {
                        "request_index": index,
                        "field": field,
                        "baseline": base_norm.get(field),
                        "candidate": cand_norm.get(field),
                    }
                )
        if settings.get("exact_token_ids"):
            if base_norm.get("token_ids") is None or cand_norm.get("token_ids") is None:
                raise ServingError(
                    "exact token comparison requested but a response exposes no token IDs or logprobs tokens"
                )
            if base_norm["token_ids"] != cand_norm["token_ids"]:
                mismatches.append(
                    {
                        "request_index": index,
                        "field": "token_ids",
                        "baseline": base_norm["token_ids"],
                        "candidate": cand_norm["token_ids"],
                    }
                )
        if minimum is not None:
            for side_name, normalized in (("baseline", base_norm), ("candidate", cand_norm)):
                actual = normalized.get("prompt_tokens")
                if not isinstance(actual, int):
                    raise ServingError(
                        f"prompt_tokens_min requested but {side_name} response has no prompt token count"
                    )
                if actual < minimum:
                    mismatches.append(
                        {
                            "request_index": index,
                            "field": f"{side_name}.prompt_tokens_min",
                            "expected_min": minimum,
                            "actual": actual,
                        }
                    )
    return mismatches


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--use-env-proxy", action="store_true")
    return parser.parse_args(argv)


def execute(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    case_path = args.case.resolve()
    case = validate_case(load_case(case_path), case_path)
    output_dir = (args.output_dir or default_output_dir(case_path)).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "case.redacted.json", redact_data(case))
    started = time.time()
    with service(case["baseline"], "baseline", output_dir, use_env_proxy=args.use_env_proxy):
        baseline = run_requests(
            case["baseline"], case["request"], "baseline", output_dir, use_env_proxy=args.use_env_proxy
        )
    with service(case["candidate"], "candidate", output_dir, use_env_proxy=args.use_env_proxy):
        candidate = run_requests(
            case["candidate"], case["request"], "candidate", output_dir, use_env_proxy=args.use_env_proxy
        )
    mismatches = compare_records(baseline, candidate, case["compare"])
    report = {
        "case": case["name"],
        "status": "match" if not mismatches else "mismatch",
        "output_dir": str(output_dir),
        "duration_sec": time.time() - started,
        "compare": case["compare"],
        "baseline": baseline,
        "candidate": candidate,
        "mismatches": mismatches,
    }
    write_json(output_dir / "report.json", report)
    return (0 if not mismatches else 1), report


def main(argv: list[str] | None = None) -> int:
    try:
        code, report = execute(parse_args(argv))
    except (ConfigError, ServingError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(redact_data(report), ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
