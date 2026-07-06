#!/usr/bin/env python3
"""Send OpenAI-compatible completions requests and print latency metadata."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _load_prompt(args: argparse.Namespace) -> str:
    sources = [args.prompt is not None, args.prompt_file is not None, args.repeat_count is not None]
    if sum(sources) != 1:
        raise SystemExit("choose exactly one of --prompt, --prompt-file, or --repeat-count")

    if args.prompt is not None:
        return args.prompt
    if args.prompt_file is not None:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if args.repeat_count <= 0:
        raise SystemExit("--repeat-count must be > 0")
    return args.repeat_text * args.repeat_count


def _parse_extra_json(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise SystemExit("--extra-json must decode to a JSON object")
    return parsed


def _build_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": _load_prompt(args),
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "stream": False,
    }
    payload.update(_parse_extra_json(args.extra_json))
    return payload


def _request(url: str, payload: dict[str, Any], timeout: float, use_env_proxy: bool) -> tuple[int, bytes, float]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener() if use_env_proxy else urllib.request.build_opener(
        urllib.request.ProxyHandler({})
    )
    start = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, body, time.perf_counter() - start
    except urllib.error.HTTPError as exc:
        body = exc.read()
        elapsed = time.perf_counter() - start
        print(f"http_error={exc.code}")
        print(f"elapsed_sec={elapsed:.6f}")
        print(body[:4000].decode("utf-8", errors="replace"))
        raise SystemExit(1) from exc
    except Exception as exc:
        elapsed = time.perf_counter() - start
        print(f"error={type(exc).__name__}: {exc}")
        print(f"elapsed_sec={elapsed:.6f}")
        raise SystemExit(1) from exc


def _parse_body(body: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _print_response(prefix: str, status: int, body: bytes, elapsed: float) -> dict[str, Any]:
    print(f"{prefix}status={status}")
    print(f"{prefix}elapsed_sec={elapsed:.6f}")
    print(f"{prefix}response_bytes={len(body)}")
    parsed = _parse_body(body)
    if parsed is None:
        print(body[:1000].decode("utf-8", errors="replace"))
        return {"status": status, "elapsed_sec": elapsed, "response_bytes": len(body)}

    print(prefix + "usage=" + json.dumps(parsed.get("usage", {}), ensure_ascii=False, sort_keys=True))
    choices = parsed.get("choices") or []
    result: dict[str, Any] = {
        "status": status,
        "elapsed_sec": elapsed,
        "response_bytes": len(body),
        "usage": parsed.get("usage", {}),
    }
    if choices:
        print(prefix + "finish_reason=" + str(choices[0].get("finish_reason")))
        print(prefix + "text=" + repr(choices[0].get("text", "")))
        result["finish_reason"] = choices[0].get("finish_reason")
        result["text"] = choices[0].get("text", "")
    return result


def _build_summary(elapsed_values: list[float]) -> dict[str, Any]:
    if not elapsed_values:
        return {}
    return {
        "measured_count": len(elapsed_values),
        "measured_elapsed_sec": [round(value, 6) for value in elapsed_values],
        "measured_min_sec": min(elapsed_values),
        "measured_avg_sec": sum(elapsed_values) / len(elapsed_values),
        "measured_max_sec": max(elapsed_values),
    }


def _print_summary(summary: dict[str, Any]) -> None:
    if not summary:
        return
    print("measured_count=" + str(summary["measured_count"]))
    print("measured_elapsed_sec=" + json.dumps(summary["measured_elapsed_sec"]))
    print(f"measured_min_sec={summary['measured_min_sec']:.6f}")
    print(f"measured_avg_sec={summary['measured_avg_sec']:.6f}")
    print(f"measured_max_sec={summary['measured_max_sec']:.6f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Service base URL, e.g. http://127.0.0.1:2723")
    parser.add_argument("--endpoint", default="/v1/completions", help="Completion endpoint path")
    parser.add_argument("--model", default="model")
    parser.add_argument("--prompt", help="Prompt text")
    parser.add_argument("--prompt-file", help="UTF-8 prompt file")
    parser.add_argument("--repeat-text", default="hello ", help="Text repeated when using --repeat-count")
    parser.add_argument("--repeat-count", type=int, help="Repeat --repeat-text this many times")
    parser.add_argument("--max-tokens", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--extra-json", help="JSON object merged into the request payload")
    parser.add_argument("--output", help="Optional path to write the final response body")
    parser.add_argument("--summary-json", help="Optional path to write structured run metadata")
    parser.add_argument("--use-env-proxy", action="store_true", help="Use HTTP proxy variables from the environment")
    parser.add_argument("--warmup-requests", type=int, default=0, help="Requests to send before measured requests")
    parser.add_argument("--requests", type=int, default=1, help="Measured requests to send")
    parser.add_argument("--interval-sec", type=float, default=0, help="Sleep between requests")
    args = parser.parse_args()
    if args.warmup_requests < 0:
        raise SystemExit("--warmup-requests must be >= 0")
    if args.requests < 1:
        raise SystemExit("--requests must be >= 1")
    if args.interval_sec < 0:
        raise SystemExit("--interval-sec must be >= 0")

    payload = _build_payload(args)
    url = args.base_url.rstrip("/") + "/" + args.endpoint.lstrip("/")
    measured_elapsed: list[float] = []
    warmup_records: list[dict[str, Any]] = []
    measured_records: list[dict[str, Any]] = []
    final_body = b""
    total_requests = args.warmup_requests + args.requests
    multi = total_requests > 1

    for index in range(args.warmup_requests):
        status, body, elapsed = _request(url, payload, args.timeout, args.use_env_proxy)
        final_body = body
        warmup_records.append(_print_response(f"warmup_{index + 1}_", status, body, elapsed))
        if args.interval_sec and (index + 1 < total_requests):
            time.sleep(args.interval_sec)

    for index in range(args.requests):
        status, body, elapsed = _request(url, payload, args.timeout, args.use_env_proxy)
        final_body = body
        measured_elapsed.append(elapsed)
        prefix = f"request_{index + 1}_" if multi else ""
        measured_records.append(_print_response(prefix, status, body, elapsed))
        if args.interval_sec and index + 1 < args.requests:
            time.sleep(args.interval_sec)

    summary = _build_summary(measured_elapsed)
    _print_summary(summary)
    if args.output:
        Path(args.output).write_bytes(final_body)
    if args.summary_json:
        metadata = {
            "url": url,
            "model": args.model,
            "endpoint": args.endpoint,
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
            "warmup_requests": args.warmup_requests,
            "requests": args.requests,
            "interval_sec": args.interval_sec,
            "prompt_chars": len(payload["prompt"]),
            "summary": summary,
            "warmup": warmup_records,
            "measured": measured_records,
        }
        Path(args.summary_json).write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
