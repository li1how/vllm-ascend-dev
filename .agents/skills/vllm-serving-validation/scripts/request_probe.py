#!/usr/bin/env python3
"""Probe an OpenAI-compatible completion or chat endpoint."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from serving_common import ServingError, http_json, join_url, normalize_response, wait_for_health, write_json


def _json_object(value: str, option: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{option} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{option} must contain a JSON object")
    return parsed


def _load_prompt(args: argparse.Namespace) -> str:
    selected = [args.prompt is not None, args.prompt_file is not None, args.repeat_count is not None]
    if sum(selected) != 1:
        raise ValueError("choose exactly one of --prompt, --prompt-file, or --repeat-count")
    if args.prompt is not None:
        return args.prompt
    if args.prompt_file is not None:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if args.repeat_count <= 0:
        raise ValueError("--repeat-count must be positive")
    return args.repeat_text * args.repeat_count


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload_file and args.payload_json:
        raise ValueError("choose only one of --payload-file and --payload-json")
    if args.payload_file:
        payload = _json_object(Path(args.payload_file).read_text(encoding="utf-8"), "--payload-file")
    elif args.payload_json:
        payload = _json_object(args.payload_json, "--payload-json")
    else:
        prompt = _load_prompt(args)
        payload = {
            "model": args.model,
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
            "stream": False,
        }
        if args.kind == "chat":
            payload["messages"] = [{"role": "user", "content": prompt}]
        else:
            payload["prompt"] = prompt
    if args.extra_json:
        payload.update(_json_object(args.extra_json, "--extra-json"))
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--kind", choices=("completion", "chat"), default="completion")
    parser.add_argument("--endpoint", help="Defaults to /v1/completions or /v1/chat/completions")
    parser.add_argument("--payload-file", type=Path)
    parser.add_argument("--payload-json")
    parser.add_argument("--model", default="model")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--repeat-text", default="hello ")
    parser.add_argument("--repeat-count", type=int)
    parser.add_argument("--max-tokens", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--extra-json")
    parser.add_argument("--warmup-requests", type=int, default=0)
    parser.add_argument("--requests", type=int, default=1)
    parser.add_argument("--interval-sec", type=float, default=0)
    parser.add_argument("--timeout-sec", type=float, default=1800)
    parser.add_argument("--wait-ready", action="store_true")
    parser.add_argument("--health-path", default="/v1/models")
    parser.add_argument("--startup-timeout-sec", type=float, default=1200)
    parser.add_argument("--use-env-proxy", action="store_true")
    parser.add_argument("--output", type=Path, help="Write the final raw JSON response")
    parser.add_argument("--summary-json", type=Path)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.warmup_requests < 0 or args.requests < 1:
        raise ValueError("warmup requests must be >= 0 and measured requests must be >= 1")
    if args.interval_sec < 0 or args.timeout_sec <= 0:
        raise ValueError("interval must be >= 0 and timeout must be positive")
    payload = build_payload(args)
    endpoint = args.endpoint or (
        "/v1/chat/completions" if args.kind == "chat" else "/v1/completions"
    )
    if args.wait_ready:
        wait_for_health(
            args.base_url,
            args.health_path,
            timeout_sec=args.startup_timeout_sec,
            use_env_proxy=args.use_env_proxy,
        )
    url = join_url(args.base_url, endpoint)
    records: list[dict[str, Any]] = []
    final_body = b""
    total = args.warmup_requests + args.requests
    for index in range(total):
        result = http_json(
            url,
            payload=payload,
            timeout_sec=args.timeout_sec,
            use_env_proxy=args.use_env_proxy,
        )
        final_body = result.body
        normalized = normalize_response(result.json_object())
        records.append(
            {
                "phase": "warmup" if index < args.warmup_requests else "measured",
                "index": index + 1,
                "status": result.status,
                "latency_sec": result.elapsed_sec,
                "response_bytes": len(result.body),
                "normalized": normalized,
            }
        )
        if args.interval_sec and index + 1 < total:
            time.sleep(args.interval_sec)
    measured = [record for record in records if record["phase"] == "measured"]
    latencies = [record["latency_sec"] for record in measured]
    summary = {
        "url": url,
        "kind": args.kind,
        "warmup_requests": args.warmup_requests,
        "requests": args.requests,
        "records": records,
        "latency": {
            "min_sec": min(latencies),
            "avg_sec": sum(latencies) / len(latencies),
            "max_sec": max(latencies),
        },
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(final_body)
    if args.summary_json:
        write_json(args.summary_json, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    try:
        summary = run(parse_args(argv))
    except (OSError, ValueError, ServingError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
